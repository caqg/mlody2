"""dag subcommand — display the workspace task dependency graph."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import networkx
from rich.console import Console
from rich.table import Table

from mlody.cli.main import cli
from mlody.core.dag import Edge, build_dag
from mlody.core.workspace import Workspace, WorkspaceLoadError

_logger = logging.getLogger(__name__)
_console = Console()


@cli.command("dag")
@click.pass_context
def dag_cmd(ctx: click.Context) -> None:
    """Display the workspace task dependency graph."""
    monorepo_root: Path = ctx.obj["monorepo_root"]
    roots: Path | None = ctx.obj.get("roots")
    verbose: bool = ctx.obj.get("verbose", False)

    workspace = Workspace(monorepo_root=monorepo_root, roots_file=roots)
    try:
        workspace.load(verbose=verbose)
    except WorkspaceLoadError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        sys.exit(1)

    dag = build_dag(workspace)

    # Topological order so dependencies always appear before dependents.
    try:
        order = list(networkx.topological_sort(dag))
    except networkx.NetworkXUnfeasible:
        click.echo(click.style("Error: cycle detected in task graph", fg="red"), err=True)
        sys.exit(1)

    table = Table(title="Workspace DAG", show_lines=True, expand=True)
    table.add_column("Task", style="cyan", no_wrap=True, ratio=4)
    table.add_column("Action", style="magenta", no_wrap=True, ratio=2)
    table.add_column("Dependencies", style="white", ratio=5)

    for node_id in order:
        task_node = dag.nodes[node_id]["task"]
        deps: list[str] = []
        for src_id, _, data in dag.in_edges(node_id, data=True):
            edge: Edge = data["edge"]
            deps.append(f"{src_id}\n  {edge.src_port} → {edge.dst_path}")
        table.add_row(
            node_id,
            task_node.action,
            "\n\n".join(deps) if deps else "—",
        )

    _console.print(table)
