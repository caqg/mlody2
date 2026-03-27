"""show subcommand — resolve and display pipeline values."""

from __future__ import annotations

import json
import logging
import os
import pwd
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import click
from rich.pretty import pretty_repr

from mlody.cli.main import cli
from mlody.core.workspace import Workspace, WorkspaceLoadError, force
from mlody.db.evaluations import open_db, write_evaluation
from mlody.db.local_diff import compute_local_diff_sha, get_repo_root
from mlody.resolver import resolve_workspace
from mlody.resolver.errors import WorkspaceResolutionError

_logger = logging.getLogger(__name__)

_DEFAULT_CACHE_SUFFIX = Path(".cache") / "mlody"
_DEFAULT_DB_NAME = "mlody.sqlite"
_DEFAULT_WORKSPACES_SUFFIX = _DEFAULT_CACHE_SUFFIX / "workspaces"


def _get_username() -> str:
    """Return the OS username; falls back to pwd lookup if os.getlogin() raises."""
    try:
        return os.getlogin()
    except OSError:
        return pwd.getpwuid(os.getuid()).pw_name


def _read_meta(cache_root: Path, resolved_sha: str) -> dict[str, object]:
    """Read the -meta.json file written by materialise(), returning {} on failure."""
    meta_path = cache_root / f"{resolved_sha}-meta.json"
    try:
        return dict(json.loads(meta_path.read_text()))  # type: ignore[arg-type]
    except Exception:
        return {}


def _record_evaluation(
    resolved_sha: str,
    requested_ref: str,
    local_only: bool,
    repo: str,
    resolved_at: str,
    value_description: str,
) -> None:
    """Write one evaluation row to the local SQLite database.

    Best-effort: logs at ERROR level and returns on any failure so a DB error
    never terminates the show command (NFR-AVAIL-001: never a silent crash —
    the error is logged clearly). Connection is always closed in the finally
    block.
    """
    db_path = Path.home() / _DEFAULT_CACHE_SUFFIX / _DEFAULT_DB_NAME
    conn = None
    try:
        conn = open_db(db_path)
        local_diff_sha = compute_local_diff_sha(get_repo_root())
        write_evaluation(
            conn,
            username=_get_username(),
            hostname=socket.gethostname(),
            requested_ref=requested_ref,
            resolved_sha=resolved_sha,
            resolved_at=resolved_at,
            repo=repo,
            local_only=local_only,
            value_description=value_description,
            local_diff_sha=local_diff_sha,
        )
    except Exception as exc:
        _logger.error("Failed to write evaluation to %s: %s", db_path, exc)
    finally:
        if conn is not None:
            conn.close()


def show_fn(
    label: str,
    monorepo_root: Path,
    roots_file: Path | None = None,
    print_fn: Callable[..., None] = print,
    verbose: bool = False,
) -> object:
    """Resolve a label to a value via a fresh workspace.

    Used by the shell REPL. Accepts a raw label (with optional committoid prefix)
    and constructs a workspace independently for each call.
    """
    workspace, _sha = resolve_workspace(
        label,
        monorepo_root=monorepo_root,
        roots_file=roots_file,
        print_fn=print_fn,
        verbose=verbose,
    )
    _committoid, inner_label = _parse_inner(label)
    print_fn(pretty_repr(_parse_label_struct(label)))
    return force(workspace.resolve(inner_label))


def _parse_inner(label: str) -> tuple[str | None, str]:
    """Extract committoid and inner label without raising — delegates to parse_label."""
    from mlody.resolver.resolver import parse_label

    return parse_label(label)


def _parse_label_struct(label: str) -> object:
    """Return the parsed Label struct for display purposes."""
    from mlody.core.label import parse_label as _core_parse_label

    return _core_parse_label(label)


def _is_primitive(value: object) -> bool:
    return isinstance(value, str | int | float | bool)


