# Design: DAG Label Filter

**Version:** 1.0 **Date:** 2026-03-29 **Architect:** @vitruvius **Status:**
Draft **Requirements:**
`mlody/openspec/changes/dag-label-filter/REQUIREMENTS.md`

---

## Problem Statement

`mlody dag` renders the full workspace task graph as a Rich table. For large
workspaces this produces dozens of rows, most of which are irrelevant to the
developer's current focus. The pruning logic needed to answer "which tasks
contribute to this value?" already exists as `ancestors_subgraph()` in
`mlody/core/dag.py` but is not exposed through the CLI. This change wires it in.

---

## Design Decisions

### D-1: Optional positional argument, not a flag

The argument is declared as
`@click.argument("label", required=False, default=None)`, making it positional
(`mlody dag model_checkpoint`) not a flag
(`mlody dag --label model_checkpoint`). This is consistent with the pattern used
by `mlody show` (which also accepts bare value names as positional arguments)
and is stated as a hard requirement in FR-001.

### D-2: Shared row-rendering logic via graph variable substitution

The full-graph path and the filtered path differ in exactly two places: which
graph is passed to `networkx.topological_sort` and which string is used as the
table title. All column definitions, `add_row` calls, and edge-iteration logic
are identical. The implementation satisfies this by computing `display_graph`
and `title` in a short conditional block and then handing both to a single,
shared rendering section — no helper function extraction is needed for a command
of this size (NFR-M-001).

### D-3: Empty subgraph is a user error, not a silent no-op

When `ancestors_subgraph()` returns a graph with zero nodes, the command prints
an error to stderr and exits with code 1. Silently printing an empty table would
be confusing; the developer would not know whether the label matched nothing or
whether the workspace genuinely has no tasks. The error message quotes the
supplied value name (NFR-U-001, FR-004).

### D-4: `ancestors_subgraph` is known to return a copy, not a view

The risk noted in R-001 of the requirements document is resolved here.
`ancestors_subgraph()` (line 339 of `mlody/core/dag.py`) calls
`dag.subgraph(all_relevant).copy()` explicitly. The returned graph is a
standalone `MultiDiGraph`; `networkx.topological_sort` on it is safe and
produces no cross-contamination with the original DAG.

### D-5: Error message uses "value" in user-facing text, "port" in code comments

Following the resolution of OQ-001 in the requirements: the error string printed
to stderr says `"Error: no task produces value '<label>'"` (user-facing
language). Internal code comments and docstrings use "output port" to match the
field name `output_ports` on `TaskNode`.

### D-6: New test file `dag_cmd_test.py` rather than extending `main_test.py`

`main_test.py` tests the `cli` group and its context propagation — it does not
touch any subcommand logic. `show_test.py` establishes the precedent of one test
file per subcommand module. A new `mlody/cli/dag_cmd_test.py` follows that
pattern and keeps subcommand tests isolated.

### D-7: Tests use `Workspace` mock via `ctx.obj` injection

Following the pattern in `show_test.py`, tests invoke the CLI via
`click.testing.CliRunner` and inject a pre-built `ctx.obj` dict to bypass
filesystem verification. Because `dag_cmd` calls `workspace.load()` directly,
the mock must cover `Workspace` construction and the `load()` call. The cleanest
approach (used by the implementation agent) is to patch
`mlody.cli.dag_cmd.Workspace` with a factory that returns a
`MagicMock`-configured workspace whose `evaluator.tasks` dict is populated with
the fixture data needed to exercise `build_dag` — or, alternatively, to call
`build_dag` against a real in-memory workspace built from `InMemoryFS` /
`pyfakefs`. The latter approach (real workspace, real `build_dag`) is preferred
because it tests the full rendering path with zero mocking of internal DAG
logic; the implementing agent chooses the approach that is simplest to express
given the available test helpers.

---

## Architecture Sketch

### Files changed

```
mlody/cli/dag_cmd.py        MODIFIED — add optional `label` argument and
                            filtered rendering path
mlody/cli/dag_cmd_test.py   NEW      — CLI tests for both paths
mlody/cli/BUILD.bazel       MODIFIED — new dag_cmd_test target (via gazelle)
```

No changes to `mlody/core/dag.py`, `mlody/core/workspace.py`, or any other file
outside `mlody/cli/`.

### Control flow

```
dag_cmd(ctx, label):

    monorepo_root = ctx.obj["monorepo_root"]
    roots         = ctx.obj.get("roots")
    verbose       = ctx.obj.get("verbose", False)

    workspace = Workspace(monorepo_root=monorepo_root, roots_file=roots)
    workspace.load(verbose=verbose)          # WorkspaceLoadError -> stderr + exit 1

    dag = build_dag(workspace)

    if label is None:
        display_graph = dag
        title = "Workspace DAG"
    else:
        display_graph = ancestors_subgraph(dag, label)
        if len(display_graph.nodes) == 0:
            click.echo(
                click.style(f"Error: no task produces value '{label}'", fg="red"),
                err=True,
            )
            sys.exit(1)
        title = f"DAG \u2014 ancestors of '{label}'"

    try:
        order = list(networkx.topological_sort(display_graph))
    except networkx.NetworkXUnfeasible:
        click.echo(click.style("Error: cycle detected in task graph", fg="red"), err=True)
        sys.exit(1)

    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("Task",         style="cyan",    no_wrap=True, ratio=4)
    table.add_column("Action",       style="magenta", no_wrap=True, ratio=2)
    table.add_column("Dependencies", style="white",               ratio=5)

    for node_id in order:
        task_node = display_graph.nodes[node_id]["task"]
        deps = [
            f"{src_id}\n  {edge.src_port} \u2192 {edge.dst_path}"
            for src_id, _, data in display_graph.in_edges(node_id, data=True)
            for edge in [data["edge"]]
        ]
        table.add_row(node_id, task_node.action, "\n\n".join(deps) if deps else "\u2014")

    _console.print(table)
```

