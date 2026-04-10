# Requirements Document: rule() Common Attrs

**Version:** 1.0 **Date:** 2026-04-10 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

Every rule definition in `mlody/` currently declares `name` and `description` as
explicit entries in its `attrs={}` dict, despite these attributes being
logically universal — every mlody rule must have a name, and all rules may
optionally carry a description. This duplication is mechanical boilerplate that
must be maintained in sync across every rule file (`task`, `action`, `value`,
`typedef`, etc.).

This change introduces a `_COMMON_ATTRS` dict in `mlody/core/rule.mlody` and
wires it into the `rule()` function so that the common attributes are
automatically merged into every rule's attribute set. Rule authors then remove
the now-redundant `name`/`description` declarations from each individual
`attrs={}` block. Call sites are unaffected: `task(name="pretrain", ...)` and
similar invocations continue to work identically.

The expected outcome is a reduction in boilerplate, a single authoritative
definition of what constitutes a "common rule attribute", and a foundation for
adding future universal attributes without touching every rule file.

---

## 2. Project Scope

### 2.1 In Scope

- Define `_COMMON_ATTRS` in `mlody/core/rule.mlody` containing `name` (mandatory
  `string`) and `description` (optional `string`, default `""`).
- Modify the `rule()` function to merge `_COMMON_ATTRS` with the caller-supplied
  `attrs` dict before storing in `state["attrs"]`.
- Remove the `name` and `description` attr declarations from every rule
  definition that currently duplicates them (`task`, `action`, `value`,
  `typedef`, and any other rule in `mlody/common/`).
- Validate the `name` attribute value at rule invocation time: must be a
  non-empty string consisting only of alphanumeric characters and underscores
  (`[a-zA-Z0-9_]+`).
- Ensure existing registry and uniqueness semantics are unchanged — the `name`
  uniqueness invariant is enforced by the existing builtins registry, not by new
  logic added at the `rule()` level.

### 2.2 Out of Scope

- Adding new common attributes beyond `name` and `description`.
- Changing call-site syntax: `task(name="pretrain", ...)` continues to work
  without modification.
- Modifying how the evaluator, workspace, or Python host layer processes
  registered structs.
- Enforcing `name` uniqueness at the `rule()` layer (the existing registry
  already raises on duplicate registration).
- Supporting hyphens, spaces, slashes, dots, or any other characters in rule
  names beyond `[a-zA-Z0-9_]`.
- Adding `description` to the registered struct output (this is an
  implementation decision left to the architect).

### 2.3 Assumptions

- The `attrs` dict passed to `rule()` is always a plain Python/Starlark dict; no
  special merge semantics are required.
- If a caller explicitly supplies `name` or `description` in their `attrs={}`, a
  conflict should be detected and raise a clear error rather than silently
  overwriting `_COMMON_ATTRS`.
- All existing rule call sites pass `name=` as a keyword argument (confirmed by
  the existing `task.mlody` usage pattern).
- The Starlark evaluator's `struct()` / `Struct` immutability constraints do not
  prevent dict merging at the `rule()` definition stage.

### 2.4 Constraints

- The change must remain within `mlody/core/rule.mlody`; no changes to
  `common/python/starlarkish` internals.
- The implementation must be valid Starlark (no `nonlocal`, no Python-only
  constructs) or use the existing `python.*` escape hatch pattern only where
  unavoidable and explicitly marked.
- Bazel BUILD files must not require manual edits — Gazelle handles dependency
  updates.

---

## 3. Stakeholders

| Role               | Name / Group          | Responsibilities                                         |
| ------------------ | --------------------- | -------------------------------------------------------- |
| Requester          | Maurizio Vitale (mav) | Defines scope and accepts the final implementation       |
| Rule authors       | mlody team            | Remove duplicate attr declarations from their rule files |
| Architect          | @vitruvious           | Designs the merge strategy and conflict detection        |
| Implementing agent | @vulcan-python        | Writes and tests the code changes                        |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Eliminate per-rule boilerplate by providing a single,
  authoritative definition of attributes common to all mlody rules.
- **BR-002:** Establish a pattern that allows future universal attributes to be
  added in one place without touching every rule file.

### 4.2 Success Metrics

- **KPI-001:** Zero occurrences of `"name": attr(...)` or
  `"description": attr(...)` in `mlody/common/` rule files after the change.
- **KPI-002:** All existing `bazel test //mlody/...` tests pass without
  modification.
- **KPI-003:** All existing call sites (`task(name=...)`, `action(name=...)`,
  etc.) continue to function without any change.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: Rule Author**

- A mlody developer writing a new rule in a `.mlody` file.
- Currently must remember to declare `name` and `description` in every new
  `attrs={}` dict.
- After this change: declares only rule-specific attrs; common attrs are
  automatically present on `ctx.attr`.

**Persona 2: Pipeline Author**

- A data scientist or ML engineer writing `.mlody` pipeline files.
- Calls rules like `task(name="pretrain", outputs=[...], action=":train")`.
- Unaffected by this change — the call-site interface is identical.

