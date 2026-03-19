# SPEC: Entity Source Ranges

**Version:** 1.0 **Date:** 2026-03-19 **Architect:** @vitruvius **Status:**
Draft **Requirements:**
`mlody/openspec/changes/entity-source-ranges/REQUIREMENTS.md`

---

## Executive Summary

Every entity (`task`, `action`, `type`, `value`, `location`, `root`) registered
from a `.mlody` file will carry a `_source_range` field — a nested `Struct` with
sub-fields `filepath`, `start_line`, and `end_line` — after the workspace
`load()` completes. This enables LSP "go to definition", CLI source display, and
future debugger tooling to navigate from a live registered entity back to the
exact lines of its declaration without re-parsing.

The gap this change closes is narrow and well-contained: the existing
`extract_entity_ranges` function in `mlody/core/source_parser.py` already parses
the correct file and produces the correct return type, but it only inspects
top-level `expression_statement` nodes. Real `.mlody` files routinely inline
rule calls as arguments to other rule calls (e.g. `action(...)` passed directly
as `task(action=action(...), ...)`) or define helper functions that contain rule
calls. These nested sites are currently invisible to the extractor, so the
entities they register receive no `_source_range`.

The fix is to extend the traversal inside `extract_entity_ranges` to walk the
full AST rather than only the module's direct children. Everything else — the
`Evaluator._register` injection, the `Workspace` wiring, and the function
signature — is already correct and must not change.

A parallel fix in `Evaluator._register` corrects the `_source_range` sub-field
name from `file` to `filepath` to match the requirements.

**Requirements addressed:** FR-001 through FR-008, NFR-P-001, NFR-M-001/002,
NFR-R-001, NFR-C-001/002.

---

## Architecture Overview

```
mlody/core/source_parser.py   (MODIFIED — primary change)
  extract_entity_ranges()     full-AST walk replaces top-level-only walk
  _walk_node()                NEW private helper: recursive descent
  _process_call()             MODIFIED — add keyword-arg name= matching

common/python/starlarkish/evaluator/evaluator.py   (MODIFIED — field rename)
  Evaluator._register()       rename `file=` to `filepath=` in _source_range Struct

mlody/core/source_parser_test.py   (MODIFIED — new tests added)
```

No new files. No new modules. No new dependencies.

Data flow (unchanged externally):

```
Workspace.__init__
  --> Evaluator(line_range_extractor=extract_entity_ranges)

Evaluator._execute_file(path)
  --> reads source text
  --> _file_ranges[path] = extract_entity_ranges(path, source)
        --> tree_sitter.parse(source)
        --> _walk_node(root_node, result)   # full AST
              for each call node at any depth:
                _process_call(call_node) -> (kind, name) | None
                record (kind, name) -> (start_line, end_line)
              raises ValueError on duplicate (kind, name)
  --> exec(source, sandbox_globals)

Evaluator._register(kind, thing, ctx)
  --> sr = _file_ranges[ctx.file].get((kind, thing.name))
  --> if sr: thing = Struct(**thing.as_mapping(),
              _source_range=Struct(filepath=str(ctx.file),
                                   start_line=sr[0], end_line=sr[1]))
  --> store thing in self.tasks / self.actions / ...

Consumer:
  entity._source_range.filepath    # str — absolute path
  entity._source_range.start_line  # int — 1-based inclusive
  entity._source_range.end_line    # int — 1-based inclusive
```

---

## Technical Stack

- Python 3.13, basedpyright strict, ruff
- `tree-sitter` and `tree-sitter-starlark` (already declared in
  `//mlody/core:source_parser_lib` and `//mlody/core:core_lib` deps)
- Bazel rules: `o_py_library`, `o_py_test` from `//build/bzl:python.bzl`
- No new third-party dependencies

---

## Detailed Component Specifications

### 1. `mlody/core/source_parser.py` — primary change

#### 1.1 `_process_call` — add keyword-argument name matching

The current implementation extracts the entity name only from the **first
positional string argument** of a helper call:

```python
name = _first_positional_string(arg_list)
```

This misses the common `.mlody` pattern `task(name="train", ...)` where `name`
is passed as a keyword argument. The fix extends `_process_call` to try the
keyword argument form if the positional form yields `None`.

Extraction order (per FR-002):

1. First positional `string` child of the `argument_list` node.
2. If that is `None`: keyword argument child of type `keyword_argument` whose
   first child identifier text is `b"name"` and whose third child is a `string`
   node.

Both the positional and keyword paths already have helper functions available:
`_first_positional_string` and `_string_value`. A new private helper
`_keyword_arg_name` encapsulates step 2:

