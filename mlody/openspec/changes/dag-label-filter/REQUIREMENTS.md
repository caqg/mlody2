# Requirements Document: DAG Label Filter

**Version:** 1.1 **Date:** 2026-03-29 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

The `mlody dag` CLI command currently displays every task in the workspace as a
Rich table, sorted in topological order. When a workspace contains dozens or
hundreds of tasks, this full-graph view is too noisy for developers who want to
understand only the minimal set of tasks contributing to a specific output
value.

This change adds an optional positional argument to the existing `dag`
subcommand. When the argument is omitted the command behaves exactly as today
(full graph, unchanged). When a value name is supplied, the command computes the
ancestor subgraph for that value — the minimal set of tasks that transitively
produce it — and renders only that pruned subgraph using the same tabular Rich
output format. The pruning algorithm is already implemented in
`ancestors_subgraph()` from `mlody/core/dag.py`; this change is purely a CLI
wiring exercise.

The expected business value is a faster, focused debugging and inspection
workflow: a developer can answer "what must run to produce `model_checkpoint`"
by running a single command with no post-processing.

---

## 2. Project Scope

### 2.1 In Scope

- An optional positional argument `[label]` on the existing `dag` subcommand in
  `mlody/cli/dag_cmd.py`.
- When `label` is absent: existing behaviour — build the full DAG, render all
  tasks in topological order.
- When `label` is present: call `ancestors_subgraph(dag, label)` on the full
  DAG, then render only the pruned subgraph in topological order using the same
  Rich table format as today.
- A user-facing error message (red, to stderr) when the supplied label matches
  no producing task and the pruned subgraph is therefore empty.
- CLI tests in `mlody/cli/dag_cmd_test.py` (or the existing `main_test.py`
  pattern) covering both the no-argument path (regression) and the filtered
  path.

### 2.2 Out of Scope

- Changes to `mlody/core/dag.py` — the `ancestors_subgraph()` function is
  already implemented and is used as-is.
- Label parsing using the full mlody `Label` grammar (workspace specifier,
  entity path, attribute path). The argument is matched directly against
  `output_ports` on `TaskNode` entries in the DAG. The argument is a plain
  output port name string, not a structured label.
- Filtering by task name, action name, or any field other than output port.
- Multiple labels / multi-target pruning in a single invocation. Supporting
  multiple values (e.g. `mlody dag val_a val_b` to display the union of both
  ancestor subgraphs) is a desired future capability that mirrors the behaviour
  of the `show` subcommand, but it is explicitly deferred and out of scope for
  this change. See Section 20 (Future Considerations).
- Interactive or streaming output.
- Changes to any HuggingFace-related code or the `value` description feature
  being developed separately on this branch.

### 2.3 Assumptions

- `ancestors_subgraph(dag, label)` returns an empty `networkx.MultiDiGraph` when
  no task exposes a port named `label`. The CLI treats an empty pruned graph as
  a user error and exits non-zero.
- The topological sort on the pruned subgraph cannot cycle (it is a subgraph of
  a valid DAG). `networkx.NetworkXUnfeasible` is not expected; if raised, it
  falls through to the existing cycle-error handler.
- The `[label]` argument is matched case-sensitively against output port names
  as registered in the workspace (port names are controlled by `.mlody` file
  authors and are case-sensitive throughout mlody).
- The output table title changes from `"Workspace DAG"` to
  `"DAG — ancestors of '<label>'"` when a label is supplied, so the user can
  tell at a glance that they are viewing a filtered subgraph.

### 2.4 Constraints

- Python 3.13, strict basedpyright type checking, ruff formatting.
- The `dag` subcommand signature must remain backwards-compatible: invocations
  without the argument must produce identical output to the current behaviour.
- No new third-party dependencies are introduced by this change.
- Bazel BUILD files must not be edited manually; use `bazel run :gazelle`.

---

## 3. Stakeholders

| Role                 | Name/Group     | Responsibilities                           |
| -------------------- | -------------- | ------------------------------------------ |
| mlody framework lead | mav            | Final acceptance, UX authority             |
| Requirements Analyst | @socrates      | Requirements elicitation and documentation |
| Solution Architect   | @vitruvious    | System design and SPEC.md                  |
| Implementation       | @vulcan-python | Python coding                              |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Allow developers to inspect only the tasks that are causally
  upstream of a specific output value, reducing the cognitive load of reading a
  full-workspace DAG table.
- **BR-002:** Expose the subgraph-pruning capability already present in
  `mlody/core/dag.py` through the CLI so it is accessible without writing Python
  code.

