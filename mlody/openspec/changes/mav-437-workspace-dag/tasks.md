# Tasks: mav-437-workspace-dag

## Task 1 — Add `networkx` dependency and repin

- Add `networkx` to the `dependencies` list in `mlody/pyproject.toml` (no
  version pin; comment: "workspace DAG builder")
- Run `o-repin` to regenerate lock files
- Verify `bazel build //mlody/...` still succeeds with the updated lockfile
- No code changes in this task — dependency availability is the only acceptance
  criterion

Status: [x]

---

## Task 2 — Core types: `TaskNode`, `Edge`, `PortRef`, `PathError`

Write the failing tests first, then implement.

**Tests** — create `mlody/core/dag_test.py` with the following test stubs (all
asserting that the types can be imported and constructed):

- `test_task_node_is_frozen` — construct a `TaskNode`; assert `frozen=True` by
  verifying `AttributeError` is raised on field assignment
- `test_edge_is_frozen` — same pattern for `Edge`
- `test_port_ref_is_frozen` — same pattern for `PortRef`
- `test_path_error_is_frozen` — same pattern for `PathError`
- `test_port_location_parse_error_is_value_error` — assert
  `issubclass(PortLocationParseError, ValueError)`
- `test_task_node_fields` — construct
  `TaskNode(node_id="task/s:n", name="n", action="a", input_ports=("x",), output_ports=("y",))`
  and assert each field

**Implementation** — create `mlody/core/dag.py` with Section 1 only:

- `TaskNode`, `Edge`, `PortRef`, `PathError` as frozen dataclasses with the
  exact field signatures from spec §3.1
- `PortLocationParseError(ValueError)` with a message including the offending
  raw string
- Module-level docstring describing the graph model, unified `Edge` type,
  `dst_path` convention, and workspace lifecycle relationship (NFR-U-001, §15.2)
- No other symbols yet

Run `bazel run :gazelle` in `mlody/core/` to generate the initial BUILD entry.
After Gazelle runs, verify that `@pip//networkx` appears in the `dag_lib` deps;
if Gazelle does not infer it from imports, add `# keep` manually to protect it.

Run `bazel test //mlody/core:dag_test --test_output=errors` — all 7 type tests
must pass.

Status: [ ]

---

## Task 3 — `parse_port_location` and its tests

Write the failing tests first, then implement.

**Tests** — add to `mlody/core/dag_test.py`:

- `TestParsePortLocationValid` — parametrize over:
  - `":a.outputs"` → `PortRef(task="a", port="outputs")`
  - `":pretrain.checkpoint"` → `PortRef(task="pretrain", port="checkpoint")`
  - `":my-task.out_val"` → `PortRef(task="my-task", port="out_val")`
  - `":t.nested.port"` → `PortRef(task="t", port="nested.port")` (dots allowed
    in port group 2)
- `TestParsePortLocationMissingColon` — `"a.outputs"` raises
  `PortLocationParseError`
- `TestParsePortLocationMissingDot` — `":a"` raises `PortLocationParseError`
- `TestParsePortLocationEmpty` — `""` raises `PortLocationParseError`
- `TestParsePortLocationBadTaskName` — `":123task.port"` raises
  `PortLocationParseError` (task name must start with letter/underscore)

**Implementation** — add Section 2 to `mlody/core/dag.py`:

- `parse_port_location(raw: str) -> PortRef` using the regex
  `^:([A-Za-z_][A-Za-z0-9_-]*)\.([A-Za-z_][A-Za-z0-9_.-]*)$` (spec §3.2)
- Docstring per NFR-U-001
- Raise `PortLocationParseError` with message
  `"Invalid port location {raw!r}: expected ':task_name.port_name'"` on mismatch

Run `bazel test //mlody/core:dag_test --test_output=errors` — all tests
including the new parser cases must pass.

Status: [ ]

---

## Task 4 — `build_dag`: node construction and isolated-task tests

Write the failing tests first, then implement.

**Test helper** — add to `mlody/core/dag_test.py`:

```python
def _build_dag_from_mlody(files: dict[str, str]) -> networkx.MultiDiGraph:
    with InMemoryFS(BASE_FILES | files, root="/project") as root:
        ev = Evaluator(root)
        ev.eval_file(root / "test.mlody")
        ev.resolve()
    ws = _FakeWorkspace(ev)
    return build_dag(ws)
```

`_FakeWorkspace` is a local test-only class exposing `.evaluator` backed by the
`Evaluator` instance. `BASE_FILES` must include the minimal set of `.mlody`
stubs needed by the evaluator (look at `mlody/core/workspace_test.py` for the
pattern).

**Tests** — add:

- `TestBuildDagIsolatedTask` — workspace with one task and no cross-task
  location references; assert graph has exactly 1 node, 0 edges, and
  `dag.nodes[node_id]["task"]` is a `TaskNode` with correct `name`, `action`,
  `input_ports`, `output_ports`
