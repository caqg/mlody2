# Requirements Document: Workspace DAG

**Version:** 1.1 **Date:** 2026-03-28 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

Today the mlody pipeline framework evaluates `.mlody` files and registers tasks,
actions, and values as flat, independent objects. Connections between tasks are
_implicit_: a task that lists `value("model")` as an output and another task
that lists `value("model")` as an input are logically related, but no data
structure captures that relationship. As a result, no subsystem can answer
questions like "what must run before task X" or "which tasks contribute to
output Y" without re-implementing ad-hoc traversal logic from scratch.

This change introduces an explicit, in-memory Directed Acyclic Graph (DAG) of
the workspace, built after evaluation completes. Nodes represent tasks. Edges
represent data-flow dependencies — a value flowing from one task's output port
into another task's destination, expressed as a single unified `Edge` type. The
destination is a dotted path (`dst_path`) that uniformly addresses both input
ports (e.g., `"model_weight"`) and config leaves (e.g.,
`"action.training.epoch"`). The distinction between "input wire" and "config
override" is conventional — both mechanisms use the same edge type — making the
graph model simpler and more composable.

The expected business value is a reusable graph layer that enables subgraph
pruning ("what is the minimal set of tasks needed to produce output Z"), full
lineage tracing, and future static validation — all without requiring execution
or plan generation.

---

## 2. Project Scope

### 2.1 In Scope

- A Python module `mlody/core/dag.py` (name subject to architecture) that builds
  a `networkx.MultiDiGraph` from a fully-evaluated `Workspace` (i.e., after
  `Workspace.load()` has returned without error).
- Node type: `TaskNode`, representing a single registered task.
- A single unified edge type `Edge` with typed annotations:
  - `src_port: str` — the output port name on the source task.
  - `dst_path: str` — a dotted path identifying the destination on the consuming
    task. A single-segment path (e.g., `"model_weight"`) addresses an input
    port; a multi-segment path (e.g., `"action.config.learning_rate"`) addresses
    a config leaf. Both are valid.
- Parsing of `location=":a.outputs"` style strings into (task name, port name)
  tuples.
- Validation of `dst_path` values against the task's type schema, accessible via
  the `value.type` field on declared `value()` objects. Path validation
  traverses the type structure starting from the declared type.
- A query interface: "which tasks produce value X", "which tasks consume value
  Y", "what is the minimal subgraph contributing to output Z" (ancestors
  pruning).
- Unit tests in `mlody/core/dag_test.py` (or equivalent).
- Bazel BUILD entry for the new target.

### 2.2 Out of Scope

- Plan generation (ordering tasks into an executable sequence) — this is a
  distinct subsystem that will consume the DAG.
- Execution engine integration (Kubernetes runner, local runner, Docker Compose
  formats).
- Cycle detection as a hard error — NetworkX's `is_directed_acyclic_graph()` may
  be called by callers, but the DAG module itself is not required to enforce
  acyclicity.
- Graph serialization to JSON or any wire format.
- Cross-workspace edges (edges that span multiple workspace snapshots).
- LSP integration updates — the DAG is a backend data structure; LSP changes are
  a separate work item.

### 2.3 Assumptions

- `Workspace.load()` has been called successfully and all tasks, values, and
  actions have been registered before `build_dag()` is invoked.
- A `location` string of the form `":a.outputs"` means: the value is the
  `outputs` port of task named `a` in the current file's scope. The colon prefix
  follows the existing entity-spec label convention.
- `dst_path` is always interpreted relative to the consuming task. A
  single-segment path (e.g., `"model_weight"`) refers to an input port declared
  in the task's `inputs` list. A multi-segment path (e.g.,
  `"action.config.learning_rate"`) traverses nested struct fields starting from
  the named top-level field.
- A task's type schema is accessible via the `value.type` field on the declared
  `value()` object. `config`, `inputs`, and `outputs` are all declared as
  `value()` objects, and `value` exposes a `type` field. `dst_path` validation
  traverses the type structure starting from the declared type.
- `networkx.MultiDiGraph` is used because multiple edges between the same task
  pair are possible (task A may produce two values both consumed by task B); a
  plain `DiGraph` would silently drop duplicate edges.
