# Requirements Document: Entity Source Ranges

**Version:** 1.1 **Date:** 2026-03-19 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

The mlody framework evaluates `.mlody` pipeline definition files and registers
named entities (tasks, actions, types, values, locations, roots) into the
`Evaluator`'s internal state. Currently, registered entity `Struct` objects
carry no information about where in the source file the rule call that produced
them was written. This makes it impossible for tooling such as the LSP server,
the CLI `show` command, or future debuggers to link a live entity back to its
origin in the user's source code.

This change introduces a `_source_range` field on every registered entity
`Struct`. The field is **a single nested `Struct` value** — i.e. one field whose
value is itself a `Struct` — with named sub-fields: `filepath` (path relative to
the monorepo root of the `.mlody` file as a string), `start_line` (1-based
inclusive start line, int), and `end_line` (1-based inclusive end line, int).
The correct access pattern is `entity._source_range.filepath`,
`entity._source_range.start_line`, `entity._source_range.end_line`. The three
sub-fields are **not** spread as flat fields directly on the entity (i.e. there
are no `entity._filepath`, `entity._start_line`, or `entity._end_line` fields).
For entities whose name cannot be statically determined (e.g. dynamically
computed names), `_source_range` is absent/`None`. For entities registered at
evaluator init time with no source file (e.g. primitive type sentinels),
`_source_range` is also absent/`None`. The field is populated by the existing
`_line_range_extractor` hook in the `Evaluator`, which calls
`mlody.core.source_parser.extract_entity_ranges` after each file is `exec()`'d.

The current `source_parser.extract_entity_ranges` implementation only walks
top-level `expression_statement` nodes. The primary functional gap this change
addresses is extending it to also find rule calls that are **nested inside other
calls or helper functions** — the common pattern where `action`, `type`, or
`location` structs are passed inline as arguments to `task(...)`.

---

## 2. Project Scope

### 2.1 In Scope

- Extend `mlody.core.source_parser.extract_entity_ranges` to find rule calls at
  any nesting depth in the AST (not just top-level expression statements).
- Add `keyword_argument` matching so that `task(name="foo", ...)` and
  `task("foo", ...)` are both recognised as calls with name `"foo"`.
- Ensure the `Evaluator._register` mechanism already in place (injecting
  `_source_range` onto the entity `Struct`) continues to work correctly for the
  full range of call sites surfaced by the extended parser.
- Unit tests for all new call-site patterns.
- Update or add Bazel `BUILD.bazel` targets as needed.

### 2.2 Out of Scope

- Column offsets — `_source_range` carries only line numbers.
- Cross-file range lookup — the range always refers to the file being
  `exec()`'d; tracking ranges that span `load()` boundaries is not required.
- Full removal or replacement of the existing top-level walk; the extension must
  be additive and backward-compatible.
- LSP or CLI changes that consume `_source_range` — those are separate work
  items.
- Serialisation of `_source_range` to disk or over the wire.
- Entities loaded from Python (not from `.mlody` files) — these are internal
  framework sentinel values (e.g. the primitive type sentinels seeded in
  `Evaluator.__init__`) and do not require source ranges; `_source_range` will
  be absent/`None` for these.
- Direct calls to `builtins.register()` from user `.mlody` code — the
  attribution mechanism targets rule function call sites (`task(...)`,
  `action(...)`, etc.), not the internal `builtins.register(...)` call that
  those functions delegate to.

### 2.3 Assumptions

- Tree-sitter and `tree-sitter-starlark` are already available as pip
  dependencies; no new third-party packages are needed.
- The rule call's `name` argument is always a keyword argument `name="..."`. No
  other positional conventions exist.
- Two files registering the same `(kind, name)` pair are distinct files; the
  lookup is per-file, so there is no cross-file ambiguity.
- Entities with dynamically computed names (e.g. `task(get_name(), ...)`)
  legitimately cannot be matched; `_source_range = None` for these is acceptable
  and not an error condition.
