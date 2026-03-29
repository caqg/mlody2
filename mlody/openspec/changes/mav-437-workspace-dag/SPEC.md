# SPEC: Workspace DAG

**Version:** 1.0 **Date:** 2026-03-28 **Architect:** @vitruvius **Status:**
Draft **Requirements:**
`mlody/openspec/changes/mav-437-workspace-dag/REQUIREMENTS.md`

---

## Executive Summary

mlody tasks are currently coupled through shared value names — one task declares
a value as an output, another lists the same value as an input, but no data
structure makes that relationship explicit or traversable. This change
introduces `mlody/core/dag.py`, a pure library module that builds an explicit
`networkx.MultiDiGraph` from a fully-evaluated workspace.

Nodes are tasks, identified by their `all`-dict tuple key (the unique,
workspace-scoped canonical identifier). Edges carry a typed `Edge` annotation
with `src_port` (the output port name on the source task) and `dst_path` (a
dot-separated destination path on the consuming task). A single `Edge` type
covers both "input wiring" (single-segment `dst_path`) and "config injection"
(multi-segment `dst_path`); no separate types are needed.

The public API exports `build_dag`, `validate_paths`, three query helpers
(`tasks_producing`, `tasks_consuming`, `ancestors_subgraph`), and
`parse_port_location`. All types are frozen dataclasses satisfying basedpyright
strict mode.

**Requirements addressed:** FR-001 through FR-014, NFR-P-001/002, NFR-SC-001,
NFR-S-001, NFR-U-001/002, NFR-M-001/002, NFR-C-001/002.

---

## Architecture Overview

```
mlody/core/
  dag.py               NEW — all public symbols (types + builder + queries)
  dag_test.py          NEW — unit tests

mlody/core/BUILD.bazel MODIFIED — two new targets: dag_lib, dag_test
```

`dag.py` is a self-contained module. It reads from `Workspace.evaluator.tasks`
(already-resolved task structs) and `Workspace.evaluator.values` (registered
value structs) after `Workspace.load()` has returned. It does not import from
any mlody subsystem other than those two accessor paths.

Data flow:

```
Workspace (loaded)
  |
  +--> evaluator.tasks            dict[str, Named]  -- "stem:name" -> task struct
  |      task.inputs              list[value struct | str]
  |      task.outputs             list[value struct | str]
  |      task.action              action struct
  |      task.name                bare name (display only)
  |
  +--> evaluator.all              dict[tuple | str, Named]
         (kind, stem, name) -> thing    <-- canonical keys for tasks
         "task/{key}" -> thing          <-- also present after resolve()
  |
  v
build_dag(workspace)
  |
  +--> for each (key, task_struct) in _iter_tasks(evaluator)
  |       add node key with TaskNode metadata
  |
  +--> for each output value on each task
  |       inspect value.location for PortRef strings (":task.port" form)
  |       if location resolves to another task's output port:
  |           add Edge(src_port=..., dst_path=...)
  |
  v
networkx.MultiDiGraph
  nodes: all-dict keys (strings derived from tuples)
  node["task"]: TaskNode
  edges: Edge annotations under edge["edge"]
```

---

## Design Decisions

### D-1: `evaluator.tasks` is the iteration source for nodes

The `Workspace.evaluator.tasks` dict maps `"{stem}:{name}"` string keys to
already-resolved task structs. This is the correct source because:

- It contains only task-kind registrations (no noise from other kinds).
- After `Workspace.load()`, all string labels in `inputs`/`outputs`/`action`
  have been resolved to struct references by `evaluator.resolve()`.

For the canonical node ID (FR-004b), we use the `all`-dict tuple key form
`(kind, stem, name)` serialized as `"task/{stem}:{name}"`. This is the
workspace-scoped unique identifier per the REQUIREMENTS. We derive it from the
`tasks` dict key by prepending `"task/"`.

**Rationale for key format:** The `evaluator.all` dict stores tasks under two
key forms after `resolve()`: the tuple `("task", stem, name)` inserted during
`_register`, and the string `"task/{key}"` inserted during `resolve()`. The
string form is simpler for use as a NetworkX node ID and is directly derivable
from the `tasks` dict key. We normalize to the string form
`"task/{stem}:{name}"` as the canonical node ID throughout.