- `TestBuildDagNodeMetadata` — assert
  `dag.nodes[node_id]["task"].node_id == node_id` and
  `dag.nodes[node_id]["task_struct"]` is the raw struct (not `None`)
- `TestBuildDagTwoIsolatedTasks` — workspace with two tasks; assert 2 nodes, 0
  edges

**Implementation** — add Section 3 (nodes only) to `mlody/core/dag.py`:

- `_iter_tasks(evaluator)` — yields `(node_id, task_struct)` pairs; derives
  `node_id = f"task/{tasks_key}"` from `evaluator.tasks` dict keys (spec §D-1)
- `build_dag(workspace: Workspace) -> networkx.MultiDiGraph` — Step 1 only (node
  construction): for each task add node with `task=TaskNode(...)` and
  `task_struct=<raw struct>` under the node key; `tasks_index` is built here for
  use in Step 2
- No edge construction yet; return `dag` after Step 1

Run `bazel test //mlody/core:dag_test --test_output=errors`.

Status: [ ]

---

## Task 5 — `build_dag`: edge construction tests

Write the failing tests first, then implement.

**Tests** — add to `mlody/core/dag_test.py`:

- `TestBuildDagLinearChain` — workspace A -> B -> C (A output consumed by B
  input, B output consumed by C input); assert 3 nodes, 2 directed edges,
  correct `src_port` and `dst_path` on each edge's `Edge` annotation (covers
  FR-001, FR-002, US-001/002)
- `TestBuildDagFork` — A produces a value consumed by both B and C; assert 3
  nodes, 2 edges both originating from A (FR-003)
- `TestBuildDagJoin` — A and B each produce a distinct value both consumed by C;
  assert 3 nodes, 2 edges both terminating at C (FR-001)
- `TestBuildDagDiamond` — A -> B -> D and A -> C -> D; assert 4 nodes, 4 edges
  (FR-001)
- `TestBuildDagMultiEdgeSamePair` — A produces two distinct values both consumed
  by B; assert 2 nodes, 2 parallel edges between A and B with distinct
  `src_port` annotations (FR-003, MultiDiGraph semantics)
- `TestBuildDagEdgeAnnotations` — for a single A -> B edge, assert
  `dag.edges[A_id, B_id, k]["edge"]` is an `Edge` instance with non-empty
  `src_port` and `dst_path` (US-003)
- `TestBuildDagSingleSegmentDstPath` — edge where `dst_path` has no dots; assert
  `Edge.dst_path == "model_weight"` (FR-005, FR-008)
- `TestBuildDagMultiSegmentDstPath` — edge where `dst_path` contains dots;
  assert `Edge.dst_path == "action.config.lr"` (FR-005, FR-008)

**Implementation** — complete Section 3 in `mlody/core/dag.py`:

- Add `_collect_edges(evaluator, tasks_index)` — implements the two-loop
  algorithm from spec §3.3: scan each task's `outputs` and `inputs` for values
  whose `label` starts with `":"`, call `parse_port_location`, resolve the
  referenced task via `tasks_index`, and yield
  `(src_node_id, dst_node_id, Edge(...))` triples
- Add Step 2 to `build_dag`: call `_collect_edges` and `dag.add_edge` for each
  triple; edge data key is `"edge"`
- Use `getattr(val, "label", None)` at the Struct boundary; add
  `# type: ignore[attr-defined]` where basedpyright cannot infer the Struct
  field (spec §D-4)

Run `bazel test //mlody/core:dag_test --test_output=errors` — all edge tests
must pass.

Status: [ ]

---

## Task 6 — Query functions: `tasks_producing` and `tasks_consuming`

Write the failing tests first, then implement.

**Tests** — add to `mlody/core/dag_test.py`:

- `TestTasksProducingKnown` — DAG with task T declaring output port `"model"`;
  assert `tasks_producing(dag, "model") == {T_node_id}` (FR-011, US-005)
- `TestTasksProducingUnknown` — same DAG; assert
  `tasks_producing(dag, "nonexistent") == set()` (FR-011)
- `TestTasksProducingMultiple` — two tasks each declaring output port
  `"checkpoint"`; assert both node IDs in the result set
- `TestTasksConsumingBothPathForms` — one edge with `src_port="tokens"` and
  single-segment `dst_path`; another edge with `src_port="weights"` and
  multi-segment `dst_path`; assert `tasks_consuming(dag, "tokens")` returns the
  correct destination node ID; same for `"weights"` (FR-012, US-006)
- `TestTasksConsumingUnknown` — assert
  `tasks_consuming(dag, "nonexistent") == set()`

**Implementation** — add Section 4 (queries only, no `ancestors_subgraph` yet)
to `mlody/core/dag.py`:

- `tasks_producing(dag, value_name)` — O(N) scan over `dag.nodes(data=True)`,
  check `node_data["task"].output_ports` (spec §3.4)
