#!/usr/bin/env python3

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import requests
import time
from pathlib import Path

from huggingface_hub import model_info

HF_FILE = "https://huggingface.co/{repo}/resolve/main/{path}"
SEGMENT_SIZE = 64 * 1024 * 1024


# -----------------------------------------
# bandwidth probe
# -----------------------------------------


def measure_bandwidth():

    test_url = "https://huggingface.co/gpt2/resolve/main/config.json"

    start = time.time()
    r = requests.get(test_url)
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


# -----------------------------------------
# segmented download
# -----------------------------------------


def download_segment(url, start, end, path, token):

    headers = {"Range": f"bytes={start}-{end}"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.get(url, headers=headers, stream=True)

    with open(path, "r+b") as f:
        f.seek(start)
        for chunk in r.iter_content(1024 * 1024):
            f.write(chunk)


def segmented_download(url, dest, token, workers):

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.head(url, headers=headers)
    size = int(r.headers["Content-Length"])

    if dest.exists() and dest.stat().st_size == size:
        return

    with open(dest, "wb") as f:
        f.truncate(size)

    segments = []

    for start in range(0, size, SEGMENT_SIZE):
        end = min(start + SEGMENT_SIZE - 1, size - 1)
        segments.append((start, end))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []

        for start, end in segments:
            futures.append(pool.submit(download_segment, url, start, end, dest, token))

        for f in futures:
            f.result()


# -----------------------------------------
# file download
# -----------------------------------------


def download_file(repo, file_path, dest, token, workers):

    url = HF_FILE.format(repo=repo, path=file_path)

    out = dest / file_path
    out.parent.mkdir(parents=True, exist_ok=True)

    r = requests.head(url)
    size = int(r.headers.get("Content-Length", 0))

    if size > 200 * 1024 * 1024:
        segmented_download(url, out, token, workers)
    else:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        with requests.get(url, headers=headers, stream=True) as r:
            with open(out, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)


# -----------------------------------------
# repo download
# -----------------------------------------


def download_repo(repo, dest, files, workers, token):

    dest.mkdir(parents=True, exist_ok=True)

    start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []

        for f in files:
            futures.append(pool.submit(download_file, repo, f, dest, token, workers))

        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            fut.result()
            print(f"[{i}/{len(files)}] complete")

    elapsed = time.time() - start
    print(f"\nFinished in {elapsed:.1f}s")


# -----------------------------------------
# main
# -----------------------------------------


def main():

    p = argparse.ArgumentParser()

    p.add_argument("repo")
    p.add_argument("-o", "--out", default="models")
    p.add_argument("-w", "--workers", type=int)

    args = p.parse_args()

    repo = args.repo
    base_out = Path(args.out)

    token = os.environ.get("HF_TOKEN")

    print("Fetching model info...")

    info = model_info(repo)

    print("\nModel info:")
    print(info)

    sha = info.sha

    print(f"\nLatest commit SHA: {sha}")

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

    download_repo(repo, model_dir, files, workers, token)


if __name__ == "__main__":
    main()