- NetworkX is available as a third-party dependency and will be added to the
  Python lockfile.
- Port names on value structs are the registered value names as they appear in
  the task's `inputs` / `outputs` lists.

### 2.4 Constraints

- Python 3.13, strict basedpyright type checking, ruff formatting.
- Bazel rules must use `o_py_library` / `o_py_test` from
  `//build/bzl:python.bzl`.
- NetworkX is the only new third-party dependency permitted for this change.
- The module must not mutate the `Workspace` object or any registered struct.
- `build_dag()` must be a pure function of the evaluated workspace state:
  calling it twice on the same workspace must produce equivalent graphs.

---

## 3. Stakeholders

| Role                 | Name/Group     | Responsibilities                            |
| -------------------- | -------------- | ------------------------------------------- |
| mlody framework lead | mav            | Final acceptance, graph semantics authority |
| Requirements Analyst | @socrates      | Requirements elicitation and documentation  |
| Solution Architect   | @vitruvious    | System design and SPEC.md                   |
| Implementation       | @vulcan-python | Python coding                               |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Replace implicit, name-based task coupling with an explicit,
  traversable data structure so that any subsystem can reason about data flow
  without re-parsing `.mlody` files.
- **BR-002:** Enable subgraph pruning so that only the tasks contributing to a
  given target output need to be materialized or executed, reducing wasted work.
- **BR-003:** Provide a foundation for future static validation (missing
  connections, type mismatches between ports) without coupling validation to
  execution.

### 4.2 Success Metrics

- **KPI-001:** `build_dag()` correctly represents all task-to-task dependencies
  detectable from the evaluated workspace — verified by unit tests covering at
  least chain, fork, join, and diamond topologies.
- **KPI-002:** `ancestors_subgraph(target_output)` returns a subgraph containing
  exactly the set of tasks that transitively produce the named output, with no
  extra tasks included — verified by unit tests.
- **KPI-003:** basedpyright strict reports zero errors on `mlody/core/dag.py`.
- **KPI-004:** Path validation errors name the offending `dst_path` and the task
  where the error was detected.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: mlody framework developer**

- Needs to answer "what tasks contribute to output Z" to implement selective
  execution, display lineage in the CLI, or power LSP "go to definition" on
  values.
- Pain point: today this requires custom traversal of flat evaluator state with
  no shared helper.
- Needs a single, typed API that returns a NetworkX subgraph.

**Persona 2: future plan-generation subsystem**

- Needs a topologically ordered list of tasks to execute, respecting data-flow
  order.
- Pain point: without an explicit DAG, ordering requires re-deriving edges from
  scratch.
- Needs `networkx.topological_sort` to be applicable to the returned graph.

### 5.2 User Stories

**Epic 1: Graph construction**

- **US-001:** As a framework developer, I want to call `build_dag(workspace)`
  and receive a `networkx.MultiDiGraph` so that I can traverse task dependencies
  without writing traversal code myself.
  - Acceptance Criteria: Given a workspace with N tasks connected by edges, when
    `build_dag` is called, then the returned graph has exactly N nodes and the
    correct edges with annotations.
  - Priority: Must Have

- **US-002:** As a framework developer, I want each node in the DAG to carry the
  full `TaskNode` metadata (task name, action name, input/output port names) so
  that I can render labels and drive execution without additional lookups.
  - Acceptance Criteria: For each node `n`, `dag.nodes[n]["task"]` returns a
    `TaskNode` instance with `name`, `action`, `input_ports`, `output_ports`.
  - Priority: Must Have

**Epic 2: Edge annotations**

- **US-003:** As a framework developer, I want every edge to carry a typed
  `Edge` annotation struct so that I can inspect the source port and destination
  path without pattern-matching on raw edge data.
  - Acceptance Criteria: Given an edge from task A to task B,
    `dag.edges[A, B, key]["edge"]` is an `Edge` instance with non-empty
    `src_port` and `dst_path`.
  - Priority: Must Have