### D-2: Edge detection via value location inspection

Cross-task wiring is expressed through the `location` field of a value struct.
When a value is declared with `location=":othertask.outputs"` (a string
conforming to the port-location grammar), it means the value's data comes from
the `outputs` port of the task named `othertask`.

After `resolve()`, `value.location` is a location struct (not the raw string).
The raw string is accessible as the `name` field on the location struct, or as
the `label` field on the value struct itself (set by `_value_impl` in
`values.mlody`).

**Edge detection algorithm:**

1. For each task T and each value V in `T.outputs`: a. Read `V.label` (the
   original string label, e.g., `":pretrain.checkpoint"`). b. If `V.label`
   starts with `":"` and contains `"."`, attempt `parse_port_location(V.label)`.
   c. If parsing succeeds and `ref.task` names a known task in the workspace,
   create an edge: source = T's node ID, destination = the named task's node ID,
   with `Edge(src_port=V.name, dst_path=ref.port)`.
2. For each task T and each value V in `T.inputs`: a. Same logic: check
   `V.label` for a port location reference. b. If it resolves to another task's
   output, create edge: source = that task, destination = T, with
   `Edge(src_port=ref.port, dst_path=V.name)`.

**Resolving OQ-003 (location string format):** This spec commits to the grammar
`":task_name.port_name"` for cross-task port references, consistent with the
entity-spec label convention already used throughout mlody. This grammar is
introduced by this change; any `.mlody` files that already use cross-task value
references with a different format will need to be migrated. The implementing
agent should grep for `location=":"` patterns across the repo before finalizing.

### D-3: `build_dag` accepts `Workspace`, not `Evaluator` directly (OQ-004)

`build_dag(workspace: Workspace)` is the public API. This provides a stable,
type-safe boundary that does not expose evaluator internals. For unit tests, the
test helper pattern from existing mlody tests (`InMemoryFS` + direct
`Evaluator`) is sufficient: tests construct a full `Workspace` over an in-memory
filesystem, which is already established practice in
`mlody/core/workspace_test.py`.

If a future caller genuinely needs evaluator-level access (e.g., the LSP
server), a private `_build_dag_from_evaluator(evaluator: Evaluator)` helper can
be factored out at that time. For now, `Workspace` is the only entry point.

### D-4: Type annotations use `object` for Struct fields

Because task and value structs are `Struct` instances (from starlarkish) and not
typed Python classes, field access like `task.inputs` returns `object` at the
type-checker level. The DAG builder uses `cast` and `getattr` with appropriate
type: ignore comments at the Struct boundary. All internal Python types
(`TaskNode`, `Edge`, `PortRef`, `PathError`) are fully typed.

### D-5: `validate_paths` is best-effort given current type system

Path validation (FR-009) traverses `value.type` to check that a `dst_path`
resolves. The type system in mlody uses `Struct` objects with `attributes` and
`_allowed_attrs` dicts. Traversal checks whether each path segment is a key in
`attributes` or a registered attr on the type. Because the type system is
dynamic, validation is best-effort: if a segment cannot be resolved (e.g., the
type has no declared fields), a `PathError` is emitted. This is preferable to
silently skipping validation.

---

## Module Structure: `mlody/core/dag.py`

The module is organized in four sections, separated by comment banners:

```
# ── Section 1: Types ──────────────────────────────────────────────────────
# Frozen dataclasses: TaskNode, Edge, PortRef, PathError
# Exception: PortLocationParseError

# ── Section 2: Port Location Parsing ─────────────────────────────────────
# parse_port_location(raw: str) -> PortRef

# ── Section 3: DAG Construction ──────────────────────────────────────────
# build_dag(workspace: Workspace) -> networkx.MultiDiGraph
# _iter_tasks(evaluator) -> iterator of (node_id, task_struct)
# _collect_edges(evaluator, tasks_index) -> list of (src, dst, Edge)

# ── Section 4: Query Interface ────────────────────────────────────────────
# tasks_producing(dag, value_name) -> set[str]
# tasks_consuming(dag, value_name) -> set[str]
# ancestors_subgraph(dag, target_output) -> networkx.MultiDiGraph
# validate_paths(dag) -> list[PathError]
```

