"""show subcommand — resolve and display pipeline values."""

from __future__ import annotations

import dataclasses
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
import networkx
from rich.console import Console
from rich.pretty import pretty_repr
from rich.syntax import Syntax
from rich.table import Table

from mlody.cli.main import cli
from mlody.core.dag import Edge, TaskNode, ancestors_subgraph, build_dag
from mlody.core.targets import parse_target
from mlody.core.workspace import Workspace, WorkspaceLoadError, force
from mlody.db.evaluations import open_db, write_evaluation
from mlody.db.local_diff import compute_local_diff_sha, get_repo_root
from mlody.resolver import (
    MlodyActionValue,
    MlodyFolderValue,
    MlodySourceValue,
    MlodyTaskValue,
    MlodyUnresolvedValue,
    MlodyValue,
    MlodyValueValue,
    MlodyVectorValue,
    MlodyWorkspaceValue,
    resolve_label_to_value,
    resolve_workspace,
)
from mlody.resolver.errors import WorkspaceResolutionError

_logger = logging.getLogger(__name__)

_DEFAULT_CACHE_SUFFIX = Path(".cache") / "mlody"
_DEFAULT_DB_NAME = "mlody.sqlite"
_DEFAULT_WORKSPACES_SUFFIX = _DEFAULT_CACHE_SUFFIX / "workspaces"
_console = Console()


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
    full_workspace: bool = False,
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
        full_workspace=full_workspace,
        print_fn=print_fn,
        verbose=verbose,
    )
    _committoid, inner_label = _parse_inner(label)
    print_fn(pretty_repr(_parse_label_struct(label)))

    from mlody.core.label import parse_label as _core_parse_label

    concrete_label = _core_parse_label(inner_label)
    mlody_value = resolve_label_to_value(concrete_label, workspace)
    return mlody_value


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


def _pretty_struct_str(obj: object, _depth: int = 0) -> str:
    """Recursively format a Starlark struct into an indented Python-like string.

    Private fields (starting with ``_``) and callable values (validators etc.)
    are omitted to keep the output readable.
    """
    pad = "    " * _depth
    inner = "    " * (_depth + 1)

    if hasattr(obj, "as_mapping"):
        fields = {
            k: v
            for k, v in obj.as_mapping().items()
            if not k.startswith("_") or k == "_source_range"
        }
        if not fields:
            return "struct()"
        parts = [f"{inner}{k}={_pretty_struct_str(v, _depth + 1)}" for k, v in fields.items()]
        return "struct(\n" + ",\n".join(parts) + f",\n{pad})"

    if isinstance(obj, list):
        if not obj:
            return "[]"
        parts = [f"{inner}{_pretty_struct_str(v, _depth + 1)}" for v in obj]
        return "[\n" + ",\n".join(parts) + f",\n{pad}]"

    if isinstance(obj, dict):
        if not obj:
            return "{}"
        parts = [f"{inner}{k!r}: {_pretty_struct_str(v, _depth + 1)}" for k, v in obj.items()]
        return "{\n" + ",\n".join(parts) + f",\n{pad}}}"

    if callable(obj) and not isinstance(obj, type):
        return "<callable>"

    return repr(obj)


def _print_mlody_value(value: MlodyValue) -> None:
    """Print a MlodyValue to the console with syntax highlighting."""
    if isinstance(value, MlodyVectorValue):
        # Render each element in the vector using the existing per-kind dispatchers.
        # Elements are printed sequentially; an empty vector produces no output.
        for elem in value.elements:
            _print_mlody_value(elem)
        return
    if isinstance(value, MlodyValueValue):
        _console.print("value:")
        _console.print(Syntax(_pretty_struct_str(value.struct), "python", theme="monokai", word_wrap=True))
        return
    if isinstance(value, MlodyTaskValue):
        _console.print("task:")
        _console.print(Syntax(_pretty_struct_str(value.struct), "python", theme="monokai", word_wrap=True))
        return
    if isinstance(value, MlodyActionValue):
        _console.print("action:")
        _console.print(Syntax(_pretty_struct_str(value.struct), "python", theme="monokai", word_wrap=True))
        return
    click.echo(_render_mlody_value(value))