### 4.2 Success Metrics

- **KPI-001:** `mlody dag model_checkpoint` displays only the tasks in the
  ancestor subgraph of the value `model_checkpoint` — verified by CLI tests that
  assert the set of rendered task IDs.
- **KPI-002:** `mlody dag` (no argument) produces output byte-for-byte
  equivalent to the current behaviour — verified by a regression test.
- **KPI-003:** `mlody dag nonexistent_value` exits with a non-zero status code
  and prints a human-readable error to stderr naming the unrecognised value.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: mlody pipeline developer**

- Needs to quickly understand which tasks feed into a specific output (e.g.,
  `model_checkpoint`, `eval_results`) when debugging a pipeline.
- Pain point: the full-graph table for a large workspace is long and requires
  manual scanning to identify relevant tasks.
- Needs a single command invocation that returns only the relevant subgraph.

### 5.2 User Stories

**Epic 1: Filtered DAG display**

- **US-001:** As a pipeline developer, I want to run `mlody dag <value>` and see
  only the tasks that contribute to that value so that I can understand the
  upstream dependencies without reading the full graph.
  - Acceptance Criteria: Given a workspace where tasks A, B, and C collectively
    produce `model_checkpoint` and tasks D and E do not contribute to it, when I
    run `mlody dag model_checkpoint`, then the table contains exactly A, B, and
    C and not D or E.
  - Priority: Must Have

- **US-002:** As a pipeline developer, I want `mlody dag` (no argument) to
  continue working exactly as before so that existing scripts and habits are
  unaffected.
  - Acceptance Criteria: Running `mlody dag` with no positional argument
    produces the same table as before this change, including all tasks.
  - Priority: Must Have

- **US-003:** As a pipeline developer, I want a clear error message when I
  supply a value that no task produces so that I can immediately identify the
  typo or misconfiguration.
  - Acceptance Criteria: Running `mlody dag typo_value` exits with a non-zero
    status and prints to stderr: `"Error: no task produces output 'typo_value'"`
    (or equivalent wording).
  - Priority: Must Have

- **US-004:** As a pipeline developer, I want the filtered table's title to
  indicate that it is a subgraph, not the full workspace, so that I am not
  misled into thinking the displayed tasks represent everything in the
  workspace.
  - Acceptance Criteria: When a label is supplied, the Rich table title reads
    `"DAG — ancestors of '<label>'"` instead of `"Workspace DAG"`.
  - Priority: Should Have

---

## 6. Functional Requirements

### 6.1 CLI Argument

**FR-001: Optional positional argument on `dag`**

- Description: The `dag` subcommand accepts an optional positional argument
  `label` of type `str`. The argument is declared with Click's
  `@click.argument("label", required=False, default=None)`.
- Inputs: An optional value name supplied on the command line.
- Processing: If `None`, proceed with the full-graph path. If non-`None`,
  proceed with the filtered path. Internally, the string is matched against
  output port names on `TaskNode` entries.
- Business Rules: The argument is positional, not a flag.
  `mlody dag model_checkpoint` (not `mlody dag --label model_checkpoint`).
- Priority: Must Have
- Dependencies: Existing `dag` subcommand structure in `dag_cmd.py`.

### 6.2 Full-Graph Path (No Argument)

**FR-002: Unchanged behaviour when no label is supplied**

- Description: When `label` is `None`, the command behaves exactly as the
  current implementation: builds the full DAG, sorts topologically, and renders
  all nodes in the `"Workspace DAG"` table.
- Priority: Must Have
- Dependencies: FR-001.

### 6.3 Filtered Path (Label Supplied)

**FR-003: Compute pruned subgraph**

- Description: When `label` is non-`None`, call `ancestors_subgraph(dag, label)`
  (imported from `mlody.core.dag`) on the full DAG to obtain a
  `networkx.MultiDiGraph` containing only the relevant tasks.
- Inputs: The full DAG (from `build_dag(workspace)`) and the `label` string.
- Outputs: A `networkx.MultiDiGraph` subgraph (possibly empty).
- Priority: Must Have
- Dependencies: FR-001; `mlody.core.dag.ancestors_subgraph` (already
  implemented).

**FR-004: Empty-subgraph error**

- Description: If the pruned subgraph has zero nodes, print to stderr:
  `"Error: no task produces value '<label>'"` (styled red via `click.style`) and
  exit with status code 1.
- Priority: Must Have
- Dependencies: FR-003.

**FR-005: Render pruned subgraph with filtered title**

