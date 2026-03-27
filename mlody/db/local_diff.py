"""Helpers for computing a deterministic hash of local source subtrees.

Used to fingerprint the current state of mlody/ and common/python/starlarkish/
at evaluation time — capturing uncommitted edits and untracked files that git
status alone would not surface in a reproducible way.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

_logger = logging.getLogger(__name__)

# The two source subtrees whose combined content is hashed.
# Paths are repo-relative; they are joined with the resolved repo root at
# call time. Hardcoded per spec §2.1 — Bazel-precise selection is deferred.
_SUBTREES = [
    Path("mlody"),
    Path("common") / "python" / "starlarkish",
]


def get_repo_root() -> Path | None:
    """Return the absolute repo root by running git rev-parse.

    Separated from compute_local_diff_sha so callers that already have the
    repo root (e.g. GitClient users) can skip the subprocess call.

    Returns None and logs a warning on any failure (non-zero exit, git not
    found, or any other subprocess error).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        _logger.warning("git not found on PATH; local_diff_sha will be None")
        return None
    except Exception as exc:
        _logger.warning("git rev-parse failed unexpectedly: %s", exc)
        return None

    if result.returncode != 0:
        _logger.warning(
            "git rev-parse --show-toplevel exited %d; local_diff_sha will be None",
            result.returncode,
        )
        return None

    return Path(result.stdout.strip())


def compute_local_diff_sha(repo_root: Path | None) -> str | None:
    """Compute a 64-char SHA-256 fingerprint of the relevant source subtrees.

    Implements Method A from spec §2.1:
    1. Enumerate all files under mlody/ and common/python/starlarkish/
       (relative to repo_root), sorted by repo-relative path.
    2. For each file compute sha256(file_bytes).hexdigest().
    3. Build the combined string "path:digest\\n..." and hash it.

    Returns None (with a warning) only when repo_root is None.
    If both subtree directories are absent, returns the SHA-256 of the empty
    string — NOT None.
    """
    if repo_root is None:
        _logger.warning(
            "repo_root is None; cannot compute local_diff_sha — storing NULL"
        )
        return None

    # Collect (repo_relative_str, file_bytes) for every file in both subtrees.
    pairs: list[tuple[str, bytes]] = []
    for subtree in _SUBTREES:
        abs_subtree = repo_root / subtree
        if not abs_subtree.exists():
            # Treat absent subtree as empty — spec §2.1 edge case.
            continue
        for path in abs_subtree.rglob("*"):
            if path.is_file():
                rel = path.relative_to(repo_root)
                pairs.append((str(rel), path.read_bytes()))

    # Sort by repo-relative path to make the hash order-independent.
    pairs.sort(key=lambda p: p[0])

    # Build the combined string and return its digest.
    parts = [
        f"{rel_path}:{hashlib.sha256(content).hexdigest()}"
        for rel_path, content in pairs
    ]
    combined = "\n".join(parts) + "\n"
    return hashlib.sha256(combined.encode()).hexdigest()
