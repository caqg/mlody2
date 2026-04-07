# Requirements Document: Label → Value Mapping (mlody)

**Version:** 1.0 **Date:** 2026-04-05 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft **Change ID:** mav-457-label-value-mapping

---

## 1. Executive Summary

Mlody currently parses command-line targets into `Label` objects and resolves
wildcard labels into concrete label lists. However, there is no unified step
that takes a concrete `Label` and produces the actual runtime **value** it
refers to — be it a folder, a source file, a task, an action, or an attribute
reached through a dotted field path on one of those entities.

This change introduces a new resolution step, `Label → MlodyValue`, that lives
alongside (not in place of) the existing target-string → Label resolver.
Wildcard expansion still happens before this step, so the new resolver only
deals with concrete labels. The `show` command is the first (and for v1, only)
consumer: given a concrete label, it will obtain a `MlodyValue` and render it.

The design must be extensible: while v1 only traverses attribute paths for tasks
and actions (whose registry struct supports trivial `__getattr__`), the
architecture must accommodate future kinds whose attribute traversal is driven
by a callable (for instance, a workspace-info function computed lazily from a
workspace) rather than a static struct.

---

## 2. Project Scope

### 2.1 In Scope

- A new module `mlody/resolver/` component exposing
  `resolve_label_to_value(label: Label, workspace: Workspace) -> MlodyValue`.
- Filesystem traversal from the workspace root, one path segment at a time, to
  classify what a label's path portion points to (folder vs. source file).
- Registry lookup to classify an entity (within a source file) as a task,
  action, or any other registered kind. The lookup must be kind-agnostic, though
  v1 only exercises task and action.
- A small hierarchy of value types:
  - `MlodyFolderValue`
  - `MlodySourceValue`
  - `MlodyTaskValue`
  - `MlodyActionValue`
  - `MlodyUnresolvedValue` (soft failure)
- Attribute-path traversal that fully consumes the label's field path during
  resolution, so that returned values never carry residual/unconsumed
  attributes.
- Integration with the `show` command: invoke the new resolver, render the
  result, and handle errors consistently with the existing
  `WorkspaceResolutionError` pattern.
- Testing: golden tests per value kind, plus end-to-end assertions on `show`'s
  printed output.

### 2.2 Out of Scope (v1)

- Attribute-path traversal for any kind other than task/action. Design hooks
  must exist, but no non-struct traversal is implemented in v1.
- Replacing the existing target-string → Label resolver.
- Wildcard expansion (continues to run before this step).
- Any consumer other than `show` (e.g. `run`, `build`, `query` — not in v1).
- Caching of resolved values across invocations.
- Any registry key restructuring. The existing registry key is used as-is.

### 2.3 Assumptions

- The existing `Label` type already carries a workspace, a path portion, an
  optional entity name, and an optional field/attribute path.
- Wildcard labels have already been expanded upstream; the new resolver only
  sees concrete labels.
- Everything that is `register`ed is reachable through the existing registry by
  its current key structure. No new key shape is introduced.
- `Struct.__getattr__` on task/action registry structs is sufficient for
  attribute traversal in v1.
- The workspace object already exposes the absolute filesystem root required for
  traversal.

### 2.4 Constraints

- Must follow repository Python conventions: `o_py_library`, `o_py_test`,
  basedpyright strict, ruff, absolute imports, type hints on all signatures.
- Must live under `mlody/resolver/` (new submodule, not a new top-level
  package).
- Must not regress the existing target-string → Label pipeline.
- Error handling must match the style of the existing `WorkspaceResolutionError`
  flow in `show`.

---

## 3. Stakeholders

| Role             | Name/Group        | Responsibilities                       |
| ---------------- | ----------------- | -------------------------------------- |
| Product / Author | mav               | Drives mlody direction, reviews design |
| Implementing eng | @vulcan-python    | Implements the resolver and tests      |
| Architect        | @vitruvious       | SPEC.md + design.md owner              |
| Reviewers        | mlody maintainers | Code review, API surface review        |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Give mlody a single, well-typed answer to the question "what does
  this label refer to?" so that command implementations do not each re-implement
  label interpretation.