- **US-004:** As a framework developer, I want the edge's `dst_path` to
  uniformly address both input ports (single-segment, e.g., `"model_weight"`)
  and config leaves (multi-segment, e.g., `"action.training.epoch"`) so that I
  do not need separate code paths for "value wiring" vs. "config injection".
  - Acceptance Criteria: An `Edge` with `dst_path="model_weight"` and an `Edge`
    with `dst_path="action.config.learning_rate"` are both valid, both stored
    under the same `Edge` type, and both traversed by query functions.
  - Priority: Must Have

**Epic 3: Query interface**

- **US-005:** As a framework developer, I want a function
  `tasks_producing(dag, value_name)` that returns the set of task names whose
  output ports include the named value.
  - Acceptance Criteria: Given a DAG where task T declares value V as an output,
    `tasks_producing(dag, "V")` returns `{"T"}`.
  - Priority: Must Have

- **US-006:** As a framework developer, I want a function
  `tasks_consuming(dag, value_name)` that returns the set of task names whose
  edges reference the named value as `src_port`.
  - Acceptance Criteria: Given a DAG where task T has an incoming edge with
    `src_port="V"`, `tasks_consuming(dag, "V")` returns `{"T"}`.
  - Priority: Must Have

- **US-007:** As a framework developer, I want a function
  `ancestors_subgraph(dag, target_output)` that returns the minimal subgraph of
  tasks that transitively contribute to a named output value.
  - Acceptance Criteria: Given a DAG with tasks A -> B -> C where C produces the
    target output, `ancestors_subgraph` returns a subgraph containing A, B, and
    C but not any other tasks. All edges are followed recursively.
  - Priority: Must Have

**Epic 4: Port/location semantics**

- **US-008:** As a framework developer, I want `location=":a.outputs"` strings
  to be parsed into structured (task_name, port_name) references so that the DAG
  builder can wire the corresponding edge without string matching at call time.
  - Acceptance Criteria: `parse_port_location(":a.outputs")` returns
    `PortRef(task="a", port="outputs")`.
  - Priority: Must Have

---

## 6. Functional Requirements

### 6.1 Graph Construction

**FR-001: `build_dag` entry point**

- Description: A public function
  `build_dag(workspace: Workspace) -> networkx.MultiDiGraph` is defined in
  `mlody/core/dag.py`. It iterates over all registered tasks in the evaluated
  workspace, creates one node per task, then inspects each task's `inputs` list
  and config struct for references that resolve to other tasks' outputs,
  creating `Edge` instances accordingly.
- Inputs: A fully-loaded `Workspace` instance.
- Outputs: A `networkx.MultiDiGraph` where nodes are task names (strings) and
  node data carries a `TaskNode` under the key `"task"`. Each edge carries an
  `Edge` annotation under the key `"edge"`.
- Business Rules: A task that references a value produced by no other task
  (i.e., an external/leaf input) does not create an edge; the task node still
  appears in the graph.
- Priority: Must Have
- Dependencies: Workspace.load() must have completed; FR-004 (node type), FR-005
  (edge type).

**FR-002: Isolated tasks**

- Description: Tasks that have no connections to other tasks (no shared values,
  no destination path sourced from another task) are represented as isolated
  nodes in the DAG with no edges.
- Priority: Must Have

**FR-003: Multi-edge handling**

- Description: If two tasks are connected by more than one value (e.g., task A
  produces both `model` and `tokenizer`, both consumed by task B), the DAG must
  store multiple edges between A and B. Because port names are carried only in
  edge annotations (not encoded as separate port nodes), multiple parallel edges
  between the same task pair are possible and must be preserved.
  `networkx.MultiDiGraph` is the required implementation.
- Priority: Must Have

### 6.2 Node Type

**FR-004: `TaskNode` dataclass**

- Description: A frozen dataclass representing a task node:

  ```python
  @dataclass(frozen=True)
  class TaskNode:
      node_id: str                       # fully-qualified all-dict key (canonical node ID)
      name: str                          # bare task name field from the .mlody file
      action: str                        # action name
      input_ports: tuple[str, ...]       # value names declared as inputs
      output_ports: tuple[str, ...]      # value names declared as outputs
  ```

  `node_id` is the key under which this task is stored in the workspace's `all`
  dictionary. It is the canonical, unique identifier for the task across the
  entire workspace and is used as the NetworkX node ID (see FR-004b). `name` is
  the bare `name` field declared in the `.mlody` file and is retained for
  display purposes only.

