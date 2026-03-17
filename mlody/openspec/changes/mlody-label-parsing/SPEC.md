# SPEC: mlody Label Parsing Rework

**Version:** 1.0 **Date:** 2026-03-17 **Architect:** @vitruvius **Status:**
Draft **Requirements:**
`mlody/openspec/changes/mlody-label-parsing/REQUIREMENTS.md`

---

## Executive Summary

mlody currently parses label-like strings in two disconnected places:
`parse_label()` in `mlody/resolver/resolver.py` handles only the
`committoid|inner_label` split, and `parse_target()` in `mlody/core/targets.py`
handles Bazel-style `@ROOT//pkg:target.field` addresses. Neither understands the
full label grammar that mlody now requires, and neither produces a typed value
suitable for use as a cache key or cross-layer data carrier.

This rework introduces a single authoritative `Label` dataclass and a
pure-syntactic parser in `mlody/core/label/`. The parser covers the complete
grammar: workspace committoid, entity path (with root, wildcard, and query
support), attribute path access, and opaque query capture. A relocated and
extended error hierarchy gives callers section-level diagnostics. The two
existing parsers in `resolver.py` and `targets.py` become thin wrappers
delegating to the new parser, with TODO comments flagging the full migration
path.

**Requirements addressed:** FR-001 through FR-015, NFR-P-001, NFR-S-001,
NFR-U-001, NFR-M-001/002, NFR-C-001/002.

---

## Architecture Overview

```
mlody/core/label/          (NEW)
  __init__.py              re-exports: Label, EntitySpec, parse_label
  label.py                 frozen dataclasses: Label, EntitySpec
  errors.py                error hierarchy (moved + extended)
  parser.py                parse_label() implementation
  parser_test.py           unit tests

mlody/resolver/errors.py   (MODIFIED) re-exports LabelParseError
mlody/resolver/resolver.py (MODIFIED) parse_label() becomes wrapper
mlody/core/targets.py      (MODIFIED) parse_target() becomes wrapper
```

Data flow for a new caller:

```
raw: str
  --> mlody.core.label.parse_label(raw)
        --> returns Label (frozen dataclass)
              workspace / workspace_query
              entity (EntitySpec | None)
              entity_query
              attribute_path / attribute_query
```

Data flow for existing resolver callers (unchanged externally):

```
raw: str
  --> mlody.resolver.resolver.parse_label(raw)
        --> mlody.core.label.parse_label(raw)   [new core]
        --> maps Label to (str | None, str)     [wrapper output]
        --> returns (committoid, inner_label)
```

Data flow for existing targets callers (unchanged externally):

```
raw: str
  --> mlody.core.targets.parse_target(raw)
        --> mlody.core.label.parse_label(raw)   [new core]
        --> maps Label to TargetAddress          [wrapper output]
        --> raises ValueError on parse failure  [re-raised for compat]
        --> returns TargetAddress
```

---

## Technical Stack

| Concern              | Choice                                                    |
| -------------------- | --------------------------------------------------------- |
| Language             | Python 3.13.2 (hermetic via rules_python)                 |
| Type checking        | basedpyright strict                                       |
| Formatting / linting | ruff                                                      |
| Bazel rules          | `o_py_library`, `o_py_test` from `//build/bzl:python.bzl` |
| Test framework       | pytest (auto-injected by `o_py_test`)                     |
| Third-party deps     | None — stdlib only                                        |

---

## Detailed Component Specifications

### 1. `mlody/core/label/label.py` — Dataclasses

**Purpose:** Define the two frozen dataclasses that are the output type of the
parser. This module has no imports outside the standard library (`__future__`,
`dataclasses`). It is safe to import for annotation-only use without pulling in
parser logic.

**Public types:**

```python
@dataclass(frozen=True)
class EntitySpec:
    root: str | None      # @root name, None if absent
    path: str | None      # //path/to/file without .mlody suffix; None if absent
    wildcard: bool        # True if path ends with ...
    name: str | None      # :entity_name, None if absent

@dataclass(frozen=True)
class Label:
    workspace: str | None               # None = CWD; never ""
    workspace_query: str | None         # raw [...] on workspace spec, None if absent
    entity: EntitySpec | None           # None for workspace-level attr access
    entity_query: str | None            # raw [...] on entity spec, None if absent
    attribute_path: tuple[str, ...] | None   # None if no ' present
    attribute_query: str | None         # raw [...] suffix on attr path, None if absent
```