def _render_mlody_value(value: MlodyValue) -> str:
    """Render a typed MlodyValue to a human-readable string for stdout.

    Each branch corresponds to a value kind. The exact format is an
    implementation-time detail (design Q-01): using str()/pretty_repr()
    for now to produce sensible output for all types.
    """
    if isinstance(value, MlodyVectorValue):
        # Render each element separated by newlines; empty vector → empty string.
        parts = [_render_mlody_value(elem) for elem in value.elements]
        return "\n".join(parts)
    if isinstance(value, MlodyWorkspaceValue):
        name = value.name or "(cwd)"
        return f"workspace: {name}\nroot: {value.root}"
    if isinstance(value, MlodyFolderValue):
        children_display = ", ".join(value.children) if value.children else "(empty)"
        return f"folder: {value.path}\nchildren: {children_display}"
    if isinstance(value, MlodySourceValue):
        return f"source: {value.path}.mlody"
    if isinstance(value, MlodyTaskValue):
        return f"task:\n{pretty_repr(value.struct)}"
    if isinstance(value, MlodyActionValue):
        return f"action:\n{pretty_repr(value.struct)}"
    if isinstance(value, MlodyValueValue):
        return f"value:\n{pretty_repr(value.struct)}"
    # _RawAttrValue — terminal attribute reached after traversal
    from mlody.resolver.label_value import _RawAttrValue

    if isinstance(value, _RawAttrValue):
        return _format_value(value.value)
    # MlodyUnresolvedValue is handled by the caller (exits 1), not here
    return pretty_repr(value)


def _short_type_name(value: object) -> str:
    t = getattr(value, "type", None)
    if t is None:
        return "?"
    t_name = getattr(t, "name", None)
    if isinstance(t_name, str) and t_name:
        return t_name
    if isinstance(t, str) and t:
        return t
    return "?"