- **BR-002:** Make the `show` command trustworthy on arbitrary labels (folder,
  source, task, action, attribute path) without hardcoding kind lookups in the
  command layer.
- **BR-003:** Provide an extensible foundation for future attribute-path
  traversal on non-struct kinds (e.g. workspace info via a callable).

### 4.2 Success Metrics

- **KPI-001:** `show <label>` produces a correct rendering for every value kind
  listed in §2.1 — measured via golden tests + end-to-end assertions.
- **KPI-002:** Zero regressions in existing resolver, parser, and `show` tests
  after the change lands.
- **KPI-003:** Adding a new value kind in the future requires changes only
  inside `mlody/resolver/` and a renderer addition — not in the `show` command's
  orchestration code.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: Mlody user (ML engineer / pipeline author)**

- Runs `mlody show <label>` to inspect what a label refers to.
- Expects legible output for folders, sources, tasks, actions, and attribute
  paths into tasks/actions.
- Expects a clean soft-failure message when a label does not resolve, rather
  than a stack trace.

**Persona 2: Mlody contributor**

- Adds new entity kinds or new attribute-traversal strategies.
- Expects one place (`mlody/resolver/`) to plug new kinds in.

### 5.2 User Stories

- **US-001:** As a mlody user, I run `mlody show //pkg/foo` and see it
  identified as a folder with its child entries. (Must)
- **US-002:** As a mlody user, I run `mlody show //pkg/foo` where `foo.mlody`
  exists and see it identified as a source file. (Must)
- **US-003:** As a mlody user, I run `mlody show //pkg/foo:my_task` and see it
  identified as a task. (Must)
- **US-004:** As a mlody user, I run `mlody show //pkg/foo:my_action` and see it
  identified as an action. (Must)
- **US-005:** As a mlody user, I run
  `mlody show //pkg/foo:my_task.some.nested.field` and see the resolved
  attribute value. (Must)
- **US-006:** As a mlody user, when a label does not resolve, I see a clear
  "unresolved" message and `show` exits with status 1. (Must)
- **US-007:** As a mlody contributor, I can add a new kind whose attribute
  traversal is driven by a callable rather than struct `__getattr__`, without
  touching the `show` command. (Should — v1 design only)

---

## 6. Functional Requirements

### 6.1 Resolver Module

**FR-001: Module location and public API**

- Location: `mlody/resolver/` (new files alongside existing `resolver.py`; do
  not rename or replace the existing file).
- Public entry point (v1):
  ```python
  def resolve_label_to_value(
      label: Label, workspace: Workspace
  ) -> MlodyValue: ...
  ```
- Priority: Must
- Dependencies: existing `Label`, `Workspace`, and registry APIs.

### 6.2 Resolution Pipeline

**FR-002: Step-by-step resolution**

The resolver MUST perform these steps, in order, for a concrete (non-wildcard)
label:

1. **Workspace anchoring.** Start at the workspace root derived from
   `label.workspace`.
2. **Path traversal.** If the label carries additional path components beyond
   the workspace, walk them one segment at a time against the filesystem to
   determine whether the terminal segment is a folder or a source file. Do not
   assume — inspect the filesystem at each step.
3. **Terminal classification (path only).**
   - The terminal path segment is checked against the filesystem: first as a
     directory, then as a `.mlody` file (appending `.mlody` to the segment
     name). The `.mlody` suffix is never present in the label itself.
   - If terminal is a directory and no entity is specified → produce
     `MlodyFolderValue`.
   - If `<segment>.mlody` exists and no entity is specified → produce
     `MlodySourceValue`.
4. **Entity lookup (when an entity is specified).** If the label specifies an
   entity name on a source file, consult the registry to determine the entity's
   kind. The lookup MUST be kind-agnostic (not hardcoded to task/action), though
   v1 only exercises task and action.
5. **Entity classification.**
   - Task → `MlodyTaskValue` wrapping the registry struct opaquely.
   - Action → `MlodyActionValue` wrapping the registry struct opaquely.
   - Any other registered kind → routed through the same extensible mechanism
     (v1: not exercised, but the code path must exist as a seam).
