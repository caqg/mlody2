# Tasks: DAG GUI Visualizer

**Change:** dag-gui **Design:** `mlody/openspec/changes/dag-gui/design.md`
**Requirements:** `mlody/openspec/changes/dag-gui/REQUIREMENTS.md`

---

## Task List

### 1. Add `matplotlib` dependency

#### [x] 1.1 Add `matplotlib` to `pyproject.toml`

Add `"matplotlib"` to the `dependencies` list in `pyproject.toml` (no version
pin per repo convention). Place it near the existing `networkx` entry with an
explanatory comment:

```toml
# interactive graph visualisation for `mlody dag --gui`.
# See: https://matplotlib.org/
"matplotlib",
```

**Acceptance:** `pyproject.toml` contains the `matplotlib` entry. `networkx`
remains unchanged.

#### [x] 1.2 Regenerate the pip lockfile

Run `o-repin` from the repo root to regenerate all `requirements*.txt` lockfiles
with `matplotlib` pinned.

**Acceptance:** Lockfile(s) updated and contain a `matplotlib` entry.
`bazel build //mlody/cli/...` resolves without missing-dependency errors.

---

### 2. Create `mlody/cli/dag_gui.py`

Create the new file `mlody/cli/dag_gui.py` implementing the full rendering
pipeline as specified in the design (D-1 through D-9).

#### [x] 2.1 Write the module docstring

Add a module-level docstring describing the rendering contract: inputs, blocking
behaviour, the two-step figure-build/display split, and the extension points for
future interactivity (FR-009).

**Acceptance:** `pydoc mlody.cli.dag_gui` prints a meaningful description.

#### [x] 2.2 Define the colour palette constants

At module top level (outside any function), define the colour constants from the
Catppuccin-inspired dark palette (D-4):

- `_BG_COLOUR = "#1e1e2e"`
- `_NODE_FILL = "#313244"`
- `_NODE_BORDER = "#89b4fa"`
- `_TEXT_COLOUR = "#cdd6f4"`
- `_EDGE_COLOUR = "#a6e3a1"`
- `_TAIL_LABEL_COLOUR = "#f9e2af"`
- `_HEAD_LABEL_COLOUR = "#fab387"`

**Acceptance:** All constants are defined at module scope with `_` prefix.
basedpyright strict: no new errors.

#### [x] 2.3 Implement `_draw_nodes`

Implement the private function:

```python
def _draw_nodes(
    ax: Axes,
    pos: dict[str, tuple[float, float]],
    dag: networkx.MultiDiGraph,
) -> dict[str, FancyBboxPatch]:
```

For each node, draw a `FancyBboxPatch` with `boxstyle="round,pad=0.3"` filled
with `_NODE_FILL`, bordered with `_NODE_BORDER`, and labelled with
`dag.nodes[nid]["task"].name` in `_TEXT_COLOUR`. Return the mapping of node ID
to patch (for potential future use by interactive layers, FR-009).

**Acceptance:** Each node displays the bare `TaskNode.name`. basedpyright
strict: no new errors. All type hints present.

#### [x] 2.4 Implement `_draw_edges`

Implement the private function:

```python
def _draw_edges(
    ax: Axes,
    pos: dict[str, tuple[float, float]],
    dag: networkx.MultiDiGraph,
) -> None:
```

For each edge `(u, v, k, data)` in `dag.edges(data=True, keys=True)`:

- Draw a `FancyArrowPatch` from `pos[u]` to `pos[v]` using
  `connectionstyle="arc3,rad=R"` where `R` is computed per edge index `k`
  (formula from D-3: `R = 0.25 * (-1) ** k * ceil((k + 1) / 2)`, `R = 0.0` for
  `k = 0`).
- Place a tail label (`data["edge"].src_port`) at parameter `t = 0.18` along the
  straight line from source to destination, in `_TAIL_LABEL_COLOUR`, with a
  small perpendicular offset.
- Place a head label (`data["edge"].dst_path`) at `t = 0.82`, in
  `_HEAD_LABEL_COLOUR`, with a small perpendicular offset.