- All fields required at construction time.
- Priority: Must Have

**FR-004b: Node identity uses the `all`-dict key**

- Description: The string used as the NetworkX node ID for each task must be the
  fully-qualified key under which that task is stored in the workspace's `all`
  dictionary — not the bare `name` field from the `.mlody` file. The `all`-dict
  key is the only workspace-scoped unique identifier for a task; bare `name`
  values are file-local and are not guaranteed to be unique across files or
  namespaces.
- Inputs: The workspace's `all` dictionary, which maps fully-qualified keys to
  registered task structs.
- Processing: For each entry `(key, task_struct)` in `workspace.all`, the node
  is added to the graph with `key` as its ID and a `TaskNode` whose `node_id` is
  set to `key`.
- Business Rules:
  - Two tasks in different `.mlody` files may share the same bare `name` field.
    Using the `all`-dict key prevents silent node collisions in the graph.
  - `TaskNode.node_id` must equal the NetworkX node ID for that node (i.e.,
    `dag.nodes[key]["task"].node_id == key` for all `key` in `dag.nodes`).
  - Query functions (`tasks_producing`, `tasks_consuming`, `ancestors_subgraph`)
    return sets of `all`-dict keys, not bare names.
- Priority: Must Have
- Dependencies: FR-004 (`TaskNode`), FR-001 (`build_dag`).

### 6.3 Edge Type and Annotations

**FR-005: `Edge` dataclass**

- Description: A single frozen dataclass representing any data-flow edge between
  two tasks:

  ```python
  @dataclass(frozen=True)
  class Edge:
      src_port: str   # output port name on the source task
      dst_path: str   # dotted destination path on the consuming task
  ```

  `dst_path` is a dot-separated string. A single-segment path (e.g.,
  `"model_weight"`) addresses an input port on the consuming task. A
  multi-segment path (e.g., `"action.config.learning_rate"`) addresses a config
  leaf by traversing nested struct fields. There is no separate `kind` field —
  the conventional distinction between "input wire" and "config override" is
  determined solely by the structure of `dst_path`, not by the edge type itself.

- `src_port` must be a non-empty string. `dst_path` must be a non-empty string
  containing at least one path segment (single-segment paths are valid).
- Priority: Must Have

### 6.4 Port and Location Semantics

**FR-006: `PortRef` dataclass**

- Description: A frozen dataclass used internally during DAG construction to
  represent a parsed port location reference:

  ```python
  @dataclass(frozen=True)
  class PortRef:
      task: str    # task name
      port: str    # port (value) name within that task
  ```

- Priority: Must Have

**FR-007: `parse_port_location` function**

- Description: A function `parse_port_location(raw: str) -> PortRef` that parses
  strings of the form `":task_name.port_name"` into `PortRef` instances. The
  leading colon is required and follows the existing entity-spec label
  convention. Parsing is purely syntactic — no validation against registered
  tasks is performed inside this function.
- Inputs: A string of the form `":task_name.port_name"`.
- Outputs: A `PortRef` instance.
- Error: Raises `PortLocationParseError` (a `ValueError` subclass) if the string
  does not match the expected pattern.
- Priority: Must Have

**FR-008: `dst_path` syntax**

- Description: `Edge.dst_path` values are dot-separated key sequences. A
  single-segment path (e.g., `"model_weight"`) refers to an input port declared
  in the consuming task's `inputs` list. A multi-segment path (e.g.,
  `"action.config.learning_rate"`) means the first segment is a top-level field
  on the task struct (e.g., `action`, `config`, `inputs`) and subsequent
  segments traverse nested struct fields. Example:
  `"action.config.learning_rate"` means `task.action.config.learning_rate`.
- Priority: Must Have

### 6.5 Path Validation

**FR-009: Deferred path validation**

- Description: After the DAG is constructed, a separate function
  `validate_paths(dag: networkx.MultiDiGraph) -> list[PathError]` checks that
  every `Edge.dst_path` resolves against the destination task's type schema. The
  type schema is accessible via the `value.type` field on the declared `value()`
  object; validation traverses the type structure starting from the declared
  type. Validation does not raise immediately; it collects all errors and
  returns them so callers can surface all problems at once.
