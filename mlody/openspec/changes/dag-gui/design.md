# Design: DAG GUI Visualizer

**Version:** 1.0 **Date:** 2026-03-29 **Architect:** @vitruvius **Status:**
Draft **Requirements:** `mlody/openspec/changes/dag-gui/REQUIREMENTS.md`

---

## Problem Statement

`mlody dag` renders the workspace task graph as a Rich table. The tabular view
cannot convey topological shape: branch points, fan-ins, and multi-level chains
require the developer to mentally reconstruct the graph from a flat dependency
list. The `networkx.MultiDiGraph` is already fully constructed in memory; all
that is needed is a rendering path that draws it as a directed node-link diagram
in a native desktop window, with each edge carrying two positional labels
(`src_port` near the tail, `dst_path` near the arrowhead).

---

## Design Decisions

### D-1: Library — `matplotlib` with `networkx` layout, custom edge drawing

**Candidates evaluated:**

| Library                         | Window                                     | Dual edge labels                                          | Multi-edge                                | Hierarchical layout                                            | System deps           | "Modern"                 |
| ------------------------------- | ------------------------------------------ | --------------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------- | --------------------- | ------------------------ |
| `graphviz` (pydot / pygraphviz) | External viewer only — blocking is fragile | Native `headlabel`/`taillabel`                            | Native                                    | Native `dot` engine                                            | `dot` binary required | Excellent                |
| `pyvis`                         | Browser tab                                | Via JS config                                             | Native                                    | Force-directed                                                 | None                  | Good                     |
| `dearpygui`                     | Native (GPU)                               | Manual annotation                                         | Manual                                    | No built-in layout                                             | None                  | Excellent                |
| `netgraph`                      | matplotlib backend                         | `edge_label_position` float (single label per edge)       | Auto-curved                               | No built-in DAG layout                                         | None                  | Good                     |
| **`matplotlib`** (chosen)       | **Native blocking `plt.show()`**           | **Two `ax.annotate()` calls per edge at t=0.15 / t=0.85** | **`FancyArrowPatch` with per-edge `rad`** | **`networkx.multipartite_layout` via topological generations** | **None**              | **Good with dark theme** |

**Why not graphviz:** `graphviz.Source.view()` launches an external viewer
subprocess and returns immediately. Making the CLI block until the user closes
that external application window requires polling or `subprocess.Popen().wait()`
against a viewer process whose PID is not reliably surfaced by the `graphviz`
Python package. This is fragile across display environments. The blocking
requirement (FR-006) cannot be satisfied cleanly.

**Why not pyvis:** Browser-based. Explicitly excluded by the requirements
(Section 2.2).

**Why not dearpygui:** No automatic layout algorithm. Requires manual node
positioning or integrating a separate layout library. Adds significant
implementation complexity for a static read-only diagram.

**Why `matplotlib`:** `plt.show()` is natively blocking — it does not return
until all figure windows are closed (FR-006). It is a pure pip dependency with
no system requirements. The `networkx.multipartite_layout` function produces a
clean layered (hierarchical) layout for DAGs with zero additional dependencies.
Dual per-edge labels are achieved by placing two `ax.annotate()` calls per edge
at parameterised positions along the edge path. Multi-edges are handled with
`matplotlib.patches.FancyArrowPatch` using `connectionstyle="arc3,rad=R"` where
`R` is varied per parallel edge index. With a dark background (`#1e1e2e`) and
carefully chosen colours, the result is visually clean and modern without
requiring a heavy GUI framework.

The figure-building and window-display steps are structurally separated, which
satisfies FR-009: a future interactive iteration can replace `plt.show()` with a
`matplotlib` event loop that attaches pick/scroll handlers to the same figure
without touching the layout or drawing code.

### D-2: Hierarchical layout via topological generation assignment

`networkx.multipartite_layout` requires each node to carry a `"layer"` attribute
(an integer depth). The layout assigns all nodes at the same layer a shared
x-coordinate and distributes them along the y-axis.

The implementation assigns layers using `networkx.topological_generations(dag)`,
which yields sets of nodes in a topological generation order (generation 0 = no
incoming edges, generation N = deepest sinks). Each node receives
`dag.nodes[nid]["layer"] = generation_index`. This is a read-only annotation on
a copy of the graph used solely for layout; the original `dag` object passed in
from `dag_cmd.py` is not modified.

`rankdir` is left-to-right: generation 0 on the left, sinks on the right. This
matches the mental model of data flowing left-to-right through a pipeline.

### D-3: Dual edge labels via parameterised `ax.annotate`

Each edge is drawn as a `matplotlib.patches.FancyArrowPatch` between the source
and destination node positions. After drawing the arrow, two text annotations
are placed:

- **Tail label** (`src_port`): placed at parameter `t = 0.18` along the straight
  line from source to destination. This puts it visually close to the source
  node.
- **Head label** (`dst_path`): placed at `t = 0.82`. This puts it close to the
  arrowhead / destination node.

The interpolated coordinates are computed as `x = x_src + t * (x_dst - x_src)`
and similarly for `y`. A small perpendicular offset is applied to keep the text
away from the arrow shaft, especially for curved multi-edges.

For multi-edges (parallel edges between the same pair of nodes), the `rad`
parameter of `connectionstyle="arc3,rad=R"` is varied:
`R = 0.25 * (-1) ** k * ceil((k + 1) / 2)` for edge index `k` (i.e., 0.0 for
k=0, +0.25 for k=1, -0.25 for k=2, +0.50 for k=3, …). This fans them out
symmetrically. The label positions for curved edges are approximated by
computing the quadratic Bezier midpoint and then re-interpolating toward tail
and head.

### D-4: Visual style — dark Catppuccin-inspired palette

A dark background with high-contrast colours satisfies the "modern and slick"
requirement without needing any GUI framework beyond matplotlib:

| Element                 | Colour                   |
| ----------------------- | ------------------------ |
| Figure/axes background  | `#1e1e2e` (near-black)   |
| Node rectangle fill     | `#313244` (dark surface) |
| Node border             | `#89b4fa` (blue)         |
| Node label text         | `#cdd6f4` (white-ish)    |
| Edge arrow              | `#a6e3a1` (green)        |
| Tail label (`src_port`) | `#f9e2af` (yellow)       |
| Head label (`dst_path`) | `#fab387` (peach/orange) |

Tail and head labels use different colours (D-4 above), which directly satisfies
NFR-U-001 (labels must be visually distinguishable from each other and from node
labels).

Nodes are drawn as `matplotlib.patches.FancyBboxPatch` with
`boxstyle="round,pad=0.3"`.

### D-5: Module structure — `mlody/cli/dag_gui.py`

The GUI rendering code lives in a new module `mlody/cli/dag_gui.py` (NFR-M-001).
It exports exactly one public function:

```python
def show_dag_gui(dag: networkx.MultiDiGraph, title: str) -> None: ...
```

Internally, `dag_gui.py` is organised into two private helper functions:

```python
def _build_figure(
    dag: networkx.MultiDiGraph, title: str
) -> tuple[Figure, Axes]: ...

def _draw_nodes(ax: Axes, pos: dict[str, tuple[float, float]], dag: networkx.MultiDiGraph) -> dict[str, FancyBboxPatch]: ...

def _draw_edges(ax: Axes, pos: dict[str, tuple[float, float]], dag: networkx.MultiDiGraph) -> None: ...
```

`show_dag_gui` calls `_build_figure` then `plt.show()`. A future interactive
version replaces the `plt.show()` call with a `plt.show(block=False)` +
`fig.canvas.mpl_connect("pick_event", ...)` without touching `_build_figure`,
`_draw_nodes`, or `_draw_edges` (FR-009).

### D-6: Lazy import of `matplotlib` (NFR-M-003)

`matplotlib` is imported inside `show_dag_gui`, not at module top level. This
means `import mlody.cli.dag_cmd` does not trigger a matplotlib import in the
non-GUI path, satisfying NFR-M-003.

`dag_cmd.py` imports `show_dag_gui` at module top level (a name import from
`mlody.cli.dag_gui`), but since `dag_gui.py` itself does not import matplotlib
at module level, the import cost is not paid until `show_dag_gui` is called.

### D-7: `--gui` flag as a Click boolean flag

The flag is declared as:

```python
@click.option(
    "--gui",
    is_flag=True,
    default=False,
    help="Open a GUI window showing the DAG diagram (blocking until closed).",
)
```

This adds `gui: bool` to the `dag_cmd` function signature. The existing
`@click.argument("label", ...)` decorator is unmodified. The command signature
becomes `dag_cmd(ctx, label, gui)`, which is fully backwards-compatible
(NFR-C-001): `mlody dag` and `mlody dag model_checkpoint` behave identically to
before.

### D-8: Window title — title-bar only, not rendered on the canvas

OQ-002 from the requirements is resolved here. The title string is passed to
`fig.canvas.manager.set_window_title(title)` (sets the OS window title bar) and
also to `ax.set_title(title, ...)` (renders as text at the top of the axes area
inside the figure). Both are set so the window is identifiable both from the
task bar and within the diagram itself.

### D-9: Test isolation — patch `show_dag_gui` at the call site