This organization satisfies NFR-M-002 (types separated from graph-building
logic) without requiring a separate module file. A single file reduces import
overhead and makes the module easier to read end-to-end.

---

## Detailed Component Specifications

### 3.1 Types

#### `TaskNode`

```python
@dataclass(frozen=True)
class TaskNode:
    node_id: str            # canonical node ID: "task/{stem}:{name}"
    name: str               # bare task name from the .mlody file (display only)
    action: str             # action name (bare name field from action struct)
    input_ports: tuple[str, ...]    # value names declared as inputs
    output_ports: tuple[str, ...]   # value names declared as outputs
```

`node_id` must equal the NetworkX node key for this node. `name` is extracted
from `task_struct.name`. `action` is extracted from
`getattr(task_struct.action, "name", str(task_struct.action))` after resolution.
`input_ports` and `output_ports` are extracted from the `name` fields of the
resolved value structs in `task_struct.inputs` and `task_struct.outputs`
respectively.

#### `Edge`

```python
@dataclass(frozen=True)
class Edge:
    src_port: str    # output port name on the source task
    dst_path: str    # dotted destination path on the consuming task
```

Both fields must be non-empty strings. A single-segment `dst_path` addresses an
input port; a multi-segment `dst_path` addresses a config leaf. There is no
`kind` discriminator field.

#### `PortRef`

```python
@dataclass(frozen=True)
class PortRef:
    task: str    # bare task name referenced by the location string
    port: str    # port (value) name on that task
```

Internal use only during DAG construction. Exported from the public API surface
for callers who wish to use `parse_port_location` directly.

#### `PathError`

```python
@dataclass(frozen=True)
class PathError:
    task: str      # destination task node_id where the error was detected
    path: str      # the full dst_path that failed to resolve
    reason: str    # human-readable explanation naming the failing segment
```

#### `PortLocationParseError`

```python
class PortLocationParseError(ValueError):
    """Raised when parse_port_location cannot parse the input string."""
```

Carries the offending raw string in its message.

---

### 3.2 `parse_port_location`

```python
def parse_port_location(raw: str) -> PortRef:
    """Parse a port location string of the form ':task_name.port_name'.

    The leading colon is required. The string must contain exactly one dot
    after the colon. Only syntactic parsing is performed; no validation
    against registered tasks is done here.

    Args:
        raw: A string of the form ':task_name.port_name'.

    Returns:
        A PortRef with task and port fields populated.

    Raises:
        PortLocationParseError: If raw does not match the expected pattern.
    """
```

Implementation uses a single regex:
`^:([A-Za-z_][A-Za-z0-9_-]*)\.([A-Za-z_][A-Za-z0-9_.-]*)$`

- Group 1: task name (alphanumeric + underscore + hyphen, must start with letter
  or underscore).
- Group 2: port name (same character set, dots allowed for nested port paths).

Any string that does not match raises `PortLocationParseError` with the message
`"Invalid port location {raw!r}: expected ':task_name.port_name'"`.

---

### 3.3 `build_dag`

```python
def build_dag(workspace: Workspace) -> networkx.MultiDiGraph:
    """Build a directed acyclic graph of task data-flow dependencies.

    Iterates over all registered tasks in the evaluated workspace and constructs
    a MultiDiGraph where nodes are tasks (keyed by their all-dict node ID) and
    edges represent value flow between tasks.

    Must be called after Workspace.load() has completed without error.
    Calling build_dag twice on the same workspace produces equivalent graphs
    (pure function of evaluated state).

    Args:
        workspace: A fully-loaded Workspace instance.

    Returns:
        A networkx.MultiDiGraph. Each node key is a string of the form
        'task/{stem}:{name}'. Node data: dag.nodes[key]['task'] is a TaskNode.
        Edge data: dag.edges[src, dst, k]['edge'] is an Edge.
    """
```

**Algorithm:**