```python
def _keyword_arg_name(
    arg_list: tree_sitter.Node,
) -> str | None:
    """Return the string value of the `name=` keyword argument, or None."""
    for child in arg_list.children:
        if child.type == "keyword_argument" and len(child.children) >= 3:
            key_node = child.children[0]
            val_node = child.children[2]
            if key_node.type == "identifier" and key_node.text == b"name":
                return _string_value(val_node)
    return None
```

Updated `_process_call` helper-form path:

```python
name = _first_positional_string(arg_list)
if name is None:
    name = _keyword_arg_name(arg_list)
if name is not None:
    return (kind, name)
```

Note: the `builtins.register(...)` direct form already uses
`_extract_name_from_struct_call` which searches the struct's keyword args — it
does not need to change.

#### 1.2 `extract_entity_ranges` — full-AST walk

The current top-level loop is replaced by a recursive descent that visits every
`call` node in the tree regardless of depth.

**Line range recorded.** For top-level call sites, the range was previously
taken from the enclosing `expression_statement` node (which can span multiple
lines for a multi-line call). For nested calls, there is no enclosing
`expression_statement`; the range is taken from the `call` node itself.

The cleanest unified approach: use the `call` node's own start/end lines in all
cases. For top-level bare calls the `expression_statement` and `call` nodes span
identical lines (the `call` is the only child of the statement). For multi-line
top-level calls the `call` node spans exactly the same lines as the
`expression_statement`. This eliminates the need to special-case the wrapping
node.

The range is 1-based and inclusive:

```python
start_line = call_node.start_point[0] + 1   # row is 0-based
end_line   = call_node.end_point[0]   + 1
```

**Duplicate detection.** When a `(kind, name)` pair is encountered more than
once during the walk of a single file, a `ValueError` is raised with a message
identifying the file, kind, name, and both line ranges (FR-008). This error
propagates through `Evaluator._execute_file` and surfaces as a
`WorkspaceLoadError` entry.

**Error-node skipping (NFR-R-001).** Any node with `node.type == "ERROR"` or
`node.has_error` is skipped along with its entire subtree. This preserves the
existing contract that syntax errors in part of the file do not prevent valid
nodes elsewhere from being processed.

**Implementation — `_walk_node` helper:**

```python
def _walk_node(
    node: tree_sitter.Node,
    result: dict[tuple[str, str], tuple[int, int]],
    file_path: Path,
) -> None:
    """Recursively walk *node*, collecting rule-call ranges into *result*."""
    if node.type == "ERROR" or node.has_error:
        return   # skip broken subtree entirely

    if node.type == "call":
        entry = _process_call(node)
        if entry is not None:
            start_line = node.start_point[0] + 1
            end_line   = node.end_point[0]   + 1
            if entry in result:
                existing = result[entry]
                raise ValueError(
                    f"Duplicate ({entry[0]!r}, {entry[1]!r}) in {file_path}: "
                    f"first at lines {existing[0]}-{existing[1]}, "
                    f"second at lines {start_line}-{end_line}"
                )
            result[entry] = (start_line, end_line)

    for child in node.children:
        _walk_node(child, result, file_path)
```

Updated `extract_entity_ranges`:

```python
def extract_entity_ranges(
    file_path: Path, source: str
) -> dict[tuple[str, str], tuple[int, int]]:
    tree = _parser.parse(source.encode())
    result: dict[tuple[str, str], tuple[int, int]] = {}
    _walk_node(tree.root_node, result, file_path)
    return result
```

Note: the parameter was previously named `_file_path` (underscore prefix,
indicating it was unused). It is renamed to `file_path` because it is now used
in duplicate-detection error messages. The public signature type is unchanged.

#### 1.3 Module docstring update

The module docstring currently describes two call forms including
`builtins.register(...)`. The docstring is updated to accurately describe the
full-AST walk and the supported call patterns.

---

### 2. `common/python/starlarkish/evaluator/evaluator.py` — field rename

In `Evaluator._register`, the `_source_range` Struct is currently constructed
with `file=str(ctx.file)`. Per FR-003 and the requirements, the sub-field must
be named `filepath`. One line changes:

Before:

```python
thing = Struct(**thing.as_mapping(), _source_range=Struct(
    file=str(ctx.file), start_line=sr[0], end_line=sr[1]
))
```

After:

```python
thing = Struct(**thing.as_mapping(), _source_range=Struct(
    filepath=str(ctx.file), start_line=sr[0], end_line=sr[1]
))
```

This is the only change to `evaluator.py`. The `_file_ranges` structure, the
`_line_range_extractor` protocol, and all other logic are already correct.

---