- Conditionally registered entities (registered only at runtime depending on
  evaluated logic) are fine — the lookup is driven by the actual registered set
  after `exec()`, so only entities that were actually registered need to be
  matched.
- The `_source_range` field name starts with an underscore as a convention
  indicating it is framework-injected metadata, not user-defined data.
- Primitive type sentinels registered during `Evaluator.__init__` have no source
  file and will correctly have `_source_range` absent/`None`; this is the
  expected and correct behaviour.

### 2.4 Constraints

- Python 3.13, strict basedpyright type checking, ruff formatting.
- Bazel rules must use `o_py_library` / `o_py_test` from
  `//build/bzl:python.bzl`.
- The `extract_entity_ranges` function signature
  `(file_path: Path, source: str) -> dict[tuple[str, str], tuple[int, int]]`
  must not change; the function must remain a pure function with no side
  effects.
- The `Evaluator._line_range_extractor` protocol type
  `Callable[[Path, str], dict[tuple[str, str], tuple[int, int]]]` must not
  change.
- No new third-party dependencies beyond those already declared.
- If the same `(kind, name)` pair appears at multiple nesting depths within a
  single file's AST, this is a loading error — it must be raised during loading,
  not silently resolved by a last/first-write policy.

---

## 3. Stakeholders

| Role                   | Name/Group     | Responsibilities                           |
| ---------------------- | -------------- | ------------------------------------------ |
| mlody framework author | mav            | Final acceptance, design authority         |
| Requirements Analyst   | @socrates      | Requirements elicitation and documentation |
| Solution Architect     | @vitruvious    | System design and SPEC.md                  |
| Implementation         | @vulcan-python | Python coding                              |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Enable LSP, CLI, and future debugger tooling to navigate from a
  live registered entity back to the exact lines in the `.mlody` source file
  where it was declared.
- **BR-002:** Make source attribution available for all rule-call patterns that
  appear in real `.mlody` files — including inlined calls and calls inside
  helper functions — not only top-level bare statements.

### 4.2 Success Metrics

- **KPI-001:** Every entity registered from a `.mlody` file whose `name`
  argument is a string literal has a non-`None` `_source_range` after the
  workspace `load()` completes.
- **KPI-002:** All existing `source_parser_test.py` tests continue to pass
  without modification.
- **KPI-003:** New tests covering nested call patterns pass under
  `bazel test //mlody/core:source_parser_test`.
- **KPI-004:** basedpyright reports zero errors on `mlody/core/source_parser.py`
  in strict mode.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: mlody framework developer / tooling author**

- Needs to know the file and line range of a registered entity in order to
  implement "go to definition" in the LSP or similar features.
- Pain point: currently there is no link between a live `Struct` value and its
  origin in source.
- Needs `entity._source_range.filepath`, `entity._source_range.start_line`, and
  `entity._source_range.end_line` to be reliably populated. `_source_range` is a
  single nested `Struct` value on the entity — the three sub-fields are accessed
  through it, not as flat fields on the entity itself.

### 5.2 User Stories

**Epic 1: Source attribution on registered entities**

- **US-001:** As a tooling author, I want every entity `Struct` registered from
  a `.mlody` file to carry a `_source_range` field so that I can link it back to
  source without re-parsing.
  - Acceptance Criteria: Given a `Workspace` that has called `load()`, when I
    look up any entity registered via a rule call with a literal string name,
    then `entity._source_range` is a single nested `Struct` value with
    sub-fields `filepath`, `start_line`, and `end_line` (accessed as
    `entity._source_range.filepath`, etc. — not as flat fields on the entity),
    and the line range encompasses the full rule call expression.
  - Priority: High (Must Have)

- **US-002:** As a tooling author, I want rule calls nested inside other rule
  calls (e.g. `action(...)` inlined inside `task(...)`) to receive correct
  source ranges, not be silently dropped.
  - Acceptance Criteria: Given a `.mlody` file containing
    `task(name="t", action=action(name="a", ...), ...)`, when the workspace
    loads, then both `task "t"` and `action "a"` have non-`None` `_source_range`
    values pointing to lines within that file.
  - Priority: High (Must Have)

