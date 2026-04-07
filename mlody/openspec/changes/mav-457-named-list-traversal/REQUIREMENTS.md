# Requirements Document: Named List Traversal for Task and Action Ports

**Version:** 1.0 **Date:** 2026-04-05 **Prepared by:** Requirements Analyst AI
**Status:** Draft

---

## 1. Executive Summary

When a `task` or `action` entity is registered in the mlody evaluator, its
`inputs`, `outputs`, and `config` fields are stored as flat Python lists of
element structs. Each element has a `name` field. The current
`Workspace.resolve` method contains a manual list-traversal workaround inside
`_step` that scans these lists by element name at query time. This is leaky —
the traversal logic is tied to the resolver rather than to the registered shape
of the entity.

This change moves that conversion to registration time. When a `task` or
`action` is registered, each of its `inputs`, `outputs`, and `config` lists is
converted into a Starlark `Struct` keyed by the `name` field of each element.
The stored entity then exposes `task.outputs` as a `Struct` with named fields
rather than a list, and downstream traversal — `task.outputs.backbone_weights`,
`task.outputs.backbone_weights.location` — works via the standard
`StructTraversalStrategy` with no additional special-casing.

The expected business value is a cleaner, more predictable label-resolution
model: after registration, all fields on `task` and `action` entities are plain
`Struct` objects or scalar values, and attribute access follows a single uniform
path.

---

## 2. Project Scope

### 2.1 In Scope

- Converting `inputs`, `outputs`, and `config` lists to named `Struct` objects
  for `task` and `action` entity kinds at registration time.
- The conversion hook lives in `mlody/core/workspace.py`, applied after
  `self._evaluator.resolve()` completes (or as a post-registration callback if
  the evaluator supports one), not inside starlarkish internals.
- Further attribute traversal on the resulting element structs (e.g.
  `.location`, `.type`, `.source`) continues to work via `getattr` as before.
- Error handling when an element in any of the three lists is missing a `name`
  field.

### 2.2 Out of Scope

- Changes to `starlarkish` internals (`evaluator.py`, `struct.py`, or any file
  under `common/python/starlarkish/`).
- Conversion of any fields other than `inputs`, `outputs`, and `config`.
- Conversion for entity kinds other than `task` and `action`.
- Changes to `StructTraversalStrategy` in `label_value.py`.
- Changes to the `_step` helper inside `Workspace.resolve` (the existing
  list-scan fallback may be left in place or removed as a follow-up; this
  feature does not require touching it).
- Schema validation beyond confirming a `name` field is present.

### 2.3 Assumptions

- By the time the conversion hook runs, starlarkish has already validated that
  every element struct that requires a `name` field has one (enforced during
  `.mlody` file evaluation). The conversion therefore does not need to implement
  a full schema validator, but it must still raise a clear error if a `name` is
  absent rather than silently dropping or skipping the element.
- Element struct field values (`type`, `location`, `source`, `_lineage`, etc.)
  must be preserved exactly as-is on the converted struct; the conversion only
  changes the container from a `list` to a `Struct`.
- `action` structs embedded inside `task` structs (stored in `task.action`) also
  have `inputs`, `outputs`, and `config` lists and must receive the same
  conversion so that `task.action.outputs.backbone_weights` works too.

### 2.4 Constraints

- No modifications to starlarkish source files are permitted from within the
  mlody package.
- The conversion must be idempotent: if the field is already a `Struct` (e.g.
  due to a future double-registration), the hook must not raise or corrupt the
  value.
- Python 3.13.2, strict basedpyright type checking, ruff formatting.
- All new code must use `o_py_library` / `o_py_test` Bazel rules from
  `//build/bzl:python.bzl`.

---

## 3. Stakeholders

