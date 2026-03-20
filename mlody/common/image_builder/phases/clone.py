"""Phase 2: repository shallow clone with cache and file locking."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from mlody.common.image_builder.errors import CloneError
from mlody.common.image_builder.log import info

_CACHE_ROOT_DEFAULT = Path.home() / ".cache" / "mlody" / "builds"


def _cache_dir(cache_root: Path, sha: str) -> Path:
    return cache_root / sha


def _lock_path(cache_root: Path, sha: str) -> Path:
    return cache_root / f"{sha}.lock"


def _check_cache(cache_root: Path, sha: str) -> bool:
    """Return True if a complete clone for sha exists in cache_root."""
    d = _cache_dir(cache_root, sha)
    return d.is_dir() and (d / ".git" / "HEAD").exists()


def _acquire_lock(cache_root: Path, sha: str) -> Path:
    """Atomically create a lock file; raise CloneError on contention."""
    lock = _lock_path(cache_root, sha)
    try:
        lock.open("x").close()
    except FileExistsError:
        raise CloneError(
            f"Another process is already cloning SHA {sha}",
            sha=sha,
            lock=str(lock),
        )
    return lock


def _release_lock(lock: Path) -> None:
    lock.unlink(missing_ok=True)


def _run_git(args: list[str], cwd: Path | None = None) -> None:
    """Run a git command; raise CloneError on non-zero exit."""
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CloneError(
            f"git command failed: {' '.join(args)}",
            stderr=result.stderr.strip(),
            returncode=result.returncode,
        )


def _run_bazel(args: list[str], cwd: Path) -> None:
    """Run a bazel command; raise CloneError on non-zero exit."""
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CloneError(
            f"bazel command failed: {' '.join(args)}",
            cwd=str(cwd),
            stderr=result.stderr.strip(),
        )


def _clone_from(source: str, sha: str, dest: Path) -> None:
    """Clone *source* at *sha* into *dest* using a shallow fetch."""
    _run_git(["git", "clone", "--no-checkout", source, str(dest)])
    _run_git(["git", "fetch", "--depth", "1", "origin", sha], cwd=dest)
    _run_git(["git", "checkout", sha], cwd=dest)


def ensure_clone(
    sha: str,
    remote_url: str,
    cache_root: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    """Ensure a shallow clone for sha exists in cache_root.

    Cache hit: reuse the existing directory, run ``bazel clean``.
    Cache miss: shallow-fetch + checkout from *remote_url*.  If the SHA is not
    found on the remote (e.g. a local-only commit), falls back to cloning from
    *cwd* (defaults to ``Path.cwd()``).
    Lock: O_CREAT|O_EXCL sentinel file prevents concurrent clones for the
    same SHA.

    Returns the path to the clone directory.
    Raises CloneError on any failure.
    """
    root = cache_root if cache_root is not None else _CACHE_ROOT_DEFAULT
    root.mkdir(mode=0o700, parents=True, exist_ok=True)

    dest = _cache_dir(root, sha)

    if _check_cache(root, sha):
        info("clone", sha=sha, cache="hit", dest=str(dest))
    else:
        lock = _acquire_lock(root, sha)
        try:
            if not _check_cache(root, sha):
                info("clone", sha=sha, cache="miss", dest=str(dest))
                dest.mkdir(parents=True, exist_ok=True)
                try:
                    _clone_from(remote_url, sha, dest)
                    info("clone", sha=sha, source="remote", dest=str(dest))
                except CloneError:
                    # SHA not on remote — try the local working directory.
                    shutil.rmtree(dest, ignore_errors=True)
                    dest.mkdir(parents=True, exist_ok=True)
                    local_source = str(cwd if cwd is not None else Path.cwd())
                    info("clone", sha=sha, source="local", local_source=local_source)
                    try:
                        _clone_from(local_source, sha, dest)
                    except CloneError:
                        shutil.rmtree(dest, ignore_errors=True)
                        raise
            else:
                info("clone", sha=sha, cache="hit-after-lock", dest=str(dest))
        finally:
            _release_lock(lock)

    info("clone", sha=sha, step="bazel_clean", dest=str(dest))
    _run_bazel(["bazel", "clean"], cwd=dest)

    return dest