- **US-003:** As a tooling author, I want entities with dynamically computed
  names to gracefully receive `_source_range = None` rather than raising an
  error.
  - Acceptance Criteria: Given `task(get_name(), ...)`, when the workspace
    loads, then the registered entity has `_source_range = None` and no
    exception is raised.
  - Priority: High (Must Have)

---

## 6. Functional Requirements

### 6.1 Source Range Extraction

**FR-001: Full-AST traversal for rule calls**

- Description: `extract_entity_ranges` must walk the entire parsed AST of the
  source file, not only top-level `expression_statement` nodes. Any `call` node
  anywhere in the tree whose function name is one of the recognised rule
  functions must be inspected for a name argument.
- The recognised rule function names are: `task`, `action`, `type`, `value`,
  `location`, `root`.
- The range recorded for each match is the 1-based start and end line of the
  enclosing `call` node (or `expression_statement` if the call is at top level)
  — whichever spans the full expression.
- If the same `(kind, name)` pair is encountered more than once in the AST of a
  single file (at any depth), this is a loading error and must be raised.
- Priority: Must Have
- Dependencies: tree-sitter-starlark parse tree structure

**FR-002: Name argument matching — positional and keyword**

- Description: For each recognised rule call, the name is extracted by:
  1. First positional string literal argument: `task("foo", ...)`
  2. Keyword argument `name="foo"`: `task(name="foo", ...)`
  - Both forms must be handled. If both are present (which is a user error but
    syntactically valid), positional takes precedence.
  - If neither yields a string literal, the call is silently skipped
    (`_source_range` will be `None` for the registered entity).
- Priority: Must Have

**FR-003: `_source_range` field on registered entity Structs**

- Description: When `Evaluator._register` is called and a range is found in
  `_file_ranges` for `(kind, name)`, the entity `Struct` is rebuilt with an
  additional `_source_range` field. The field value is **a single nested
  `Struct`** — one field whose value is itself a `Struct` instance — with the
  following sub-fields:
  - `filepath: str` — path relative to the monorepo root of the `.mlody` file as
    a string
  - `start_line: int` — 1-based inclusive start line
  - `end_line: int` — 1-based inclusive end line
- The correct access pattern is `entity._source_range.filepath`,
  `entity._source_range.start_line`, `entity._source_range.end_line`. The
  sub-fields must **not** be spread as three separate flat fields on the entity
  (e.g. `entity._filepath` is incorrect).
- Concretely:
  `entity._source_range == Struct(filepath="...", start_line=1, end_line=5)`
- This behaviour is already implemented in `Evaluator._register`; the
  `_source_range` Struct field names must be updated to match (`filepath`
  instead of `file` if they differ).
- Priority: Must Have

**FR-004: `_source_range = None` for unmatched entities**

- Description: If `extract_entity_ranges` does not return a range for a given
  `(kind, name)` pair (because the name is computed, the call is not recognised,
  or the file has a syntax error), the `Evaluator._register` code path leaves
  `_source_range` absent on the entity `Struct`. Callers must treat a missing
  `_source_range` attribute as equivalent to `None`.
- Entities registered during `Evaluator.__init__` (e.g. primitive type
  sentinels) have no source file and are correctly left without `_source_range`;
  this is intended behaviour.
- [Note: the current evaluator code only adds `_source_range` when `sr is not
  None`; entities without a match are left without the field. This is the
  intended behaviour.]
- Priority: Must Have

**FR-005: No cross-file range lookup**

- Description: The range is always looked up from `_file_ranges[ctx.file]` — the
  file currently on the `_eval_stack`. Rule function definitions (e.g. `task()`
  defined in `mlody/common/task.mlody`) are in different files from their call
  sites; only the call site file is searched.