| Role                      | Name/Group    | Responsibilities                        | Contact |
| ------------------------- | ------------- | --------------------------------------- | ------- |
| Requester / Product Owner | mav           | Feature definition, acceptance sign-off | —       |
| Implementation            | vulcan-python | Python implementation in `workspace.py` | —       |
| Architecture              | vitruvious    | Spec and design review                  | —       |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Label traversal for `task` and `action` entities must follow a
  single, uniform attribute-access model so that
  `@lexica//diamond:pretrain.outputs.backbone_weights` resolves without any
  list-scan fallback code in the resolver.
- **BR-002:** The registered shape of `task` and `action` entities must be
  self-consistent: consumers of `self._evaluator.all` (CLI, LSP, future tooling)
  should be able to rely on `task.outputs` being a `Struct`, not a list.

### 4.2 Success Metrics

- **KPI-001:** `ws.resolve("@lexica//diamond:pretrain.outputs")` returns a
  `Struct` with a field named `backbone_weights` (and any other named outputs).
  Measurement: test assertion.
- **KPI-002:**
  `ws.resolve("@lexica//diamond:pretrain.outputs.backbone_weights")` returns the
  full element struct (same object as `resolve(...).outputs.backbone_weights`).
  Measurement: test assertion with `==` comparison.
- **KPI-003:** Further traversal —
  `ws.resolve("@lexica//diamond:pretrain.outputs.backbone_weights.location")` —
  returns the `.location` value of that element struct. Measurement: test
  assertion.
- **KPI-004:** Zero changes required to `StructTraversalStrategy` or any
  starlarkish file. Measurement: `git diff common/python/starlarkish/` is empty
  after the change.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: mlody pipeline author**

- Writes `.mlody` files; uses labels like
  `@lexica//diamond:pretrain.outputs.backbone_weights` in other pipeline
  definitions or CLI show commands.
- Expects `.outputs` to behave like a struct with named fields, consistent with
  how every other struct field in the mlody data model works.
- Does not care where in the Python stack the conversion happens as long as it
  is transparent.

**Persona 2: mlody CLI / tooling author**

- Iterates `self._evaluator.all` to inspect registered entities.
- Expects `task.outputs` and `action.outputs` to be `Struct` objects so that
  generic struct-introspection code (e.g. LSP hover, `mlody show`) does not need
  to special-case lists.

### 5.2 User Stories

**Epic 1: Named port access via label traversal**

- **US-001:** As a pipeline author, I want
  `@lexica//diamond:pretrain.outputs.backbone_weights` to resolve to the full
  element struct, so that I can reference individual outputs by name in other
  pipeline definitions.
  - Acceptance Criteria:
    - Given a task `pretrain` with
      `outputs=[value(name="backbone_weights", ...)]` registered in root
      `lexica`,
    - When I call
      `ws.resolve("@lexica//diamond:pretrain.outputs.backbone_weights")`,
    - Then the result is the element struct with `name == "backbone_weights"`.
  - Priority: High (Must Have)

- **US-002:** As a pipeline author, I want further traversal such as
  `@lexica//diamond:pretrain.outputs.backbone_weights.location` to work, so that
  I can access individual fields of an output element.
  - Acceptance Criteria:
    - Given US-001 passes,
    - When I call
      `ws.resolve("@lexica//diamond:pretrain.outputs.backbone_weights.location")`,
    - Then the result equals `getattr(element_struct, "location")`.
  - Priority: High (Must Have)

- **US-003:** As a pipeline author, I want `@lexica//diamond:pretrain.outputs`
  (without a further key) to return the synthesised `Struct` of all named
  outputs, so that I can inspect the full output namespace of a task.
  - Acceptance Criteria:
    - Given a task `pretrain` with two outputs named `backbone_weights` and
      `tokenizer`,
    - When I call `ws.resolve("@lexica//diamond:pretrain.outputs")`,
    - Then the result is a `Struct` with fields `backbone_weights` and
      `tokenizer`.
  - Priority: High (Must Have)