6. **Attribute-path traversal.** If the label carries a field path, fully
   consume it against the entity value using the kind's configured traversal
   strategy (see FR-005). Attributes are consumed during resolution; the
   returned value MUST NOT carry residual attributes.
7. **Soft failure.** Any step that cannot proceed (missing directory, unknown
   entity, missing attribute, traversal not supported for kind) MUST return
   `MlodyUnresolvedValue` rather than raising. Include enough context in the
   unresolved value to render a useful message.

- Priority: Must

### 6.3 Value Types

**FR-003: `MlodyValue` hierarchy**

All value types share a common base `MlodyValue`. None of them carry a
`field_path` attribute — by the time resolution returns, all attributes have
been consumed.

- `MlodyFolderValue` — identifies a folder on disk under the workspace. Fields:
  workspace-relative path, list of immediate children (at minimum enough to
  render).
- `MlodySourceValue` — identifies a source file on disk. Fields: workspace-
  relative path only. Does **not** carry the raw file text.
- `MlodyTaskValue` — opaque wrapper around the task's registry `Struct`. No
  field extraction into dedicated attributes.
- `MlodyActionValue` — opaque wrapper around the action's registry `Struct`. No
  field extraction into dedicated attributes.
- `MlodyUnresolvedValue` — carries the original label and a human-readable
  reason string.

- Priority: Must

### 6.4 Registry Lookup

**FR-004: Kind-agnostic registry lookup**

- Use the existing registry key as-is. Do not introduce a new key shape.
- The lookup function must accept a source file + entity name and return
  whatever the registry holds, along with its kind tag, without special-casing
  kinds at the call site.
- v1 will only see task and action kinds in practice, but the dispatch must be a
  table / mapping keyed on kind, not an `if kind == "task"` chain.

- Priority: Must

### 6.5 Attribute-Path Traversal (Extensible)

**FR-005: Traversal strategy per kind**

- Each kind registers (or is configured with) a traversal strategy. v1 ships one
  strategy:
  - **Struct traversal:** for task and action, consume the field path by trivial
    `Struct.__getattr__` walks. At each step, if the next attribute is missing,
    return `MlodyUnresolvedValue`.
- The design MUST make it straightforward to add additional strategies in future
  without touching caller code. In particular, it must be possible to add a
  **callable-based strategy**, where a kind's traversal function takes a
  `Workspace` (and/or the current partially-resolved value) and computes the
  next step dynamically — e.g. a future `workspace-info` kind whose struct is
  computed lazily rather than stored.
- v1 MUST NOT implement any callable-based strategy. It only needs to leave the
  seam (a strategy interface / dispatch point) in place and covered by a design
  note.
- Strategies MUST fully consume the field path or soft-fail. Partial consumption
  is not a supported return state.

- Priority: Must (struct strategy) / Should (extension seam)

### 6.6 Ephemeral / Virtual Location Handling

**FR-006: "Virtual" locations**

Some values returned by the resolver correspond to entities that do not exist as
a standalone file or directory on disk — for example, an attribute deep inside a
task struct. These MUST be representable as the appropriate `MlodyValue` subtype
(e.g. the traversal's terminal Python value wrapped in the task/action value, or
an unresolved value if traversal fails). The resolver MUST NOT fabricate
filesystem paths for such locations, and MUST NOT fail solely because the
terminal value lacks a filesystem backing.

- Priority: Must

### 6.7 Error Handling

**FR-007: Soft failures inside the resolver**

- The resolver never raises for "label does not resolve" conditions. It returns
  `MlodyUnresolvedValue` with a reason.
- Programmer errors (type mismatches, invalid inputs, corrupted registry) MAY
  still raise — those are bugs, not user-visible resolution failures.

**FR-008: Hard failures in `show`**

- The `show` command MUST catch the same family of errors already handled for
  `WorkspaceResolutionError`, print the error in red, and call `sys.exit(1)`.
- `MlodyUnresolvedValue` is NOT an exception — `show` renders it as a
  user-facing "unresolved" message and also exits non-zero (status 1) to match
  the existing behavior pattern.

