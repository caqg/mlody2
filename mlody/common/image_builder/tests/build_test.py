"""Tests for phases/build.py — bazel build invocation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mlody.common.image_builder.errors import BazelBuildError
from mlody.common.image_builder.phases.build import BazelResult, _DYN_PKG, run_bazel_build

_CLONE_DIR = Path("/fake/clone/dir")
_TARGETS = ["//mlody/lsp:lsp_server", "//mlody/core:worker"]


def _make_subprocess_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


def test_run_bazel_build_returns_bazel_result_on_success(fs: object) -> None:
    _CLONE_DIR.mkdir(parents=True)  # type: ignore[union-attr]
    with patch(
        "mlody.common.image_builder.phases.build.subprocess.run",
        return_value=_make_subprocess_result(stdout="build output", stderr=""),
    ):
        result = run_bazel_build(_CLONE_DIR, _TARGETS)

    assert isinstance(result, BazelResult)
    assert result.stdout == "build output"


def test_run_bazel_build_raises_on_nonzero_exit(fs: object) -> None:
    _CLONE_DIR.mkdir(parents=True)  # type: ignore[union-attr]
    with patch(
        "mlody.common.image_builder.phases.build.subprocess.run",
        return_value=_make_subprocess_result(returncode=1, stderr="ERROR: build failed"),
    ):
        with pytest.raises(BazelBuildError) as exc_info:
            run_bazel_build(_CLONE_DIR, _TARGETS)

    assert exc_info.value.context["returncode"] == 1
    assert "ERROR: build failed" in str(exc_info.value.context["stderr"])


def test_run_bazel_build_writes_build_bazel_with_targets(fs: object) -> None:
    _CLONE_DIR.mkdir(parents=True)  # type: ignore[union-attr]

    with patch(
        "mlody.common.image_builder.phases.build.subprocess.run",
        return_value=_make_subprocess_result(),
    ):
        run_bazel_build(_CLONE_DIR, _TARGETS)

    build_file = _CLONE_DIR / _DYN_PKG / "BUILD.bazel"
    assert build_file.exists()
    content = build_file.read_text()
    for target in _TARGETS:
        assert target in content
    assert "oci_image" in content
    assert "pkg_tar" in content


def test_run_bazel_build_invokes_correct_target(fs: object) -> None:
    _CLONE_DIR.mkdir(parents=True)  # type: ignore[union-attr]
    captured_calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> MagicMock:
        captured_calls.append(args)
        return _make_subprocess_result()

    with patch("mlody.common.image_builder.phases.build.subprocess.run", side_effect=fake_run):
        run_bazel_build(_CLONE_DIR, _TARGETS)

    assert len(captured_calls) == 1
    cmd = captured_calls[0]
    assert f"//{_DYN_PKG}:image" in cmd


def test_run_bazel_build_error_contains_targets(fs: object) -> None:
    _CLONE_DIR.mkdir(parents=True)  # type: ignore[union-attr]
    with patch(
        "mlody.common.image_builder.phases.build.subprocess.run",
        return_value=_make_subprocess_result(returncode=1, stderr=""),
    ):
        with pytest.raises(BazelBuildError) as exc_info:
            run_bazel_build(_CLONE_DIR, _TARGETS)

    assert exc_info.value.context["targets"] == _TARGETS
