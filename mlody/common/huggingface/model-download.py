#!/usr/bin/env python3

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import requests
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi, model_info

HF_FILE = "https://huggingface.co/{repo}/resolve/{revision}/{path}"
SEGMENT_SIZE = 64 * 1024 * 1024
REQUEST_TIMEOUT = (10, 60)


# -----------------------------------------
# bandwidth probe
# -----------------------------------------


def measure_bandwidth():
    test_url = "https://huggingface.co/gpt2/resolve/main/config.json"

    start = time.time()
    with requests.get(test_url, timeout=REQUEST_TIMEOUT) as r:
        r.raise_for_status()
        size = len(r.content)
    elapsed = time.time() - start

    bps = size / elapsed
    gbps = bps * 8 / 1e9

    return max(gbps, 0.1)


# -----------------------------------------
# worker estimation
# -----------------------------------------


def estimate_workers(gbps):
    cores = multiprocessing.cpu_count()

    cpu_limit = cores * 4
    net_limit = int(gbps * 16)

    return max(4, min(cpu_limit, net_limit))


def partial_path(path):
    return path.with_name(f"{path.name}.partial")


def partial_metadata_path(path):
    return path.with_name(f"{path.name}.metadata.json")


def build_segments(size):
    segments = []

    for start in range(0, size, SEGMENT_SIZE):
        end = min(start + SEGMENT_SIZE - 1, size - 1)
        segments.append((start, end))

    return segments


def load_partial_metadata(path, size, segment_count):
    metadata_path = partial_metadata_path(path)

    if not path.exists() or not metadata_path.exists():
        return None

    try:
        with open(metadata_path) as f:
            metadata = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if (
        metadata.get("size") != size
        or metadata.get("segment_size") != SEGMENT_SIZE
        or metadata.get("segment_count") != segment_count
    ):
        return None

    completed = metadata.get("completed_segments")

    if not isinstance(completed, list) or len(completed) != segment_count:
        return None

    if not all(isinstance(value, bool) for value in completed):
        return None

    return metadata


def initialize_partial_state(path, size, segment_count):
    with open(path, "wb") as f:
        f.truncate(size)

    metadata = {
        "size": size,
        "segment_size": SEGMENT_SIZE,
        "segment_count": segment_count,
        "completed_segments": [False] * segment_count,
    }

    with open(partial_metadata_path(path), "w") as f:
        json.dump(metadata, f)

    return metadata


# -----------------------------------------
# segmented download
# -----------------------------------------


