# Requirements Document: DAG GUI Visualizer

**Version:** 1.0 **Date:** 2026-03-29 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

The `mlody dag` CLI command renders the workspace task graph as a Rich table in
the terminal. While this tabular view is useful for quick inspection, it cannot
convey the topological shape of the graph — branch points, fan-ins, and
multi-level dependency chains are difficult to read in a flat list.

This change adds a `--gui` flag to the existing `mlody dag` subcommand. When
supplied, the command opens a native desktop window showing the same graph (full
workspace or ancestor subgraph, depending on whether a label argument is also
given) as an interactive node-link diagram. The window is blocking: the CLI
waits until the user closes it. The `--gui` flag is additive — the existing Rich
table is still printed to the terminal before the window opens.

The expected business value is a faster, more intuitive understanding of
pipeline topology. A developer can run a single command to see the full
dependency structure rendered as a graph rather than a table, with edge labels
identifying the data ports at each end of every connection.

---

## 2. Project Scope

### 2.1 In Scope

- A `--gui` boolean flag on the existing `dag` subcommand in
  `mlody/cli/dag_cmd.py`. The flag is opt-in; the default is `False`.
- When `--gui` is given: after printing the Rich table, open a native desktop
  window displaying the same graph as a node-link diagram and block until the
  window is closed.
- Node rendering: each node displays the bare task name (the `TaskNode.name`
  field, not the fully-qualified `node_id`).
- Edge rendering: each edge displays two labels — `src_port` near the tail
  (source end) and `dst_path` near the arrowhead (destination end).
- Graph content: the GUI displays exactly the same graph that was rendered in
  the Rich table — the pruned ancestor subgraph when a `[label]` argument is
  supplied, the full workspace DAG otherwise.
- The GUI window layout should be structured to allow adding interactivity (pan,
  zoom, click-to-inspect) in a future iteration without requiring a rewrite of
  the rendering layer.
- CLI tests asserting that `--gui` can be invoked without error in a headless
  environment (e.g., by mocking or disabling the window-open call).

### 2.2 Out of Scope

- Hover or click information panels on nodes or edges. The window is read-only
  in this iteration.
- Pan, zoom, or any interactive manipulation of the graph. The window displays a
  static render.
- Saving or exporting the graph image to a file from within the GUI.
- Any changes to the Rich table output, the `[label]` filtering logic, or
  `mlody/core/dag.py`.
- The `--gui` flag being the default or triggered implicitly. It is always
  opt-in.
- Web-based or browser-rendered output. The window is a native desktop window.
- Cross-platform packaging or installer changes. The feature is a developer
  tool; developers are assumed to have a display available when using `--gui`.

### 2.3 Assumptions

- The developer's machine has a display available when `--gui` is used. If no
  display is present (e.g., a headless CI server), the GUI library will raise an
  error; this is acceptable and not required to be handled gracefully in this
  iteration.
- The window is modal and blocking from the CLI's perspective: `mlody dag --gui`
  does not return to the shell prompt until the user closes the window.
- The Rich table is always printed first, even when `--gui` is given, so that
  output is available if the window cannot open.
- Graph layout (node positioning) is computed automatically by the chosen
  library using a layout algorithm appropriate for directed graphs (e.g.,
  hierarchical / Sugiyama layout). Manual positioning is out of scope.
- The `TaskNode.name` field is suitable for display as a node label; it is the
  human-readable name authored in the `.mlody` file.