### 3. `mlody/core/source_parser_test.py` — new test cases

All existing tests must pass without modification (NFR-C-001). The following new
test functions are added.

#### Naming pattern

New tests follow the existing `test_<scenario>` convention, all calling
`extract_entity_ranges(_fake_path(), source)`.

#### New test cases

| Test function                        | Input pattern                                             | Expected result                                  |
| ------------------------------------ | --------------------------------------------------------- | ------------------------------------------------ |
| `test_nested_call_as_keyword_arg`    | `task(name="t", action=action(name="a", ...))`            | both `("task","t")` and `("action","a")` present |
| `test_nested_call_as_positional_arg` | `task("t", action("a", ...))`                             | both `("task","t")` and `("action","a")` present |
| `test_call_inside_function_body`     | `def mk():\n    task("t", ...)`                           | `("task","t")` present                           |
| `test_keyword_name_arg`              | `task(name="train", inputs=[])`                           | `("task","train")` present                       |
| `test_computed_name_no_entry`        | `task(get_name(), ...)`                                   | empty dict                                       |
| `test_multiple_nested_entities`      | two `action(name=...)` inside one `task(...)`             | all three entities present                       |
| `test_duplicate_kind_name_raises`    | `task("train", ...)` at top level AND inside a helper def | `ValueError` raised                              |
| `test_keyword_name_arg_line_numbers` | multiline `task(name="train", ...)`                       | correct `(start, end)` tuple                     |

The duplicate-detection test uses `pytest.raises(ValueError)` and asserts the
message contains the kind, name, and file path.

---

## Data Architecture

No persistent storage. All data is in-memory for the lifetime of the `Evaluator`
instance.

### `_file_ranges`

```
Evaluator._file_ranges: dict[Path, dict[tuple[str, str], tuple[int, int]]]
```

Populated once per file during `_execute_file`, before `exec()` is called. Keyed
by absolute `Path`. Inner dict maps `(kind, name)` to `(start_line, end_line)`
(1-based inclusive integers).

### `_source_range` on registered entity Structs

```
entity._source_range: Struct(
    filepath: str,    # absolute path string
    start_line: int,  # 1-based inclusive
    end_line: int,    # 1-based inclusive
)
```

Access: `entity._source_range.filepath`, `entity._source_range.start_line`,
`entity._source_range.end_line`. The three sub-fields are not spread as flat
fields on the entity (i.e. `entity._filepath` does not exist).

Absent when: the entity was registered from Python (init-time sentinels), the
entity name was computed at runtime, or the source file had a syntax error that
prevented the call site from being recognised.

---

## Security and Authentication

Not applicable. This is a pure in-memory parsing feature with no I/O beyond
reading already-opened `.mlody` files.

---

## Implementation Plan

### Phase 1 — `source_parser.py` changes (no evaluator dependency)

1. Add `_keyword_arg_name` helper.
2. Update `_process_call` to call `_keyword_arg_name` as fallback.
3. Add `_walk_node` recursive helper.
4. Replace the body of `extract_entity_ranges` with the `_walk_node` call.
5. Rename `_file_path` parameter to `file_path`.
6. Update module docstring.

### Phase 2 — `evaluator.py` field rename

7. Rename `file=` to `filepath=` in the `_source_range` Struct construction
   inside `Evaluator._register`.

### Phase 3 — tests

8. Add new test functions to `source_parser_test.py`.
9. Run `bazel test //mlody/core:source_parser_test` — all tests green.
10. Run `bazel test //mlody/core:workspace_test` — integration green.
11. Run `bazel build --config=lint //mlody/core:source_parser_test` — no lint
    errors.

### Dependency order

Phase 1 and Phase 2 are independent of each other. Phase 3 depends on both.

### Estimated complexity

| Item                                  | Complexity                    |
| ------------------------------------- | ----------------------------- |
| Phase 1 — `source_parser.py`          | Small (< 60 lines net change) |
| Phase 2 — `evaluator.py` field rename | Trivial (1 line)              |
| Phase 3 — new tests                   | Small (< 100 lines)           |

### BUILD.bazel changes

No new Bazel targets are required. The existing `source_parser_lib` target
already declares `@pip//tree_sitter` and `@pip//tree_sitter_starlark`. The
existing `source_parser_test` target already depends on `:core_lib` which
transitively pulls in tree-sitter. No `bazel run :gazelle` invocation is needed
unless files are added (none are).

---

## Testing Strategy

### Unit tests — `bazel test //mlody/core:source_parser_test`

All tests call `extract_entity_ranges(_fake_path(), source)` with inline source
strings. No filesystem access required; `_FAKE_PATH` is a constant `Path` used
only for error message content in duplicate-detection tests.

