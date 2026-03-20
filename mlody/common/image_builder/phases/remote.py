"""Phase 1: git remote URL resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mlody.common.image_builder.errors import CloneError
from mlody.common.image_builder.log import info


def resolve_remote(remote_override: str | None, cwd: Path) -> str:
    """Return the git remote URL to use for cloning.

    If remote_override is supplied, return it directly.
    Otherwise, run `git remote get-url origin` in cwd.

    Raises CloneError if git subprocess fails.
    """
    if remote_override is not None:
        info("remote", remote_url=remote_override, source="--remote flag")
        return remote_override

    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CloneError(
            "Failed to resolve git remote URL from cwd",
            cwd=str(cwd),
            stderr=result.stderr.strip(),
        )

    remote_url = result.stdout.strip()
    info("remote", remote_url=remote_url, source="git remote get-url origin")
    return remote_url