- This is already correct in the current evaluator; the requirement documents it
  explicitly to prevent regression.
- Priority: Must Have

**FR-006: No change to `extract_entity_ranges` signature or return type**

- Description: The function signature and return type
  `dict[tuple[str, str], tuple[int, int]]` must remain unchanged. The extension
  is purely internal to the function's implementation.
- Priority: Must Have

### 6.2 Recognised Rule Functions

**FR-007: Recognised rule function set**

- Description: The set of rule function names whose calls are matched is fixed
  at: `{"task", "action", "type", "value", "location", "root"}`. This matches
  the existing `_HELPER_KINDS` mapping in `source_parser.py`.
- Direct calls to `builtins.register(...)` from user code are out of scope for
  this change. The attribution mechanism works by searching the AST of the file
  being loaded for rule function call sites; `builtins.register(...)` is the
  internal implementation detail of those rule functions and is not a target.
- Priority: Must Have

### 6.3 Duplicate (kind, name) Error Handling

**FR-008: Duplicate (kind, name) at multiple AST depths is a loading error**

- Description: If `extract_entity_ranges` encounters the same `(kind, name)`
  pair at more than one location in the AST of a single file (e.g.
  `task("train", ...)` appears both at the top level and inside a helper
  function in the same file), it must raise a loading error. This is not a
  last/first-write ambiguity to be silently resolved — it indicates a malformed
  pipeline file.
- The error must be raised during the `load()` phase, before the workspace
  finishes loading.
- Priority: Must Have

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-P-001:** `extract_entity_ranges` must complete in under 50 ms for any
  `.mlody` file up to 2 000 lines on a modern development machine. Tree-sitter
  parsing is already fast; the full-AST walk must not introduce quadratic
  behaviour.

### 7.2 Maintainability Requirements

- **NFR-M-001:** All public functions must have type hints satisfying
  basedpyright strict mode.
- **NFR-M-002:** The `_HELPER_KINDS` mapping in `source_parser.py` remains the
  single authoritative list of recognised rule function names. Adding a new rule
  function requires only adding an entry there.

### 7.3 Reliability Requirements

- **NFR-R-001:** A syntax error or unrecognised node type anywhere in the AST
  must not propagate as an exception from `extract_entity_ranges`. Nodes with
  errors are silently skipped; valid nodes in the same file continue to be
  processed. (This is already the contract for top-level nodes; it must hold for
  nested nodes too.) The duplicate `(kind, name)` loading error (FR-008) is
  explicitly exempt from this silent-skip rule — it must be raised.

### 7.4 Compatibility Requirements

- **NFR-C-001:** All currently passing tests in `source_parser_test.py` must
  continue to pass without modification.
- **NFR-C-002:** The `Evaluator` constructor's `line_range_extractor` parameter
  type must not change.

---

## 8. Data Requirements

### 8.1 Data Entities