- Priority: Must

### 6.8 `show` Command Integration

**FR-009: `show` pipeline**

The `show` command, for a given user-supplied target string, MUST:

1. Parse the target string into a `Label` (existing path).
2. Expand wildcards into concrete labels (existing path).
3. For each concrete label, call `resolve_label_to_value(label, workspace)`.
4. Dispatch rendering based on the returned `MlodyValue` subtype.
5. On `MlodyUnresolvedValue`, print a red error and exit 1.
6. On resolver-level exceptions (bugs / workspace errors), match the existing
   `WorkspaceResolutionError` handling — red message, exit 1.

- Priority: Must

---

## 7. Non-Functional Requirements

### 7.1 Performance

- Filesystem traversal MUST be one step at a time but MAY cache directory
  listings within a single resolver call. Cross-call caching is out of scope.

### 7.2 Maintainability

- Adding a new value kind or traversal strategy MUST be a localized change
  inside `mlody/resolver/`, plus a renderer addition in `show`.
- No kind-specific branching in `show`'s orchestration code — only in its
  rendering layer.

### 7.3 Compatibility

- No changes to the existing target-string → Label resolver's public API.
- No changes to the `Label` type's public shape.
- Python 3.13.2, basedpyright strict, ruff-clean.

### 7.4 Usability

