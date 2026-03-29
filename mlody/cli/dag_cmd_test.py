"""Tests for mlody.cli.dag_cmd — dag subcommand.

All tests trace back to named requirements and scenarios in
mlody/openspec/changes/dag-label-filter/REQUIREMENTS.md and design.md.

Workspace content is provided via a patched Workspace factory that returns
a MagicMock whose evaluator.tasks dict is populated with simple Struct-like
objects.  build_dag and ancestors_subgraph are exercised for real — no mocking
of internal DAG logic.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from mlody.cli.main import cli


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_port(name: str, source: str = "") -> SimpleNamespace:
    """Construct a minimal port/value namespace matching the task struct field shape."""
    return SimpleNamespace(name=name, source=source)


def _make_action(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _make_task_struct(
    name: str,
    action_name: str,
    outputs: list[str] | None = None,
    inputs: list[str] | None = None,
) -> SimpleNamespace:
    """Build a minimal task struct that build_dag can read."""
    return SimpleNamespace(
        kind="task",
        name=name,
        action=_make_action(action_name),
        outputs=[_make_port(p) for p in (outputs or [])],
        inputs=[_make_port(p) for p in (inputs or [])],
    )


def _make_workspace_mock(tasks: dict[str, SimpleNamespace]) -> MagicMock:
    """Return a Workspace mock whose evaluator.tasks yields the given task structs.

    The dict key must follow the evaluator convention: ``"{stem}:{name}"``.
    build_dag derives the node_id as ``"task/{stem}:{name}"``.
    """
    evaluator = MagicMock()
    evaluator.tasks = tasks
    ws = MagicMock()
    ws.evaluator = evaluator
    return ws


def _invoke_dag(
    tmp_path: Path,
    extra_args: list[str],
    tasks: dict[str, SimpleNamespace],
) -> object:
    """Patch Workspace and invoke 'mlody dag' with the given args and tasks."""
    ws_mock = _make_workspace_mock(tasks)

    runner = CliRunner()
    with patch("mlody.cli.dag_cmd.Workspace") as mock_cls:
        mock_cls.return_value = ws_mock
        result = runner.invoke(
            cli,
            ["dag"] + extra_args,
            obj={"monorepo_root": tmp_path, "roots": None, "verbose": False},
        )
    return result


# ---------------------------------------------------------------------------
# Shared fixture tasks
#
# Graph:  upstream -> midstream -> downstream
#                                     outputs: "model_checkpoint"
#
# isolated_task: produces "isolated_value", no deps, no connection to above.
# ---------------------------------------------------------------------------

_UPSTREAM = _make_task_struct("upstream", "train_action", outputs=["weights"])
_MIDSTREAM = _make_task_struct(
    "midstream",
    "eval_action",
    outputs=["eval_metrics"],
    inputs=["weights"],
)
_DOWNSTREAM = _make_task_struct(
    "downstream",
    "export_action",
    outputs=["model_checkpoint"],
    inputs=["eval_metrics"],
)
_ISOLATED = _make_task_struct("isolated_task", "misc_action", outputs=["isolated_value"])

_ALL_TASKS: dict[str, SimpleNamespace] = {
    "test:upstream": _UPSTREAM,
    "test:midstream": _MIDSTREAM,
    "test:downstream": _DOWNSTREAM,
    "test:isolated_task": _ISOLATED,
}

# Note: the tasks above have no cross-task source references (`:task.port`)
# so build_dag will produce no edges between them.  That is fine for the CLI
# rendering tests; ancestors_subgraph falls back to tasks_producing() which
# scans output_ports — so filtering by "model_checkpoint" will find
# "task/test:downstream" and its (empty) ancestor set, producing a one-row
# subgraph.  The three-task chain tests (TestDagFilteredPath) use a DAG where
# edges are wired via source labels, which requires a more explicit setup.


# ---------------------------------------------------------------------------
# Fixture tasks WITH cross-task wiring (for ancestor chain tests)
# ---------------------------------------------------------------------------


def _make_wired_port(name: str, source: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, source=source)


def _wired_tasks() -> dict[str, SimpleNamespace]:
    """Build a three-task chain where ancestors_subgraph can follow edges.

    upstream  --weights--> midstream  --processed_weights--> downstream (model_checkpoint)
    isolated_task: no deps, outputs isolated_value
    """
    upstream = SimpleNamespace(
        kind="task",
        name="upstream",
        action=_make_action("train_action"),
        outputs=[_make_port("weights")],
        inputs=[],
    )
    midstream = SimpleNamespace(
        kind="task",
        name="midstream",
        action=_make_action("eval_action"),
        # source ":upstream.weights" means midstream consumes upstream.weights
        inputs=[_make_wired_port("weights", ":upstream.weights")],
        outputs=[_make_port("processed_weights")],
    )
    downstream = SimpleNamespace(
        kind="task",
        name="downstream",
        action=_make_action("export_action"),
        inputs=[_make_wired_port("processed_weights", ":midstream.processed_weights")],
        outputs=[_make_port("model_checkpoint")],
    )
    isolated = SimpleNamespace(
        kind="task",
        name="isolated_task",
        action=_make_action("misc_action"),
        outputs=[_make_port("isolated_value")],
        inputs=[],
    )
    return {
        "test:upstream": upstream,
        "test:midstream": midstream,
        "test:downstream": downstream,
        "test:isolated_task": isolated,
    }


# ---------------------------------------------------------------------------
# 2.1 TestDagFullGraph
# ---------------------------------------------------------------------------


class TestDagFullGraph:
    """FR-002, KPI-002: full-graph path when no label is supplied."""

    def test_no_arg_shows_all_tasks(self, tmp_path: Path) -> None:
        """No label supplied; all task node IDs appear in stdout; exit code 0."""
        result = _invoke_dag(tmp_path, [], _ALL_TASKS)

        assert result.exit_code == 0, result.output  # type: ignore[union-attr]
        output: str = result.output  # type: ignore[union-attr]
        assert "task/test:upstream" in output
        assert "task/test:midstream" in output
        assert "task/test:downstream" in output
        assert "task/test:isolated_task" in output

    def test_no_arg_title_is_workspace_dag(self, tmp_path: Path) -> None:
        """FR-002, US-004: output contains the literal string 'Workspace DAG'."""
        result = _invoke_dag(tmp_path, [], _ALL_TASKS)

        assert result.exit_code == 0  # type: ignore[union-attr]
        assert "Workspace DAG" in result.output  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 2.2 TestDagFilteredPath
# ---------------------------------------------------------------------------


class TestDagFilteredPath:
    """FR-003, FR-005, US-001, KPI-001: filtered path when a label is supplied."""

    def test_label_shows_ancestor_nodes_only(self, tmp_path: Path) -> None:
        """Only ancestor task IDs appear in stdout; exit code 0 (FR-003, KPI-001)."""
        result = _invoke_dag(tmp_path, ["model_checkpoint"], _wired_tasks())

        assert result.exit_code == 0  # type: ignore[union-attr]
        output: str = result.output  # type: ignore[union-attr]
        # downstream produces model_checkpoint; upstream and midstream are ancestors
        assert "task/test:downstream" in output
        assert "task/test:upstream" in output
        assert "task/test:midstream" in output

    def test_label_excludes_unrelated_tasks(self, tmp_path: Path) -> None:
        """US-001: isolated_task is absent when filtering by model_checkpoint."""
        result = _invoke_dag(tmp_path, ["model_checkpoint"], _wired_tasks())

        assert result.exit_code == 0  # type: ignore[union-attr]
        assert "task/test:isolated_task" not in result.output  # type: ignore[union-attr]

    def test_label_title_contains_value_name(self, tmp_path: Path) -> None:
        """FR-005, US-004: title contains 'ancestors of' and the supplied label."""
        result = _invoke_dag(tmp_path, ["model_checkpoint"], _wired_tasks())

        assert result.exit_code == 0  # type: ignore[union-attr]
        output: str = result.output  # type: ignore[union-attr]
        assert "ancestors of" in output
        assert "model_checkpoint" in output

    def test_label_single_producer_one_row(self, tmp_path: Path) -> None:
        """FR-005: isolated label produced by exactly one task with no upstream deps."""
        tasks: dict[str, SimpleNamespace] = {
            "test:only_task": _make_task_struct(
                "only_task", "solo_action", outputs=["solo_value"]
            ),
        }
        result = _invoke_dag(tmp_path, ["solo_value"], tasks)

        assert result.exit_code == 0  # type: ignore[union-attr]
        output: str = result.output  # type: ignore[union-attr]
        assert "task/test:only_task" in output
        # No other task IDs should appear
        assert "task/test:" not in output.replace("task/test:only_task", "")


# ---------------------------------------------------------------------------
# 2.3 TestDagErrorPath
# ---------------------------------------------------------------------------


class TestDagErrorPath:
    """FR-004, US-003, KPI-003, NFR-U-001: error path for unrecognised label."""

    def test_unknown_label_exits_nonzero(self, tmp_path: Path) -> None:
        """Unrecognised label; exit code 1; stderr contains 'Error:' and label (FR-004)."""
        runner = CliRunner()
        ws_mock = _make_workspace_mock(_ALL_TASKS)

        with patch("mlody.cli.dag_cmd.Workspace") as mock_cls:
            mock_cls.return_value = ws_mock
            result = runner.invoke(
                cli,
                ["dag", "nonexistent_value"],
                obj={"monorepo_root": tmp_path, "roots": None, "verbose": False},
            )

        assert result.exit_code == 1
        stderr: str = result.stderr  # type: ignore[union-attr]
        assert "Error:" in stderr
        assert "nonexistent_value" in stderr

    def test_unknown_label_error_to_stderr(self, tmp_path: Path) -> None:
        """FR-004, NFR-U-001: error text is emitted via stderr (click.echo err=True).

        Click's CliRunner routes err=True output to result.stderr regardless of
        stream mixing.  Asserting the error appears in result.stderr is the
        reliable cross-version check that click.echo(..., err=True) was used.
        """
        runner = CliRunner()
        ws_mock = _make_workspace_mock(_ALL_TASKS)

        with patch("mlody.cli.dag_cmd.Workspace") as mock_cls:
            mock_cls.return_value = ws_mock
            result = runner.invoke(
                cli,
                ["dag", "nonexistent_value"],
                obj={"monorepo_root": tmp_path, "roots": None, "verbose": False},
            )

        assert "Error:" in result.stderr  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 2.4 TestDagCaseSensitivity
# ---------------------------------------------------------------------------


class TestDagCaseSensitivity:
    """FR-003, §8.2: label matching is case-sensitive."""

    def test_wrong_case_not_found(self, tmp_path: Path) -> None:
        """'ModelCheckpoint' returns error when value is registered as 'model_checkpoint'."""
        runner = CliRunner()
        # Only lower-case port name is registered
        tasks: dict[str, SimpleNamespace] = {
            "test:exporter": _make_task_struct(
                "exporter", "export_action", outputs=["model_checkpoint"]
            ),
        }
        ws_mock = _make_workspace_mock(tasks)

        with patch("mlody.cli.dag_cmd.Workspace") as mock_cls:
            mock_cls.return_value = ws_mock
            result = runner.invoke(
                cli,
                ["dag", "ModelCheckpoint"],
                obj={"monorepo_root": tmp_path, "roots": None, "verbose": False},
            )

        assert result.exit_code == 1
        assert "Error:" in result.stderr  # type: ignore[union-attr]
        assert "ModelCheckpoint" in result.stderr  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 2.5 TestDagRegression
# ---------------------------------------------------------------------------


class TestDagRegression:
    """NFR-C-001, KPI-002: full-graph path regression after this change."""

    def test_no_arg_regression(self, tmp_path: Path) -> None:
        """Full-graph output contains all pre-existing task IDs; title is 'Workspace DAG'.

        Guards against regression introduced by the dag-label-filter change.
        """
        result = _invoke_dag(tmp_path, [], _wired_tasks())

        assert result.exit_code == 0  # type: ignore[union-attr]
        output: str = result.output  # type: ignore[union-attr]
        # All four tasks must appear in the full-graph output
        assert "task/test:upstream" in output
        assert "task/test:midstream" in output
        assert "task/test:downstream" in output
        assert "task/test:isolated_task" in output
        # Title must remain unchanged
        assert "Workspace DAG" in output
        # Filtered title must NOT appear when no argument is given
        assert "ancestors of" not in output