**Existing tests (must stay green, no modification):**

- `test_direct_register_single`
- `test_direct_register_multiline`
- `test_helper_call_root`
- `test_helper_call_task`
- `test_helper_call_action`
- `test_multiple_entities`
- `test_computed_name_skipped`
- `test_non_registration_calls_ignored`
- `test_assignment_statement_ignored`
- `test_error_nodes_skipped`
- `test_line_numbers_across_file`

**New tests (see Section 3 above for full list).**

### Integration test — `bazel test //mlody/core:workspace_test`

Exercises the full `Workspace.load()` path using `pyfakefs` in-memory
filesystem. Verifies that:

- Entities registered via top-level rule calls have `_source_range` set.
- Entities registered via nested inline calls have `_source_range` set.
- `entity._source_range.filepath` is the absolute path string of the `.mlody`
  file.
- `entity._source_range.start_line >= 1` and
  `entity._source_range.end_line >= entity._source_range.start_line`.

The workspace test is already present; the implementor should verify it passes
after the changes and add targeted assertions if the existing test does not
cover the `_source_range` field.

### Type checking

```
bazel build --config=lint //mlody/core:source_parser_lib
```

basedpyright strict must report zero errors on `mlody/core/source_parser.py`
after the change.

---

## Non-Functional Requirements

### Performance (NFR-P-001)

The `_walk_node` recursive walk visits every node in the tree exactly once
(linear in the number of AST nodes). tree-sitter parse time is O(n) in source
length. The total cost is O(n) — no quadratic behaviour. For a 2000-line
`.mlody` file on a modern development machine, tree-sitter parse + walk
completes well under 50 ms.

### Maintainability (NFR-M-002)

`_HELPER_KINDS` remains the single authoritative set of recognised rule function
names. Adding a new rule function requires only a new entry in that dict — no
other code paths change.

### Reliability (NFR-R-001)

Nodes with `type == "ERROR"` or `has_error == True` short-circuit the recursion
for their entire subtree. The duplicate `(kind, name)` `ValueError` (FR-008) is
explicitly exempt from this silent-skip rule and propagates as a loading error.

---

## Risks and Mitigation

| Risk                                                                      | Mitigation                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R-001: Duplicate `(kind, name)` in real `.mlody` files triggers new error | Error message identifies file, kind, name, and both line ranges clearly. This is correct behaviour — the file is malformed.                                                                                                               |
| R-002: tree-sitter-starlark AST shape changes between versions            | Add a comment in `source_parser.py` citing the verified node types against tree-sitter-starlark 1.3.0 (already documented in `mlody/lsp/CLAUDE.md`).                                                                                      |
| R-003: `_walk_node` visits `call` nodes inside `load()` calls             | `load` is not in `_HELPER_KINDS`; `_process_call` returns `None` for it immediately. No false positives.                                                                                                                                  |
| R-004: `builtins.register(...)` calls in user code become visible         | The direct-form path in `_process_call` handles these correctly as before. The only difference is that nested `builtins.register(...)` calls (e.g. inside a helper def) are now also matched. This is intentional and correct per FR-007. |

---

## Divergences from Draft Implementation

The branch contains an uncommitted partial implementation that diverges from the
requirements in several ways. The spec-compliant implementation must differ as
follows:

1. **`_source_range` field name:** The draft uses `file=`. The spec requires
   `filepath=` (FR-003, REQUIREMENTS.md §6.1 FR-003, §9.1).

2. **Keyword-argument name matching:** The draft `_process_call` uses only
   `_first_positional_string`. The spec requires falling back to
   `_keyword_arg_name` (FR-002). The assumption in §2.3 of REQUIREMENTS.md
   confirms `name=` keyword form is the primary pattern in real `.mlody` files.

3. **Duplicate detection:** The draft performs a last-write-wins insert. The
   spec requires raising a `ValueError` on the second encounter (FR-008).

4. **`file_path` parameter use:** The draft keeps the `_file_path` name
   (unused). The spec uses it in duplicate-detection error messages.

The draft's recursive walk structure and `_walk_node` concept are directionally
correct and can be retained as the foundation.

---

## Future Considerations

- Column offsets for `_source_range` (currently out of scope per §2.2) — the
  tree-sitter nodes already carry `start_point.column` / `end_point.column`;
  adding columns later requires only extending the `Struct` construction.
- LSP "go to definition" consumer (separate work item): reads
  `entity._source_range.filepath` + `start_line`/`end_line` to produce an
  `lsprotocol.types.Location`.
- CLI `mlody show --source` consumer (separate work item): prints the source
  lines referenced by `_source_range`.