- Description: If the pruned subgraph is non-empty, sort it topologically and
  render the same Rich table format as the full-graph path, with two
  differences:
  1. The table title is `f"DAG — ancestors of '{label}'"`.
  2. Only the nodes present in the pruned subgraph are rendered. All column
     definitions, styles, and per-row rendering logic remain identical to the
     full-graph path (task node ID, action name, inbound edges with
     `src_port -> dst_port`).
- Priority: Must Have
- Dependencies: FR-003.

**FR-006: Topological sort on pruned subgraph**

- Description: Topological sorting is applied to the pruned subgraph, not the
  full DAG, so the rendered row order reflects dependency order within the
  subgraph.
- Priority: Must Have
- Dependencies: FR-005.

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-P-001:** The additional latency introduced by this change (i.e., the
  `ancestors_subgraph` call) must be less than 50 ms for a workspace with up to
  500 tasks. This is already guaranteed by `NFR-P-002` in the DAG
  REQUIREMENTS.md; no additional constraint is introduced here.

### 7.2 Scalability Requirements

- Not applicable beyond what is inherited from the DAG library.

### 7.3 Availability & Reliability

- Not applicable — this is a CLI command with no networked dependencies.

### 7.4 Security Requirements

- **NFR-S-001:** The `label` argument is used only as a string key to query the
  in-memory DAG. It is never executed, interpolated into shell commands, or
  written to files.

### 7.5 Usability Requirements

- **NFR-U-001:** The error message for an unrecognised value (FR-004) must quote
  the supplied value name so the user can identify a typo at a glance.
- **NFR-U-002:** The table title change (FR-005) must be visible in both
  terminal and redirected output (i.e., it is part of the Rich table metadata,
  not a separate print statement that might be suppressed).

### 7.6 Maintainability Requirements

- **NFR-M-001:** The filtered path and the full-graph path must share the same
  row-rendering logic (no duplication). The only branching is: which graph to
  pass to `topological_sort` and which title string to use.
- **NFR-M-002:** All new or changed functions must satisfy basedpyright strict
  mode with no new `# type: ignore` comments beyond those already present in the
  file.

### 7.7 Compatibility Requirements

- **NFR-C-001:** The command signature change must be backwards-compatible.
  Existing invocations of `mlody dag` without arguments must continue to work
  without modification.

---

## 8. Data Requirements

### 8.1 Data Entities

| Entity                  | Source                 | Description                                           |
| ----------------------- | ---------------------- | ----------------------------------------------------- |
| `networkx.MultiDiGraph` | `build_dag(workspace)` | Full workspace task graph                             |
| `networkx.MultiDiGraph` | `ancestors_subgraph()` | Pruned subgraph for the supplied label                |
| `str` (`label`)         | CLI positional arg     | Output port name to filter by; `None` means no filter |

### 8.2 Data Quality Requirements

- The `label` argument is matched case-sensitively against output port names in
  `TaskNode.output_ports`. No normalisation is applied.

### 8.3 Data Retention & Archival

Not applicable — all data is ephemeral for the duration of the CLI invocation.

### 8.4 Data Privacy & Compliance

Not applicable.

---

## 9. Integration Requirements

### 9.1 External Systems

No new external system integrations. The change is internal to the mlody CLI.

### 9.2 API Requirements

The change calls the following already-public symbols from `mlody.core.dag`:

```python
from mlody.core.dag import Edge, build_dag, ancestors_subgraph
```

`ancestors_subgraph` is the only new import relative to the current
`dag_cmd.py`. No changes to the `mlody.core.dag` public API are required.

---

## 10. User Interface Requirements

### 10.1 CLI Invocation

```
mlody dag [VALUE]
```

- `VALUE` — optional value name. If omitted: full workspace DAG. If provided:
  ancestors of `VALUE`.

Example invocations:

```sh
# Full graph (unchanged)
mlody dag

# Pruned subgraph for a specific value
mlody dag model_checkpoint

# Error path — value not found
mlody dag typo_value
# stderr: Error: no task produces value 'typo_value'
# exit code: 1
```

### 10.2 Output Format

**Full-graph path (no label):** Identical to current output.

**Filtered path (label supplied):**

```
                   DAG — ancestors of 'model_checkpoint'
┌────────────────────────┬──────────────┬────────────────────────────────┐
│ Task                   │ Action       │ Dependencies                   │
│ ...                    │ ...          │ ...                            │
```

The table structure, column widths, styles, and per-row content are identical to
the full-graph path. Only the title and the set of rendered rows differ.

### 10.3 Error Output