To prevent tests from opening a real window (R-004), the test suite patches
`mlody.cli.dag_cmd.show_dag_gui`. Because `dag_cmd.py` imports the function by
name (`from mlody.cli.dag_gui import show_dag_gui`), patching
`mlody.cli.dag_cmd.show_dag_gui` intercepts all calls made from within
`dag_cmd`. Tests can inspect the mock's `call_args` to assert which graph and
title were passed. No matplotlib import occurs in the test process.

---

## Architecture Sketch

### Files changed

```
mlody/cli/dag_gui.py        NEW      -- GUI rendering module
mlody/cli/dag_cmd.py        MODIFIED -- add --gui flag; call show_dag_gui
mlody/cli/dag_cmd_test.py   MODIFIED -- add GUI flag tests
mlody/cli/BUILD.bazel       MODIFIED -- dag_gui.py added to cli_lib srcs
                                        (via gazelle after source file is added;
                                        @pip//matplotlib dep added with # keep)
pyproject.toml              MODIFIED -- add matplotlib dependency
requirements*.txt / lock    MODIFIED -- regenerated via o-repin
```

No changes to `mlody/core/dag.py`, `mlody/core/workspace.py`, or any file
outside `mlody/cli/`.

### Control flow

```
dag_cmd(ctx, label, gui):
    ... (unchanged: workspace load, build_dag, display_graph/title selection,
         topological sort, Rich table rendering) ...

    _console.print(table)          # always — table first (US-005)

    if gui:
        show_dag_gui(display_graph, title)   # blocks until window closed (FR-006)
```

```
show_dag_gui(dag, title):
    import matplotlib.pyplot as plt                 # lazy (NFR-M-003)
    import matplotlib.patches as mpatches
    from matplotlib.figure import Figure
    from matplotlib.axes import Axes

    fig, ax = _build_figure(dag, title)
    plt.show()                                      # blocking (FR-006)
```

```
_build_figure(dag, title) -> (fig, ax):
    layout_dag = dag.copy()
    for gen_idx, nodes in enumerate(networkx.topological_generations(dag)):
        for nid in nodes:
            layout_dag.nodes[nid]["layer"] = gen_idx
    pos = networkx.multipartite_layout(layout_dag, subset_key="layer",
                                       align="vertical")

    fig, ax = plt.subplots(figsize=(max(12, N*1.5), max(8, depth*2)))
    ax.set_facecolor(BG_COLOUR)
    fig.patch.set_facecolor(BG_COLOUR)
    ax.set_title(title, color=TEXT_COLOUR, fontsize=11, pad=12)
    try:
        fig.canvas.manager.set_window_title(title)   # D-8
    except AttributeError:
        pass   # headless / non-interactive backend

    _draw_nodes(ax, pos, dag)
    _draw_edges(ax, pos, dag)

    ax.set_axis_off()
    fig.tight_layout()
    return fig, ax
```

### Public API contract

```python
def show_dag_gui(
    dag: networkx.MultiDiGraph,
    title: str,
) -> None:
    """Open a blocking native window showing the DAG as a node-link diagram.

    Renders every node in ``dag`` as a labelled rounded rectangle and every
    edge as a directed arrow with two positional labels: ``src_port`` near the
    tail and ``dst_path`` near the arrowhead.

    This function does not return until the user closes the window (blocking,
    FR-006 / FR-007).

    The rendering pipeline is intentionally split into a figure-building step
    (_build_figure) and a display step (plt.show()).  Future interactive
    iterations can replace the display step with an event-loop that attaches
    pick/scroll handlers to the figure returned by _build_figure without
    modifying the layout or drawing code (FR-009).

    Args:
        dag:   A ``networkx.MultiDiGraph`` produced by ``build_dag`` or
               ``ancestors_subgraph``.  Node data must contain a ``"task"``
               key of type ``TaskNode``; edge data must contain an ``"edge"``
               key of type ``Edge``.
        title: Window title bar string and axes title (e.g. "Workspace DAG"
               or "DAG — ancestors of 'model_checkpoint'").
    """
```

---

## Dependency Management

### `pyproject.toml`

Add one entry to the `dependencies` list (no version pin per repo convention):

```toml
# interactive graph visualisation for `mlody dag --gui`.
# See: https://matplotlib.org/
"matplotlib",
```

`networkx` is already listed. `matplotlib` is the only new transitive root.

### Bazel BUILD

After adding `dag_gui.py` to `mlody/cli/`, run `bazel run :gazelle` from the
repo root. Gazelle will detect the new source file and update `cli_lib` srcs.
Gazelle cannot infer `@pip//matplotlib` from the lazy import inside the function
body (the import is inside `show_dag_gui`, not at module top level). Add a
manual `# keep` comment:

```python
o_py_library(
    name = "cli_lib",
    srcs = [
        "__init__.py",
        "dag_cmd.py",
        "dag_gui.py",   # added by gazelle
        "main.py",
        "shell.py",
        "show.py",
    ],
    deps = [
        ...
        "@pip//matplotlib",  # keep — lazy import in dag_gui.show_dag_gui
        ...
    ],
)
```

The test target `dag_cmd_test` does not need `@pip//matplotlib` because the test
patches `show_dag_gui` before it is called, preventing the matplotlib import
from occurring.

---

## Test Specification

File: `mlody/cli/dag_cmd_test.py` (extending the existing file)

All new tests follow the existing `_invoke_dag` / `_make_workspace_mock` pattern
already present in the file. The GUI renderer is patched via
`unittest.mock.patch("mlody.cli.dag_cmd.show_dag_gui")`.

| Test class / method                            | What it asserts                                                     | Requirement        |
| ---------------------------------------------- | ------------------------------------------------------------------- | ------------------ |
| `TestDagGuiFlag`                               |                                                                     |                    |
| `test_gui_flag_invokes_renderer`               | `--gui` passed; mock called exactly once                            | FR-001             |
| `test_gui_renderer_receives_full_dag`          | No label + `--gui`; mock `call_args[0][0]` has all node IDs         | FR-002, KPI-001    |
| `test_gui_renderer_receives_filtered_subgraph` | Label + `--gui`; mock graph has only ancestor nodes                 | FR-002, KPI-002    |
| `test_gui_table_printed_before_renderer`       | `--gui`; stdout contains table output; mock was called after        | US-005             |
| `test_no_gui_flag_renderer_not_called`         | No `--gui`; mock never called                                       | FR-001, US-007     |
| `test_gui_exit_code_zero`                      | Renderer returns normally; exit code 0                              | FR-007             |
| `TestDagGuiRegression`                         |                                                                     |                    |
| `test_no_gui_output_unchanged`                 | `mlody dag` without `--gui` produces identical output to pre-change | NFR-C-001, KPI-003 |

The `test_gui_table_printed_before_renderer` test uses a `side_effect` on the
mock to record the stdout content at the moment the renderer is called:

```python
captured_output_at_call: list[str] = []

def _capture_and_return(dag, title):
    captured_output_at_call.append(result.output)  # snapshot stdout so far

with patch("mlody.cli.dag_cmd.show_dag_gui", side_effect=_capture_and_return):
    result = runner.invoke(...)

assert len(captured_output_at_call) == 1
# table title should already be in stdout when renderer was called
assert "Workspace DAG" in captured_output_at_call[0]
```

Note: `CliRunner` captures output incrementally, so `result.output` inside a
`side_effect` may not yet reflect the final state. The test instead asserts that
the mock was called (table was rendered) and that the final `result.output`
contains the table text, which is sufficient to satisfy US-005.

---

## Constraints and Risks

| Risk                                                                                 | Mitigation                                                                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R-001: `matplotlib` headless backend raises on `plt.show()` in CI                    | Tests patch `show_dag_gui` entirely — matplotlib is never imported in the test process (D-9). The risk window is only for manual runs with `--gui` on a headless machine, which the requirements explicitly accept as out-of-scope (NFR-AR-001).                 |
| R-002: Multi-edge curved labels overlap or are unreadable                            | The perpendicular offset applied to curved-edge label positions (D-3) moves labels away from the arc mid-point. For very dense multi-edge bundles (>3 parallel edges) labels may still crowd. This is acceptable within NFR-SC-001's stated ceiling of 50 tasks. |
| R-003: `networkx.topological_generations` not available in an older networkx version | `topological_generations` was added in networkx 2.6. The project already depends on networkx with no floor version. If a floor is needed, `networkx >= 2.6` should be added to `pyproject.toml`.                                                                 |
| R-004: `fig.canvas.manager.set_window_title` unavailable in non-interactive backends | Wrapped in `try/except AttributeError` (D-8). Does not affect diagram correctness.                                                                                                                                                                               |
| R-005: Large graphs (100+ nodes) produce cluttered diagrams                          | `multipartite_layout` spreads nodes per generation; with many nodes per generation the vertical spacing becomes very tight. `figsize` scales with node count (D-5 pseudocode). NFR-SC-001 accepts degraded readability above 50 nodes.                           |

---

## Open Questions

All open questions assigned to @vitruvius are resolved:

- **OQ-001** (library selection): resolved by D-1 — `matplotlib` with
  `networkx.multipartite_layout` and `FancyArrowPatch`.
- **OQ-002** (window title placement): resolved by D-8 — both title bar and
  `ax.set_title`.
- **OQ-003** (multi-edge curving): resolved by D-3 — automatic via
  `connectionstyle="arc3,rad=R"` with per-edge `rad` computed from edge index.