```
dag = networkx.MultiDiGraph()

# Step 1: collect all task nodes
tasks_index: dict[str, tuple[str, Struct]] = {}
  # maps bare task name -> (node_id, task_struct)
  # used in Step 2 to resolve PortRef.task to a node_id

for (tasks_key, task_struct) in evaluator.tasks.items():
    node_id = f"task/{tasks_key}"          # "task/{stem}:{name}"
    task_node = TaskNode(
        node_id=node_id,
        name=task_struct.name,
        action=getattr(task_struct.action, "name", str(task_struct.action)),
        input_ports=tuple(v.name for v in task_struct.inputs),
        output_ports=tuple(v.name for v in task_struct.outputs),
    )
    dag.add_node(node_id, task=task_node)
    tasks_index[task_struct.name] = (node_id, task_struct)

# Step 2: collect edges from value labels
for (tasks_key, task_struct) in evaluator.tasks.items():
    src_node_id = f"task/{tasks_key}"

    # Check each output value: does it source from another task's port?
    for out_val in task_struct.outputs:
        label = getattr(out_val, "label", None)
        if not isinstance(label, str) or not label.startswith(":"):
            continue
        try:
            ref = parse_port_location(label)
        except PortLocationParseError:
            continue
        if ref.task not in tasks_index:
            continue
        # This task's output comes FROM another task's port
        # Edge: ref.task -> src_node_id (the value flows into this task's output)
        # Wait -- this is an OUTPUT declared by src_node_id, sourced from ref.task
        # Edge direction: ref.task (producer) -> src_node_id (consumer)
        # But src_node_id is the task declaring this value as an output...
        # Re-reading: a value with location=":a.outputs" means the data at
        # this value's location IS the output of task a.  The value is shared.
        # Edge: a -> current_task, meaning current_task CONSUMES a's output.
        producer_node_id, _ = tasks_index[ref.task]
        edge = Edge(src_port=ref.port, dst_path=out_val.name)
        dag.add_edge(producer_node_id, src_node_id, edge=edge)

    # Check each input value: does it source from another task's output?
    for in_val in task_struct.inputs:
        label = getattr(in_val, "label", None)
        if not isinstance(label, str) or not label.startswith(":"):
            continue
        try:
            ref = parse_port_location(label)
        except PortLocationParseError:
            continue
        if ref.task not in tasks_index:
            continue
        producer_node_id, _ = tasks_index[ref.task]
        edge = Edge(src_port=ref.port, dst_path=in_val.name)
        dag.add_edge(producer_node_id, src_node_id, edge=edge)

return dag
```

**Notes on edge direction semantics:**

A `location=":pretrain.checkpoint"` on a value `V` means: V's data is sourced
from the `checkpoint` port of the task named `pretrain`. If V is an input of
task `finetune`, the edge is `pretrain -> finetune` with
`Edge(src_port="checkpoint", dst_path="checkpoint")` (or whatever the input port
name is on `finetune`). If V is an output of task `finetune` but its storage
location references `pretrain`'s output, the same edge direction holds:
`pretrain -> finetune`.

**Important:** Tasks with no port-location references in their values appear as
isolated nodes with no edges (FR-002 satisfied).

---

### 3.4 Query Functions

#### `tasks_producing`

```python
def tasks_producing(dag: networkx.MultiDiGraph, value_name: str) -> set[str]:
    """Return the set of node IDs whose output_ports include value_name."""
```

Iterates `dag.nodes(data=True)`, checks `task_node.output_ports` for
`value_name`. O(N) in number of nodes.

#### `tasks_consuming`

```python
def tasks_consuming(dag: networkx.MultiDiGraph, value_name: str) -> set[str]:
    """Return the set of node IDs that have an incoming edge with src_port==value_name."""
```

Iterates all edges via `dag.edges(data=True, keys=True)`, checks
`edge_data["edge"].src_port`. Returns the destination node IDs. O(E) in number
of edges.

#### `ancestors_subgraph`

```python
def ancestors_subgraph(
    dag: networkx.MultiDiGraph, target_output: str
) -> networkx.MultiDiGraph:
    """Return the minimal subgraph of tasks that contribute to target_output.

    Uses networkx.ancestors() to find all transitive predecessors of each task
    that produces target_output, then returns the induced subgraph.

    If no task produces target_output, returns an empty MultiDiGraph.
    """
```

Algorithm (from Appendix D of REQUIREMENTS):

```python
producers = tasks_producing(dag, target_output)
if not producers:
    return networkx.MultiDiGraph()
all_relevant: set[str] = set(producers)
for task_id in producers:
    all_relevant |= networkx.ancestors(dag, task_id)
return dag.subgraph(all_relevant).copy()
```