- **US-004:** As a tooling author, I want `task.inputs`, `task.outputs`, and
  `task.config` to be `Struct` objects in `self._evaluator.all`, so that generic
  struct-traversal code does not need list-scan logic.
  - Acceptance Criteria:
    - Given a loaded workspace,
    - When I iterate `self._evaluator.all` and find a `task` entity,
    - Then `getattr(entity, "outputs")` is an instance of `Struct`.
  - Priority: High (Must Have)

- **US-005:** As a tooling author, I want the same named-struct shape on the
  `action` struct embedded inside a `task` (i.e. `task.action.outputs`), so that
  action-port traversal is also uniform.
  - Acceptance Criteria:
    - Given a task `pretrain` whose `action` has
      `outputs=[value(name="weights")]`,
    - When I call
      `ws.resolve("@lexica//diamond:pretrain.action.outputs.weights")`,
    - Then the result is the action-level element struct for `weights`.
  - Priority: Medium (Should Have)

---

## 6. Functional Requirements

### 6.1 Port List Conversion

**FR-001: Convert `inputs`, `outputs`, `config` lists to named Structs after
registration**

- **Description:** After `self._evaluator.resolve()` completes in
  `Workspace.load()`, iterate over all registered `task` and `action` entities
  and replace the `inputs`, `outputs`, and `config` fields with a `Struct`
  constructed by calling `struct(**{el.name: el for el in field_list})`.
- **Inputs:** A registered `task` or `action` Struct whose `inputs`, `outputs`,
  or `config` field is a Python list (or Starlark list) of element structs, each
  carrying a `name` field.
- **Processing:**
  1. For each entity of kind `"task"` or `"action"` in `self._evaluator.all`: a.
     For each of the three fields (`inputs`, `outputs`, `config`):
     - If the field is already a `Struct`, skip it (idempotency).
     - If the field is `None` or an empty list, replace with an empty `Struct`
       (i.e. `struct()`).
     - Otherwise, build a mapping `{el.name: el for el in field_list}`.
     - If any element is missing a `name` field, raise `ValueError` with a
       message that includes the entity kind, entity name, field name, and
       element index.
     - Construct a new `Struct` from the mapping using `struct(**mapping)`. b.
       Reconstruct the entity Struct with the three converted fields, keeping
       all other fields unchanged. c. Write the reconstructed Struct back into
       `self._evaluator.all` at the original key.
  2. For `task` entities, also apply the same conversion to the `action` field
     if it is itself a `Struct` with `kind == "action"` (i.e. the merged action
     embedded in the task struct).
- **Outputs:** All `task` and `action` entries in `self._evaluator.all` have
  `inputs`, `outputs`, and `config` as `Struct` objects keyed by element name.
- **Business Rules:**
  - BR-CONV-1: The conversion must not alter any field other than `inputs`,
    `outputs`, and `config` on the entity struct.
  - BR-CONV-2: The conversion must not alter the element structs themselves;
    each element value inside the named `Struct` must be identical (by
    reference) to the original element in the list.
  - BR-CONV-3: Duplicate names within a single list are an error; raise
    `ValueError` identifying the entity, field, and duplicate name.
  - BR-CONV-4: The conversion must run after `self._evaluator.resolve()` and
    before `Workspace.load()` returns, so that all downstream callers (including
    `Workspace.resolve`) see the converted shape.
- **Priority:** Must Have
- **Dependencies:** `starlarkish.core.struct.struct` and `Struct` (read-only
  usage)

**FR-002: Error on missing `name` field**

- **Description:** If an element in a port list does not carry a `name` field
  (i.e. `getattr(el, "name", None) is None`), raise a `ValueError` before any
  partial conversion is applied.
- **Inputs:** Element struct without a `name` attribute.
- **Processing:** Check `getattr(el, "name", None)` for each element. On
  failure, raise with a message:
  `"<kind> '<entity_name>'.<field>: element at index <i> is missing required 'name' field"`.
