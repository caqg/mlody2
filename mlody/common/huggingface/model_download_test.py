import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    module_path = Path(__file__).with_name("model-download.py")
    spec = importlib.util.spec_from_file_location("model_download", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, *, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks


def test_download_segment_raises_when_range_request_is_not_partial(
    monkeypatch, tmp_path
):
    module = _load_module()
    destination = tmp_path / "weights.bin"
    destination.write_bytes(b"\0" * 8)
    request_kwargs = {}

    def fake_get(url, **kwargs):
        del url
        request_kwargs.update(kwargs)
        return _FakeResponse(status_code=200, chunks=[b"abcd"])

    monkeypatch.setattr(module.requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="returned 200 instead of 206"):
        module.download_segment("https://example.invalid/model", 0, 3, destination, None)

    assert request_kwargs["timeout"] == module.REQUEST_TIMEOUT


def test_segmented_download_follows_redirects_for_head(monkeypatch, tmp_path):
    module = _load_module()
    destination = tmp_path / "weights.bin"
    head_kwargs = {}

    def fake_head(url, **kwargs):
        del url
        head_kwargs.update(kwargs)
        return _FakeResponse(headers={"Content-Length": "4"})

    def fake_download_segment(url, start, end, path, token):
        del url, token
        with open(path, "r+b") as handle:
            handle.seek(start)
            handle.write(b"x" * (end - start + 1))

    monkeypatch.setattr(module.requests, "head", fake_head)
    monkeypatch.setattr(module, "download_segment", fake_download_segment)

    module.segmented_download(
        "https://example.invalid/model",
        destination,
        token=None,
        workers=1,
    )

    assert head_kwargs["allow_redirects"] is True
    assert head_kwargs["timeout"] == module.REQUEST_TIMEOUT
    assert destination.read_bytes() == b"xxxx"


def test_download_file_ignores_same_size_partial_and_replaces_final(
    monkeypatch, tmp_path
):
    module = _load_module()
    destination_dir = tmp_path / "cache"
    destination_dir.mkdir()
    final_path = destination_dir / "weights.bin"
    partial_path = destination_dir / "weights.bin.partial"
    partial_path.write_bytes(b"\0" * 4)
    calls = {"segmented": 0}

    def fake_head(url, **kwargs):
        del url, kwargs
        return _FakeResponse(headers={"Content-Length": str(300 * 1024 * 1024)})

    def fake_segmented_download(url, dest, token, workers):
        del url, token, workers
        calls["segmented"] += 1
        assert dest == partial_path
        dest.write_bytes(b"done")

    monkeypatch.setattr(module.requests, "head", fake_head)
    monkeypatch.setattr(module, "segmented_download", fake_segmented_download)

    module.download_file(
        "google/gemma",
        "main",
        "weights.bin",
        destination_dir,
        token=None,
        workers=1,
    )

    assert calls["segmented"] == 1
    assert final_path.read_bytes() == b"done"
    assert not partial_path.exists()


def test_segmented_download_resumes_only_incomplete_segments(monkeypatch, tmp_path):
    module = _load_module()
    destination = tmp_path / "weights.bin.partial"
    metadata_path = tmp_path / "weights.bin.partial.metadata.json"
    original_segment_size = module.SEGMENT_SIZE
    head_kwargs = {}
    downloaded = []

    monkeypatch.setattr(module, "SEGMENT_SIZE", 4)

    def fake_head(url, **kwargs):
        del url
        head_kwargs.update(kwargs)
        return _FakeResponse(headers={"Content-Length": "12"})

    def fake_download_segment(url, start, end, path, token):
        del url, token
        downloaded.append((start, end))
        with open(path, "r+b") as handle:
            handle.seek(start)
            handle.write(bytes([65 + (start // 4)]) * (end - start + 1))

    destination.write_bytes(b"AAAA\0\0\0\0\0\0\0\0")
    metadata_path.write_text(
        '{"size": 12, "segment_size": 4, "segment_count": 3, "completed_segments": [true, false, false]}'
    )

    monkeypatch.setattr(module.requests, "head", fake_head)
    monkeypatch.setattr(module, "download_segment", fake_download_segment)

    try:
        module.segmented_download(
            "https://example.invalid/model",
            destination,
            token=None,
            workers=1,
        )

        assert head_kwargs["allow_redirects"] is True
        assert downloaded == [(4, 7), (8, 11)]
        assert destination.read_bytes() == b"AAAABBBBCCCC"
        metadata = module.load_partial_metadata(destination, 12, 3)
        assert metadata is not None
        assert metadata["completed_segments"] == [True, True, True]
    finally:
        monkeypatch.setattr(module, "SEGMENT_SIZE", original_segment_size)


def test_download_file_uses_dataset_repo_type_in_hf_hub_url(monkeypatch, tmp_path):
    module = _load_module()
    destination_dir = tmp_path / "cache"
    destination_dir.mkdir()
    final_path = destination_dir / "train" / "data.parquet"
    hf_hub_url_kwargs = {}
    head_urls = []

    def fake_hf_hub_url(**kwargs):
        hf_hub_url_kwargs.update(kwargs)
        return "https://example.invalid/dataset-file"

    def fake_head(url, **kwargs):
        del kwargs
        head_urls.append(url)
        return _FakeResponse(headers={"Content-Length": "4"})

    def fake_get(url, **kwargs):
        del url, kwargs
        return _FakeResponse(chunks=[b"data"])

    monkeypatch.setattr(module, "hf_hub_url", fake_hf_hub_url)
    monkeypatch.setattr(module.requests, "head", fake_head)
    monkeypatch.setattr(module.requests, "get", fake_get)

    module.download_file(
        "bigcode/the-stack",
        "main",
        "train/data.parquet",
        destination_dir,
        token=None,
        workers=1,
        repo_type="dataset",
    )

    assert hf_hub_url_kwargs["repo_id"] == "bigcode/the-stack"
    assert hf_hub_url_kwargs["filename"] == "train/data.parquet"
    assert hf_hub_url_kwargs["revision"] == "main"
    assert hf_hub_url_kwargs["repo_type"] == "dataset"
    assert head_urls == ["https://example.invalid/dataset-file"]
    assert final_path.read_bytes() == b"data"


def test_list_tags_and_refs_forward_dataset_repo_type(monkeypatch):
    module = _load_module()
    calls = []

    class _FakeApi:
        def __init__(self, token):
            assert token == "hf-token"

        def list_repo_refs(self, repo_id, repo_type):
            calls.append((repo_id, repo_type))
            return SimpleNamespace(
                branches=[SimpleNamespace(name="main", target_commit="branch-sha")],
                tags=[SimpleNamespace(name="v1.0", target_commit="tag-sha")],
            )

    monkeypatch.setattr(module, "HfApi", _FakeApi)

    module.list_tags("bigcode/the-stack", "hf-token", repo_type="dataset")
    module.list_refs("bigcode/the-stack", "hf-token", repo_type="dataset")

    assert calls == [
        ("bigcode/the-stack", "dataset"),
        ("bigcode/the-stack", "dataset"),
    ]


def test_main_download_dataset_uses_dataset_info_and_dataset_cache_root(
    monkeypatch, tmp_path
):
    module = _load_module()
    captured = {}
    fake_info = SimpleNamespace(
        sha="dataset-sha",
        siblings=[SimpleNamespace(rfilename="train/data.parquet")],
    )

    def fake_dataset_info(repo, revision=None, token=None):
        captured["dataset_info"] = (repo, revision, token)
        return fake_info

    def fake_model_info(*args, **kwargs):
        del args, kwargs
        raise AssertionError("model_info should not be used for --dataset")

    def fake_download_repo(
        repo, revision, dest, files, workers, token, repo_type="model"
    ):
        captured["download_repo"] = {
            "repo": repo,
            "revision": revision,
            "dest": dest,
            "files": files,
            "workers": workers,
            "token": token,
            "repo_type": repo_type,
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "hf-token")
    monkeypatch.setattr(module, "dataset_info", fake_dataset_info)
    monkeypatch.setattr(module, "model_info", fake_model_info)
    monkeypatch.setattr(module, "download_repo", fake_download_repo)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "model-download.py",
            "download",
            "--dataset",
            "bigcode/the-stack",
            "-w",
            "1",
        ],
    )

    module.main()

    expected_dir = (
        tmp_path
        / ".cache"
        / "mlody"
        / "artifacts"
        / "huggingface"
        / "datasets"
        / "bigcode"
        / "the-stack"
        / "dataset-sha"
    )
    assert captured["dataset_info"] == ("bigcode/the-stack", None, "hf-token")
    assert captured["download_repo"]["dest"] == expected_dir
    assert captured["download_repo"]["repo_type"] == "dataset"
    assert (expected_dir / "dataset_info.json").exists()


def test_main_backward_compatibility_defaults_to_model_repo(
    monkeypatch, tmp_path
):
    module = _load_module()
    captured = {}
    fake_info = SimpleNamespace(
        sha="model-sha",
        siblings=[SimpleNamespace(rfilename="config.json")],
    )

    def fake_model_info(repo, revision=None, token=None):
        captured["model_info"] = (repo, revision, token)
        return fake_info

    def fake_dataset_info(*args, **kwargs):
        del args, kwargs
        raise AssertionError("dataset_info should not be used without --dataset")

    def fake_download_repo(
        repo, revision, dest, files, workers, token, repo_type="model"
    ):
        captured["download_repo"] = {
            "repo": repo,
            "revision": revision,
            "dest": dest,
            "files": files,
            "workers": workers,
            "token": token,
            "repo_type": repo_type,
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "hf-token")
    monkeypatch.setattr(module, "dataset_info", fake_dataset_info)
    monkeypatch.setattr(module, "model_info", fake_model_info)
    monkeypatch.setattr(module, "download_repo", fake_download_repo)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "model-download.py",
            "google/gemma",
            "-w",
            "1",
        ],
    )

    module.main()

    expected_dir = (
        tmp_path
        / ".cache"
        / "mlody"
        / "artifacts"
        / "huggingface"
        / "google"
        / "gemma"
        / "model-sha"
    )
    assert captured["model_info"] == ("google/gemma", None, "hf-token")
    assert captured["download_repo"]["dest"] == expected_dir
    assert captured["download_repo"]["repo_type"] == "model"
    assert (expected_dir / "model_info.json").exists()