Returns a copy (not a view) so the caller can modify it independently.

#### `validate_paths`

```python
def validate_paths(dag: networkx.MultiDiGraph) -> list[PathError]:
    """Validate all Edge.dst_path values against the destination task's type schema.

    Traverses each edge in the DAG and checks that the dst_path resolves against
    the task struct's declared fields. Does not raise; returns all errors.

    Returns:
        A list of PathError instances. An empty list means no errors found.
    """
```

**Traversal logic:**

For each edge `(src, dst, key, data)` in `dag.edges(keys=True, data=True)`:

1. Extract `edge: Edge = data["edge"]`.
2. Retrieve `task_node: TaskNode = dag.nodes[dst]["task"]`.
3. Walk `dst_path.split(".")` against the destination task struct (retrieved
   from `dag.nodes[dst]["task_struct"]` — see note below).
4. If any segment fails to resolve, append a `PathError`.

**Note on task_struct access:** `build_dag` must store the raw task struct on
each node in addition to the `TaskNode` wrapper, under the key `"task_struct"`.
This is needed by `validate_paths` to traverse the actual type schema. The
`TaskNode` wrapper holds only port name strings (sufficient for queries and
display), not the full struct.

Node data will contain two keys:

- `"task"`: a `TaskNode` instance
- `"task_struct"`: the raw Struct from the evaluator (for path validation)

**dst_path traversal algorithm:**

```python
segments = dst_path.split(".")
obj: object = task_struct   # start from the task struct itself
for i, segment in enumerate(segments):
    if not hasattr(obj, segment):
        errors.append(PathError(
            task=dst_node_id,
            path=dst_path,
            reason=f"segment {segment!r} not found on {type(obj).__name__} "
                   f"at position {i} in path {dst_path!r}",
        ))
        break
    obj = getattr(obj, segment)
```

For `dst_path="model_weight"` (single segment), this checks `task_struct` for a
field named `model_weight`. For `dst_path="action.config.learning_rate"`, it
traverses `task_struct.action.config.learning_rate`.

---

## Data Architecture

All data is ephemeral in-memory. No persistence layer.

### Node data schema

```
dag.nodes[node_id] = {
    "task":        TaskNode(node_id, name, action, input_ports, output_ports),
    "task_struct": <raw Struct from evaluator>   # for validate_paths only
}
```

### Edge data schema

```
dag.edges[src, dst, key] = {
    "edge": Edge(src_port, dst_path)
}
```

---

## Bazel BUILD Changes

`mlody/core/BUILD.bazel` gains two new targets. They must be added by running
`bazel run :gazelle` after `dag.py` and `dag_test.py` are created — BUILD files
must not be edited manually per CLAUDE.md.

However, Gazelle cannot infer `@pip//networkx` from imports alone unless the
import is present. After Gazelle runs, the networkx dep line must be protected
with `# keep` if Gazelle does not pick it up automatically.

Expected targets (for reference — actual BUILD entries are Gazelle-managed):

```python
o_py_library(
    name = "dag_lib",
    srcs = ["dag.py"],
    visibility = ["//:__subpackages__"],
    deps = [
        ":workspace_lib",   # keep — Workspace type
        "//common/python/starlarkish/evaluator:evaluator_lib",  # keep
        "@pip//networkx",   # keep
    ],
)

o_py_test(
    name = "dag_test",
    srcs = ["dag_test.py"],
    deps = [
        ":dag_lib",
        ":core_lib",
        "//common/python/starlarkish/evaluator:evaluator_lib",
        "//common/python/starlarkish/evaluator:testing_lib",  # InMemoryFS
        "@pip//pyfakefs",   # keep
        "@pip//networkx",   # keep
        "@pip//pytest",
    ],
)
```

`networkx` must also be added to `mlody/pyproject.toml` (or the root
`pyproject.toml` used for mlody deps), then `o-repin` run to regenerate the
lockfile.

---

## Testing Strategy

All tests live in `mlody/core/dag_test.py`. Tests use `InMemoryFS` or `pyfakefs`
with direct `Evaluator` calls (matching the pattern in
`mlody/common/task_test.py`). No real filesystem access. No mocking of
starlarkish internals.