- Error messages surfaced via `MlodyUnresolvedValue` MUST name both the label
  and the specific step that failed (e.g. "no such directory", "entity not found
  in registry", "attribute `foo.bar` missing on task").

---

## 8. Data Requirements

### 8.1 Entities

- `Label` — already exists.
- `Workspace` — already exists.
- `MlodyValue` and subtypes — new, defined in `mlody/resolver/`.
- Registry entries — already exist; accessed via existing API and key structure.

### 8.2 Data Retention

- Not applicable. The resolver is stateless between calls.

---

## 9. Integration Requirements

### 9.1 Internal

- Label parser / wildcard expander — upstream of this step, unchanged.
- Registry — read-only dependency.
- `show` command — the sole v1 consumer.

### 9.2 External

- None.

---

## 10. User Interface Requirements

Out of scope for this change beyond what `show` already does. Rendering per
value kind is a presentation detail of `show`; the resolver only supplies typed
values.

---

## 11. Reporting & Analytics

Not applicable.

---

## 12. Security & Compliance

Not applicable — no new surface exposed beyond the existing CLI.

---

## 13. Infrastructure & Deployment

No new infrastructure. Standard mlody Bazel targets.

---

## 14. Testing & Quality Assurance

### 14.1 Testing Scope

- **Golden tests, per value kind.** For each of `MlodyFolderValue`,
  `MlodySourceValue`, `MlodyTaskValue`, `MlodyActionValue`,
  `MlodyUnresolvedValue`, and attribute-path traversal into tasks and actions: a
  golden fixture asserting the resolver returns the expected value shape.
- **End-to-end `show_test.py` tests.** Run `show` against a fixture workspace
  and assert on the printed output for each kind above, including the unresolved
  case.
- **Extensibility placeholder test (Should).** A unit test that registers a
  dummy kind with a stub traversal strategy and verifies the resolver dispatches
  to it — guards the seam without implementing callable traversal in product
  code.

### 14.2 Acceptance Criteria

- All golden tests pass.
- All `show_test.py` end-to-end assertions pass.
- `bazel test //mlody/...` green.
- `bazel build --config=lint //mlody/...` clean.
- Existing tests unchanged or updated only where the new value types replace
  ad-hoc interpretation.

---

## 15. Training & Documentation

- Inline docstrings on the resolver public API and on each value type.
- A short design note inside the module (or in `design.md`) describing the
  extension seam for callable-based traversal.

---

## 16. Risks & Mitigation

| Risk ID | Description                                                                  | Impact | Probability | Mitigation                                                                                 |
| ------- | ---------------------------------------------------------------------------- | ------ | ----------- | ------------------------------------------------------------------------------------------ |
| R-001   | Attribute traversal design leaks struct assumptions, blocking callable kinds | High   | Medium      | Define the traversal strategy as a protocol/interface from day one, even with one impl     |
| R-002   | Ambiguity between "folder" and "source" when filenames collide               | Medium | Low         | Traverse one segment at a time and inspect the terminal segment's filesystem kind directly |
| R-003   | Registry key semantics change in future                                      | Medium | Low         | Treat the registry key as opaque; go through the existing lookup function                  |
| R-004   | `MlodyUnresolvedValue` swallowed silently                                    | Medium | Low         | `show` MUST exit 1 on unresolved, and tests assert the exit code                           |
| R-005   | Duplicated logic between old resolver and new resolver                       | Low    | Medium      | Document clearly in design.md that old = string→Label, new = Label→Value                   |

---

## 17. Dependencies

| Dependency                     | Type     | Status   | Impact if Delayed        |
| ------------------------------ | -------- | -------- | ------------------------ |
| Existing `Label` type          | Internal | Complete | Blocks all               |
| Existing wildcard expansion    | Internal | Complete | Blocks `show` wiring     |
| Existing registry lookup API   | Internal | Complete | Blocks entity classify   |
| Existing `show` error handling | Internal | Complete | Blocks error integration |

---

## 18. Open Questions & Action Items

| ID   | Question/Action                                                                                                        | Owner       | Status |
| ---- | ---------------------------------------------------------------------------------------------------------------------- | ----------- | ------ |
| Q-01 | Exact rendering format for each value kind in `show`                                                                   | mav         | Open   |
| Q-02 | Whether the struct traversal strategy should live inside the resolver module or next to the registry struct definition | @vitruvious | Open   |
| Q-03 | Naming: `MlodyValue` vs. `ResolvedValue` vs. other — confirm in design.md                                              | @vitruvious | Open   |

---

## 19. Revision History

| Version | Date       | Author                  | Changes       |
| ------- | ---------- | ----------------------- | ------------- |
| 1.0     | 2026-04-05 | Requirements Analyst AI | Initial draft |

---

## Appendices

### Appendix A: Glossary

- **Label** — mlody's structured reference to a location: workspace + path +
  optional entity + optional field path.
- **Concrete label** — a label with no wildcards (the only input the new
  resolver accepts).
- **Registry** — mlody's runtime table of everything passed to `register`.
- **Entity** — a named, registered thing inside a source file (task, action,
  etc.).
- **Kind** — the classification tag of an entity (task, action, …).
- **Attribute path / field path** — the dotted suffix on a label that navigates
  into an entity's structure.
- **Unresolved value** — the soft-failure sentinel returned when any step of
  resolution cannot proceed.

### Appendix B: References

- Existing `mlody/resolver/resolver.py` (target-string → Label; unchanged).
- Existing `show` command error handling for `WorkspaceResolutionError`.
- Prior changes: `mlody-label-parsing`, `dag-label-filter`.

### Appendix C: Example Resolution Traces

Labels never include the `.mlody` suffix — the resolver infers it by checking
the filesystem. The colon (`:`) separates the path portion from the entity name;
the last path segment before `:` (or the terminal path segment when no `:` is
present) is the one checked for a corresponding `.mlody` file on disk.

1. `//pkg/foo` → walk `pkg/foo`, terminal is a directory → `MlodyFolderValue`.
2. `//pkg/foo` → walk `pkg/foo`, no directory but `foo.mlody` exists →
   `MlodySourceValue`.
3. `//pkg/foo:my_task` → last path segment is `foo`; `foo.mlody` exists in
   `pkg/`; entity `my_task` found in registry with kind=task →
   `MlodyTaskValue(struct)`.
4. `//pkg/foo:my_action` → same path resolution; entity `my_action` found with
   kind=action → `MlodyActionValue(struct)`.
5. `//pkg/foo:my_task.inputs.count` → as trace 3, then consume `.inputs.count`
   via struct traversal → terminal value (or `MlodyUnresolvedValue` if any step
   is missing).
6. `//pkg/foo:does_not_exist` → `foo.mlody` exists but entity not in registry →
   `MlodyUnresolvedValue(reason="entity not found")`.

---

**End of Requirements Document**