- **Priority:** Must Have

**FR-003: Idempotent conversion**

- **Description:** If a port field is already a `Struct` instance (not a list),
  the conversion hook must leave it unchanged.
- **Priority:** Must Have

### 6.2 Resolver Integration

**FR-004: No changes to `Workspace.resolve` required for basic traversal**

- **Description:** After FR-001 is in place, `Workspace.resolve` must be able to
  resolve labels of the form `@root//pkg:task.outputs.backbone_weights` purely
  via the existing `getattr` chain in `_step`. No new list-scan logic is needed.
- **Priority:** Must Have

**FR-005: Existing list-scan fallback in `_step` may remain**

- **Description:** The `_step` helper in `Workspace.resolve` currently contains
  a `isinstance(obj, list)` branch that scans by element name. This can remain
  as-is for backward compatibility; it is not required to be removed by this
  feature.
- **Priority:** Won't Have (removal is out of scope for this change)

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-PERF-001:** The conversion pass must complete in O(N \* M) time where N
  is the number of registered `task`/`action` entities and M is the maximum
  number of elements in any single port list. No database or I/O operations are
  involved; this is a pure in-memory transformation.

### 7.2 Correctness and Safety

- **NFR-SAFE-001:** The conversion must not mutate any `Struct` object in place.
  All modifications must produce new `Struct` instances.
- **NFR-SAFE-002:** No element struct may be lost during conversion. The set of
  element structs reachable from a converted field must equal the set reachable
  from the original list.

### 7.3 Maintainability

- **NFR-MAINT-001:** The conversion logic must be encapsulated in a clearly
  named private helper function within `workspace.py` (e.g.
  `_convert_ports_to_structs`) with a docstring, rather than inlined into
  `load()`.
- **NFR-MAINT-002:** Type annotations must be complete and pass basedpyright in
  strict mode.

### 7.4 Compatibility

- **NFR-COMPAT-001:** The change must not break any existing test in
  `//mlody/...`. All tests must pass after the change.
- **NFR-COMPAT-002:** The starlarkish package
  (`//common/python/starlarkish/...`) must remain unmodified.

---

## 8. Data Requirements

### 8.1 Data Entities

**Task Struct (before conversion)**

```
Struct(
  kind    = "task",
  name    = "<str>",
  inputs  = [Struct(kind="value", name="<str>", ...), ...],
  outputs = [Struct(kind="value", name="<str>", ...), ...],
  action  = Struct(kind="action", ...),
  config  = [Struct(kind="value", name="<str>", ...), ...],
)
```

**Task Struct (after conversion)**

```
Struct(
  kind    = "task",
  name    = "<str>",
  inputs  = Struct(<name>=Struct(kind="value", name="<str>", ...), ...),
  outputs = Struct(<name>=Struct(kind="value", name="<str>", ...), ...),
  action  = Struct(kind="action", inputs=Struct(...), outputs=Struct(...), ...),
  config  = Struct(<name>=Struct(kind="value", name="<str>", ...), ...),
)
```

**Action Struct (before and after conversion):** Same pattern as task —
`inputs`, `outputs`, `config` lists become named Structs.

### 8.2 Data Quality Requirements

- Every element struct in `inputs`, `outputs`, `config` must have a non-empty
  string `name` field after starlarkish evaluation. The conversion assumes this
  invariant and raises `ValueError` if it is violated.

---

## 9. Integration Requirements

### 9.1 Integration Point: starlarkish Evaluator

- **Purpose:** Read registered entities from `self._evaluator.all` and write
  back the converted forms.
- **Type:** In-process Python object mutation (no IPC or network).
- **Direction:** Read then write to `self._evaluator.all`.
- **Constraints:** The evaluator's internal `_roots_by_name`, `loaded_files`,
  and `_module_globals` must not be touched. Only `self._evaluator.all` is
  modified.