**Invariants enforced by the parser (not the dataclass itself):**

- `workspace` is `None` (CWD) or a non-empty string; never `""`.
- `EntitySpec.wildcard=True` requires `EntitySpec.path is not None`.
- `EntitySpec.path` never ends with `.mlody`.

**Bazel target:** `//mlody/core/label:label_lib`

```python
o_py_library(
    name = "label_lib",
    srcs = ["label.py"],
    visibility = ["//:__subpackages__"],
)
```

---

### 2. `mlody/core/label/errors.py` — Error Hierarchy

**Purpose:** Single authoritative location for all label-parse error types.
`LabelParseError` is relocated here from `mlody/resolver/errors.py` and
re-rooted under `ValueError` (not `WorkspaceResolutionError`) so it is usable
from any layer that imports `mlody.core` without pulling in the resolver.

**Class hierarchy:**

```
ValueError
  LabelParseError(label: str, reason: str)
    WorkspaceParseError(label, reason, workspace_fragment: str)
    EntityParseError(label, reason, entity_fragment: str)
    AttributeParseError(label, reason, attribute_fragment: str)
```

**Signatures:**

```python
class LabelParseError(ValueError):
    label: str
    reason: str
    def __init__(self, label: str, reason: str) -> None: ...

class WorkspaceParseError(LabelParseError):
    workspace_fragment: str
    def __init__(self, label: str, reason: str, workspace_fragment: str) -> None: ...

class EntityParseError(LabelParseError):
    entity_fragment: str
    def __init__(self, label: str, reason: str, entity_fragment: str) -> None: ...

class AttributeParseError(LabelParseError):
    attribute_fragment: str
    def __init__(self, label: str, reason: str, attribute_fragment: str) -> None: ...
```

**Note on `mlody/resolver/errors.py`:** The existing `LabelParseError`
definition is replaced with a re-export:

```python
# Backward-compat re-export. Import from mlody.core.label.errors directly.
# TODO(mlody-label-parsing): remove re-export after all callers are migrated.
from mlody.core.label.errors import LabelParseError as LabelParseError
```

`WorkspaceResolutionError` and its other subclasses are unaffected. Because
`LabelParseError` is no longer a `WorkspaceResolutionError` subclass, any caller
that catches `WorkspaceResolutionError` and expects to catch label parse errors
will need updating — this is intentional (see NFR-C-001). The existing
`resolver_test.py` tests that check `LabelParseError` continue to pass because
the class identity is preserved via re-export.

**Bazel target:** `//mlody/core/label:errors_lib`

```python
o_py_library(
    name = "errors_lib",
    srcs = ["errors.py"],
    visibility = ["//:__subpackages__"],
)
```

---

### 3. `mlody/core/label/parser.py` — Parser Implementation

**Purpose:** Pure-syntactic `parse_label(raw: str) -> Label`. No git I/O, no
file I/O, no network calls. Deterministic and side-effect-free.

**Module-level docstring** must include the full ABNF grammar (from Appendix C
of REQUIREMENTS.md) for in-source reference.

**Public API:**

```python
def parse_label(raw: str) -> Label:
    """Parse a raw label string into a structured Label.

    Raises:
        LabelParseError: on any syntactic error (or a section-specific subclass).
    """
```

#### 3.1 Parsing Algorithm

The parser is a single-pass, hand-written recursive-descent / linear scanner
over the raw string. No `re` module. The three sections are extracted
left-to-right using the disambiguation rules below.

**Step 1 — Workspace/entity split**

Apply the three disambiguation rules in order:

1. If `|` is present: split on the first `|`. Left side = workspace fragment
   (may be empty). Right side = entity+attribute fragment.
2. If `|` is absent and the string starts with `//` or `@`: workspace fragment =
   `""` (maps to `None`), full string = entity+attribute fragment.
3. If `|` is absent and the string does not start with `//` or `@`: split on the
   first `'`. Everything before the `'` (or the whole string if no `'`) =
   workspace fragment. No entity section.

**Step 2 — Parse workspace fragment**

- Empty string → `workspace=None`, `workspace_query=None`.
- Non-empty: strip a trailing `[...]` query (first `[` to matching `]`) →
  `workspace_query` (brackets stripped). The remainder is the committoid stored
  verbatim as `workspace`.