- Inputs: A fully-constructed DAG.
- Outputs: A list of `PathError` instances (empty list = no errors).
- Each `PathError` must include: the destination task name, the full dotted
  path, and a human-readable message explaining which segment failed to resolve.
- Priority: Must Have

**FR-010: `PathError` dataclass**

- Description:

  ```python
  @dataclass(frozen=True)
  class PathError:
      task: str          # destination task name
      path: str          # the full dst_path that failed
      reason: str        # human-readable explanation
  ```

- Priority: Must Have

### 6.6 Query Interface

**FR-011: `tasks_producing(dag, value_name)`**

- Description: Returns the set of task names (`set[str]`) whose `output_ports`
  include `value_name`.
- Priority: Must Have

**FR-012: `tasks_consuming(dag, value_name)`**

- Description: Returns the set of task names (`set[str]`) that have an incoming
  edge whose `src_port` equals `value_name`. This covers both single-segment
  `dst_path` (input wiring) and multi-segment `dst_path` (config injection)
  uniformly.
- Priority: Must Have

**FR-013: `ancestors_subgraph(dag, target_output)`**

- Description: Returns a `networkx.MultiDiGraph` that is the minimal induced
  subgraph containing every task that transitively contributes to the task(s)
  producing `target_output`. Uses NetworkX's `ancestors()` function internally.
  All edges are traversed regardless of `dst_path` structure.
- Algorithm:
  1. Find all tasks that produce `target_output` (via `tasks_producing`).
  2. For each such task T, compute `networkx.ancestors(dag, T)`.
  3. Take the union of all ancestor sets plus the producing tasks themselves.
  4. Return the subgraph induced by that union.
- Priority: Must Have

### 6.7 Topological Ordering

**FR-014: Compatibility with `networkx.topological_sort`**

- Description: The DAG returned by `build_dag` must be a valid input to
  `networkx.topological_sort`. If the workspace contains a cycle (which is
  invalid but not yet enforced), `topological_sort` will raise
  `networkx.NetworkXUnfeasible`; this exception is not caught by the DAG module.
- Priority: Must Have

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-P-001:** `build_dag()` must complete in under 500 ms for a workspace
  with up to 500 tasks and 2,000 values on a modern development machine.
- **NFR-P-002:** `ancestors_subgraph()` must complete in under 50 ms for a graph
  with up to 500 nodes.

### 7.2 Scalability Requirements

- **NFR-SC-001:** The in-memory graph must not copy task struct data; node data
  must hold references (or lightweight `TaskNode` wrappers) rather than deep
  copies of evaluated structs.

### 7.3 Availability & Reliability

- Not applicable — the DAG is a pure in-memory library module with no networked
  dependencies.

### 7.4 Security Requirements

- **NFR-S-001:** The DAG module must not execute or evaluate any content sourced
  from `.mlody` files. It reads already-evaluated struct fields only.

### 7.5 Usability Requirements

- **NFR-U-001:** All public functions must have docstrings explaining their
  contract, expected inputs, outputs, and raised exceptions.
- **NFR-U-002:** Error messages for `PortLocationParseError` and `PathError`
  must name the offending value or path so a developer can locate the problem in
  the source `.mlody` file without additional tooling.

### 7.6 Maintainability Requirements

- **NFR-M-001:** All public functions, dataclasses, and their fields must have
  type hints satisfying basedpyright strict mode.
- **NFR-M-002:** `TaskNode`, `Edge`, `PortRef`, and error types must be defined
  in a separate `types` module (or clearly separated section) from the
  graph-building logic to allow annotation-only imports.

### 7.7 Compatibility Requirements

- **NFR-C-001:** The module must not modify or wrap any of the existing
  `Workspace`, `Evaluator`, or registered struct types.
- **NFR-C-002:** The module must not depend on any mlody subsystem outside of
  `mlody/core/workspace.py` and the registered evaluator state.

---

## 8. Data Requirements

### 8.1 Data Entities