### 5.2 User Stories

**Epic 1: Common Attribute Centralisation**

- **US-001:** As a rule author, I want `name` and `description` to be
  automatically available on `ctx.attr` without declaring them in my rule's
  `attrs={}`, so that I do not repeat boilerplate in every rule definition.
  - Acceptance Criteria:
    - Given a `rule(implementation=..., kind=..., attrs={...})` call that omits
      `name` and `description`,
    - When a pipeline file calls `my_rule(name="foo", ...)`,
    - Then `ctx.attr.name == "foo"` and `ctx.attr.description == ""` (or the
      supplied value) inside the implementation function.
  - Priority: Must Have

- **US-002:** As a rule author, I want an explicit error if I accidentally
  re-declare a common attribute in my rule's `attrs={}`, so that the merge
  conflict is caught early rather than silently ignored.
  - Acceptance Criteria:
    - Given a `rule(..., attrs={"name": attr(...), ...})` call,
    - When the `rule()` function processes the attrs,
    - Then a `ValueError` (or equivalent) is raised immediately, naming the
      conflicting key.
  - Priority: Must Have

- **US-003:** As a pipeline author, I want `name=""` (empty string) to be
  rejected with a clear error message, so that rules with meaningless names are
  never registered.
  - Acceptance Criteria:
    - Given a rule invocation with `name=""`,
    - When `_validate_args` runs,
    - Then a `ValueError` is raised indicating that `name` must be non-empty.
  - Priority: Must Have

- **US-004:** As a pipeline author, I want `name` values with spaces, hyphens,
  slashes, or other special characters to be rejected, so that the name remains
  a valid Starlark identifier-like token.
  - Acceptance Criteria:
    - Given `name="my task"`, `name="my-task"`, or `name="my/task"`,
    - When the rule is invoked,
    - Then a `ValueError` is raised indicating the allowed character set
      (`[a-zA-Z0-9_]+`).
  - Priority: Must Have

---

## 6. Functional Requirements

### 6.1 Common Attrs Definition

**FR-001: `_COMMON_ATTRS` constant**

- Description: A module-level dict in `mlody/core/rule.mlody` that defines
  attributes available on every rule.
- Content:
  - `"name"`: `attr(type="string", mandatory=True)` — the rule's identifier.
  - `"description"`: `attr(type="string", mandatory=False, default="")` — a
    human-readable description.
- Priority: Must Have
- Dependencies: Existing `attr()` helper from `mlody/common/attrs.mlody` is NOT
  used here because `rule.mlody` must not create a circular load dependency.
  `_COMMON_ATTRS` should use the same plain-dict format that `attr()` would
  produce.

**FR-002: Merge in `rule()`**

- Description: The `rule()` function merges `_COMMON_ATTRS` with the
  caller-supplied `attrs` dict before storing it in `state["attrs"]`.
- Processing:
  1. Check for key conflicts between `_COMMON_ATTRS` and the caller's `attrs`.
     If any conflict exists, raise a `ValueError` naming the conflicting key(s).
  2. Merge: `merged = dict(_COMMON_ATTRS)` followed by `merged.update(attrs)` —
     or the Starlark-equivalent — so that caller-supplied attrs win (but since
     conflicts are rejected in step 1, the merge order is effectively
     non-ambiguous).
  3. Store `merged` in `state["attrs"]`.
- Priority: Must Have

**FR-003: `name` value validation**

- Description: When a rule is invoked (`f(**kwargs)` inside `rule()`), validate
  the `name` value before any other processing.
- Rules:
  - `name` must be present (mandatory enforcement already handled by
    `_validate_args`).
  - `name` must not be an empty string.
  - `name` must match `^[a-zA-Z0-9_]+$`.
- Error: Raise `ValueError` with a message that states both the rejected value
  and the allowed pattern.
- Priority: Must Have

**FR-004: Remove duplicate declarations from rule files**

- Description: After `rule()` is updated, remove the `"name"` and
  `"description"` entries from `attrs={}` in every rule definition that
  currently declares them.
- Affected files (indicative, architect to confirm):
  - `mlody/common/task.mlody` — `task` rule
  - `mlody/common/action.mlody` — `action` rule
  - `mlody/common/values.mlody` — `value` rule (if present)
  - `mlody/common/types.mlody` — `typedef` rule (if present)
  - Any other `.mlody` file containing `rule(...)` with a `name` attr
- Priority: Must Have

### 6.2 Backwards Compatibility

**FR-005: Call-site transparency**

- Description: All existing rule call sites must continue to work without
  change. `task(name="pretrain", outputs=[...], action=":train")` must behave
  identically before and after.
- Acceptance Criteria: `bazel test //mlody/...` passes without modifying any
  test file or pipeline `.mlody` file.
- Priority: Must Have

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- NFR-001: The merge of `_COMMON_ATTRS` into a rule's attrs dict happens once at
  `rule()` definition time (module load), not on every rule invocation.
  Per-invocation overhead must be zero beyond the existing `_validate_args`
  loop.