A shared test helper constructs a minimal workspace from inline `.mlody` content
strings:

```python
def _build_dag_from_mlody(files: dict[str, str]) -> networkx.MultiDiGraph:
    with InMemoryFS(BASE_FILES | files, root="/project") as root:
        ev = Evaluator(root)
        ev.eval_file(root / "test.mlody")
        ev.resolve()
    ws = _FakeWorkspace(ev)   # thin wrapper exposing evaluator
    return build_dag(ws)
```

`_FakeWorkspace` is a local test-only class that exposes the `.evaluator`
property backed by the `Evaluator` instance.

### Required test cases

| Test class / method                      | Covers                                                     |
| ---------------------------------------- | ---------------------------------------------------------- |
| `TestBuildDagLinearChain`                | FR-001, FR-002, US-001/002 — A->B->C, 3 nodes 2 edges      |
| `TestBuildDagFork`                       | FR-003 — A->B and A->C, 3 nodes 2 edges                    |
| `TestBuildDagJoin`                       | FR-001 — A->C and B->C, 3 nodes 2 edges                    |
| `TestBuildDagDiamond`                    | FR-001 — A->B->D and A->C->D, 4 nodes 4 edges              |
| `TestBuildDagSingleSegmentDstPath`       | FR-005, FR-008 — `dst_path="model_weight"`                 |
| `TestBuildDagMultiSegmentDstPath`        | FR-005, FR-008 — `dst_path="action.config.lr"`             |
| `TestBuildDagIsolatedTask`               | FR-002 — node present, no edges                            |
| `TestBuildDagMultiEdgeSamePair`          | FR-003 — two edges A->B preserved                          |
| `TestBuildDagNodeMetadata`               | US-002 — `dag.nodes[n]["task"]` is TaskNode                |
| `TestBuildDagEdgeAnnotations`            | US-003 — `dag.edges[a,b,k]["edge"]` is Edge                |
| `TestParsePortLocationValid`             | FR-007 — `:a.outputs` -> PortRef(task="a", port="outputs") |
| `TestParsePortLocationMissingColon`      | FR-007 — raises PortLocationParseError                     |
| `TestParsePortLocationMissingDot`        | FR-007 — raises PortLocationParseError                     |
| `TestTasksProducingKnown`                | FR-011, US-005 — returns correct set                       |
| `TestTasksProducingUnknown`              | FR-011 — returns empty set                                 |
| `TestTasksConsumingBothPathForms`        | FR-012, US-006 — single and multi segment                  |
| `TestAncestorsSubgraphChain`             | FR-013, US-007 — correct minimal subgraph                  |
| `TestAncestorsSubgraphExcludesUnrelated` | FR-013 — no extraneous tasks                               |
| `TestAncestorsSubgraphSingleTask`        | FR-013 — single-task workspace                             |
| `TestAncestorsSubgraphNoProducer`        | FR-013 — empty graph returned                              |
| `TestValidatePathsValid`                 | FR-009 — empty error list                                  |
| `TestValidatePathsInvalid`               | FR-009/010 — PathError names task and path                 |
| `TestTopologicalSortCompatible`          | FR-014 — `nx.topological_sort` succeeds                    |

Run with: `bazel test //mlody/core:dag_test --test_output=errors`

---

## Implementation Plan

### Phase 1 — Dependency setup (prerequisite)

1. Add `networkx` to `mlody/pyproject.toml` (no version pin).
2. Run `o-repin` to regenerate the lockfile.
3. Confirm `bazel build //mlody/...` succeeds with the new dep.

### Phase 2 — Core types and parser

4. Create `mlody/core/dag.py` with Sections 1 and 2:
   - `TaskNode`, `Edge`, `PortRef`, `PathError` dataclasses.
   - `PortLocationParseError` exception.
   - `parse_port_location` function.
5. Run `bazel run :gazelle` in `mlody/core/` to generate the initial BUILD
   entry.
6. Add `# keep` to any deps Gazelle cannot infer.
7. Write `TestParsePortLocation*` tests.
8. Run `bazel test //mlody/core:dag_test --test_output=errors`.

### Phase 3 — DAG construction

9. Implement `_iter_tasks`, `_collect_edges`, and `build_dag` (Section 3).
10. Write all `TestBuildDag*` tests.
11. Run `bazel test //mlody/core:dag_test --test_output=errors`.