def _format_value_list(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "—"
    rendered: list[str] = []
    for v in values:
        name = getattr(v, "name", None)
        if not isinstance(name, str) or not name:
            name = str(v)
        rendered.append(f"{name}:{_short_type_name(v)}")
    return ", ".join(rendered)


def _format_action_cell(action_obj: object, fallback_name: str) -> str:
    if action_obj is None:
        return fallback_name
    name = getattr(action_obj, "name", None)
    if not isinstance(name, str) or not name:
        name = fallback_name
    a_inputs = _format_value_list(getattr(action_obj, "inputs", []))
    a_outputs = _format_value_list(getattr(action_obj, "outputs", []))
    a_config = _format_value_list(getattr(action_obj, "config", []))
    return f"{name}\nAIn:  {a_inputs}\nAOut: {a_outputs}\nACfg: {a_config}"


def _render_dag_table(display_graph: networkx.MultiDiGraph, title: str) -> None:
    try:
        order = list(networkx.topological_sort(display_graph))
    except networkx.NetworkXUnfeasible:
        click.echo(
            click.style("Error: cycle detected in task graph", fg="red"), err=True
        )
        return

    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("Task", style="cyan", no_wrap=True, ratio=4)
    table.add_column("Action", style="magenta", no_wrap=False, ratio=2)
    table.add_column("Dependencies", style="white", ratio=5)

    for node_id in order:
        task_node = display_graph.nodes[node_id]["task"]
        task_struct = display_graph.nodes[node_id]["task_struct"]
        deps: list[str] = []
        for src_id, _, data in display_graph.in_edges(node_id, data=True):
            edge: Edge = data["edge"]
            deps.append(f"{src_id}\n  {edge.src_port} → {edge.dst_path}")
        inputs_str = _format_value_list(getattr(task_struct, "inputs", []))
        outputs_str = _format_value_list(getattr(task_struct, "outputs", []))
        config_str = _format_value_list(getattr(task_struct, "config", []))
        task_cell = (
            f"{node_id}\nIn:  {inputs_str}\nOut: {outputs_str}\nCfg: {config_str}"
        )
        table.add_row(
            task_cell,
            _format_action_cell(getattr(task_struct, "action", None), task_node.action),
            "\n\n".join(deps) if deps else "—",
        )

    _console.print(table)


def _subgraph_for_show_output_label(
    dag: networkx.MultiDiGraph, label: str
) -> networkx.MultiDiGraph | None:
    try:
        addr = parse_target(label)
    except ValueError:
        return None
    if len(addr.field_path) == 2 and addr.field_path[0] == "outputs":
        return ancestors_subgraph(dag, addr.field_path[1])
    return None


def _maybe_print_dag_plan(workspace: Workspace, label: str) -> None:
    try:
        dag = build_dag(workspace)
        subgraph = _subgraph_for_show_output_label(dag, label)
        if subgraph is None or len(subgraph.nodes) == 0:
            return
        _render_dag_table(subgraph, f"DAG — ancestors of '{label}'")
    except Exception as exc:
        _logger.debug("Skipping DAG plan rendering for %r: %s", label, exc)


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
    full_workspace: bool = ctx.obj.get("full_workspace", False)

    for target in targets:
        try:
            workspace, resolved_sha = resolve_workspace(
                target,
                monorepo_root=monorepo_root,
                roots_file=roots,
                full_workspace=full_workspace,
                verbose=verbose,
            )
            if resolved_sha is not None:
                _logger.debug("Resolved %s to %s", target.split("|")[0], resolved_sha)

            _committoid, inner_label = _parse_inner(target)
            for expanded_inner in workspace.expand_wildcard_label(inner_label):
                full_label = (
                    f"{_committoid}|{expanded_inner}" if _committoid else expanded_inner
                )
                if verbose:
                    click.echo(
                        json.dumps(
                            dataclasses.asdict(_parse_label_struct(full_label)),
                            indent=2,
                        )
                    )
                _maybe_print_dag_plan(workspace, expanded_inner)

                # Resolve the concrete label to a typed MlodyValue (new pipeline step)
                from mlody.core.label import parse_label as _core_parse_label
                from mlody.core.label.label import Label as _Label

                if expanded_inner == "":
                    # Bare workspace label (e.g. "HEAD", "main") — construct
                    # the label directly rather than parsing an empty string.
                    concrete_label = _Label(
                        workspace=_committoid,
                        workspace_query=None,
                        entity=None,
                        entity_query=None,
                        attribute_path=None,
                        attribute_query=None,
                    )
                else:
                    concrete_label = _core_parse_label(expanded_inner)
                mlody_value = resolve_label_to_value(concrete_label, workspace)

                if isinstance(mlody_value, MlodyUnresolvedValue):
                    has_error = True
                    click.echo(
                        click.style(f"Error: {mlody_value.reason}", fg="red"), err=True
                    )
                    continue

                if resolved_sha is not None:
                    # Only record evaluations for committoid-qualified labels
                    # (cwd-relative labels have no resolved_sha).
                    cache_root = Path.home() / _DEFAULT_WORKSPACES_SUFFIX
                    meta = _read_meta(cache_root, resolved_sha)
                    _record_evaluation(
                        resolved_sha=resolved_sha,
                        requested_ref=str(
                            meta.get("requested_ref", _committoid or target)
                        ),
                        local_only=bool(meta.get("local_only", False)),
                        repo=meta.get("repo")
                        if isinstance(meta.get("repo"), str)
                        else "",  # type: ignore[arg-type]
                        resolved_at=str(
                            meta.get(
                                "resolved_at", datetime.now(timezone.utc).isoformat()
                            )
                        ),
                        value_description=_render_mlody_value(mlody_value),
                    )

                print("-------------------------------")
                _print_mlody_value(mlody_value)
                print("-------------------------------")
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
            continue
        except AttributeError as exc:
            has_error = True
            click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
            continue

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
            _maybe_print_dag_plan(workspace, target)
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
