# Requirements Document: mlody Label Parsing Rework

**Version:** 1.0 **Date:** 2026-03-17 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

The mlody framework currently parses label-like strings in two disconnected
places: `parse_label()` in `mlody/resolver/resolver.py` splits a
`committoid|inner_label` pair, and `parse_target()` in `mlody/core/targets.py`
parses a Bazel-style `@ROOT//pkg:target.field` address. Neither function
understands the full label grammar now required by mlody — which encompasses
workspace specifiers, entity paths with wildcard and query support, and
attribute path access — and neither is suitable for use as a standalone reusable
type.

This rework introduces a unified `Label` dataclass and a pure-syntactic parser
in the pre-existing but currently empty `mlody/core/label/` directory. The
parser produces a structured, frozen, immutable value that can be passed between
layers of the system. A separate validation layer (outside this rework's scope)
is responsible for checking that workspace refs and file paths actually exist.
The existing parsers in `resolver.py` and `targets.py` will become thin wrappers
delegating to the new parser, with TODO comments documenting the migration plan.

The expected business value is a single authoritative grammar implementation,
enabling consistent label handling across the resolver, LSP, CLI diff commands,
and any future typedef or serialisation use cases.

---

## 2. Project Scope

### 2.1 In Scope

- Define the `Label` and `EntitySpec` frozen dataclasses in `mlody/core/label/`.
- Implement a pure-syntactic `parse_label(raw: str) -> Label` function.
- Move `LabelParseError` from `mlody/resolver/errors.py` to a shared location
  (`mlody/core/label/errors.py`) and extend the error hierarchy with
  section-specific subclasses (workspace, entity, attribute).
- Replace the implementation bodies of `parse_label()` in `resolver.py` and
  `parse_target()` in `targets.py` with thin wrappers that delegate to the new
  parser, with TODO comments explaining the full migration plan.
- Unit tests covering all grammar production rules, disambiguation cases, and
  error paths.
- Bazel BUILD file for `mlody/core/label/`.

### 2.2 Out of Scope

- Workspace validation (checking that a branch/SHA/tag actually exists in the
  git repo) — this belongs to the existing resolver layer.
- File path validation (checking that a `.mlody` file exists in the workspace).
- Round-trip serialisation of `Label` back to a string.
- Query sub-grammar parsing — query content is captured as an opaque string.
- LSP integration updates (separate work item).
- Deletion of the legacy `parse_label()` or `parse_target()` functions (retained
  as wrappers; full removal is a future migration task).

### 2.3 Assumptions

- The `.mlody` file suffix is always omitted from entity paths in labels; the
  parser does not append it.
- A label string is always a single UTF-8 string with no embedded newlines.
- Query brackets `[...]` do not contain unescaped nested `]` characters; the
  parser may treat the first `]` as the closing bracket.
- The `'` (apostrophe/single-quote, U+0027) is the sole attribute path
  separator; no escaping mechanism exists in this version.
- `parse_target()` in `targets.py` raises `ValueError` today; the wrapper may
  continue to raise `ValueError` for backward compatibility, translating from
  `LabelParseError` internally. [Assumption — Requires Validation]

### 2.4 Constraints

- Python 3.13, strict basedpyright type checking, ruff formatting.
- All Bazel rules must use `o_py_library` / `o_py_test` from
  `//build/bzl:python.bzl`.
- No new third-party dependencies — the parser must use the Python standard
  library only.
- The `mlody/resolver/errors.py` module must re-export `LabelParseError` (or a
  compatible alias) so that existing callers outside this rework are not broken.

---

## 3. Stakeholders

| Role                   | Name/Group     | Responsibilities                           |
| ---------------------- | -------------- | ------------------------------------------ |
| mlody framework author | mav            | Final acceptance, grammar authority        |
| Requirements Analyst   | @socrates      | Requirements elicitation and documentation |
| Solution Architect     | @vitruvious    | System design and SPEC.md                  |
| Implementation         | @vulcan-python | Python coding                              |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Provide a single, authoritative label parser that all mlody
  subsystems (resolver, LSP, CLI) share, eliminating grammar drift between
  implementations.
- **BR-002:** Separate syntactic parsing from workspace validation so that
  labels can be used as pure value types (e.g. for future typedefs, caching
  keys, or serialisation) without triggering git I/O.
- **BR-003:** Improve error diagnostics by reporting which section of a label
  (workspace, entity, or attribute) failed to parse.

### 4.2 Success Metrics

- **KPI-001:** All callers of the old `parse_label()` and `parse_target()` pass
  their existing test suites without modification after the wrappers are in
  place.
- **KPI-002:** 100% of grammar production rules defined in Section 6 have a
  corresponding passing unit test.
- **KPI-003:** basedpyright reports zero errors on `mlody/core/label/` in strict
  mode.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: mlody framework developer**

- Needs to pass label strings between subsystems (resolver, LSP, CLI) as typed
  values.
- Pain point: current code silently accepts malformed labels or requires
  knowledge of two separate parsing APIs.
- Needs a single import point and a predictable error hierarchy.

**Persona 2: mlody end user (via CLI or LSP)**

- Types label strings in the terminal or `.mlody` editor.
- Pain point: cryptic error messages that do not indicate where in the label the
  problem is.
- Needs clear, positioned error messages.

### 5.2 User Stories

**Epic 1: Unified label type**

- **US-001:** As a framework developer, I want to call `parse_label(raw)` and
  receive a `Label` dataclass so that I can inspect workspace, entity, and
  attribute parts without string manipulation.
  - Acceptance Criteria: Given any syntactically valid label string, when
    `parse_label` is called, then a `Label` instance is returned with all fields
    correctly populated.
  - Priority: High (Must Have)

- **US-002:** As a framework developer, I want `Label` to be a frozen dataclass
  so that I can use it safely as a dict key or in sets.
  - Acceptance Criteria: `hash(label)` and equality comparisons work; mutation
    raises `FrozenInstanceError`.
  - Priority: High (Must Have)

**Epic 2: Error diagnostics**

- **US-003:** As an end user, I want parse errors to identify which section of
  the label failed (workspace, entity, or attribute) so that I can correct the
  right part.
  - Acceptance Criteria: Given a label with a malformed entity section, when
    `parse_label` raises, then the exception is an instance of
    `EntityParseError` (or the equivalent section-specific subclass) and its
    message names the offending substring.
  - Priority: Medium (Should Have)

**Epic 3: Backward compatibility**

- **US-004:** As an existing caller of `mlody.resolver.resolver.parse_label`, I
  want the function signature and exception types to be unchanged so that I do
  not need to update call sites immediately.
  - Acceptance Criteria: Existing resolver tests pass without modification.
  - Priority: High (Must Have)

---

## 6. Functional Requirements

### 6.1 Label Grammar

**FR-001: Top-level label structure**

- Description: A label string conforms to the grammar:
  `[workspace_spec '|'] [entity_spec] ["'" attribute_path]`
- The three sections are optional but at least one must be present.
- Priority: Must Have

**FR-002: Workspace spec disambiguation**

- Description: The parser applies the following rules in order to locate the
  workspace spec boundary:
  1. If `|` is present in the string, everything before the first `|` is the
     workspace spec; everything after is parsed as
     `[entity_spec]["'" attribute_path]`.
  2. If `|` is absent AND the remainder starts with `//` or `@`, the workspace
     spec is empty (CWD) and the entire string is parsed as entity spec +
     optional attribute path.
  3. If `|` is absent AND the remainder does NOT start with `//` or `@`,
     everything before the first `'` (if any) is the workspace spec and there is
     no entity spec; if no `'` is present the entire string is the workspace
     spec.
- Inputs: raw label string
- Outputs: workspace spec string (possibly empty) or `None` for CWD when empty
- Priority: Must Have

**FR-003: Workspace spec values**

- Description: After disambiguation, the workspace spec may be:
  - Empty string — resolved to `None` (meaning CWD / current working tree).
  - A short SHA prefix (hex characters only) — must be at least 4 characters;
    extended to 40 characters by the validation layer (not the parser).
  - A branch name or tag name — any non-empty string not matching the short-SHA
    pattern.
  - A query suffix `[...]` may optionally appear on the workspace spec (e.g.
    `my-branch[git:author=mav]`). The parser captures the query content as an
    opaque string and does not interpret it.
- Priority: Must Have

**FR-004: Entity spec parsing**

- Description: The entity spec, if present, matches:
  `[@root_name "//"] path_segments [":" entity_name] ["[" query "]"]`
  - `@root_name` — optional root qualifier; the identifier between `@` and `//`.
  - `//path/segments` — required if entity spec is present; represents a path to
    a `.mlody` file (suffix omitted). The final segment may be `...` (wildcard)
    to select all files under the parent directory.
  - `:entity_name` — optional; selects a specific named entity within the file.
  - `[query]` — optional opaque query filter applied to entity results.
- When the entity spec is absent, `Label.entity` is `None`.
- Priority: Must Have

**FR-005: Attribute path parsing**

- Description: If a `'` character is present (after workspace and entity
  sections have been consumed), everything following it is the attribute path.
  The attribute path is split on `.` to produce a tuple of path segments.
  - Example: `'outputs.model` → `("outputs", "model")`
  - Example: `'info` → `("info",)`
  - A query suffix `[...]` may appear at the end of the attribute path. It is
    captured as an opaque raw string in `Label.attribute_query`; the brackets
    are stripped from the stored value.
- When no `'` is present, `Label.attribute_path` is `None` and
  `Label.attribute_query` is `None`.
- Priority: Must Have

**FR-006: Shorthand workspace-only attribute labels**

- Description: The shorthand form `'attr` (no `|`, no `//`, no `@`) must parse
  as `Label(workspace=None, entity=None, attribute_path=("attr",))`. Similarly,
  `my-branch'attr` parses as
  `Label(workspace="my-branch", entity=None, attribute_path=("attr",))`.
- These labels access synthesised workspace-level attributes (git metadata:
  branch name, HEAD SHA, commits ahead/behind main, clean/dirty state, untracked
  files) without requiring workspace materialisation.
- Priority: Must Have

**FR-007: Wildcard entity paths**

- Description: A path ending with `...` (triple dot) sets
  `EntitySpec.wildcard = True` and strips the trailing `...` from the stored
  path. A path ending with `.../` is not valid and must raise a parse error.
  When `wildcard=True` the path represents all `.mlody` files recursively under
  the named directory.
- Priority: Must Have

**FR-008: Empty label rejection**

- Description: An empty string must raise `LabelParseError`.
- Priority: Must Have

### 6.2 Label and EntitySpec Dataclasses

**FR-009: `Label` dataclass**

- Description: The `Label` type is a frozen dataclass defined in
  `mlody/core/label/label.py`:

  ```python
  @dataclass(frozen=True)
  class Label:
      workspace: str | None               # None = CWD; else branch/sha/tag
      workspace_query: str | None         # raw [...] on workspace spec, None if absent
      entity: EntitySpec | None           # None for workspace-level attr access
      entity_query: str | None            # raw [...] on entity spec, None if absent
      attribute_path: tuple[str, ...] | None  # None if no "'" present
      attribute_query: str | None         # raw [...] suffix on attr path, None if absent
  ```

- All fields are required at construction time (no defaults). `workspace=None`
  means CWD; `workspace=""` is not a valid constructed value.
- Queries are top-level fields on `Label`, not embedded inside `EntitySpec`, so
  that the core structural fields remain separate from filters.
- Priority: Must Have

**FR-010: `EntitySpec` dataclass**

- Description: The `EntitySpec` type is a frozen dataclass defined in
  `mlody/core/label/label.py`:

  ```python
  @dataclass(frozen=True)
  class EntitySpec:
      root: str | None     # @root name, None if absent
      path: str | None     # //path/to/file without .mlody suffix; None if absent
      wildcard: bool       # True if path ends with ...
      name: str | None     # :entity_name, None if absent
  ```

- There is no `query` field on `EntitySpec`; entity filtering is expressed via
  `Label.entity_query` (e.g. `entity_query="kind=action"`).
- There is no explicit `type` field; entity kind filtering is handled entirely
  through the query mechanism.
- Priority: Must Have

### 6.3 Error Hierarchy

**FR-011: `LabelParseError` relocation and base class**

- Description: `LabelParseError` is moved from `mlody/resolver/errors.py` to
  `mlody/core/label/errors.py`. The class signature `(label: str, reason: str)`
  is preserved. `mlody/resolver/errors.py` re-exports it to maintain backward
  compatibility.
- `LabelParseError` becomes a direct subclass of `ValueError` (not of
  `WorkspaceResolutionError`) since label parsing now covers workspace, entity,
  and attribute sections — not only workspace resolution.
- Priority: Must Have

**FR-012: Section-specific error subclasses**

- Description: Three subclasses of `LabelParseError` are introduced:
  - `WorkspaceParseError(label, reason, workspace_fragment)` — raised when the
    workspace section cannot be parsed.
  - `EntityParseError(label, reason, entity_fragment)` — raised when the entity
    section cannot be parsed.
  - `AttributeParseError(label, reason, attribute_fragment)` — raised when the
    attribute path section cannot be parsed.
- Each subclass stores the offending fragment as a named attribute for
  programmatic inspection.
- Priority: Should Have

### 6.4 Parser Function

**FR-013: `parse_label` public API**

- Description: A public function `parse_label(raw: str) -> Label` is defined in
  `mlody/core/label/parser.py` and re-exported from
  `mlody/core/label/__init__.py`.
- The function is purely syntactic: it does not perform git operations, file
  I/O, or any network calls.
- The function is deterministic and side-effect-free.
- Priority: Must Have

### 6.5 Backward-Compatibility Wrappers

**FR-014: `resolver.py` wrapper**

- Description: The body of `parse_label()` in `mlody/resolver/resolver.py` is
  replaced with a call to `mlody.core.label.parse_label`, translating the return
  value to `(committoid: str | None, inner_label: str)` as before. A
  `# TODO(mlody-label-parsing): replace callers with Label directly and delete this wrapper`
  comment is added.
- Priority: Must Have

**FR-015: `targets.py` wrapper**

- Description: The body of `parse_target()` in `mlody/core/targets.py` is
  replaced with a call to `mlody.core.label.parse_label`, mapping `Label` fields
  to `TargetAddress` fields. A corresponding TODO comment is added.
- Priority: Must Have

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-P-001:** `parse_label` must complete in under 1 ms for any label string
  up to 1 000 characters on a modern development machine. The parser must not
  use regex backtracking patterns that are quadratic in input length.

### 7.2 Scalability Requirements

- Not applicable — the parser is a pure function with no state.

### 7.3 Availability & Reliability

- Not applicable for a library module.

### 7.4 Security Requirements

- **NFR-S-001:** The parser must not evaluate or execute any content of the
  label string. Query content captured as an opaque string must not be
  interpreted.

### 7.5 Usability Requirements

- **NFR-U-001:** Parse error messages must include the full original label
  string and identify the offending section or character offset where practical.

### 7.6 Maintainability Requirements

- **NFR-M-001:** All public functions and dataclasses must have type hints
  satisfying basedpyright strict mode.
- **NFR-M-002:** The parser implementation must be in a separate module
  (`parser.py`) from the dataclass definitions (`label.py`) to allow the
  dataclasses to be imported without importing the parser (useful for
  annotation-only imports).

### 7.7 Compatibility Requirements

- **NFR-C-001:** The public interface of `mlody.resolver.errors.LabelParseError`
  (constructor signature, attribute names) must not change. The exception
  hierarchy changes: `LabelParseError` becomes a `ValueError` subclass rather
  than a subclass of `WorkspaceResolutionError`. Callers catching
  `WorkspaceResolutionError` will no longer catch `LabelParseError`; this is
  intentional and desirable.
- **NFR-C-002:** `parse_target()` in `targets.py` must continue to raise
  `ValueError` on malformed input (the wrapper translates internally).

---

## 8. Data Requirements

### 8.1 Data Entities

| Entity                | Location                     | Description                              |
| --------------------- | ---------------------------- | ---------------------------------------- |
| `Label`               | `mlody/core/label/label.py`  | Top-level parsed label value             |
| `EntitySpec`          | `mlody/core/label/label.py`  | Parsed entity section of a label         |
| `LabelParseError`     | `mlody/core/label/errors.py` | Base parse error (`ValueError` subclass) |
| `WorkspaceParseError` | `mlody/core/label/errors.py` | Workspace section error                  |
| `EntityParseError`    | `mlody/core/label/errors.py` | Entity section error                     |
| `AttributeParseError` | `mlody/core/label/errors.py` | Attribute path section error             |

### 8.2 Data Quality Requirements

- `Label.workspace` must never be an empty string; the parser converts `""`
  (empty workspace spec) to `None`.
- `EntitySpec.path` must not include a `.mlody` suffix.
- `EntitySpec.wildcard=True` requires `EntitySpec.path` to be non-`None`.

### 8.3 Data Retention & Archival

Not applicable — `Label` is an in-memory value type with no persistence.

### 8.4 Data Privacy & Compliance

Not applicable.

---

## 9. Integration Requirements

### 9.1 External Systems

None — the parser is a pure Python library with no external dependencies.

### 9.2 API Requirements

The public API surface exported from `mlody/core/label/__init__.py`:

```python
from mlody.core.label import Label, EntitySpec, parse_label
from mlody.core.label.errors import (
    LabelParseError,
    WorkspaceParseError,
    EntityParseError,
    AttributeParseError,
)
```

---

## 10. User Interface Requirements

Not applicable — this is a library module with no UI.

---

## 11. Reporting & Analytics Requirements

Not applicable.

---

## 12. Security & Compliance Requirements

See NFR-S-001. No authentication, authorisation, or compliance requirements.

---

## 13. Infrastructure & Deployment Requirements

### 13.1 Hosting & Environment

Pure Python library; deployed as part of the mlody Bazel target graph.

### 13.2 Deployment

- `mlody/core/label/BUILD.bazel` must define an `o_py_library` target
  (`//mlody/core/label`) with no external pip dependencies.
- A separate `o_py_test` target must cover the unit tests.

### 13.3 Disaster Recovery

Not applicable.

---

## 14. Testing & Quality Assurance Requirements

### 14.1 Testing Scope

All tests live in `mlody/core/label/` alongside the source. Test file name
convention: `*_test.py`.

Required test coverage:

| Test area                                                           | Cases                                                           |
| ------------------------------------------------------------------- | --------------------------------------------------------------- |
| Workspace disambiguation — rule 1 (`\|` present)                    | empty workspace, short SHA, branch, branch+query                |
| Workspace disambiguation — rule 2 (`//` or `@` prefix, no `\|`)     | CWD entity-only, CWD entity+attr                                |
| Workspace disambiguation — rule 3 (shorthand, no `\|`, no `//`/`@`) | `'attr`, `branch'attr`, workspace-only (no `'`)                 |
| Entity spec — all combinations                                      | root+path+name, root+path only, path+name, path only, wildcard  |
| Entity spec — query                                                 | presence and absence                                            |
| Attribute path                                                      | single segment, multi-segment, with query                       |
| Error cases                                                         | empty string, missing `//` after `@`, malformed `[` without `]` |
| Backward-compat wrapper                                             | `resolver.parse_label` returns `(committoid, inner_label)`      |

### 14.2 Acceptance Criteria

- All unit tests pass under `bazel test //mlody/core/label/...`.
- `bazel build --config=lint //mlody/core/label/...` reports no errors.
- basedpyright strict reports no errors.
- Existing tests under `//mlody/resolver/...` and `//mlody/core:targets_test`
  continue to pass.

---

## 15. Training & Documentation Requirements

### 15.1 User Documentation

Not required for an internal library module.

### 15.2 Technical Documentation

- Each public symbol (`Label`, `EntitySpec`, `parse_label`, error classes) must
  have a docstring explaining its contract, accepted inputs, and raised
  exceptions.
- Grammar productions should be documented in a module-level docstring in
  `parser.py` using ABNF or equivalent notation.

### 15.3 Training

Not applicable.

---

## 16. Risks & Mitigation Strategies

| Risk ID | Description                                                                                                                           | Impact | Probability       | Mitigation                                                                      | Owner          |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------ | ----------------- | ------------------------------------------------------------------------------- | -------------- |
| R-001   | Wrapper in `targets.py` changes exception type, breaking callers that catch `ValueError`                                              | Medium | Medium            | Wrapper explicitly catches `LabelParseError` and re-raises as `ValueError`      | @vulcan-python |
| R-002   | `LabelParseError` re-export from `resolver/errors.py` breaks import paths that do `from mlody.resolver.errors import LabelParseError` | Medium | Low               | Keep the name in `resolver/errors.py` as a re-export; add a deprecation comment | @vulcan-python |
| R-003   | Query `[...]` opaque capture fails silently for nested brackets                                                                       | Low    | Low               | Document the no-nested-unescaped-bracket assumption; add a test case            | @vulcan-python |
| R-004   | ~~Attribute path query representation introduces API churn~~ — resolved: raw suffix string in `Label.attribute_query` per FR-005      | Low    | ~~Medium~~ Closed | OQ-001 answered; FR-005 updated                                                 | mav            |

---

## 17. Dependencies

| Dependency                      | Type                      | Status    | Impact if Delayed        | Owner |
| ------------------------------- | ------------------------- | --------- | ------------------------ | ----- |
| `mlody/core/label/` directory   | Directory (exists, empty) | Ready     | None                     | —     |
| basedpyright / ruff toolchain   | Dev tooling               | Available | Cannot enforce NFR-M-001 | —     |
| Existing resolver tests passing | Test baseline             | Verified  | Cannot confirm NFR-C-001 | —     |

---

## 18. Open Questions & Action Items

| ID     | Question/Action                                                                                                                                                            | Owner | Target Date | Status       |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ----------- | ------------ |
| OQ-001 | Queries are top-level fields on `Label` (`workspace_query`, `entity_query`, `attribute_query`) as raw opaque strings. `EntitySpec` has no `query` field.                   | mav   | 2026-03-17  | **Resolved** |
| OQ-002 | `LabelParseError` is a direct `ValueError` subclass (not under `WorkspaceResolutionError`). See FR-011 and NFR-C-001.                                                      | mav   | 2026-03-17  | **Resolved** |
| OQ-003 | `\|//foo/bar` (empty workspace before `\|`) → `workspace=None` (CWD). Not an error.                                                                                        | mav   | 2026-03-17  | **Resolved** |
| OQ-004 | No minimum SHA length in the parser; any prefix that resolves uniquely in the repo is accepted (validation layer responsibility, same as current `resolve_sha` behaviour). | mav   | 2026-03-17  | **Resolved** |

---

## 19. Revision History

| Version | Date       | Author                              | Changes                                                                       |
| ------- | ---------- | ----------------------------------- | ----------------------------------------------------------------------------- |
| 1.0     | 2026-03-17 | Requirements Analyst AI (@socrates) | Initial draft                                                                 |
| 1.1     | 2026-03-17 | mav                                 | Resolved OQ-001–004: query fields, error base class, empty workspace, SHA len |

---

## Appendices

### Appendix A: Glossary

| Term           | Definition                                                                                             |
| -------------- | ------------------------------------------------------------------------------------------------------ |
| committoid     | Any string that identifies a git commit: branch name, tag name, full 40-char SHA, or short SHA prefix  |
| CWD            | Current working directory / current working tree — the live monorepo checkout, not a cached clone      |
| entity         | A named object registered in a `.mlody` file via `builtins.register()`                                 |
| inner label    | The `@ROOT//path:name` portion of a label after the workspace spec is stripped                         |
| label          | A string that identifies a value in the mlody system: workspace + optional entity + optional attribute |
| materialise    | Clone a specific commit into `~/.cache/mlody/workspaces/<sha>` so its files can be read                |
| workspace      | Either the current live checkout (CWD) or a cached clone of a specific git commit                      |
| workspace spec | The part of a label before `\|` (or before `'` in shorthand form) that identifies the workspace        |

### Appendix B: References

- `mlody/resolver/resolver.py` — existing `parse_label()` and
  `resolve_workspace()`
- `mlody/core/targets.py` — existing `parse_target()` and `TargetAddress`
- `mlody/resolver/errors.py` — existing `LabelParseError` and sibling error
  classes
- `mlody/CLAUDE.md` — framework architecture overview
- `mlody/core/label/` — target directory (currently empty)

### Appendix C: Grammar Reference (ABNF)

```abnf
label           = [workspace-spec "|"] [entity-spec] ["'" attribute-path]
                / "'" attribute-path

; Workspace section
workspace-spec  = committoid [query]       ; query stored in Label.workspace_query
committoid      = branch-or-tag / short-sha
branch-or-tag   = 1*(ALPHA / DIGIT / "-" / "_" / ".")
short-sha       = 1*HEXDIG                 ; any length; uniqueness check is validation

; Entity section
entity-spec     = ["@" root-name] "//" path-spec [":" entity-name] [query]
                                           ; query stored in Label.entity_query
root-name       = 1*(ALPHA / DIGIT / "-" / "_")
path-spec       = path-segments ["..."]
path-segments   = segment *("/" segment)
segment         = 1*(ALPHA / DIGIT / "-" / "_" / ".")
entity-name     = 1*(ALPHA / DIGIT / "-" / "_")

; Attribute section
attribute-path  = attr-segment *("." attr-segment) [query]
                                           ; query stored in Label.attribute_query
attr-segment    = 1*(ALPHA / DIGIT / "-" / "_")

; Shared
query           = "[" *(%x00-5C / %x5E-FF) "]"   ; any chars except unescaped ]
```

Note: this grammar is informational. The parser is the normative specification.

---

**End of Requirements Document**