- If a `[` is present but has no matching `]`, raise `WorkspaceParseError`.

**Step 3 — Parse entity+attribute fragment** (only when entity section is
present, i.e. rules 1 or 2)

Split on the first `'` that is not inside a `[...]` bracket. Everything before
the `'` = entity fragment. Everything after = attribute fragment.

**Step 4 — Parse entity fragment** (if non-empty)

```
[@root_name] "//" path-spec [":" entity-name] [query]
```

- If `@` is present, read up to `//` to extract `root`. Missing `//` after `@` →
  `EntityParseError`.
- Strip leading `//`. If not present → `EntityParseError`.
- Read path segments separated by `/`. A final `...` token sets `wildcard=True`
  and is stripped. Path may not end with `/...` followed by more content. Empty
  path after `//` → `EntityParseError`.
- If `:` present, read entity name. Empty name after `:` → `EntityParseError`.
- If `[` present, capture query. Missing `]` → `EntityParseError`.

**Step 5 — Parse attribute fragment** (if `'` was found)

- Split on the last `[` that closes before end-of-string to extract
  `attribute_query`. Missing `]` → `AttributeParseError`.
- Remaining string split on `.` → `attribute_path` tuple. Empty string after
  split (e.g. trailing `.`) → `AttributeParseError`.

**Step 6 — Empty label check**

If the raw string is empty → `LabelParseError(raw, "label must not be empty")`.

**Step 7 — At-least-one-section check**

If all of `workspace`, `entity`, and `attribute_path` are `None` after parsing →
`LabelParseError`. In practice this only occurs if the input was only a bare `|`
or similar degenerate string.

**Query capture helper:**

```python
def _strip_query(fragment: str) -> tuple[str, str | None]:
    """Return (body, query_content) where query_content has brackets stripped.
    Raises ValueError if '[' is present but ']' is absent.
    """
```

**Performance:** All operations are O(n) in input length. No regex.

**Bazel target:** `//mlody/core/label:parser_lib`

```python
o_py_library(
    name = "parser_lib",
    srcs = ["parser.py"],
    visibility = ["//:__subpackages__"],
    deps = [
        ":errors_lib",
        ":label_lib",
    ],
)
```

---

### 4. `mlody/core/label/__init__.py` — Public API Surface

Re-exports exactly the symbols callers need:

```python
from mlody.core.label.errors import (
    AttributeParseError as AttributeParseError,
    EntityParseError as EntityParseError,
    LabelParseError as LabelParseError,
    WorkspaceParseError as WorkspaceParseError,
)
from mlody.core.label.label import EntitySpec as EntitySpec, Label as Label
from mlody.core.label.parser import parse_label as parse_label
```

Callers use `from mlody.core.label import Label, EntitySpec, parse_label`.

**Bazel target:** included in `//mlody/core/label:label_pkg` (see BUILD section
below).

---

### 5. `mlody/core/label/BUILD.bazel`

```python
load("//build/bzl:python.bzl", "o_py_library", "o_py_test")

o_py_library(
    name = "label_lib",
    srcs = ["label.py"],
    visibility = ["//:__subpackages__"],
)

o_py_library(
    name = "errors_lib",
    srcs = ["errors.py"],
    visibility = ["//:__subpackages__"],
)

o_py_library(
    name = "parser_lib",
    srcs = ["parser.py"],
    visibility = ["//:__subpackages__"],
    deps = [
        ":errors_lib",
        ":label_lib",
    ],
)

o_py_library(
    name = "label_pkg",
    srcs = ["__init__.py"],
    visibility = ["//:__subpackages__"],
    deps = [
        ":errors_lib",
        ":label_lib",
        ":parser_lib",
    ],
)

o_py_test(
    name = "parser_test",
    srcs = ["parser_test.py"],
    deps = [
        ":label_pkg",
        "@pip//pytest",
    ],
)
```

---

### 6. `mlody/resolver/resolver.py` — Wrapper

The body of `parse_label()` is replaced. The function signature and return type
are unchanged: `tuple[str | None, str]`.

**Translation logic:**