def _format_value(value: object) -> str:
    if _is_primitive(value):
        return str(value)
    return pretty_repr(value)


@cli.command()
@click.argument("targets", nargs=-1, required=True)
@click.pass_context
def show(ctx: click.Context, targets: tuple[str, ...]) -> None:
    """Resolve and display pipeline values.

    TARGETS: One or more Bazel-style target references. A target may be
    prefixed with a committoid and '|' separator (e.g. main|@root//pkg:tgt)
    to resolve against a specific commit rather than the current workspace.
    """
    # Support legacy test injection of a pre-built workspace via ctx.obj
    if "workspace" in ctx.obj:
        _show_with_legacy_workspace(ctx, targets)
        return

    monorepo_root: Path = ctx.obj["monorepo_root"]
    roots: Path | None = ctx.obj.get("roots")
    has_error = False

    verbose: bool = ctx.obj.get("verbose", False)

    for target in targets:
        try:
            workspace, resolved_sha = resolve_workspace(
                target,
                monorepo_root=monorepo_root,
                roots_file=roots,
                verbose=verbose,
            )
            if resolved_sha is not None:
                _logger.debug("Resolved %s to %s", target.split("|")[0], resolved_sha)

            print("====")
            for k in workspace.evaluator.all.keys():
                print(f"> {k}")
            print(_parse_label_struct(target))
            print("--")
            click.echo(pretty_repr(_parse_label_struct(target)))
            print(".")
            click.echo(
                pretty_repr(workspace.evaluator.all[("root", "mlody/roots", "lexica")])
            )
            print("====")
            _committoid, inner_label = _parse_inner(target)
            value = force(workspace.resolve(inner_label))

            if resolved_sha is not None:
                # Only record evaluations for committoid-qualified labels
                # (cwd-relative labels have no resolved_sha).
                cache_root = Path.home() / _DEFAULT_WORKSPACES_SUFFIX
                meta = _read_meta(cache_root, resolved_sha)
                _record_evaluation(
                    resolved_sha=resolved_sha,
                    requested_ref=str(meta.get("requested_ref", _committoid or target)),
                    local_only=bool(meta.get("local_only", False)),
                    repo=meta.get("repo") if isinstance(meta.get("repo"), str) else "",  # type: ignore[arg-type]
                    resolved_at=str(
                        meta.get("resolved_at", datetime.now(timezone.utc).isoformat())
                    ),
                    value_description=pretty_repr(value),
                )
        except WorkspaceLoadError as exc:
            has_error = True
            click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
            continue
        except WorkspaceResolutionError as exc:
            has_error = True
            click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
            continue
        except KeyError as exc:
            has_error = True
            click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
            if hasattr(workspace, "root_infos"):
                available = list(workspace.root_infos.keys())
                if available:
                    click.echo(
                        click.style(
                            f"Available roots: {', '.join(available)}", fg="red"
                        ),
                        err=True,
                    )
            continue
        except AttributeError as exc:
            has_error = True
            click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
            continue

        print("-------------------------------")
        click.echo(_format_value(value))
        print("-------------------------------")

    if has_error:
        sys.exit(1)


def _show_with_legacy_workspace(ctx: click.Context, targets: tuple[str, ...]) -> None:
    """Handle the legacy test injection path where ctx.obj['workspace'] is set.

    This path is used by existing tests that inject a pre-built workspace mock.
    It preserves backward compatibility for those tests.
    """
    workspace: Workspace = ctx.obj["workspace"]
    has_error = False

    for target in targets:
        try:
            value = force(workspace.resolve(target))
        except KeyError as exc:
            has_error = True
            click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
            available = list(workspace.root_infos.keys())
            if available:
                click.echo(
                    click.style(f"Available roots: {', '.join(available)}", fg="red"),
                    err=True,
                )
            continue
        except AttributeError as exc:
            has_error = True
            click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
            continue

        click.echo(_format_value(value))

    if has_error:
        sys.exit(1)
