"""Public factory for workspace resolution — parse, resolve, materialise."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable, NamedTuple

from mlody.core.workspace import Workspace
from mlody.resolver.cache import (
    acquire_lock,
    cache_dir,
    check_cache,
    ensure_cache_root,
    release_lock,
    write_metadata,
)
from mlody.resolver.errors import (
    AmbiguousRefError,
    BranchTagCollisionError,
    CorruptCacheError,
    NoMlodyAtCommitError,
    UnknownRefError,
)
from mlody.resolver.git_client import GitClient

_logger = logging.getLogger(__name__)

_DEFAULT_CACHE_SUFFIX = Path(".cache") / "mlody" / "workspaces"


class ResolvedRef(NamedTuple):
    """Result of SHA resolution — the full 40-char SHA and provenance flag."""

    sha: str
    local_only: bool


def parse_label(label: str) -> tuple[str | None, str]:
    """Split a raw label into (committoid, inner_label).

    Delegates to the core label parser and projects the resulting Label
    into the (committoid, inner_label) shape expected by resolver callers.
    Raises LabelParseError when the label has neither an entity spec nor an
    attribute path (i.e. it cannot resolve to any value).

    # TODO(mlody-label-parsing): replace callers with Label directly and delete wrapper.
    """
    from mlody.core.label import parse_label as _core_parse_label
    from mlody.core.label.errors import LabelParseError as _LabelParseError

    lbl = _core_parse_label(label)  # raises LabelParseError on bad input

    committoid = lbl.workspace  # None = CWD

    if lbl.entity is None and lbl.attribute_path is None:
        raise _LabelParseError(
            label,
            "label has no entity spec and no attribute path — cannot resolve to a value",
        )

    if lbl.entity is None:
        # Workspace-level attribute access (e.g. "'info", "457f'info").
        # Re-serialise the attribute portion as inner_label for workspace.resolve.
        assert lbl.attribute_path is not None  # guaranteed by the check above
        attr_str = ".".join(lbl.attribute_path)
        if lbl.attribute_query:
            attr_str += f"[{lbl.attribute_query}]"
        return (committoid, f"'{attr_str}")

    # Entity-bearing label: re-serialise inner_label from Label fields.
    parts: list[str] = []
    if lbl.entity.root is not None:
        parts.append(f"@{lbl.entity.root}")
    path = lbl.entity.path or ""
    if lbl.entity.wildcard:
        parts.append(f"//{path}/...")
    else:
        parts.append(f"//{path}")
    if lbl.entity.name is not None:
        parts.append(f":{lbl.entity.name}")
    inner_label = "".join(parts)
    return (committoid, inner_label)


def resolve_sha(committoid: str, git_client: GitClient) -> ResolvedRef:
    """Resolve a committoid (branch, tag, short/full SHA) to a ResolvedRef.

    Resolution order:
    1. Exact branch match (refs/heads/<name>)
    2. Exact tag match (refs/tags/<name>), preferring the ^{} deref SHA for
       annotated tags over the tag object SHA.
    3. If both a branch and a tag match, raise BranchTagCollisionError.
    4. SHA prefix match across all remote SHAs — unique match returns the full
       SHA; multiple matches raise AmbiguousRefError.
    5. Local remote-tracking refs — covers merged/deleted branches fetched
       locally but no longer on the remote (local_only=False, was landed).
    6. Local-only fallback via git rev-parse — covers branches and SHAs that
       exist only in the CWD and have never been pushed (local_only=True).
    7. Nothing matched — raise UnknownRefError.
    """
    pairs = git_client.ls_remote()

    branch_shas = {sha for sha, ref in pairs if ref == f"refs/heads/{committoid}"}

    # Prefer the dereferenced SHA (^{}) for annotated tags; fall back to the
    # tag object SHA for lightweight tags.
    deref_shas = {sha for sha, ref in pairs if ref == f"refs/tags/{committoid}^{{}}"}
    plain_tag_shas = {sha for sha, ref in pairs if ref == f"refs/tags/{committoid}"}
    tag_shas = deref_shas if deref_shas else plain_tag_shas

    if branch_shas and tag_shas:
        head_sha = next(iter(branch_shas))
        tag_sha = next(iter(tag_shas))
        raise BranchTagCollisionError(committoid, head_sha, tag_sha)

    exact_shas = branch_shas | tag_shas
    if len(exact_shas) == 1:
        return ResolvedRef(exact_shas.pop(), False)

    # SHA prefix match — search across all (sha, ref) pairs
    all_shas = {sha for sha, _ in pairs}
    prefix_matches = {sha for sha in all_shas if sha.startswith(committoid)}
    if len(prefix_matches) == 1:
        return ResolvedRef(prefix_matches.pop(), False)
    if len(prefix_matches) > 1:
        raise AmbiguousRefError(committoid, sorted(prefix_matches))

    # Fall back to local remote-tracking refs — covers merged/deleted branches
    # that were fetched locally but no longer appear on the remote.
    local_pairs = git_client.local_remote_tracking_refs()
    local_branch_shas = {sha for sha, ref in local_pairs if ref == f"refs/heads/{committoid}"}
    if len(local_branch_shas) == 1:
        _logger.debug(
            "Ref %r not found on remote; resolved from local remote-tracking ref", committoid
        )
        return ResolvedRef(local_branch_shas.pop(), False)

    # Local-only fallback — branch or SHA exists only in the CWD, not pushed.
    local_sha = git_client.rev_parse_local(committoid)
    if local_sha:
        _logger.debug(
            "Ref %r not found on remote; resolved from local repo (not landed)", committoid
        )
        return ResolvedRef(local_sha, True)

    raise UnknownRefError(committoid, "origin")


def materialise(
    full_sha: str,
    monorepo_root: Path,
    git_client: GitClient,
    cache_root: Path,
    committoid: str,
    local_only: bool = False,
) -> Path:
    """Ensure a workspace directory for full_sha exists in cache_root.

    Checks the cache first — returns immediately on a hit. On a miss, acquires
    an exclusive lock, clones (local or remote depending on local commit
    availability), writes metadata, and releases the lock in a finally block.

    Partial directories are cleaned up if the clone fails.
    """
    status = check_cache(cache_root, full_sha)
    if status == "hit":
        return cache_dir(cache_root, full_sha)
    if status == "corrupt":
        raise CorruptCacheError(cache_dir(cache_root, full_sha))

    lock_path = acquire_lock(cache_root, full_sha)
    dest = cache_dir(cache_root, full_sha)
    try:
        local = git_client.cat_file_type(full_sha) == "commit"
        if local:
            git_client.clone_local(dest=dest, sha=full_sha)
        else:
            git_client.clone_remote(dest=dest, sha=full_sha)

        repo_url = git_client.remote_url()
        write_metadata(cache_root, full_sha, requested_ref=committoid, repo_url=repo_url, local_only=local_only)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        release_lock(lock_path)

    return dest


def resolve_workspace(
    label: str,
    monorepo_root: Path,
    roots_file: Path | None = None,
    print_fn: Callable[..., None] = print,
    git_client: GitClient | None = None,
    cache_root: Path | None = None,
    verbose: bool = False,
) -> tuple[Workspace, str | None]:
    """Resolve a raw label to a ready Workspace and optional resolved SHA.

    For cwd-relative labels (@//-prefixed) the monorepo_root workspace is used
    directly and resolved_sha is None. For committoid-qualified labels the
    resolver fetches the remote SHA, materialises a cached clone, and returns
    a Workspace rooted there along with the full 40-char SHA.

    All error conditions raise WorkspaceResolutionError subclasses — callers
    are responsible for catching and formatting them.
    """
    committoid, inner_label = parse_label(label)

    if committoid is None:
        ws = Workspace(
            monorepo_root=monorepo_root,
            roots_file=roots_file,
            print_fn=print_fn,
        )
        ws.load(verbose=verbose)
        return (ws, None)

    client = git_client or GitClient(monorepo_root)
    root = cache_root or (Path.home() / _DEFAULT_CACHE_SUFFIX)
    ensure_cache_root(root)

    resolved = resolve_sha(committoid, client)
    _logger.debug("Resolved %s to %s", committoid, resolved.sha)

    dest = materialise(resolved.sha, monorepo_root, client, root, committoid, local_only=resolved.local_only)
    ws = Workspace(
        monorepo_root=dest,
        roots_file=None,
        print_fn=print_fn,
    )
    try:
        ws.load(verbose=verbose)
    except FileNotFoundError:
        raise NoMlodyAtCommitError(committoid, resolved.sha) from None
    return (ws, resolved.sha)