```python
# TODO(mlody-label-parsing): replace callers with Label directly
#   and delete this wrapper.
def parse_label(label: str) -> tuple[str | None, str]:
    from mlody.core.label import parse_label as _core_parse_label
    from mlody.core.label.errors import LabelParseError

    lbl = _core_parse_label(label)  # raises LabelParseError on bad input

    committoid = lbl.workspace  # None = CWD

    # Reconstruct the inner label (entity portion) expected by callers.
    # The inner label must start with '@' or '//'.
    if lbl.entity is None:
        raise LabelParseError(
            label,
            "inner label must start with '@' or '//' — no entity spec found",
        )
    # Re-serialise entity portion from Label fields (no attribute path).
    parts: list[str] = []
    if lbl.entity.root is not None:
        parts.append(f"@{lbl.entity.root}")
    parts.append(f"//{lbl.entity.path or ''}")
    if lbl.entity.name is not None:
        parts.append(f":{lbl.entity.name}")
    inner_label = "".join(parts)
    return (committoid, inner_label)
```

**Important:** The `resolver_lib` BUILD target gains a dep on
`//mlody/core/label:label_pkg`.

---

### 7. `mlody/core/targets.py` — Wrapper

The body of `parse_target()` is replaced. Signature and return type are
unchanged: `TargetAddress`. The function continues to raise `ValueError` on
malformed input per NFR-C-002.

**Translation logic:**

```python
# TODO(mlody-label-parsing): replace callers with Label directly
#   and delete this wrapper.
def parse_target(raw: str) -> TargetAddress:
    from mlody.core.label import parse_label as _core_parse_label
    from mlody.core.label.errors import LabelParseError

    try:
        lbl = _core_parse_label(raw)
    except LabelParseError as exc:
        raise ValueError(str(exc)) from exc

    if lbl.entity is None:
        raise ValueError(f"Target string has no entity spec: {raw!r}")

    entity = lbl.entity

    # Map attribute path: Label uses tuple[str,...] | None;
    # TargetAddress.field_path is tuple[str,...] (empty if no attr).
    field_path: tuple[str, ...] = (
        lbl.attribute_path if lbl.attribute_path is not None else ()
    )

    return TargetAddress(
        root=entity.root,
        package_path=entity.path,
        target_name=entity.name or "",
        field_path=field_path,
    )
```

**Note:** The existing `parse_target` also accepts `:target_name` forms
(entity-relative, no `//`). The new `Label` grammar requires `//` for an entity
spec, which means bare `:name` strings do not parse as entity specs under the
new grammar. The wrapper must handle this case: if raw starts with `:`, use the
legacy logic directly (or parse it as an entity with `path=None`). See the
edge-case discussion in Section "Mapping Gaps" below.

**`targets_lib` BUILD target** gains a dep on `//mlody/core/label:label_pkg`.

---

## Mapping Gaps and Edge Cases

### `:target_name` shorthand in `parse_target`

`parse_target(":config.lr")` is used by existing callers (see `targets_test.py`
lines 62-65). This form has no `//` prefix, so it does not trigger entity-spec
parsing under the new grammar (disambiguation rule 3 applies, treating it as a
workspace-spec string starting with `:`, which would fail the workspace
committoid character class).

**Resolution:** The `targets.py` wrapper detects the `:` prefix before calling
the new parser and routes it through the original logic for that specific form.
The TODO comment documents this as a known deviation. Alternatively, the new
parser can be extended to accept `:name` as a degenerate entity spec with
`path=None`; this is acceptable since `parse_target` is a wrapper only. The
implementer should prefer whichever approach keeps the wrapper simplest, but
must not change `parse_target`'s external behaviour.

### `resolver.parse_label` inner-label re-serialisation

The current `resolver.parse_label` returns the raw inner-label string exactly as
the user typed it (e.g. `@root//path:name`). The wrapper above re-serialises
from `Label` fields. This is correct for all valid inputs because the parser
normalises entity parts. No information is lost because queries and wildcards
are not used by the resolver layer.

---

## Implementation Plan

The work is decomposed into five small, independently reviewable PRs. Each PR is
mergeable on its own; later PRs depend on earlier ones only in the sense that
they import the new module.

### PR 1 — Error hierarchy

**Files changed:**

- `mlody/core/label/errors.py` (new)
- `mlody/core/label/__init__.py` (new, skeleton re-exporting errors only)
- `mlody/core/label/BUILD.bazel` (new, `errors_lib` target only)
- `mlody/resolver/errors.py` (modified: replace `LabelParseError` definition
  with re-export)