| Entity                                   | Location                     | Description                                                                                                                                                                              |
| ---------------------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_source_range` field on entity `Struct` | Set by `Evaluator._register` | Framework-injected metadata; a **single nested `Struct`** value: `Struct(filepath="...", start_line=N, end_line=M)`, accessed as `entity._source_range.filepath` etc. — or absent/`None` |
| `_file_ranges`                           | `Evaluator` instance state   | `dict[Path, dict[tuple[str,str], tuple[int,int]]]`                                                                                                                                       |
| `extract_entity_ranges` return value     | `mlody.core.source_parser`   | `dict[tuple[str,str], tuple[int,int]]` — (kind,name) → (start,end)                                                                                                                       |

### 8.2 Data Invariants

- `start_line >= 1` and `end_line >= start_line` for all stored ranges.
- `filepath` in `_source_range` is always an absolute path string.
- The `_source_range` field name is reserved for framework use; user `.mlody`
  code must not set a field with this name on any `struct()`.
- Primitive type sentinels registered at evaluator init time have no source
  file; their `_source_range` is correctly absent/`None`.

### 8.3 Data Retention

Not applicable — all data is in-memory for the lifetime of the `Evaluator`
instance.

---

## 9. Integration Requirements

### 9.1 Internal Interfaces

The change touches two existing modules:

| Module                        | Change                                                                               |
| ----------------------------- | ------------------------------------------------------------------------------------ |
| `mlody/core/source_parser.py` | Extend `extract_entity_ranges` to walk the full AST                                  |
| `mlody/core/workspace.py`     | No change required; already passes `extract_entity_ranges` as `line_range_extractor` |
| `common/.../evaluator.py`     | Update `_source_range` Struct field name to `filepath` if currently named `file`     |

### 9.2 External Dependencies

No new external dependencies. The existing `tree-sitter` and
`tree-sitter-starlark` pip packages are sufficient.

---

## 10. Testing & Quality Assurance Requirements

### 10.1 Testing Scope

All tests live in `mlody/core/source_parser_test.py` (existing file, extended).
Test file name convention: `*_test.py`.

Required new test coverage:

| Test area                                                 | Example input pattern                                      |
| --------------------------------------------------------- | ---------------------------------------------------------- |
| Rule call inlined as keyword arg to another rule call     | `task(name="t", action=action(name="a", ...))`             |
| Rule call inlined as positional arg to another rule call  | `task("t", action("a", ...))`                              |
| Rule call inside a helper function body                   | `def mk(): task("t", ...)`                                 |
| Rule call with `name=` keyword arg (not positional)       | `task(name="train", inputs=[], ...)`                       |
| Computed name still yields no entry                       | `task(get_name(), ...)` → `{}`                             |
| Multiple nested entities in the same file                 | Two inlined `action(name=...)` inside a single `task(...)` |
| Duplicate `(kind, name)` at multiple depths raises error  | `task("train", ...)` at top level and inside a helper      |
| Existing top-level patterns still pass (regression guard) | All tests in current `source_parser_test.py`               |

### 10.2 Acceptance Criteria

- `bazel test //mlody/core:source_parser_test` passes with all new and existing
  tests green.
- `bazel build --config=lint //mlody/core:source_parser_test` reports no errors.
- basedpyright strict reports no errors on `mlody/core/source_parser.py`.
- Integration: `bazel test //mlody/core:workspace_test` passes (ensures the
  updated extractor works end-to-end through the `Workspace` load path).

---

## 11. Infrastructure & Deployment Requirements

### 11.1 Hosting & Environment

Pure Python library; deployed as part of the mlody Bazel target graph. No
infrastructure changes required.

### 11.2 Deployment

- `mlody/core/BUILD.bazel` must be updated if new source files are added (run
  `bazel run :gazelle` to regenerate).
- The existing `o_py_library` target for `source_parser.py` must declare
  `tree-sitter` and `tree-sitter-starlark` in its `deps` if not already present.

---

## 12. Risks & Mitigation Strategies

| Risk ID | Description                                                                                               | Impact | Probability | Mitigation                                                                                                 | Owner          |
| ------- | --------------------------------------------------------------------------------------------------------- | ------ | ----------- | ---------------------------------------------------------------------------------------------------------- | -------------- |
| R-001   | Full-AST walk encounters duplicate `(kind, name)` entries — now a loading error per FR-008                | Medium | Low         | Raise a clear error with file, kind, name, and both line numbers to aid debugging                          | @vulcan-python |
| R-002   | tree-sitter-starlark AST shape differs between versions, breaking node type assumptions                   | Medium | Low         | Pin tree-sitter-starlark version; add a version assertion or comment in `source_parser.py`                 | @vulcan-python |
| R-003   | Rule calls inside helper functions assign ranges from the wrong file (definition file vs. call site file) | High   | Low         | The extractor only parses the file being `exec()`'d; function definitions in other files are not re-parsed | mav            |
| R-004   | Performance regression from full-AST walk on very large `.mlody` files                                    | Low    | Low         | Benchmark against NFR-P-001; tree-sitter is incremental and fast by design                                 | @vulcan-python |