Key differences from the current implementation (highlighted):

1. `@click.argument("label", required=False, default=None)` added to the
   decorator stack.
2. `label: str | None` added to the function signature.
3. The `display_graph` / `title` branch block (five lines) inserted between
   `build_dag()` and the topological sort.
4. The topological sort and table construction operate on `display_graph`
   instead of `dag` directly.
5. `dag.nodes[node_id]` and `dag.in_edges(...)` are replaced by their
   `display_graph` equivalents throughout the rendering loop.
6. The updated docstring describes both code paths and the `VALUE` argument.

### Import delta

One new name imported from `mlody.core.dag`:

```python
from mlody.core.dag import Edge, ancestors_subgraph, build_dag
```

`ancestors_subgraph` is the only addition.

---

## Test Specification

File: `mlody/cli/dag_cmd_test.py`

All tests use `click.testing.CliRunner`. Workspace content is provided either
via `pyfakefs` (`fs` fixture) building a real `.mlody` filesystem, or via
`Workspace` mock injection through `ctx.obj`. The implementing agent selects
whichever approach produces the most readable test for each case.

| Test class / method                    | What it asserts                                                                                                           | Requirement             |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| `TestDagFullGraph`                     |                                                                                                                           |                         |
| `test_no_arg_shows_all_tasks`          | All task node IDs appear in output; title contains `"Workspace DAG"`                                                      | FR-002, KPI-002         |
| `test_no_arg_title_is_workspace_dag`   | Output contains the literal string `"Workspace DAG"`                                                                      | FR-002, US-004          |
| `TestDagFilteredPath`                  |                                                                                                                           |                         |
| `test_label_shows_ancestor_nodes_only` | Only ancestor task IDs appear; non-ancestor task IDs are absent; exit code 0                                              | FR-003, US-001, KPI-001 |
| `test_label_excludes_unrelated_tasks`  | Workspace has tasks outside the ancestor set; confirm they are absent from output                                         | US-001                  |
| `test_label_title_contains_value_name` | Table title contains `"ancestors of"` and the supplied label                                                              | FR-005, US-004          |
| `test_label_single_producer_one_row`   | Label produced by exactly one isolated task; output contains exactly that one task ID                                     | FR-005                  |
| `TestDagErrorPath`                     |                                                                                                                           |                         |
| `test_unknown_label_exits_nonzero`     | Unrecognised label; exit code 1; stderr contains `"Error:"` prefix and the label name                                     | FR-004, US-003, KPI-003 |
| `test_unknown_label_error_to_stderr`   | The error text is on stderr (not stdout)                                                                                  | FR-004, NFR-U-001       |
| `TestDagCaseSensitivity`               |                                                                                                                           |                         |
| `test_wrong_case_not_found`            | `ModelCheckpoint` returns error when value is registered as `model_checkpoint`                                            | FR-003, §8.2            |
| `TestDagRegression`                    |                                                                                                                           |                         |
| `test_no_arg_regression`               | Full-graph output is identical before and after this change; all pre-existing task IDs appear; title is `"Workspace DAG"` | NFR-C-001, KPI-002      |

---

## Bazel BUILD changes

After adding `dag_cmd_test.py`, run `bazel run :gazelle` from the repo root.
Gazelle will detect the new test file and add an `o_py_test` target. The
expected target will look like:

```python
o_py_test(
    name = "dag_cmd_test",
    srcs = ["dag_cmd_test.py"],
    deps = [
        ":cli_lib",
        "//common/python/starlarkish/evaluator:evaluator_lib",  # keep
        "@pip//click",
        "@pip//networkx",
        "@pip//pyfakefs",
        "@pip//pytest",
    ],
)
```

The `# keep` comment on the evaluator dep is needed if the test builds a real
`InMemoryFS`-backed workspace; Gazelle cannot infer transitive deps from imports
alone. The implementing agent adds `# keep` on any dep Gazelle would drop.

---

## Constraints and Risks

| Risk                                                                               | Mitigation                                                                                                                             |
| ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| R-001: `ancestors_subgraph` returns a view (not a copy), breaking topological sort | Resolved: `dag.py` line 339 calls `.copy()` explicitly. Confirmed by reading source before coding.                                     |
| R-002: Users expect a full mlody label address, not a bare value name              | `--help` docstring updated to say "VALUE is an output value name (e.g. `model_checkpoint`), not a full mlody address."                 |
| R-003: `NetworkXUnfeasible` on pruned subgraph                                     | Unreachable in practice (subgraph of a DAG cannot cycle). Existing cycle-error handler is inherited unchanged; no new handling needed. |

---

## Open Questions

All open questions from the requirements document are resolved:

- **OQ-001** (user-facing terminology "port" vs "value"): resolved in
  requirements v1.1 — "value" in user-facing text, "port" in technical contexts.
  Applied consistently in D-5 above.
- **OQ-002** (multi-label support): explicitly out of scope per requirements
  v1.2, tracked as a future consideration. No design work performed here.