- Both labels are added via `ax.annotate`.

**Acceptance:** Parallel edges between the same node pair are drawn with
distinct curvature. Each edge carries a tail label and a head label in different
colours (NFR-U-001). Arrowheads are present and point toward the destination
(NFR-U-002). basedpyright strict: no new errors.

#### [x] 2.5 Implement `_build_figure`

Implement the private function:

```python
def _build_figure(
    dag: networkx.MultiDiGraph,
    title: str,
) -> tuple[Figure, Axes]:
```

Steps (per design D-2, D-5, D-8):

1. Copy `dag` into `layout_dag` (do not mutate the caller's graph).
2. Assign `layout_dag.nodes[nid]["layer"] = gen_idx` for each node using
   `networkx.topological_generations`.
3. Compute positions via
   `networkx.multipartite_layout(layout_dag, subset_key="layer", align="vertical")`.
4. Create a figure with `figsize` scaled to node count and generation depth.
5. Set figure and axes background to `_BG_COLOUR`.
6. Set `ax.set_title(title, ...)` with `_TEXT_COLOUR`.
7. Call `fig.canvas.manager.set_window_title(title)` wrapped in
   `try/except AttributeError` (for headless backends).
8. Call `_draw_nodes` and `_draw_edges`.
9. Call `ax.set_axis_off()` and `fig.tight_layout()`.
10. Return `(fig, ax)`.

**Acceptance:** Function returns a valid `(Figure, Axes)` pair. The original
`dag` argument has no `"layer"` key added to its node data after the call.
basedpyright strict: no new errors.

#### [x] 2.6 Implement `show_dag_gui`

Implement the public function with the exact signature and docstring from the
design:

```python
def show_dag_gui(
    dag: networkx.MultiDiGraph,
    title: str,
) -> None:
```

The function must:

- Import `matplotlib.pyplot`, `matplotlib.patches`, `matplotlib.figure`, and
  `matplotlib.axes` lazily inside the function body (NFR-M-003).
- Call `_build_figure(dag, title)`.
- Call `plt.show()` (blocking, FR-006).

**Acceptance:** `matplotlib` is not imported at module load time — confirmed by
inspecting the module's top-level imports. The function does not return while a
window is open. basedpyright strict: no new errors.

---

### 3. Modify `mlody/cli/dag_cmd.py`

#### [x] 3.1 Add the import of `show_dag_gui`

Add at module top level, in the local-imports block:

```python
from mlody.cli.dag_gui import show_dag_gui
```

**Acceptance:** `from mlody.cli.dag_gui import show_dag_gui` resolves without
error. basedpyright strict: no new errors. `matplotlib` is still not imported at
module load time (NFR-M-003 preserved, because `dag_gui.py` itself has no
top-level matplotlib import).

#### [x] 3.2 Add the `--gui` Click option to the `dag` command

Declare the flag immediately before the existing `@click.pass_context` decorator
(or after the existing argument decorator — keep decorator order consistent with
the file's style):

```python
@click.option(
    "--gui",
    is_flag=True,
    default=False,
    help="Open a GUI window showing the DAG diagram (blocking until closed).",
)
```

Add `gui: bool` to the `dag_cmd` function signature.

**Acceptance:** `mlody dag --help` shows `--gui` with the specified help string.
`mlody dag` (no flag) still exits 0 and produces the Rich table unchanged
(NFR-C-001).

#### [x] 3.3 Add the conditional `show_dag_gui` call

After the `_console.print(table)` call (the last line of the existing rendering
block), append:

```python
if gui:
    show_dag_gui(display_graph, title)
```

**Acceptance:** `mlody dag --gui` (with a mocked `show_dag_gui`) invokes the
function exactly once with the full DAG and `"Workspace DAG"`.
`mlody dag <label> --gui` passes the pruned subgraph and the correct title. The
Rich table is always printed first (US-005).

#### [x] 3.4 Update the command docstring

Extend the `dag` command's docstring (the `click` help text visible via
`--help`) to mention the `--gui` flag and describe its behaviour (blocking
window that shows the diagram).

**Acceptance:** `mlody dag --help` describes the `--gui` flag's effect in the
command-level help text.

---

### 4. Write GUI tests in `dag_cmd_test.py`

Extend the existing `mlody/cli/dag_cmd_test.py` with the tests specified in the
design's Test Specification section. All tests patch
`mlody.cli.dag_cmd.show_dag_gui` via `unittest.mock.patch` to prevent any real
window from opening. Use the existing `_invoke_dag` / workspace-fixture helpers
already present in the file.

#### [x] 4.1 `TestDagGuiFlag.test_gui_flag_invokes_renderer`

Pass `--gui`; assert the mock was called exactly once.

**Acceptance:** Test passes. Covers FR-001.

#### [x] 4.2 `TestDagGuiFlag.test_gui_renderer_receives_full_dag`

No label, `--gui`; assert the first positional argument to the mock (the graph)
contains all expected node IDs from the workspace fixture.

**Acceptance:** Test passes. Covers FR-002, KPI-001.

#### [x] 4.3 `TestDagGuiFlag.test_gui_renderer_receives_filtered_subgraph`

Label supplied, `--gui`; assert the graph passed to the mock contains only the
ancestor nodes of the given label and not unrelated nodes.

**Acceptance:** Test passes. Covers FR-002, KPI-002.

#### [x] 4.4 `TestDagGuiFlag.test_gui_table_printed_before_renderer`

Pass `--gui`; assert the final `result.output` contains the table title text,
and assert the mock was called (i.e., the table rendered first, then the
renderer was invoked).

**Acceptance:** Test passes. Covers US-005.

#### [x] 4.5 `TestDagGuiFlag.test_no_gui_flag_renderer_not_called`

No `--gui` flag; assert the mock was never called.

**Acceptance:** Test passes. Covers FR-001, US-007.

#### [x] 4.6 `TestDagGuiFlag.test_gui_exit_code_zero`

Pass `--gui`; mock returns normally; assert exit code is 0.

**Acceptance:** Test passes. Covers FR-007.

#### [x] 4.7 `TestDagGuiRegression.test_no_gui_output_unchanged`

Run `mlody dag` without `--gui`; assert the output is identical to the expected
pre-change output (same title, same node rows, no new lines introduced).

**Acceptance:** Test passes. Covers NFR-C-001, KPI-003.

---

### [x] 5. Update BUILD files via Gazelle

Run `bazel run :gazelle` from the repo root after adding `dag_gui.py` to
`mlody/cli/` so Gazelle detects the new source file and updates the `cli_lib`
`srcs` list.

Because `matplotlib` is imported lazily inside `show_dag_gui` (not at module top
level), Gazelle cannot infer the dependency from the import graph. Manually add
the dep with a `# keep` comment to the `cli_lib` target in
`mlody/cli/BUILD.bazel`:

```python
"@pip//matplotlib",  # keep — lazy import in dag_gui.show_dag_gui
```

**Acceptance:** `bazel build //mlody/cli/...` succeeds.
`bazel test //mlody/cli:dag_cmd_test` resolves and all tests pass.
`bazel build --config=lint //mlody/cli/...` reports no errors.

---

## Acceptance Criteria (change-level)

- [x] `mlody dag` (no flag) produces output byte-for-byte identical to
      pre-change behaviour.
- [x] `mlody dag --gui` (mocked in tests) invokes `show_dag_gui` with the full
      DAG and title `"Workspace DAG"`.
- [x] `mlody dag <label> --gui` (mocked in tests) invokes `show_dag_gui` with
      only the ancestor subgraph and the correct title.
- [x] `matplotlib` is not imported at module load time when `--gui` is not used.
- [x] All new tests pass: `bazel test //mlody/cli:dag_cmd_test`
- [x] No regressions: `bazel test //mlody/cli/...`
- [x] Lint clean: `bazel build --config=lint //mlody/cli/...`
- [x] basedpyright strict: zero new errors on `mlody/cli/dag_cmd.py` and
      `mlody/cli/dag_gui.py`