**No parser logic.** No tests required beyond confirming `LabelParseError` is
importable from both old and new paths. The existing `errors_test.py` in the
resolver package must continue to pass.

**Acceptance:** `bazel test //mlody/resolver:errors_test` green.

---

### PR 2 — Dataclasses

**Files changed:**

- `mlody/core/label/label.py` (new)
- `mlody/core/label/__init__.py` (updated: add `Label`, `EntitySpec` re-exports)
- `mlody/core/label/BUILD.bazel` (updated: add `label_lib`, update `label_pkg`)

**No parser logic.** The `Label` and `EntitySpec` dataclasses are constructed
manually in tests to verify immutability and hashability, following the same
pattern as `TestTargetAddressImmutability` in `targets_test.py`.

**Acceptance:** `bazel test //mlody/core/label:...` green (immutability tests).
`bazel build --config=lint //mlody/core/label:...` clean.

---

### PR 3 — Parser and tests

**Files changed:**

- `mlody/core/label/parser.py` (new)
- `mlody/core/label/parser_test.py` (new)
- `mlody/core/label/__init__.py` (updated: add `parse_label` re-export)
- `mlody/core/label/BUILD.bazel` (updated: add `parser_lib`, `parser_test`)

This is the largest PR. Test coverage must satisfy the full table from
REQUIREMENTS.md Section 14.1. See "Testing Strategy" below for the required test
classes.

**Acceptance:** `bazel test //mlody/core/label:parser_test` green. basedpyright
strict zero errors on `mlody/core/label/`.

---

### PR 4 — `resolver.py` wrapper

**Files changed:**

- `mlody/resolver/resolver.py` (modified: replace `parse_label` body)
- `mlody/resolver/BUILD.bazel` (modified: add dep on
  `//mlody/core/label:label_pkg`)

**Acceptance:** `bazel test //mlody/resolver:resolver_test` green. No other
resolver tests regress.

---

### PR 5 — `targets.py` wrapper

**Files changed:**

- `mlody/core/targets.py` (modified: replace `parse_target` body)
- `mlody/core/BUILD.bazel` (modified: add dep on `//mlody/core/label:label_pkg`
  in `targets_lib`)

**Acceptance:** `bazel test //mlody/core:targets_test` green. No other core
tests regress.

---

## Testing Strategy

### Unit tests — `mlody/core/label/parser_test.py`

Test classes mirror the structure of `targets_test.py`. Each class maps to one
grammar rule or disambiguation scenario.

| Class                       | Scenarios                                                                                                         |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `TestEmptyLabelRejection`   | empty string → `LabelParseError`                                                                                  |
| `TestDisambiguationRule1`   | `\|` present: empty ws, SHA-like ws, branch, branch+query, entity+attr after `\|`                                 |
| `TestDisambiguationRule2`   | starts `//`: entity only; starts `@`: entity only; entity+attr                                                    |
| `TestDisambiguationRule3`   | `'attr` (CWD attr); `branch'attr`; workspace-only (no `'`)                                                        |
| `TestWorkspaceQueryCapture` | query stripped, content captured; unclosed `[` raises `WorkspaceParseError`                                       |
| `TestEntitySpecFull`        | root+path+name; root+path; path+name; path only                                                                   |
| `TestEntitySpecWildcard`    | `//foo/...` sets `wildcard=True`, path=`foo`; `//...` edge case                                                   |
| `TestEntitySpecQuery`       | query captured in `entity_query`; unclosed `[` raises `EntityParseError`                                          |
| `TestEntitySpecErrors`      | `@ROOT` without `//`; empty path; empty entity name                                                               |
| `TestAttributePath`         | single segment; multi-segment; with query; query stripped                                                         |
| `TestAttributePathErrors`   | trailing `.`; unclosed `[`                                                                                        |
| `TestLabelImmutability`     | `Label` frozen; `EntitySpec` frozen; hashable                                                                     |
| `TestResolverWrapper`       | `resolver.parse_label` returns `(committoid, inner_label)` for valid labels; raises `LabelParseError` for invalid |
| `TestTargetsWrapper`        | `parse_target` round-trip for the five formats in `targets_test.py`; still raises `ValueError`                    |