| Entity                  | Location               | Description                                           |
| ----------------------- | ---------------------- | ----------------------------------------------------- |
| `TaskNode`              | `mlody/core/dag.py`    | Frozen dataclass representing a DAG node              |
| `Edge`                  | `mlody/core/dag.py`    | Frozen dataclass annotating a data-flow edge          |
| `PortRef`               | `mlody/core/dag.py`    | Frozen dataclass for a parsed port location reference |
| `PathError`             | `mlody/core/dag.py`    | Frozen dataclass describing a path validation error   |
| `networkx.MultiDiGraph` | third-party (networkx) | Graph container; nodes are task name strings          |

### 8.2 Data Quality Requirements

- `Edge.src_port` must never be an empty string.
- `Edge.dst_path` must never be an empty string; single-segment paths are valid.
- `TaskNode.node_id` must equal the NetworkX node ID for that node and must
  match the key in the workspace's `all` dictionary exactly.
- `TaskNode.name` is the bare name field from the `.mlody` file and is retained
  for display; it is not guaranteed to be unique across the workspace.

### 8.3 Data Retention & Archival

Not applicable — the DAG is an ephemeral in-memory structure.

### 8.4 Data Privacy & Compliance

Not applicable.

---

## 9. Integration Requirements

### 9.1 External Systems

| System   | Purpose              | Type           | Direction | Notes                         |
| -------- | -------------------- | -------------- | --------- | ----------------------------- |
| NetworkX | Graph data structure | Python library | Consumed  | Must be added to pip lockfile |

### 9.2 API Requirements

The public API surface exported from `mlody/core/dag.py` (or its `__init__.py`):

```python
from mlody.core.dag import (
    TaskNode,
    Edge,
    PortRef,
    PathError,
    PortLocationParseError,
    build_dag,
    validate_paths,
    tasks_producing,
    tasks_consuming,
    ancestors_subgraph,
    parse_port_location,
)
```

---

## 10. User Interface Requirements

Not applicable — this is a library module with no UI.

---

## 11. Reporting & Analytics Requirements

Not applicable for this change. The DAG is an infrastructure layer that future
analytics/reporting features will consume.

---

## 12. Security & Compliance Requirements

See NFR-S-001. No authentication, authorization, or compliance requirements
beyond what applies to the mlody framework as a whole.

---

## 13. Infrastructure & Deployment Requirements

### 13.1 Hosting & Environment

Pure Python library; deployed as part of the mlody Bazel target graph.

### 13.2 Deployment

- A `o_py_library` target must be defined in `mlody/core/BUILD.bazel` (or a new
  `mlody/core/dag/BUILD.bazel`) with `@pip//networkx` in `deps`.
- A separate `o_py_test` target must cover the unit tests.
- `bazel run :gazelle` must be run after adding the new target; BUILD files must
  not be edited manually.

### 13.3 Disaster Recovery

Not applicable.

---

## 14. Testing & Quality Assurance Requirements

### 14.1 Testing Scope

All tests live alongside the source in `mlody/core/`. Test file convention:
`*_test.py`. Tests must use `pyfakefs` or `InMemoryFS` for `.mlody` file content
and must not touch the real filesystem.

Required test coverage:

| Test area                                            | Cases                                                                |
| ---------------------------------------------------- | -------------------------------------------------------------------- |
| `build_dag` — linear chain A -> B -> C               | Three nodes, two edges, correct `src_port` and `dst_path`            |
| `build_dag` — fork: A -> B and A -> C                | Three nodes, two edges with distinct `dst_path` values               |
| `build_dag` — join: A -> C and B -> C                | Three nodes, two edges with distinct `src_port` values               |
| `build_dag` — diamond topology                       | A -> B -> D and A -> C -> D; four nodes, four edges                  |
| `build_dag` — single-segment `dst_path` (input wire) | `Edge.dst_path` is a plain port name; edge is stored and traversed   |
| `build_dag` — multi-segment `dst_path` (config leaf) | `Edge.dst_path` contains dots; edge is stored and traversed          |
| `build_dag` — isolated task                          | Node present with no edges                                           |
| `build_dag` — task with multiple edges to same peer  | MultiDiGraph preserves all parallel edges; port annotations distinct |
| `parse_port_location` — valid input                  | Returns correct `PortRef`                                            |
| `parse_port_location` — missing colon                | Raises `PortLocationParseError`                                      |
| `parse_port_location` — missing dot                  | Raises `PortLocationParseError`                                      |
| `tasks_producing`                                    | Correct task set returned; empty set for unknown value               |
| `tasks_consuming`                                    | Correct task set; covers both single- and multi-segment `dst_path`   |
| `ancestors_subgraph`                                 | Minimal subgraph; excludes tasks unrelated to target output          |
| `ancestors_subgraph` — single-task workspace         | Returns subgraph with just that task                                 |
| `validate_paths` — valid paths                       | Returns empty list                                                   |
| `validate_paths` — invalid path segment              | Returns `PathError` naming the task and path                         |
| Topological sort compatibility                       | `networkx.topological_sort(dag)` succeeds for a valid DAG            |