### Phase 4 — Query interface and path validation

12. Implement `tasks_producing`, `tasks_consuming`, `ancestors_subgraph`
    (Section 4).
13. Implement `validate_paths` (Section 4).
14. Write all remaining test cases.
15. Run `bazel test //mlody/core:dag_test --test_output=errors`.

### Phase 5 — Lint and type check

16. Run `bazel build --config=lint //mlody/core/...`.
17. Verify basedpyright strict reports zero errors on `dag.py`.
18. Run `bazel test //mlody/...` to confirm no regressions.

### Critical path

Steps 1-3 (dep setup) must precede everything else. Steps 4-8 and 9-11 are
strictly sequential. Steps 12-15 can begin as soon as `build_dag` passes its
tests.

---

## Non-Functional Requirements

### Performance

- `build_dag` iterates tasks once for nodes (O(T)) and once per task for edges
  (O(T \* max_outputs)). For 500 tasks with 4 outputs each, this is ~2000
  iterations — well within the 500 ms budget (NFR-P-001).
- `ancestors_subgraph` delegates to `networkx.ancestors`, which is O(V + E) in
  the subgraph. For 500 nodes, this is well within 50 ms (NFR-P-002).
- Node data holds `TaskNode` wrappers (string fields only) and a reference to
  the original struct. No deep copies (NFR-SC-001).

### Immutability

`build_dag` never modifies `workspace`, `evaluator`, or any registered struct.
It only reads. Structs stored under `"task_struct"` in node data are references,
not copies (NFR-SC-001, NFR-S-001).

### Compatibility

`dag.py` imports only:

- `mlody.core.workspace.Workspace` (for type annotation)
- `common.python.starlarkish.evaluator.evaluator.Evaluator` (for internal
  helper)
- `networkx`
- Python stdlib (`dataclasses`, `re`)

No other mlody subsystem is imported (NFR-C-001/002).

---

## Risks and Mitigations

| Risk                                                             | Mitigation                                                                                                                                                                                                             |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R-001: `value.label` not always set on resolved value structs    | Inspect `values.mlody` — `label` is an optional attr (`mandatory=False`). `getattr(val, "label", None)` with a None check is safe. Cross-task location strings are the case where `label` is set.                      |
| R-003: Location strings use a different format in existing files | Grep `location=":"` across repo before implementing edge detection. If no files use this format yet, edge detection is effectively a no-op (all tasks become isolated nodes).                                          |
| R-002: NetworkX `ancestors()` traversal                          | Covered by `TestAncestorsSubgraphChain` and `TestAncestorsSubgraphExcludesUnrelated` which exercise multi-edge and mixed `dst_path` topologies.                                                                        |
| `all` dict key format changes                                    | The `evaluator.tasks` dict key format `"{stem}:{name}"` is used directly (prepend `"task/"`). If `_register` changes its key format, `dag.py` will need updating. The test `TestBuildDagNodeMetadata` will catch this. |
| basedpyright strict on Struct field access                       | Use `cast`, `getattr`, and per-line `# type: ignore[attr-defined]` at Struct boundaries. All module-level functions must have full type hints.                                                                         |

---

## Open Questions Resolved by This Spec

| ID     | Resolution                                                                                                                                               |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OQ-003 | Grammar `:task_name.port_name` is the canonical cross-task location format. Implementing agent must grep for existing uses before coding edge detection. |
| OQ-004 | `build_dag(workspace: Workspace)` is the only public entry point. Evaluator-level access is not exposed.                                                 |

---

## Future Considerations

- **Plan generation:** The plan subsystem (`mlody/core/plan.py`) will consume
  this DAG. `networkx.topological_sort(build_dag(ws))` provides a valid
  execution order.
- **LSP integration:** The query functions (`tasks_producing`,
  `ancestors_subgraph`) can power "go to definition" and "find references" on
  value names in `.mlody` files.
- **Cycle detection:** `networkx.is_directed_acyclic_graph(dag)` can be called
  by any consumer to detect cycles. A future validation pass could surface this
  as a workspace load error.
- **Serialization:** If DAG snapshots are needed for caching or debugging,
  `networkx.node_link_data` provides JSON-serializable output.
