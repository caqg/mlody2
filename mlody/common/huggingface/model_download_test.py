import importlib.util
from pathlib import Path

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