### 14.2 Acceptance Criteria

- All unit tests pass under `bazel test //mlody/core:dag_test` (or equivalent
  target).
- `bazel build --config=lint //mlody/core/...` reports no errors.
- basedpyright strict reports zero errors on `mlody/core/dag.py`.
- Existing tests under `//mlody/core/...` and `//mlody/common/...` continue to
  pass without modification.

---

## 15. Training & Documentation Requirements

### 15.1 User Documentation

Not required for an internal library module.

### 15.2 Technical Documentation

- Each public symbol must have a docstring explaining its contract, accepted
  inputs, and raised exceptions.
- A module-level docstring in `dag.py` must describe the overall graph model,
  the unified `Edge` type, the `dst_path` convention for input ports vs. config
  leaves, and the relationship to the workspace evaluation lifecycle.

### 15.3 Training

Not applicable.

---

## 16. Risks & Mitigation Strategies

| Risk ID | Description                                                                                               | Impact | Probability | Mitigation                                                                                                       | Owner          |
| ------- | --------------------------------------------------------------------------------------------------------- | ------ | ----------- | ---------------------------------------------------------------------------------------------------------------- | -------------- |
| R-001   | Config type schema may not be accessible via a stable `value.type` path after evaluation                  | High   | Medium      | Investigate evaluator internals before implementation; document the accessor path in SPEC.md                     | @vitruvious    |
| R-002   | NetworkX `ancestors()` traverses all edge types uniformly; verify traversal includes all edges in pruning | Medium | Low         | Add explicit test for multi-edge and mixed-`dst_path` ancestor subgraph to confirm NetworkX traversal is correct | @vulcan-python |
| R-003   | Location strings may use a different format than `":task.port"` in existing `.mlody` files                | Medium | Medium      | Audit existing team `.mlody` files before finalizing `parse_port_location` grammar                               | mav            |
| R-004   | Adding `networkx` as a dependency may increase hermetic Python image size meaningfully                    | Low    | Low         | Confirm size impact with `bazel build` before merging                                                            | @vulcan-python |

---

## 17. Dependencies

| Dependency                             | Type            | Status                             | Impact if Delayed                           | Owner          |
| -------------------------------------- | --------------- | ---------------------------------- | ------------------------------------------- | -------------- |
| `networkx` pip package                 | Third-party lib | Must be added to lockfile          | Cannot build graph                          | @vulcan-python |
| `Workspace.load()` stable API          | Internal module | Available (existing)               | None                                        | mav            |
| Evaluator `_tasks_by_name` access path | Internal API    | Available (existing, private attr) | May require public accessor if attr removed | mav            |
| `value.type` schema accessor           | Internal API    | Available (resolved per 2.3)       | Path validation blocked if removed          | mav            |

---

## 18. Open Questions & Action Items

| ID     | Question/Action                                                                                                                                                   | Owner | Target Date | Status                                                                                                                                                                |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OQ-001 | Should the graph be `networkx.DiGraph` or `networkx.MultiDiGraph`?                                                                                                | mav   | 2026-03-28  | Closed — use `MultiDiGraph`; multiple edges between the same task pair are possible (task A may produce two values both consumed by task B).                          |
| OQ-002 | What is the precise Python path to access a task's type schema after evaluation?                                                                                  | mav   | 2026-03-28  | Closed — schema is accessible via the `value.type` field on declared `value()` objects. Path validation traverses the type structure starting from the declared type. |
| OQ-003 | Are there existing `.mlody` files that use `location=":task.port"` style references today, or is this a new convention being introduced by this change?           | mav   | TBD         | Open                                                                                                                                                                  |
| OQ-004 | Should `build_dag` accept an already-evaluated evaluator directly (for use in tests without a full `Workspace`), or should `Workspace` always be the entry point? | mav   | TBD         | Open                                                                                                                                                                  |