def download_segment(url, start, end, path, token):
    headers = {"Range": f"bytes={start}-{end}"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    expected_size = end - start + 1
    written = 0

    with requests.get(
        url,
        headers=headers,
        stream=True,
        timeout=REQUEST_TIMEOUT,
    ) as r:
        r.raise_for_status()

        if r.status_code != 206:
            raise RuntimeError(
                f"Range request for {path} returned {r.status_code} instead of 206"
            )

        with open(path, "r+b") as f:
            f.seek(start)
            for chunk in r.iter_content(1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                written += len(chunk)

    if written != expected_size:
        raise RuntimeError(
            f"Segment {start}-{end} for {path} wrote {written} bytes, expected {expected_size}"
        )


def segmented_download(url, dest, token, workers):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.head(
        url,
        headers=headers,
        allow_redirects=True,
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    size = int(r.headers["Content-Length"])

    segments = build_segments(size)
    metadata = load_partial_metadata(dest, size, len(segments))

    if metadata is None:
        metadata = initialize_partial_state(dest, size, len(segments))

    pending_segments = [
        (index, start, end)
        for index, (start, end) in enumerate(segments)
        if not metadata["completed_segments"][index]
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}

        for index, start, end in pending_segments:
            future = pool.submit(download_segment, url, start, end, dest, token)
            futures[future] = index

        for future in concurrent.futures.as_completed(futures):
            future.result()
            metadata["completed_segments"][futures[future]] = True
            with open(partial_metadata_path(dest), "w") as f:
                json.dump(metadata, f)


# -----------------------------------------
# file download
# -----------------------------------------


def download_file(repo, revision, file_path, dest, token, workers):
    url = HF_FILE.format(repo=repo, revision=revision, path=file_path)

    out = dest / file_path
    out.parent.mkdir(parents=True, exist_ok=True)

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.head(
        url,
        headers=headers,
        allow_redirects=True,
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    size = int(r.headers.get("Content-Length", 0))
    partial_out = partial_path(out)
    metadata_path = partial_metadata_path(partial_out)

    if out.exists() and out.stat().st_size == size:
        if partial_out.exists():
            partial_out.unlink()
        if metadata_path.exists():
            metadata_path.unlink()
        return

    if size > 200 * 1024 * 1024:
        segmented_download(url, partial_out, token, workers)
    else:
        with requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        ) as r:
            r.raise_for_status()
            with open(partial_out, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)

    os.replace(partial_out, out)
    if metadata_path.exists():
        metadata_path.unlink()


# -----------------------------------------
# repo download
# -----------------------------------------


def download_repo(repo, revision, dest, files, workers, token):
    dest.mkdir(parents=True, exist_ok=True)

    start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []

        for f in files:
            futures.append(
                pool.submit(download_file, repo, revision, f, dest, token, workers)
            )

        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            fut.result()
            print(f"[{i}/{len(files)}] complete")

    elapsed = time.time() - start
    print(f"\nFinished in {elapsed:.1f}s")


def list_tags(repo, token):
    api = HfApi(token=token)
    refs = api.list_repo_refs(repo_id=repo, repo_type="model")

    tags = refs.tags or []

    if not tags:
        print("No tags found.")
        return

    print(f"Found {len(tags)} tag(s):")
    for tag in tags:
        target_commit = getattr(tag, "target_commit", None) or getattr(
            tag, "commit_id", ""
        )
        print(f"{tag.name}\t{target_commit}")


def list_refs(repo, token):
    api = HfApi(token=token)
    refs = api.list_repo_refs(repo_id=repo, repo_type="model")

    branches = refs.branches or []
    tags = refs.tags or []

    if not branches and not tags:
        print("No branches or tags found.")
        return

    print(f"Found {len(branches)} branch(es) and {len(tags)} tag(s).")

    if branches:
        print("\nBranches:")
        for branch in branches:
            target_commit = getattr(branch, "target_commit", None) or getattr(
                branch, "commit_id", ""
            )
            print(f"{branch.name}\t{target_commit}")

    if tags:
        print("\nTags:")
        for tag in tags:
            target_commit = getattr(tag, "target_commit", None) or getattr(
                tag, "commit_id", ""
            )
            print(f"{tag.name}\t{target_commit}")


# -----------------------------------------
# main
# -----------------------------------------


def main():
    p = argparse.ArgumentParser()
    subparsers = p.add_subparsers(dest="command")

    p_download = subparsers.add_parser("download", help="Download a model snapshot")
    p_download.add_argument("repo")
    p_download.add_argument("-o", "--out", default=None)
    p_download.add_argument("-w", "--workers", type=int)
    p_download.add_argument(
        "-r",
        "--revision",
        default=None,
        help="Specific model revision to download (commit SHA, branch, or tag). Defaults to latest when omitted.",
    )

    p_tags = subparsers.add_parser("tags", help="List available tags for a model repo")
    p_tags.add_argument("repo")
    p_releases = subparsers.add_parser(
        "releases", help="List available releases (tags) for a model repo"
    )
    p_releases.add_argument("repo")
    p_refs = subparsers.add_parser(
        "refs", help="List available branches and tags for a model repo"
    )
    p_refs.add_argument("repo")

    # Backward compatibility: allow `model-download.py <repo> ...` without explicit subcommand.
    argv = sys.argv[1:]
    if argv and argv[0] not in {
        "download",
        "tags",
        "releases",
        "refs",
        "-h",
        "--help",
    }:
        argv = ["download"] + argv

    args = p.parse_args(argv)

    token = os.environ.get("HF_TOKEN")

    if args.command is None:
        p.print_help()
        return

    if args.command in {"tags", "releases"}:
        if args.command == "releases":
            print("Hugging Face releases are represented as git tags.\n")
        list_tags(args.repo, token)
        return
    if args.command == "refs":
        list_refs(args.repo, token)
        return

    repo = args.repo
    requested_revision = args.revision
    if args.out is None:
        vendor, model = repo.split("/")
        base_out = Path(
            f"~/.cache/mlody/artifacts/huggingface/{vendor}/{model}"
        ).expanduser()
    else:
        base_out = Path(args.out)

    print(f"base_out: {base_out}")

    print("Fetching model info...")

    info = model_info(repo, revision=requested_revision, token=token)

    print("\nModel info:")
    print(info)

    sha = info.sha

    print(f"\nResolved commit SHA: {sha}")

    if requested_revision:
        print(f"Requested revision: {requested_revision}")
    else:
        print("Requested revision: latest (default)")

    model_dir = base_out / sha

    if model_dir.exists():
        print(f"\nModel already downloaded at {model_dir}")
        return

    files = [s.rfilename for s in info.siblings]

    # prioritize big weight files
    files.sort(key=lambda x: x.endswith(".safetensors"), reverse=True)

    if args.workers:
        workers = args.workers
    else:
        print("\nMeasuring bandwidth...")
        gbps = measure_bandwidth()
        print(f"Estimated {gbps:.2f} Gbps")

        workers = estimate_workers(gbps)

    print(f"Workers: {workers}")

    # save metadata
    model_dir.mkdir(parents=True, exist_ok=True)

    with open(model_dir / "model_info.json", "w") as f:
        json.dump(info.__dict__, f, indent=2, default=str)

    print("\nDownloading files...")

    download_repo(repo, sha, model_dir, files, workers, token)


if __name__ == "__main__":
    main()