### 7.2 Maintainability Requirements

- NFR-002: Adding a new common attribute in the future must require a change to
  only `_COMMON_ATTRS` in `rule.mlody`, with no changes required in individual
  rule files.

### 7.3 Compatibility Requirements

- NFR-003: The change must be valid Starlark. Any `python.*` escapes used must
  be necessary and clearly commented.
- NFR-004: No changes to the `common/python/starlarkish` evaluator internals.

---

## 8. Data Requirements

### 8.1 Data Entities

- **Rule attrs dict** — a plain Starlark dict mapping string attribute names to
  `attr()` metadata dicts
  (`{"type": ..., "metadata": {"mandatory": ..., "default": ...}}`).
- **`ctx.attr` struct** — the `struct(**kwargs)` passed to the implementation
  function; must expose `ctx.attr.name` and `ctx.attr.description` alongside
  rule-specific attrs.

### 8.2 Name Attribute Constraints

| Property         | Constraint                                                                        |
| ---------------- | --------------------------------------------------------------------------------- |
| Type             | `string`                                                                          |
| Mandatory        | Yes                                                                               |
| Empty string     | Rejected (`ValueError`)                                                           |
| Allowed chars    | `[a-zA-Z0-9_]` only                                                               |
| Max length       | [TBD — Pending Stakeholder Input]                                                 |
| Uniqueness scope | Enforced by existing builtins registry; no new constraint added at `rule()` level |

### 8.3 Description Attribute Constraints

| Property      | Constraint          |
| ------------- | ------------------- |
| Type          | `string`            |
| Mandatory     | No                  |
| Default value | `""` (empty string) |
| Allowed chars | Unrestricted        |

---

## 9. Integration Requirements

### 9.1 Internal Dependencies

| Component                   | Interaction                                                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mlody/common/attrs.mlody`  | `rule.mlody` does NOT load `attrs.mlody` to avoid circular deps; `_COMMON_ATTRS` uses inline dict literals in the same format `attr()` produces. |
| `mlody/core/builtins.mlody` | No change. `builtins.register` continues to receive the same struct shape.                                                                       |
| `mlody/common/task.mlody`   | Removes `"name"` (and `"description"` if present) from `attrs={}`.                                                                               |
| `mlody/common/action.mlody` | Same as above.                                                                                                                                   |
| Other rule files            | Same as above.                                                                                                                                   |

---

## 10. Security & Compliance Requirements

No security or compliance requirements for this change. The `name` validation
(alphanumeric + underscore) is a correctness constraint, not a security
boundary.

---

## 11. Testing & Quality Assurance Requirements

### 11.1 Testing Scope

- **Unit tests for `rule()`:**
  - A rule defined without `name`/`description` in `attrs` exposes both on
    `ctx.attr`.
  - Conflict detection: `rule(..., attrs={"name": ...})` raises `ValueError`.
  - Conflict detection: `rule(..., attrs={"description": ...})` raises
    `ValueError`.
- **Validation tests for `name`:**
  - `name=""` raises `ValueError`.
  - `name="my task"` raises `ValueError` (space).
  - `name="my-task"` raises `ValueError` (hyphen).
  - `name="my/task"` raises `ValueError` (slash).
  - `name="valid_name_123"` succeeds.
  - `name="A"` succeeds (single alphanumeric character).
- **Regression tests:**
  - All existing `bazel test //mlody/...` targets pass unchanged.
- **Integration test:**
  - A full workspace load with the modified rule files (task, action, etc.)
    resolves correctly.

### 11.2 Acceptance Criteria

The change is complete when:

1. `_COMMON_ATTRS` is defined in `rule.mlody` with `name` and `description`.
2. `rule()` merges `_COMMON_ATTRS` and detects conflicts.
3. Name validation enforces non-empty and `[a-zA-Z0-9_]+`.
4. All `attrs={}` dicts in `mlody/common/` no longer contain `name` or
   `description`.
5. `bazel test //mlody/...` is green with no test file modifications.

---

## 12. Open Questions & Action Items

| ID   | Question / Action                                                                                                      | Owner       | Target Date | Status |
| ---- | ---------------------------------------------------------------------------------------------------------------------- | ----------- | ----------- | ------ |
| OQ-1 | Maximum length constraint for `name`? (e.g., 64 chars)                                                                 | mav         | TBD         | Open   |
| OQ-2 | Should `description` be propagated into the registered struct, or is it evaluator-only metadata?                       | mav         | TBD         | Open   |
| OQ-3 | Exact list of `.mlody` rule files that declare `name`/`description` — architect to enumerate during impact assessment. | @vitruvious | TBD         | Open   |

---

## 13. Revision History

| Version | Date       | Author                  | Changes       |
| ------- | ---------- | ----------------------- | ------------- |
| 1.0     | 2026-04-10 | Requirements Analyst AI | Initial draft |

---

**End of Requirements Document**
