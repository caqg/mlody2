# Tasks: DAG Label Filter

**Change:** dag-label-filter **Design:**
`mlody/openspec/changes/dag-label-filter/design.md` **Requirements:**
`mlody/openspec/changes/dag-label-filter/REQUIREMENTS.md`

---

## Task List

### 1. Modify `dag_cmd.py`

#### 1.1 Add `ancestors_subgraph` to the import from `mlody.core.dag`

Update the existing import line in `mlody/cli/dag_cmd.py` to include
`ancestors_subgraph`:

```python
from mlody.core.dag import Edge, ancestors_subgraph, build_dag
```

**Acceptance:** `basedpyright` reports no new errors; `ruff` passes.

#### 1.2 Add optional positional `label` argument to the `dag` Click command

Declare the argument on the `dag` command using
`@click.argument("label", required=False, default=None)` and add
`label: str | None` to the function signature.

**Acceptance:** `mlody dag --help` shows `[LABEL]` as an optional positional
argument. Running `mlody dag` (no argument) still works.

#### 1.3 Insert the `display_graph` / `title` branch block

Between the `build_dag(workspace)` call and the topological sort, insert:

```python
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
```

**Acceptance:** FR-003, FR-004, FR-005 satisfied; basedpyright strict passes.

#### 1.4 Replace `dag` with `display_graph` in the rendering section

Update the topological sort call and all node/edge lookups in the rendering loop
to use `display_graph` instead of `dag`.

**Acceptance:** Full-graph path still renders all tasks. Filtered path renders
only ancestor nodes. NFR-M-001 satisfied (no duplicated rendering logic).

#### 1.5 Update the command docstring

Extend the `dag` command docstring to describe the optional `VALUE` argument,
both code paths, and the error condition. Use "value" in user-facing language
and "output port" in technical descriptions (per D-5).

**Acceptance:** `mlody dag --help` includes a description of the `VALUE`
argument and its effect.

---

### 2. Write `dag_cmd_test.py`

Create `mlody/cli/dag_cmd_test.py` with the following test classes and methods.
Use `click.testing.CliRunner` for all tests. Build workspace content with
`pyfakefs` (`fs` fixture) or `InMemoryFS` — no real filesystem access and no
mocking of `build_dag` or `ancestors_subgraph` internals.

#### 2.1 `TestDagFullGraph`

- `test_no_arg_shows_all_tasks` — no label supplied; all task node IDs appear in
  stdout; exit code 0. (FR-002, KPI-002)
- `test_no_arg_title_is_workspace_dag` — stdout contains the literal string
  `"Workspace DAG"`. (FR-002, US-004)

#### 2.2 `TestDagFilteredPath`

- `test_label_shows_ancestor_nodes_only` — label matches a subset of tasks; only
  ancestor task IDs appear in stdout; non-ancestor IDs are absent; exit code 0.
  (FR-003, US-001, KPI-001)
- `test_label_excludes_unrelated_tasks` — workspace has tasks outside the
  ancestor set; confirm they are absent from stdout. (US-001)
- `test_label_title_contains_value_name` — stdout contains `"ancestors of"` and
  the supplied label. (FR-005, US-004)
- `test_label_single_producer_one_row` — label is produced by exactly one
  isolated task with no upstream dependencies; stdout contains exactly that one
  task ID. (FR-005)

#### 2.3 `TestDagErrorPath`

- `test_unknown_label_exits_nonzero` — unrecognised label; exit code 1; stderr
  contains `"Error:"` and the label name. (FR-004, US-003, KPI-003)
- `test_unknown_label_error_to_stderr` — error text is on stderr, not stdout.
  (FR-004, NFR-U-001)

#### 2.4 `TestDagCaseSensitivity`

- `test_wrong_case_not_found` — `ModelCheckpoint` returns an error when the
  value is registered as `model_checkpoint`. (FR-003, §8.2)

#### 2.5 `TestDagRegression`

- `test_no_arg_regression` — full-graph output contains all pre-existing task
  IDs and title is `"Workspace DAG"`; guards against regression introduced by
  this change. (NFR-C-001, KPI-002)

---

### 3. Update BUILD files via Gazelle

Run `bazel run :gazelle` from the repo root after creating `dag_cmd_test.py` so
that Gazelle generates the `o_py_test` target for the new test file.

If any dependency Gazelle cannot infer from imports (e.g. a transitive dep
needed by the in-memory workspace fixture) is required, add it manually with a
`# keep` comment on that dep line.

**Acceptance:** `bazel test //mlody/cli:dag_cmd_test` resolves and all tests
pass. `bazel build --config=lint //mlody/cli/...` reports no errors.

---

## Acceptance Criteria (change-level)

- [x] `mlody dag` (no argument) produces output identical to pre-change
      behaviour.
- [x] `mlody dag <value>` renders only the ancestor subgraph of `<value>` with
      title `DAG — ancestors of '<value>'`.
- [x] `mlody dag <unknown>` exits with code 1 and prints a red error to stderr
      naming the unrecognised value.
- [x] All new tests pass: `bazel test //mlody/cli:dag_cmd_test`
- [x] No regressions: `bazel test //mlody/cli/...`
- [x] Lint clean: `bazel build --config=lint //mlody/cli/...`
- [x] basedpyright strict: zero new errors on `mlody/cli/dag_cmd.py`