- `tasks_consuming(dag, value_name)` — O(E) scan over
  `dag.edges(data=True, keys=True)`, check `edge_data["edge"].src_port`; return
  destination node IDs (spec §3.4)
- Full docstrings on both functions (NFR-U-001)

Run `bazel test //mlody/core:dag_test --test_output=errors`.

Status: [ ]

---

## Task 7 — `ancestors_subgraph` and topological sort

Write the failing tests first, then implement.

**Tests** — add to `mlody/core/dag_test.py`:

- `TestAncestorsSubgraphChain` — A -> B -> C where C produces target output;
  assert returned subgraph contains all three nodes and both edges (FR-013,
  US-007)
- `TestAncestorsSubgraphExcludesUnrelated` — A -> B -> C and isolated task D;
  assert D is not in the subgraph for C's output (FR-013)
- `TestAncestorsSubgraphSingleTask` — DAG with one task that produces the target
  output; assert subgraph has 1 node and 0 edges (FR-013)
- `TestAncestorsSubgraphNoProducer` — target output not produced by any task;
  assert returned subgraph is empty (FR-013)
- `TestAncestorsSubgraphReturnsCopy` — assert modifying the returned subgraph
  does not affect the original DAG (spec §3.4: `.copy()` semantics)
- `TestTopologicalSortCompatible` — call
  `list(networkx.topological_sort(build_dag(ws)))` on a valid acyclic workspace
  and assert no exception is raised; assert all node IDs appear in the result
  (FR-014)

**Implementation** — add `ancestors_subgraph` to Section 4 of
`mlody/core/dag.py`:

- Implement the algorithm from spec §3.4 / REQUIREMENTS Appendix D:
  `tasks_producing` -> `networkx.ancestors` -> union ->
  `dag.subgraph(...).copy()`
- Return an empty `networkx.MultiDiGraph()` when no producer is found
- Docstring per NFR-U-001

Run `bazel test //mlody/core:dag_test --test_output=errors`.

Status: [ ]

---

## Task 8 — `validate_paths` and `PathError` tests

Write the failing tests first, then implement.

**Tests** — add to `mlody/core/dag_test.py`:

- `TestValidatePathsValid` — DAG where every edge's `dst_path` resolves against
  the destination task struct; assert `validate_paths(dag) == []` (FR-009)
- `TestValidatePathsInvalidSegment` — DAG with one edge whose `dst_path`
  contains a non-existent field; assert `validate_paths(dag)` returns a list
  containing one `PathError` whose `task` field is the destination node ID,
  `path` field is the full `dst_path`, and `reason` names the failing segment
  (FR-009, FR-010, NFR-U-002)
- `TestValidatePathsMultipleErrors` — two invalid edges; assert two `PathError`
  instances returned (FR-009: all errors collected, no early exit)
- `TestValidatePathsMultiSegment` — `dst_path="action.config.lr"` where `action`
  exists on the struct but `config` does not; assert `PathError.reason` names
  `"config"` as the failing segment

**Implementation** — add `validate_paths` to Section 4 of `mlody/core/dag.py`:

- Iterate `dag.edges(keys=True, data=True)`; for each edge retrieve
  `task_struct` from `dag.nodes[dst]["task_struct"]`; walk `dst_path.split(".")`
  via the `hasattr`/`getattr` traversal from spec §3.4
- Append `PathError(task=dst, path=dst_path, reason=...)` for each failing
  segment; do not raise
- Docstring per NFR-U-001

Run `bazel test //mlody/core:dag_test --test_output=errors` — all tests must
pass.

Status: [ ]

---

## Task 9 — Grep for existing `location=":"` patterns

Before the edge-detection logic is considered complete, run:

```
grep -r 'location=":"' mlody/
grep -r "location=':" mlody/
```

Document findings inline in this task. If any `.mlody` files already use a
cross-task location format that differs from `":task_name.port_name"`, note them
here and open a follow-up migration item. If no files use this format, note that
edge detection is effectively a no-op on the current corpus (spec §D-2, R-003).

No code changes required unless existing files need to be migrated to the
canonical grammar. This task unblocks the completeness assertion in the PR
description.

**Findings (2026-03-28):** No `.mlody` files in the repository use the
`location=":"` or `location=':'` cross-task format. The grep matched only
documentation files. Edge detection is effectively a no-op on the current
corpus — all tasks will be isolated nodes until `.mlody` files adopt the
`":task_name.port_name"` grammar. No migration required.

Status: [x]

---

## Task 10 — Lint, type check, and regression

- Run `bazel build --config=lint //mlody/core/...` — fix all ruff warnings and
  errors
- Verify basedpyright strict reports zero errors on `mlody/core/dag.py` (use
  `bazel build --config=lint //mlody/core:dag_lib` or invoke basedpyright
  directly; KPI-003)
- Run `bazel test //mlody/...` — full mlody suite must pass with no regressions
  in existing targets (REQUIREMENTS §14.2)

Status: [ ]