The `TestResolverWrapper` and `TestTargetsWrapper` classes may live in their
respective existing test files instead of `parser_test.py` — the implementer
should choose whatever keeps test isolation clearest.

### Regression tests (must remain green, no changes)

```sh
bazel test //mlody/resolver:...
bazel test //mlody/core:targets_test
```

### Linting

```sh
bazel build --config=lint //mlody/core/label/...
bazel build --config=lint //mlody/resolver:resolver_lib
bazel build --config=lint //mlody/core:targets_lib
```

---

## Security

NFR-S-001 is satisfied by the parser design: query content is stored as a raw
opaque string and never evaluated, executed, or passed to `eval()`/`exec()`. The
parser uses no `re` module, eliminating ReDoS risk entirely. No external input
reaches the filesystem or network.

---

## Non-Functional Requirements

| ID        | Requirement                                        | How satisfied                                        |
| --------- | -------------------------------------------------- | ---------------------------------------------------- |
| NFR-P-001 | `parse_label` < 1 ms, no quadratic backtracking    | Linear single-pass scanner, no `re`                  |
| NFR-S-001 | Query content never interpreted                    | Captured as opaque `str`, no eval                    |
| NFR-U-001 | Errors include original label + offending fragment | Section-specific error subclasses carry `*_fragment` |
| NFR-M-001 | All public symbols typed for basedpyright strict   | Enforced by lint target                              |
| NFR-M-002 | Dataclasses importable without importing parser    | `label.py` has no dep on `parser.py`                 |
| NFR-C-001 | `LabelParseError` constructor signature preserved  | Re-export in `resolver/errors.py`                    |
| NFR-C-002 | `parse_target` continues to raise `ValueError`     | Wrapper catches `LabelParseError`, re-raises         |

---

## Risks and Mitigation

| Risk                                                                                                        | Impact | Mitigation                                                                                                |
| ----------------------------------------------------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------- |
| R-001: `targets.py` wrapper changes exception type for callers catching `ValueError`                        | Medium | Wrapper catches `LabelParseError` and re-raises as `ValueError`; existing `targets_test.py` verifies this |
| R-002: `LabelParseError` re-export breaks callers doing `from mlody.resolver.errors import LabelParseError` | Medium | Re-export preserves class identity; add deprecation comment                                               |
| R-003: Query `[...]` opaque capture silently wrong for nested brackets                                      | Low    | Documented assumption: no unescaped nested `]`; add a test case with an inner `[` to document behaviour   |
| R-004: `:name` shorthand in `parse_target` not covered by new grammar                                       | Medium | Wrapper detects `:` prefix and routes separately; documented as known deviation in TODO                   |

---

## Future Considerations

- **Round-trip serialisation:** `Label.__str__` or a `format_label()` function
  producing the canonical string representation of a `Label`. Not in scope but
  the dataclass fields are sufficient to implement it later.
- **Full migration:** Once all callers of `resolver.parse_label` and
  `parse_target` are updated to use `Label` directly, the two wrappers and the
  re-export in `resolver/errors.py` can be deleted.
- **LSP integration:** The LSP hover and completion providers can use
  `parse_label` to parse labels from document text without triggering workspace
  I/O.
- **`:name` shorthand in core grammar:** Consider whether to add entity-relative
  (no `//`) as a first-class grammar production in a future revision, removing
  the special-case from the `targets.py` wrapper.

---

## Appendix: File Inventory

| File                              | Status                     | PR      |
| --------------------------------- | -------------------------- | ------- |
| `mlody/core/label/__init__.py`    | New (grows across PRs 1-3) | 1, 2, 3 |
| `mlody/core/label/label.py`       | New                        | 2       |
| `mlody/core/label/errors.py`      | New                        | 1       |
| `mlody/core/label/parser.py`      | New                        | 3       |
| `mlody/core/label/parser_test.py` | New                        | 3       |
| `mlody/core/label/BUILD.bazel`    | New (grows across PRs 1-3) | 1, 2, 3 |
| `mlody/resolver/errors.py`        | Modified (re-export)       | 1       |
| `mlody/resolver/resolver.py`      | Modified (wrapper)         | 4       |
| `mlody/resolver/BUILD.bazel`      | Modified (add dep)         | 4       |
| `mlody/core/targets.py`           | Modified (wrapper)         | 5       |
| `mlody/core/BUILD.bazel`          | Modified (add dep)         | 5       |