---

## 13. Dependencies

| Dependency                                  | Type            | Status    | Impact if Delayed                         | Owner |
| ------------------------------------------- | --------------- | --------- | ----------------------------------------- | ----- |
| `tree-sitter` / `tree-sitter-starlark`      | Pip dependency  | Available | Cannot parse AST; entire feature blocked  | —     |
| `Evaluator._register` `_source_range` logic | Existing code   | Shipped   | None; already handles the range injection | —     |
| `mlody/core/source_parser.py`               | Existing module | Shipped   | Starting point for the extension          | —     |

---

## 14. Open Questions & Action Items

| ID     | Question/Action                                                                                                                                                     | Owner | Target Date | Status |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ----------- | ------ |
| OQ-001 | Duplicate `(kind, name)` at multiple AST depths in a single file — resolved: this is a loading error to be raised during `load()`. See FR-008.                      | mav   | 2026-03-19  | Closed |
| OQ-002 | `builtins.register()` direct call support — resolved: out of scope. The mechanism targets rule function call sites only. See FR-007 and §2.2.                       | mav   | 2026-03-19  | Closed |
| OQ-003 | `_source_range` for primitive type sentinels registered at `Evaluator.__init__` — resolved: no range needed; `_source_range` is absent/`None`. See §2.2 and FR-004. | mav   | 2026-03-19  | Closed |

---

## 15. Revision History

| Version | Date       | Author                              | Changes                                                                                                                                                                                           |
| ------- | ---------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2026-03-19 | Requirements Analyst AI (@socrates) | Initial draft                                                                                                                                                                                     |
| 1.1     | 2026-03-19 | Requirements Analyst AI (@socrates) | Resolve OQ-001/002/003; rename `file` to `filepath` in `_source_range`; add FR-008; update R-001; close open questions                                                                            |
| 1.2     | 2026-03-19 | Requirements Analyst AI (@socrates) | Clarify `_source_range` shape: it is a single nested `Struct` value (not three flat fields on the entity); add explicit access pattern and negative example throughout §1, §5, §6, §8, Appendix A |

---

## Appendices

### Appendix A: Glossary

| Term            | Definition                                                                                                                                                                                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| entity          | A named object registered in a `.mlody` file via a rule function (`task`, `action`, etc.)                                                                                                                                                                                      |
| rule function   | A high-level DSL function (`task`, `action`, `type`, `value`, `location`, `root`) that internally calls `builtins.register`                                                                                                                                                    |
| `_source_range` | Framework-injected field on a registered entity `Struct`. Its value is a **single nested `Struct`** with sub-fields `filepath`, `start_line`, `end_line` — accessed as `entity._source_range.filepath` etc. The sub-fields are not spread as flat fields on the entity itself. |
| top-level call  | A rule call that is a direct child `expression_statement` of the module root node in the AST                                                                                                                                                                                   |
| nested call     | A rule call that appears as an argument to another call, inside a function body, or at any other non-top-level depth                                                                                                                                                           |
| call site       | The `.mlody` file and line(s) containing the rule call that registered an entity                                                                                                                                                                                               |
| `_file_ranges`  | Per-file cache in `Evaluator` mapping `(kind, name)` to `(start_line, end_line)`, populated by `extract_entity_ranges`                                                                                                                                                         |

### Appendix B: References

- `mlody/core/source_parser.py` — existing extractor (starting point for
  extension)
- `mlody/core/source_parser_test.py` — existing tests (must remain green)
- `common/python/starlarkish/evaluator/evaluator.py` — `Evaluator._register` and
  `_line_range_extractor` hook
- `mlody/core/workspace.py` — passes `extract_entity_ranges` as the extractor
- `mlody/CLAUDE.md` — framework architecture overview
- `mlody/common/task.mlody`, `mlody/common/action.mlody` — representative rule
  function definitions showing the `builtins.register` call pattern

---

**End of Requirements Document**