- `Edge.src_port` and `Edge.dst_path` are always non-empty strings (guaranteed
  by `mlody/core/dag.py`'s data quality requirements).
- The final library choice is delegated to the Solution Architect
  (`@vitruvious`) with the constraint that it must be "modern and slick" in
  appearance. See Section 3 (Library Selection) for guidance.

### 2.4 Constraints

- Python 3.13, strict basedpyright type checking, ruff formatting.
- The `dag` subcommand signature must remain backwards-compatible: existing
  invocations without `--gui` must produce identical output to the current
  behaviour.
- The `--gui` flag must not affect the exit code of the command (assuming the
  window opens and closes normally).
- Bazel BUILD files must not be edited manually; use `bazel run :gazelle`.
- Any new pip dependency must be added to the lockfile via `o-repin`.

---

## 3. Library Selection

The choice of Python GUI / graph-rendering library is **delegated to the
Solution Architect**. The following requirements and preferences constrain the
choice:

- **Must** render a directed graph with labelled nodes and labelled edges.
- **Must** support displaying two per-edge labels (one near the tail, one near
  the arrowhead).
- **Must** open a native desktop window (not a browser tab or embedded widget).
- **Must** be installable as a pip dependency without system-level build
  tooling.
- **Should** produce a visually polished, "modern and slick" result with minimal
  configuration.
- **Should** support a layout algorithm that handles directed, potentially
  multi-edge graphs well (hierarchical layout preferred for DAGs).
- **Should** be structured so that interactivity (pan, zoom, node click) can be
  added later without replacing the rendering layer.
- **Could** use a web-rendering backend embedded in a native window (e.g.,
  Electron-style or a `webview` wrapper) if that produces the best visual
  result.

Candidates the architect may evaluate include (but are not limited to): `pyvis`,
`graphviz` + `matplotlib`, `networkx` + `matplotlib`, `igraph`, `pydot` +
display, `dash-cytoscape`, `bokeh`, `pyqtgraph`, `dearpygui`, or a custom
HTML/SVG rendered in a `webview`. The architect should document the chosen
library and rationale in the SPEC.md.

---

## 4. Stakeholders

| Role                 | Name/Group     | Responsibilities                           |
| -------------------- | -------------- | ------------------------------------------ |
| mlody framework lead | mav            | Final acceptance, UX authority             |
| Requirements Analyst | @socrates      | Requirements elicitation and documentation |
| Solution Architect   | @vitruvious    | System design, library selection, SPEC.md  |
| Implementation       | @vulcan-python | Python coding                              |

---

## 5. Business Requirements

### 5.1 Business Objectives

- **BR-001:** Give developers a visual, topology-aware view of the workspace
  task graph so that branch points, fan-ins, and multi-level chains are
  immediately apparent without reading a flat list.
- **BR-002:** Surface the `src_port` / `dst_path` edge annotations graphically
  so that data-flow connections can be inspected without cross-referencing the
  table.

### 5.2 Success Metrics

- **KPI-001:** `mlody dag --gui` opens a window displaying a node-link diagram
  of the workspace DAG and blocks until the window is closed — verified manually
  and by a smoke test.
- **KPI-002:** `mlody dag [label] --gui` opens the same diagram as
  `mlody dag [label]` would show in the table (pruned ancestor subgraph) —
  verified by inspecting node count in a headless test.
- **KPI-003:** `mlody dag` (no `--gui`) produces output byte-for-byte equivalent
  to the current behaviour — verified by a regression test.
- **KPI-004:** Each edge in the diagram shows `src_port` near the tail and
  `dst_path` near the arrowhead — verified visually during acceptance review.

---

## 6. User Requirements

### 6.1 User Personas

**Persona 1: mlody pipeline developer**

- Needs to understand the shape of a pipeline at a glance — how many stages,
  where data splits or merges, what port names connect adjacent tasks.
- Pain point: the Rich table shows the correct data but cannot convey graph
  topology; counting "2 upstream tasks" in a table is slower than seeing them as
  two arrows entering a node.
- Needs a command that opens a clear graph diagram without requiring knowledge
  of external graph tools.

### 6.2 User Stories

**Epic 1: GUI flag**

- **US-001:** As a pipeline developer, I want to run `mlody dag --gui` and see a
  node-link diagram of the workspace DAG so that I can immediately understand
  the pipeline's topology.
  - Acceptance Criteria: Given a workspace with N tasks and E edges, when I run
    `mlody dag --gui`, then a window opens showing N nodes and E edges in a
    directed layout before the CLI prompt returns.
  - Priority: Must Have

- **US-002:** As a pipeline developer, I want each node to display the bare task
  name so that I can identify tasks by the names I authored.
  - Acceptance Criteria: Every node label in the diagram matches the `name`
    field of the corresponding `TaskNode` (not the fully-qualified `node_id`).
  - Priority: Must Have

- **US-003:** As a pipeline developer, I want each edge to display `src_port`
  near its tail and `dst_path` near its arrowhead so that I can trace the data
  flow between tasks without consulting the table.
  - Acceptance Criteria: For an edge from task A to task B with
    `src_port="tokens"` and `dst_path="model.inputs"`, the diagram shows
    `"tokens"` adjacent to A's end of the edge and `"model.inputs"` adjacent to
    B's end.
  - Priority: Must Have

- **US-004:** As a pipeline developer, I want `mlody dag [label] --gui` to show
  the same pruned subgraph as the table so that the GUI and table are always
  consistent.
  - Acceptance Criteria: When `label` is supplied, the window shows only the
    ancestor subgraph nodes and edges — the same set the table renders. No
    additional tasks appear.
  - Priority: Must Have

- **US-005:** As a pipeline developer, I want the Rich table to still be printed
  when I use `--gui` so that I can read the table output even if I close the
  window quickly.
  - Acceptance Criteria: `mlody dag --gui` prints the Rich table to stdout
    before opening the window. The table is not suppressed.
  - Priority: Must Have

- **US-006:** As a pipeline developer, I want the window to block the terminal
  so that I can close it when I am done and immediately continue using the
  shell.
  - Acceptance Criteria: The CLI does not return to the shell prompt while the
    window is open. When the window is closed, the CLI exits normally (exit code
    0).
  - Priority: Must Have

- **US-007:** As a pipeline developer, I want `mlody dag` without `--gui` to
  behave exactly as before so that existing scripts and habits are unaffected.
  - Acceptance Criteria: `mlody dag` and `mlody dag [label]` without `--gui`
    produce identical output to the current implementation with no new
    dependencies loaded or side-effects triggered.
  - Priority: Must Have

---

## 7. Functional Requirements

### 7.1 CLI Flag

**FR-001: `--gui` flag on `dag` subcommand**

- Description: The `dag` subcommand gains a boolean flag `--gui` / `--no-gui`
  (Click `is_flag=True`, default `False`). The flag is additive: it does not
  suppress existing behaviour.
- Inputs: `--gui` on the command line.
- Processing: If `False` (default), the command behaves exactly as today. If
  `True`, the command executes the full existing logic (table printing), then
  calls the GUI rendering function with the same graph and title.
- Business Rules: `--gui` is always opt-in. There is no environment variable or
  config file that enables it implicitly.
- Priority: Must Have
- Dependencies: Existing `dag` subcommand structure in `dag_cmd.py`.

### 7.2 Graph Passed to the GUI

**FR-002: GUI receives the same graph as the table**

- Description: The `networkx.MultiDiGraph` passed to the GUI rendering function
  is the same object (or an equivalent copy) used to render the Rich table — the
  full workspace DAG when no label argument is given, or the pruned ancestor
  subgraph when a label argument is given.
- Priority: Must Have
- Dependencies: FR-001; existing label-filtering logic (dag-label-filter
  change).

### 7.3 Node Rendering

**FR-003: Node label is the bare task name**

- Description: Each node in the diagram is labelled with `TaskNode.name` (the
  bare name field from the `.mlody` file). The fully-qualified `node_id` is not
  displayed.
- Inputs: `dag.nodes[node_id]["task"].name` for each node.
- Priority: Must Have
- Dependencies: FR-002.

### 7.4 Edge Rendering

**FR-004: Dual edge labels**

- Description: Each directed edge in the diagram carries two text labels:
  1. `src_port` — displayed near the tail (source end) of the edge.
  2. `dst_path` — displayed near the arrowhead (destination end) of the edge.
- Inputs: `edge_data["edge"].src_port` and `edge_data["edge"].dst_path` for each
  edge in the graph.
- Priority: Must Have
- Dependencies: FR-002.

**FR-005: Multi-edge handling**

- Description: Because the underlying graph is a `networkx.MultiDiGraph`, two
  tasks may be connected by more than one edge (one per shared value). All
  parallel edges must be rendered; they must be visually distinguishable (e.g.,
  curved or offset) so their labels can be read independently.
- Priority: Must Have
- Dependencies: FR-004.

### 7.5 Window Behaviour

**FR-006: Blocking window**

- Description: The GUI rendering function opens the window and does not return
  until the user closes it. The CLI's process does not exit while the window is
  open.
- Priority: Must Have
- Dependencies: FR-001.

**FR-007: Normal exit after window close**

- Description: After the window is closed by the user, the CLI exits with code 0
  (assuming no prior error). The window close is not treated as an error
  condition.
- Priority: Must Have
- Dependencies: FR-006.

### 7.6 Layout

**FR-008: Automatic directed-graph layout**

- Description: Node positions are computed automatically by a layout algorithm
  suited to directed acyclic graphs. A hierarchical (top-to-bottom or
  left-to-right) layout is preferred. Manual positioning is not required.
- Priority: Should Have
- Dependencies: FR-002.

### 7.7 Future-Proofing Structure

**FR-009: Rendering layer structured for future interactivity**

- Description: The GUI rendering code must be structured such that pan, zoom,
  and click-to-inspect interactions can be added in a future iteration without
  rewriting the rendering pipeline. Concretely: the graph data-to-visual-element
  mapping must be a separable step from the event loop / window management step.
  This is a structural requirement on the implementation, not a user-visible
  feature in this iteration.
- Priority: Should Have
- Dependencies: FR-002.

---

## 8. Non-Functional Requirements

### 8.1 Performance Requirements

- **NFR-P-001:** The GUI window must open (first paint) within 3 seconds of the
  Rich table being printed for a workspace with up to 100 tasks and 300 edges on
  a modern development machine.
- **NFR-P-002:** The existing Rich table rendering performance is unaffected by
  this change when `--gui` is not passed.

### 8.2 Scalability Requirements

- **NFR-SC-001:** The GUI should remain legible for graphs of up to 50 tasks
  (the expected common case). For larger graphs, the window may be cluttered but
  must not crash.

### 8.3 Availability & Reliability

- **NFR-AR-001:** If the GUI library fails to open a window (e.g., no display),
  the error propagates as an unhandled exception. The CLI does not attempt to
  continue silently.

### 8.4 Security Requirements

- **NFR-S-001:** The GUI renders only data from the in-memory DAG. It does not
  execute content from `.mlody` files, make network requests, or write to the
  filesystem.

### 8.5 Usability Requirements

- **NFR-U-001:** The diagram must visually distinguish node labels from edge
  labels (e.g., by placement, font size, or colour) so the two are not confused.
- **NFR-U-002:** Arrowheads must be present on edges to indicate direction of
  data flow (source task → destination task).
- **NFR-U-003:** The window title should reflect the graph being shown — e.g.,
  `"Workspace DAG"` for the full graph or `"DAG — ancestors of '<label>'"` for a
  filtered subgraph — matching the Rich table title.

### 8.6 Maintainability Requirements

- **NFR-M-001:** The GUI rendering logic must live in a dedicated function or
  module (e.g., `mlody/cli/dag_gui.py`) separate from `dag_cmd.py` to keep the
  CLI entry point clean.
- **NFR-M-002:** All new functions must satisfy basedpyright strict mode with no
  new `# type: ignore` comments beyond those already present.
- **NFR-M-003:** The `--gui` code path must not import the GUI library at module
  load time (lazy import inside the rendering function). This ensures
  `mlody dag` without `--gui` does not pay the import cost and does not fail in
  environments where the GUI library is unavailable.

### 8.7 Compatibility Requirements

- **NFR-C-001:** The `dag` subcommand signature change must be
  backwards-compatible. Existing invocations without `--gui` must produce
  identical output.
- **NFR-C-002:** The GUI feature is explicitly a developer workstation tool.
  There is no requirement to support headless or server environments.

---

## 9. Data Requirements

### 9.1 Data Entities

| Entity                  | Source                                 | Description                                      |
| ----------------------- | -------------------------------------- | ------------------------------------------------ |
| `networkx.MultiDiGraph` | `build_dag()` / `ancestors_subgraph()` | Graph passed to both the table and GUI renderers |
| `TaskNode.name`         | `dag.nodes[id]["task"].name`           | Bare task name; used as the node label           |
| `Edge.src_port`         | `dag.edges[u, v, k]["edge"].src_port`  | Port name at the source end; tail label          |
| `Edge.dst_path`         | `dag.edges[u, v, k]["edge"].dst_path`  | Destination path at the sink end; head label     |

### 9.2 Data Quality Requirements

- Node labels (`TaskNode.name`) are guaranteed non-empty by the workspace
  evaluator.
- Edge labels (`src_port`, `dst_path`) are guaranteed non-empty by
  `mlody/core/dag.py`'s data quality rules.

### 9.3 Data Retention & Archival

Not applicable — all data is ephemeral for the duration of the CLI invocation.

### 9.4 Data Privacy & Compliance

Not applicable.

---

## 10. Integration Requirements

### 10.1 External Systems

| System      | Purpose                      | Type           | Direction | Notes                               |
| ----------- | ---------------------------- | -------------- | --------- | ----------------------------------- |
| GUI library | Native window + graph render | Python library | Consumed  | TBD by architect; added to lockfile |

### 10.2 API Requirements

The GUI rendering function signature (indicative; exact form per SPEC.md):

```python
def show_dag_gui(
    dag: networkx.MultiDiGraph,
    title: str,
) -> None:
    """Open a blocking native window showing the DAG as a node-link diagram."""
    ...
```

Called from `dag_cmd.py` after the Rich table is printed, guarded by the `--gui`
flag. The function must not return until the window is closed.

---

## 11. User Interface Requirements

### 11.1 CLI Invocation

```
mlody dag [VALUE] [--gui]
```

- `VALUE` — optional value name (existing, from dag-label-filter change).
- `--gui` — open the GUI window after printing the table.

Example invocations:

```sh
# Full graph, table only (unchanged)
mlody dag

# Full graph + GUI window
mlody dag --gui

# Filtered subgraph, table only (unchanged from dag-label-filter)
mlody dag model_checkpoint

# Filtered subgraph + GUI window
mlody dag model_checkpoint --gui
```

### 11.2 Window Layout

The window displays:

- **Nodes:** Rounded rectangles (or circles) labelled with the bare task name.
- **Edges:** Directed arrows between nodes. Each arrow carries:
  - `src_port` label near the tail (source node end).
  - `dst_path` label near the arrowhead (destination node end).
- **Window title:** Matches the Rich table title — `"Workspace DAG"` or
  `"DAG — ancestors of '<label>'"`.
- **Layout:** Automatic hierarchical or force-directed layout suited to DAGs;
  exact algorithm delegated to architect.

The visual style should be clean and modern. Exact colours, fonts, and spacing
are delegated to the Solution Architect.

### 11.3 Navigation & Workflow

In this iteration the window is static (read-only). The user closes it via the
window manager's standard close button. No in-window controls are required.

Future interactivity (pan, zoom, click-to-inspect) is documented in Section 19
(Future Considerations).

---

## 12. Reporting & Analytics Requirements

Not applicable.

---

## 13. Security & Compliance Requirements

See NFR-S-001. No authentication, authorization, or compliance requirements
beyond what applies to the mlody CLI as a whole.

---

## 14. Infrastructure & Deployment Requirements

### 14.1 Hosting & Environment

Pure Python CLI change; deployed as part of the mlody Bazel target graph.
Requires a development workstation with a display.

### 14.2 Deployment

- The GUI rendering module (e.g., `mlody/cli/dag_gui.py`) must be added as a new
  `o_py_library` target or merged into the existing `mlody/cli` target.
- The chosen GUI library must be added to `deps` with a `# keep` comment if
  Gazelle cannot infer it from imports.
- `bazel run :gazelle` must be run after adding any new source files or targets.
  BUILD files must not be edited manually.
- Any new pip dependency must be added to `pyproject.toml` (without version pin)
  and the lockfile regenerated via `o-repin`.

### 14.3 Disaster Recovery

Not applicable.

---

## 15. Testing & Quality Assurance Requirements

### 15.1 Testing Scope

Tests live in `mlody/cli/` following the existing pattern. CLI tests use
`click.testing.CliRunner`. Because the GUI opens a native window, the window
call must be interceptable in tests (e.g., by patching the rendering function or
using a headless backend provided by the chosen library).

Required test coverage:

| Test                                    | Cases                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------- |
| `test_dag_gui_flag_invokes_renderer`    | `--gui` is passed; the GUI rendering function is called exactly once with the correct graph |
| `test_dag_gui_full_graph`               | No label + `--gui`; renderer receives the full DAG (all nodes present)                      |
| `test_dag_gui_filtered_subgraph`        | Label + `--gui`; renderer receives only the ancestor subgraph nodes                         |
| `test_dag_gui_table_still_printed`      | `--gui` is passed; Rich table output is present in stdout before renderer is called         |
| `test_dag_no_gui_flag_no_renderer_call` | No `--gui`; GUI rendering function is never called                                          |
| `test_dag_no_gui_regression`            | `mlody dag` without `--gui` produces output identical to pre-change behaviour               |
| `test_dag_gui_exit_code_zero`           | After renderer returns normally, exit code is 0                                             |

### 15.2 Acceptance Criteria

- All new tests pass under `bazel test //mlody/cli:...` (or equivalent target).
- `bazel build --config=lint //mlody/cli/...` reports no errors.
- basedpyright strict reports zero new errors on `mlody/cli/dag_cmd.py` and the
  new GUI module.
- Existing tests under `//mlody/cli/...` pass without modification (regression).
- Manual acceptance: running `mlody dag --gui` on a workstation opens a window
  with the correct nodes, directed edges, and dual edge labels.

---

## 16. Training & Documentation Requirements

### 16.1 User Documentation

The `--help` output for `mlody dag` should document the `--gui` flag and state
that it opens a blocking window. Click auto-generates help text from the flag
declaration; the flag help string should read:
`"Open a GUI window showing the DAG diagram (blocking until closed)."`.

### 16.2 Technical Documentation

- A module-level docstring in `dag_gui.py` describing the rendering contract:
  inputs, blocking behaviour, and the extension points for future interactivity.
- A docstring on `show_dag_gui` explaining the `dag` parameter, the `title`
  parameter, and the guarantee that the function does not return until the
  window is closed.

### 16.3 Training

Not applicable.

---

## 17. Risks & Mitigation Strategies

| Risk ID | Description                                                                                   | Impact | Probability | Mitigation                                                                                                                                    | Owner          |
| ------- | --------------------------------------------------------------------------------------------- | ------ | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| R-001   | Chosen GUI library has no stable support for dual per-edge labels (tail + head)               | High   | Medium      | Architect must verify dual-label support before committing to a library; fall back to a single combined label `"src→dst"` only if unavoidable | @vitruvious    |
| R-002   | Multi-edge rendering (parallel edges) is unsupported or visually broken in the chosen library | Medium | Medium      | Verify multi-edge support in library evaluation; consider offsetting parallel edges manually if needed                                        | @vitruvious    |
| R-003   | GUI library adds a large transitive dependency footprint, slowing `bazel build`               | Low    | Low         | Confirm size/build-time impact before merging; use lazy import (NFR-M-003) to avoid loading in the non-GUI path                               | @vulcan-python |
| R-004   | Blocking window call prevents test suite from running in CI (headless)                        | High   | High        | The `show_dag_gui` function must be patchable in tests (see Section 15); architect to document the patch point in SPEC.md                     | @vitruvious    |
| R-005   | Large workspaces (100+ tasks) produce cluttered, unreadable diagrams                          | Medium | Medium      | Document scalability ceiling (NFR-SC-001); consider a note in `--help` that the GUI is most useful for focused subgraphs via `[label] --gui`  | mav            |

---

## 18. Dependencies

| Dependency                                   | Type         | Status                      | Impact if Delayed                  | Owner       |
| -------------------------------------------- | ------------ | --------------------------- | ---------------------------------- | ----------- |
| `mlody.core.dag` (build_dag, TaskNode, Edge) | Internal API | Implemented (PR #437)       | Cannot build or render the graph   | mav         |
| dag-label-filter change                      | Predecessor  | In progress (same branch)   | Filtered-subgraph GUI path blocked | mav         |
| GUI library (TBD)                            | Third-party  | To be selected by architect | Entire GUI feature blocked         | @vitruvious |

---

## 19. Open Questions & Action Items

| ID     | Question/Action                                                                                                                              | Owner       | Target Date | Status |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | ----------- | ------ |
| OQ-001 | Which GUI / graph-rendering library should be used? Evaluate against the constraints in Section 3 and document in SPEC.md.                   | @vitruvious | TBD         | Open   |
| OQ-002 | Should the window title be a GUI-level title bar string, or also rendered as a visible title inside the diagram canvas?                      | mav         | TBD         | Open   |
| OQ-003 | For multi-edge graphs, should parallel edges be curved automatically by the library, or does the implementation need to curve them manually? | @vitruvious | TBD         | Open   |

---

## 20. Revision History

| Version | Date       | Author                              | Changes       |
| ------- | ---------- | ----------------------------------- | ------------- |
| 1.0     | 2026-03-29 | Requirements Analyst AI (@socrates) | Initial draft |

---

## Appendices

### Appendix A: Glossary

| Term            | Definition                                                                                                                |
| --------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `--gui`         | CLI flag that opts into opening the GUI window after the Rich table is printed                                            |
| bare task name  | `TaskNode.name` — the `name` field as authored in the `.mlody` file; used as the node label in the GUI                    |
| `src_port`      | `Edge.src_port` — the output port name on the source task; rendered as the tail label on each directed edge               |
| `dst_path`      | `Edge.dst_path` — the dotted destination path on the consuming task; rendered as the head label on each directed edge     |
| blocking window | A GUI window whose open call does not return until the user closes the window; the CLI process waits during this time     |
| tail label      | Text placed near the source-node end of a directed edge                                                                   |
| head label      | Text placed near the destination-node end (arrowhead) of a directed edge                                                  |
| multi-edge      | Two or more parallel directed edges between the same pair of nodes; arises when one task feeds multiple values to another |
| pruned subgraph | The ancestor subgraph returned by `ancestors_subgraph()` when a `[label]` argument is supplied to `mlody dag`             |

### Appendix B: References

- `mlody/cli/dag_cmd.py` — existing `dag` subcommand implementation
- `mlody/core/dag.py` — `build_dag`, `ancestors_subgraph`, `TaskNode`, `Edge`
- `mlody/openspec/changes/mav-437-workspace-dag/REQUIREMENTS.md` — DAG library
  requirements
- `mlody/openspec/changes/dag-label-filter/REQUIREMENTS.md` — label-filter
  requirements (predecessor change; `[label]` argument and `--gui` compose)

### Appendix C: Control Flow (Pseudocode)

```
dag_cmd(ctx, label, gui):
    workspace = Workspace(...)
    workspace.load()

    dag = build_dag(workspace)

    if label is None:
        display_graph = dag
        title = "Workspace DAG"
    else:
        display_graph = ancestors_subgraph(dag, label)
        if len(display_graph.nodes) == 0:
            print_error(f"no task produces value '{label}'")
            exit(1)
        title = f"DAG — ancestors of '{label}'"

    order = topological_sort(display_graph)
    render_table(display_graph, order, title)   # always

    if gui:
        show_dag_gui(display_graph, title)       # blocks until window closed
```

---

## 20. Future Considerations (Backlog)

### 20.1 Interactive GUI (Pan, Zoom, Click-to-Inspect)

**Summary:** Add pan, zoom, and click-to-inspect interactions to the GUI window.

**Motivation:** A static diagram is useful for understanding topology. An
interactive diagram would allow developers to inspect node metadata (full
`node_id`, action name, input/output port lists) and edge details without
switching to the terminal table.

**Why deferred:** Interactivity requires either a library that natively supports
event handling on graph elements, or a custom event loop. The library selection
(Section 3) and static rendering architecture (FR-009) are designed to make this
addition straightforward in a future iteration without a rewrite.

**Suggested future scope:**

- Click on a node to open a side panel showing `TaskNode.node_id`, `action`,
  `input_ports`, and `output_ports`.
- Click on an edge to show the full `Edge` struct (`src_port`, `dst_path`).
- Pan and zoom via mouse drag and scroll wheel.
- Keyboard shortcut to reset the view to fit-all.

### 20.2 Export to Image

**Summary:** Add a `--save <path>` flag to export the diagram as PNG or SVG
without opening an interactive window.

**Motivation:** Useful for documentation and PR review comments.

**Why deferred:** Export formats depend on the chosen library and are out of
scope for the initial read-only window implementation.

---

**End of Requirements Document**