---

## 19. Revision History

| Version | Date       | Author                              | Changes                                                                                                                                                               |
| ------- | ---------- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2026-03-28 | Requirements Analyst AI (@socrates) | Initial draft                                                                                                                                                         |
| 1.1     | 2026-03-28 | Requirements Analyst AI (@socrates) | Unified `ValueEdge`/`ConfigEdge` into single `Edge` type; closed OQ-001 (MultiDiGraph) and OQ-002 (value.type accessor); removed OQ-005 (now non-issue).              |
| 1.2     | 2026-03-28 | Requirements Analyst AI (@socrates) | Added FR-004b (node identity uses `all`-dict key); updated `TaskNode` to include `node_id`; added data quality rules for `node_id`; added "node name" glossary entry. |

---

## Appendices

### Appendix A: Glossary

| Term          | Definition                                                                                                                                                                                                     |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DAG           | Directed Acyclic Graph — a directed graph with no cycles                                                                                                                                                       |
| node name     | The fully-qualified key under which a task is stored in the workspace's `all` dictionary. This is the canonical, unique identifier for a task across the entire workspace and is used as the NetworkX node ID. |
| edge          | A directed connection in the workspace DAG representing a value flowing from one task's output port to a destination on another task, annotated by `Edge`                                                      |
| port          | A named value slot on a task — either an input port (from `inputs` list) or an output port (from `outputs` list)                                                                                               |
| port location | A label string of the form `":task_name.port_name"` referencing a specific port on a specific task                                                                                                             |
| `dst_path`    | A dot-separated string identifying the destination on a consuming task. Single-segment: input port. Multi-segment: config leaf via struct traversal.                                                           |
| subgraph      | A subset of the DAG containing only nodes and edges relevant to a given target output                                                                                                                          |
| ancestors     | In graph terms: all nodes from which a given node is reachable by following directed edges forward                                                                                                             |
| task          | A registered `.mlody` entity with `kind="task"`, linking an action to its input and output values                                                                                                              |
| action        | A registered `.mlody` entity with `kind="action"`, describing a computation without specifying data                                                                                                            |
| value         | A registered `.mlody` entity with `kind="value"`, describing a data artifact at a named location                                                                                                               |

### Appendix B: References

- `mlody/common/task.mlody` — task rule definition; `inputs`, `outputs`,
  `action`, `config` fields
- `mlody/common/action.mlody` — action rule definition
- `mlody/common/values.mlody` — value rule definition
- `mlody/core/workspace.py` — `Workspace.load()` and registered-object access
- `mlody/CLAUDE.md` — framework architecture overview
- `mlody/openspec/changes/mlody-label-parsing/REQUIREMENTS.md` — label grammar
  reference

### Appendix C: Edge Annotation Examples

**Input-wiring edge** — task `tokenize` produces value `tokens` which is
consumed as input port `tokens` on task `embed`:

```python
Edge(
    src_port="tokens",   # output port on "tokenize"
    dst_path="tokens",   # input port on "embed" (single-segment)
)
```

**Config-injection edge** — task `pretrain` produces value `checkpoint` which is
injected into the `action.config.init_checkpoint` field of task `finetune`:

```python
Edge(
    src_port="checkpoint",                     # output port on "pretrain"
    dst_path="action.config.init_checkpoint",  # config leaf on "finetune" (multi-segment)
)
```

Both edges are the same type. The `dst_path` segment count is the only
structural difference.

### Appendix D: Pruning Algorithm (Pseudocode)

```
function ancestors_subgraph(dag, target_output):
    producers = tasks_producing(dag, target_output)
    if producers is empty:
        return empty subgraph
    all_relevant = set(producers)
    for task in producers:
        all_relevant |= networkx.ancestors(dag, task)
    return dag.subgraph(all_relevant)
```

---

**End of Requirements Document**