Errors are printed to stderr using `click.echo(..., err=True)` with
`click.style(..., fg="red")`, consistent with the existing error handling style
in `dag_cmd.py`.

---

## 11. Reporting & Analytics Requirements

Not applicable.

---

## 12. Security & Compliance Requirements

See NFR-S-001. No authentication, authorisation, or compliance requirements
beyond what applies to the mlody CLI as a whole.

---

## 13. Infrastructure & Deployment Requirements

### 13.1 Hosting & Environment

Pure Python CLI change; deployed as part of the mlody Bazel target graph. No
infrastructure changes.

### 13.2 Deployment

- `dag_cmd.py` is already part of the `mlody/cli/` Bazel target. No new BUILD
  targets are needed unless test coverage requires a new `o_py_test` target.
- `bazel run :gazelle` must be run if any new test file is created.

### 13.3 Disaster Recovery

Not applicable.

---

## 14. Testing & Quality Assurance Requirements

### 14.1 Testing Scope

Tests live in `mlody/cli/` following the existing pattern in `main_test.py` and
`show_test.py`. CLI tests use `click.testing.CliRunner`. Workspace content uses
`InMemoryFS` or `pyfakefs` — no real filesystem access.

Required test coverage:

| Test                                     | Cases                                                                                      |
| ---------------------------------------- | ------------------------------------------------------------------------------------------ |
| `test_dag_no_arg_full_graph`             | No label supplied; all tasks appear in output; title is `"Workspace DAG"`                  |
| `test_dag_label_pruned_subgraph`         | Label matches a subset of tasks; only ancestor nodes appear; title contains the label      |
| `test_dag_label_excludes_unrelated`      | Workspace has tasks outside the ancestor set; confirm they are absent from output          |
| `test_dag_label_not_found_exits_nonzero` | Unrecognised label; exit code 1; stderr contains the label name and an "Error:" prefix     |
| `test_dag_label_single_producer`         | Label produced by exactly one task with no upstream dependencies; subgraph has 1 row       |
| `test_dag_label_case_sensitive`          | `ModelCheckpoint` (wrong case) is not found when value is registered as `model_checkpoint` |
| `test_dag_no_arg_regression`             | Full-graph output is identical before and after this change (regression guard)             |

### 14.2 Acceptance Criteria

- All new tests pass under `bazel test //mlody/cli:...` (or equivalent target).
- `bazel build --config=lint //mlody/cli/...` reports no errors.
- basedpyright strict reports zero new errors on `mlody/cli/dag_cmd.py`.
- Existing tests under `//mlody/cli/...` pass without modification (regression).

---

## 15. Training & Documentation Requirements

### 15.1 User Documentation

The `--help` output for `mlody dag` should reflect the new optional argument.
Click auto-generates help text from the docstring and argument declarations; the
command docstring should be updated to mention the optional `VALUE` argument and
its effect.

### 15.2 Technical Documentation

- A docstring on `dag_cmd` describing the full-graph and filtered-graph paths,
  the error condition, and the source of the pruning logic.

### 15.3 Training

Not applicable.

---

## 16. Risks & Mitigation Strategies

| Risk ID | Description                                                                                            | Impact | Probability | Mitigation                                                                                                                     | Owner          |
| ------- | ------------------------------------------------------------------------------------------------------ | ------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------- |
| R-001   | `ancestors_subgraph` returns a view, not a copy; topological sort on a view may behave unexpectedly    | Low    | Low         | The existing SPEC.md for the DAG library specifies `.copy()` is returned; confirm this before relying on it in `dag_cmd.py`    | @vulcan-python |
| R-002   | Users expect the argument to be a full mlody address (`@root//pkg:name`) rather than a bare value name | Medium | Medium      | Document clearly in `--help` that `VALUE` is a value name (matched against output ports internally), not a full entity address | mav            |
| R-003   | Cycle detection in the pruned subgraph raises `NetworkXUnfeasible` unexpectedly                        | Low    | Very Low    | The pruned subgraph is a subgraph of a DAG; cycles are impossible unless `ancestors_subgraph` has a bug                        | @vulcan-python |

---

## 17. Dependencies

