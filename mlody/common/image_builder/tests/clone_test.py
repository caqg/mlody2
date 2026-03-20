"""Tests for phases/clone.py — cache/clone/lock logic using pyfakefs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mlody.common.image_builder.errors import CloneError
from mlody.common.image_builder.phases.clone import (
    _acquire_lock,
    _cache_dir,
    _check_cache,
    _lock_path,
    ensure_clone,
)

_SHA = "a" * 40
_REMOTE = "https://example.com/repo.git"
_CACHE_ROOT = Path("/fake/cache")
_CLONE_DEST = _CACHE_ROOT / _SHA


def _make_valid_clone(fs: object, cache_root: Path, sha: str) -> None:
    """Simulate a completed clone by creating the .git/HEAD sentinel."""
    dest = cache_root / sha
    fs.create_file(str(dest / ".git" / "HEAD"), contents="ref: refs/heads/main\n")  # type: ignore[attr-defined]


class TestCheckCache:
    def test_returns_false_when_directory_absent(self, fs: object) -> None:
        assert _check_cache(_CACHE_ROOT, _SHA) is False

    def test_returns_false_when_directory_exists_but_no_git_head(
        self, fs: object
    ) -> None:
        _CLONE_DEST.mkdir(parents=True)
        assert _check_cache(_CACHE_ROOT, _SHA) is False

    def test_returns_true_when_git_head_exists(self, fs: object) -> None:
        (_CLONE_DEST / ".git").mkdir(parents=True)
        (_CLONE_DEST / ".git" / "HEAD").write_text("ref: refs/heads/main")
        assert _check_cache(_CACHE_ROOT, _SHA) is True


class TestAcquireLock:
    def test_creates_lock_file(self, fs: object) -> None:
        _CACHE_ROOT.mkdir(parents=True)
        lock = _acquire_lock(_CACHE_ROOT, _SHA)
        assert lock.exists()

    def test_raises_clone_error_on_contention(self, fs: object) -> None:
        _CACHE_ROOT.mkdir(parents=True)
        # Simulate an existing lock (another process already acquired it)
        _lock_path(_CACHE_ROOT, _SHA).write_text("locked")
        with pytest.raises(CloneError) as exc_info:
            _acquire_lock(_CACHE_ROOT, _SHA)
        assert "lock" in exc_info.value.context


class TestEnsureClone:
    def test_cache_miss_invokes_git_commands_and_bazel_clean(
        self, fs: object
    ) -> None:
        _CACHE_ROOT.mkdir(parents=True)

        bazel_calls: list[list[str]] = []

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            # After git checkout, simulate the .git/HEAD sentinel appearing
            if args[:3] == ["git", "checkout", _SHA]:
                dest = _cache_dir(_CACHE_ROOT, _SHA)
                git_dir = dest / ".git"
                git_dir.mkdir(parents=True, exist_ok=True)
                (git_dir / "HEAD").write_text("ref: refs/heads/main")
            if args[0] == "bazel":
                bazel_calls.append(args)
            return mock

        with patch("mlody.common.image_builder.phases.clone.subprocess.run", side_effect=fake_run):
            result = ensure_clone(_SHA, _REMOTE, cache_root=_CACHE_ROOT)

        assert result == _CACHE_ROOT / _SHA
        assert ["bazel", "clean"] in bazel_calls

    def test_cache_hit_skips_git_clone_calls(self, fs: object) -> None:
        _CACHE_ROOT.mkdir(parents=True)
        # Pre-populate a valid clone
        (_CLONE_DEST / ".git").mkdir(parents=True)
        (_CLONE_DEST / ".git" / "HEAD").write_text("ref: refs/heads/main")

        git_calls: list[list[str]] = []
        bazel_calls: list[list[str]] = []

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            if args[0] == "git":
                git_calls.append(args)
            if args[0] == "bazel":
                bazel_calls.append(args)
            return mock

        with patch("mlody.common.image_builder.phases.clone.subprocess.run", side_effect=fake_run):
            result = ensure_clone(_SHA, _REMOTE, cache_root=_CACHE_ROOT)

        # On cache hit, no git commands should run
        assert git_calls == []
        # bazel clean must always run
        assert ["bazel", "clean"] in bazel_calls
        assert result == _CLONE_DEST

    def test_partial_directory_cleaned_up_on_clone_failure(
        self, fs: object
    ) -> None:
        _CACHE_ROOT.mkdir(parents=True)

        call_count = 0

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            # First git command (clone) creates the dest dir
            if args[0] == "git" and "--no-checkout" in args:
                _CLONE_DEST.mkdir(parents=True, exist_ok=True)
            # Second git command (fetch) fails
            if args[0] == "git" and "--depth" in args:
                mock.returncode = 1
                mock.stderr = "fatal: shallow fetch error"
            return mock

        with pytest.raises(CloneError):
            with patch(
                "mlody.common.image_builder.phases.clone.subprocess.run",
                side_effect=fake_run,
            ):
                ensure_clone(_SHA, _REMOTE, cache_root=_CACHE_ROOT)

        # The partial destination directory must be removed after failure
        assert not _CLONE_DEST.exists()

    def test_falls_back_to_local_cwd_when_remote_fetch_fails(
        self, fs: object
    ) -> None:
        """If the remote fetch fails, retry cloning from the local cwd."""
        _CACHE_ROOT.mkdir(parents=True)
        local_cwd = Path("/local/repo")

        remote_attempted: list[str] = []
        local_attempted: list[str] = []

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            if args[0] == "git" and "--no-checkout" in args:
                source = args[-2]
                if source == _REMOTE:
                    remote_attempted.append(source)
                    mock.returncode = 1
                    mock.stderr = "fatal: repository not found"
                else:
                    local_attempted.append(source)
            if args[:3] == ["git", "checkout", _SHA]:
                dest = _cache_dir(_CACHE_ROOT, _SHA)
                git_dir = dest / ".git"
                git_dir.mkdir(parents=True, exist_ok=True)
                (git_dir / "HEAD").write_text("ref: refs/heads/main")
            return mock

        with patch("mlody.common.image_builder.phases.clone.subprocess.run", side_effect=fake_run):
            result = ensure_clone(_SHA, _REMOTE, cache_root=_CACHE_ROOT, cwd=local_cwd)

        assert result == _CLONE_DEST
        assert _REMOTE in remote_attempted
        assert str(local_cwd) in local_attempted

    def test_lock_contention_raises_clone_error(self, fs: object) -> None:
        _CACHE_ROOT.mkdir(parents=True)
        # Simulate an existing lock file from another process
        _lock_path(_CACHE_ROOT, _SHA).write_text("locked by other process")

        with pytest.raises(CloneError) as exc_info:
            ensure_clone(_SHA, _REMOTE, cache_root=_CACHE_ROOT)

        assert "lock" in exc_info.value.context