- **Timing:** After `self._evaluator.resolve()` returns, before `Workspace.load`
  returns to its caller.

### 9.2 starlarkish API Used

| Symbol                | Module                            | Usage                                 |
| --------------------- | --------------------------------- | ------------------------------------- |
| `Struct`              | `starlarkish.core.struct`         | `isinstance` check and reconstruction |
| `struct()`            | `starlarkish.core.struct`         | Build named-field containers          |
| `self._evaluator.all` | `starlarkish.evaluator.evaluator` | Read/write registered entities        |

---

## 10. User Interface Requirements

Not applicable. This is a backend data-model transformation with no UI surface.

---

## 11. Reporting & Analytics Requirements

Not applicable.

---

## 12. Security & Compliance Requirements

Not applicable. This change operates entirely on in-process Python objects
derived from trusted `.mlody` source files loaded by the workspace owner.

---

## 13. Infrastructure & Deployment Requirements

Not applicable. This change is a pure Python library modification with no
deployment or infrastructure impact.

---

## 14. Testing & Quality Assurance Requirements

### 14.1 Testing Scope

All tests must use `pyfakefs` or `starlarkish.evaluator.testing.InMemoryFS` for
`.mlody` content. No real filesystem access in tests. No mocking of starlarkish.

**Required test cases:**

- **TC-001 — Basic named access:** A task with `outputs=[value(name="weights")]`
  is registered; after `ws.load()`,
  `ws.resolve("@root//pkg:task.outputs.weights")` returns the element struct.
- **TC-002 — Synthesised outputs struct:**
  `ws.resolve("@root//pkg:task.outputs")` returns a `Struct`;
  `getattr(result, "weights")` returns the element struct.
- **TC-003 — Deep traversal:**
  `ws.resolve("@root//pkg:task.outputs.weights.location")` returns
  `getattr(element_struct, "location")`.
- **TC-004 — Config and inputs:** Same assertions for `inputs` and `config`
  fields.
- **TC-005 — Action entity:** Directly registered `action` entities undergo the
  same conversion; `ws.resolve("@root//pkg:action_name.outputs.weights")` works.
- **TC-006 — Embedded action:** `task.action.outputs.weights` is accessible via
  `ws.resolve("@root//pkg:task.action.outputs.weights")`.
- **TC-007 — Empty list:** A task with `config=[]` produces an empty `Struct` on
  `task.config`; no error is raised.
- **TC-008 — Missing name field:** A task whose output element lacks a `name`
  field causes `Workspace.load()` to raise `ValueError` with a message
  identifying the entity and field.
- **TC-009 — Duplicate name:** Two elements with the same `name` in one field
  cause `Workspace.load()` to raise `ValueError`.
- **TC-010 — Idempotency:** Calling the conversion helper twice on the same
  entity does not raise or double-wrap the struct.
- **TC-011 — Non-port fields unchanged:** `task.name`, `task.kind`,
  `task.action.kind`, `task.action.name`, etc. are not affected.

### 14.2 Acceptance Criteria

All eleven test cases pass. `bazel test //mlody/...` reports no regressions.
`bazel build --config=lint //mlody/...` reports no lint or type errors.

---

## 15. Training & Documentation Requirements

Not applicable for this internal framework change. The `.mlody` sandbox
documentation in `mlody/CLAUDE.md` may warrant a brief note that `task.outputs`
is a named Struct rather than a list, but this is a follow-up concern outside
this change's scope.

---

## 16. Risks & Mitigation Strategies