| Dependency                             | Type         | Status                | Impact if Delayed                     | Owner |
| -------------------------------------- | ------------ | --------------------- | ------------------------------------- | ----- |
| `mlody.core.dag.ancestors_subgraph`    | Internal API | Implemented (PR #437) | Filtered path blocked entirely        | mav   |
| `mlody.core.dag.build_dag`             | Internal API | Implemented (PR #437) | Entire command blocked                | mav   |
| `mav-437-workspace-dag` tasks complete | Predecessor  | In progress           | Must be merged or cherry-picked first | mav   |

---

## 18. Open Questions & Action Items

| ID     | Question/Action                                                                                                                                                                                                              | Owner | Target Date | Status                                                                                                                                                                                    |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OQ-001 | Should the `LABEL` argument's help text say "output value name" or "output port name"? Align with the terminology used in `mlody dag` output columns.                                                                        | mav   | 2026-03-29  | Resolved                                                                                                                                                                                  |
| OQ-002 | Should a future version support multiple value arguments (e.g. `mlody dag val_a val_b`) to display the union of both ancestor subgraphs in one table? Should this be added to the backlog, or explicitly ruled out of scope? | mav   | 2026-03-29  | Resolved — multi-label support is desired (mirroring `show`) but is NOT a priority for this change. It is explicitly out of scope here and tracked in Section 20 (Future Considerations). |

---

## 19. Revision History

| Version | Date       | Author                              | Changes                                                                                                                                       |
| ------- | ---------- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2026-03-29 | Requirements Analyst AI (@socrates) | Initial draft                                                                                                                                 |
| 1.1     | 2026-03-29 | Requirements Analyst AI (@socrates) | OQ-001 resolved: "port" in technical contexts, "value" in user-facing text throughout document                                                |
| 1.2     | 2026-03-29 | Requirements Analyst AI (@socrates) | OQ-002 resolved: multi-label support explicitly out of scope; added Section 20 Future Considerations; refined Section 2.2 out-of-scope bullet |

---

## Appendices

### Appendix A: Glossary

| Term            | Definition                                                                                                                                                                                 |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| label           | Internal parameter name for the CLI argument. User-facing: "value" (the thing being queried). Technical: matched against output port names on `TaskNode`. Not a full mlody entity address. |
| ancestor        | A task that transitively produces data consumed by the target task, following directed edges in the workspace DAG.                                                                         |
| pruned subgraph | The `networkx.MultiDiGraph` returned by `ancestors_subgraph()` containing only nodes contributing to a named output.                                                                       |
| full-graph path | The code path taken when no `LABEL` is supplied — renders the entire workspace DAG.                                                                                                        |
| filtered path   | The code path taken when a `LABEL` is supplied — renders only the pruned ancestor subgraph.                                                                                                |

### Appendix B: References

- `mlody/cli/dag_cmd.py` — existing `dag` subcommand implementation
- `mlody/core/dag.py` — `build_dag`, `ancestors_subgraph`, `TaskNode`, `Edge`
- `mlody/openspec/changes/mav-437-workspace-dag/REQUIREMENTS.md` — DAG library
  requirements (predecessor change)
- `mlody/openspec/changes/mav-437-workspace-dag/SPEC.md` — DAG library design
- `mlody/openspec/changes/mav-437-workspace-dag/tasks.md` — DAG library tasks

### Appendix C: Control Flow (Pseudocode)

```
dag_cmd(ctx, label):
    workspace = Workspace(...)
    workspace.load()

    dag = build_dag(workspace)

    if label is None:
        display_graph = dag
        title = "Workspace DAG"
    else:
        display_graph = ancestors_subgraph(dag, label)
        if len(display_graph.nodes) == 0:
            print_error(f"no task produces output '{label}'")
            exit(1)
        title = f"DAG — ancestors of '{label}'"

    order = topological_sort(display_graph)
    render_table(display_graph, order, title)
```

---

## 20. Future Considerations (Backlog)

The following capability is explicitly out of scope for this change but is
recorded here so it can be prioritised in a future iteration.

### 20.1 Multi-Label Filtering

**Summary:** Allow `mlody dag` to accept more than one value argument in a
single invocation and display the union of all ancestor subgraphs in one table.

**Motivation:** The `show` subcommand already supports multiple label arguments;
`dag` should eventually mirror that ergonomics so users can inspect the combined
dependency footprint of several values at once (e.g.
`mlody dag model_checkpoint eval_results`).

**Why deferred:** The single-label case covers the primary use case identified
in this change. Implementing union-of-subgraphs requires deciding how to present
shared ancestors, ordering, and title formatting — a non-trivial UX decision
that warrants its own requirements pass.

**Suggested future scope:**

- Accept `[VALUE]...` (variadic Click argument) instead of a single `[VALUE]`.
- Compute the union of `ancestors_subgraph(dag, v)` for each supplied value.
- Render a single merged table with a title such as
  `"DAG — ancestors of 'val_a', 'val_b'"`.
- Error if any one of the supplied values produces an empty subgraph.

---

**End of Requirements Document**