| Risk ID | Description                                                                                          | Impact | Probability | Mitigation                                                                                                                            | Owner         |
| ------- | ---------------------------------------------------------------------------------------------------- | ------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| R-001   | `self._evaluator.all` does not support item assignment (read-only mapping)                           | High   | Low         | Verify API before implementing; if read-only, use a separate post-processing dict or extend Evaluator with a sanctioned mutation hook | vulcan-python |
| R-002   | Embedded action struct inside task struct is not updated, breaking `task.action.outputs.X` traversal | Medium | Medium      | Explicitly recurse into `task.action` during conversion (covered in FR-001 processing step 2)                                         | vulcan-python |
| R-003   | Existing tests rely on `task.outputs` being a list (e.g. iterating with `for v in task.outputs`)     | Medium | Low         | Audit existing tests before implementing; add compatibility shim if needed                                                            | vulcan-python |
| R-004   | Starlark-level code inside `.mlody` files iterates `task.outputs` as a list                          | High   | Low         | Search for `for .* in.*outputs` patterns in `.mlody` files before landing                                                             | mav           |

---

## 17. Dependencies

| Dependency                                      | Type               | Status                             | Impact if Delayed                                        | Owner            |
| ----------------------------------------------- | ------------------ | ---------------------------------- | -------------------------------------------------------- | ---------------- |
| starlarkish `struct()` / `Struct` API stability | Internal library   | Stable                             | Conversion logic must be rewritten if Struct API changes | starlarkish team |
| `self._evaluator.all` being a mutable mapping   | Runtime assumption | [Assumption — Requires Validation] | May require alternative mutation approach                | vulcan-python    |

---

## 18. Open Questions & Action Items

| ID     | Question/Action                                                                                                                                                                 | Owner         | Target Date           | Status |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | --------------------- | ------ |
| OQ-001 | Is `self._evaluator.all` a mutable dict or a read-only view? Confirm before implementing FR-001.                                                                                | vulcan-python | Before implementation | Open   |
| OQ-002 | Should the existing list-scan fallback in `Workspace.resolve._step` be removed as part of this change or deferred?                                                              | mav           | Before implementation | Open   |
| OQ-003 | Are there any `.mlody` files in the current codebase that iterate over `task.outputs` or `action.outputs` as a list at the Starlark level? Requires grep before implementation. | vulcan-python | Before implementation | Open   |
| OQ-004 | Should the empty-list case produce `struct()` (empty Struct) or keep `None`/`[]`? Current assumption: `struct()`.                                                               | mav           | Before implementation | Open   |

---

## 19. Revision History

| Version | Date       | Author                  | Changes       |
| ------- | ---------- | ----------------------- | ------------- |
| 1.0     | 2026-04-05 | Requirements Analyst AI | Initial draft |

---

## Appendices

### Appendix A: Glossary

| Term                      | Definition                                                                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Element struct            | A `Struct` with `kind="value"` representing a single input, output, or config slot on a task or action. Always carries a `name` field.      |
| Port list                 | The `inputs`, `outputs`, or `config` field of a registered `task` or `action` entity, currently stored as a Python list of element structs. |
| Named Struct              | A `Struct` whose fields are the `name` values of the elements from the original port list. `struct(backbone_weights=el1, tokenizer=el2)`.   |
| Registration              | The act of calling `builtins.register("task", task_struct)` from within a `.mlody` file; starlarkish stores the struct in `Evaluator.all`.  |
| `StructTraversalStrategy` | The component in `mlody/lsp/label_value.py` (and used by `Workspace.resolve`) that traverses nested `Struct` objects via `getattr`.         |

### Appendix B: References

- `mlody/core/workspace.py` — primary implementation target; see
  `Workspace.load` and `Workspace.resolve._step`.
- `mlody/common/task.mlody` — defines `_task_impl` which builds the `task`
  struct with list-typed `inputs`/`outputs`/`config`.
- `mlody/common/action.mlody` — defines `_action_impl` which builds the `action`
  struct.
- `common/python/starlarkish/core/struct.py` — `Struct` and `struct()` API
  (read-only reference; must not be modified).
- `common/python/starlarkish/evaluator/evaluator.py` — `Evaluator.all` mapping
  (read-only reference).

---

**End of Requirements Document**
