# Requirements Document: Mlody Value Representation

**Version:** 1.0 **Date:** 2026-04-07 **Prepared by:** Requirements Analyst AI
**Status:** Draft

---

## 1. Executive Summary

Mlody values (`value()` declarations) describe named data ports in a pipeline:
their type, location, and optional default. Currently, a `value()` carries no
information about _how_ its data is serialised on disk. This matters because the
same logical value may be materialised in multiple formats: a model might be
stored both as a canonical binary checkpoint and as a JSON metadata sidecar;
config files are commonly JSON or YAML; feature vectors may be Parquet or CSV.

The proposed feature introduces a `representation` attribute on `value()` that
associates a serialisation format — initially JSON — with a value. A
representation is a first-class, registered DSL concept (analogous to how
`location` is a registered kind) so that the framework, tooling, and future
execution engines can discover and act on serialisation requirements without
inspecting raw field values.

The feature is scoped to the DSL layer: parsing, validation, and struct
construction in the `.mlody` runtime. Actual serialisation and deserialisation
logic — reading/writing bytes — is deferred to a future phase.

The expected value is: a machine-readable contract for how each pipeline value
is encoded on disk, enabling future executors to deserialise without ad-hoc
conventions, and allowing the LSP and tooling to surface representation
metadata.

---

## 2. Project Scope

### 2.1 In Scope

- A new `representation` attribute on the `value()` rule accepting a
  representation struct or `None`.
- A new `representation.mlody` standard file under `mlody/common/` that declares
  the `json()` built-in representation.
- Registration of representations in the evaluator's registry under the
  `"representation"` kind.
- Validation of the `representation` attribute: any value passed must be a
  struct with `kind="representation"` (the `"representation_ref"` attr-type).
- `representation=json()` as the first concrete representation. `json()` is a
  bare marker (no arguments in this phase); the schema field is TBD for a later
  phase.
- `representation` is valid in all `value()` usage sites: top-level
  declarations, `typedef` field definitions, and `task`/`action`
  `inputs`/`outputs`/`config` lists.
- Propagation of the `representation` field through task/action port unification
  (the existing `_merge_value_structs` mechanism).
- A new `"representation_ref"` attr-type recognised by `_validate_attr_value()`
  in `attrs.mlody`.

### 2.2 Out of Scope

- Actual serialisation/deserialisation of data (reading or writing bytes in any
  format).
- Additional representation kinds beyond `json()` (YAML, Parquet, CSV, etc.) —
  deferred to future changes.
- Arguments or schema fields on `json()` — the `schema` parameter is a
  placeholder for a later phase.
- Changes to the `Workspace` class or the label-resolution pipeline.
- UI or LSP hover/completion for `representation` values (that is a follow-on
  LSP change).
- Migration of existing `.mlody` files to add `representation` — the attribute
  is optional everywhere.

### 2.3 Assumptions

- The evaluator already supports `builtins.register(kind, value)` for arbitrary
  kind strings; adding `"representation"` follows the same pattern as
  `"location"`.
- The existing `_merge_value_structs` union logic in `task.mlody` already
  handles unknown fields gracefully; `representation` needs to be threaded
  through it explicitly only if the merge logic filters by known fields.
- `json()` will be injected into the sandbox via `builtins.inject`, making it
  available without a `load()` call in user `.mlody` files (same pattern as
  `posix()`, `s3()`, etc.).
- The `repr` function name in `types.mlody` — already used for representation
  descriptors in `typedef` — is a different concept from the `representation`
  attribute on `value()`; these two must not be conflated.

### 2.4 Constraints

- Python 3.13.2, hermetic via rules_python.
- Type checking: basedpyright strict mode; all new function signatures must
  carry complete type hints.
- Formatting/linting: ruff.
- Build rules: `o_py_library`, `o_py_binary`, `o_py_test`; no raw `py_*` rules.
- `.mlody` files are Starlark — no Python-only features unless marked
  `python.*`.
- The `Workspace` class (`mlody/core/workspace.py`) must not be modified as part
  of this feature unless strictly necessary for registry support.
- No new `python.*` escapes without explicit prior approval.

---

## 3. Stakeholders

| Role                | Name/Group          | Responsibilities                                      |
| ------------------- | ------------------- | ----------------------------------------------------- |
| Primary user        | ML pipeline authors | Author `.mlody` files with `representation=json()`    |
| Feature author      | Polymath Solutions  | Design, implement, review                             |
| Future stakeholders | Execution engine    | Consume representation metadata during task execution |
| Future stakeholders | LSP / tooling       | Surface representation in completions and hover       |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Enable pipeline authors to declare the serialisation format of a
  value at the DSL level, so that the format is a first-class piece of metadata
  rather than an implicit convention.
- **BR-002:** Ensure representation information survives all value composition
  paths (top-level declarations, typedef fields, task/action ports) so that
  downstream tooling always has access to it.
- **BR-003:** Keep the feature additive and non-breaking: all existing `.mlody`
  files that omit `representation` must continue to evaluate without error.

### 4.2 Success Metrics

- **KPI-001:** A `value()` with `representation=json()` evaluates without error
  and the resulting struct has `representation.kind == "representation"` and
  `representation.name == "json"`.
- **KPI-002:** A `value()` without `representation` evaluates and the resulting
  struct has `representation == None` (backward compatibility).
- **KPI-003:** Passing a non-representation struct (e.g. a `location` struct) as
  `representation` raises a `TypeError` with a clear message.
- **KPI-004:** All existing `//mlody/...` tests pass unchanged.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: ML Pipeline Author (primary)**

- Goals: declare that a model's metadata file is JSON so that the executor knows
  how to read it; express this once in the value definition rather than in each
  task's implementation.
- Pain points: currently there is no standard place to record serialisation
  format; each action implementation must guess or re-encode the contract.
- Needs: a concise, optional attribute on `value()` that is validated at parse
  time.

**Persona 2: Execution Engine Developer (future consumer)**

- Goals: inspect `value.representation` to choose the correct deserialiser when
  a task runs.
- Needs: a stable, machine-readable struct with a `kind` field and a `name`
  field (`"json"`, etc.).

### 5.2 User Stories

**Epic 1: Declare Representation on a Value**

- **US-001:** As a pipeline author, I want to write
  `value(name="model_info", type=top(), representation=json(), location=posix(path="model_info.json"))`
  so that I can express that the file is JSON without hard-coding this in the
  action implementation.
  - Acceptance Criteria: Given a valid `.mlody` file with the above, when the
    workspace is loaded, then `ws.resolve(":model_info").representation.name`
    equals `"json"`.
  - Priority: Must Have

- **US-002:** As a pipeline author, I want to omit `representation` from a
  `value()` and have it default to `None` so that I do not need to update
  existing files.
  - Acceptance Criteria: Given a `.mlody` file with
    `value(name="x", type=string(), location=posix())` (no `representation`),
    when evaluated, the resulting struct's `representation` field is `None`.
  - Priority: Must Have

- **US-003:** As a pipeline author, I want to use `representation=json()` inside
  a `typedef(fields=[...])` so that every value of that type inherits the
  representation.
  - Acceptance Criteria: Given
    `typedef(name="foo", base=record(fields=[value( name="bar", type=string(), representation=json(), location=posix())]))`,
    when evaluated, the nested value struct has `representation.name == "json"`.
  - Priority: Must Have

- **US-004:** As a pipeline author, I want to use `representation=json()` on
  `value()` declarations inside `task(inputs=[...])` and `action(outputs=[...])`
  so that port-level representations are declared alongside port-level types and
  locations.
  - Acceptance Criteria: A task/action port value carrying
    `representation=json()` survives the port-unification merge with the
    representation field intact.
  - Priority: Must Have

- **US-005:** As a pipeline author, I want a clear error if I accidentally pass
  a `location` struct as `representation` so that I catch mistakes at load time.
  - Acceptance Criteria: Given
    `value(name="x", type=string(), representation=posix(), location=posix())`,
    when evaluated, a `TypeError` is raised naming the expected kind
    (`"representation"`) and the actual kind (`"location"`).
  - Priority: Must Have

---

## 6. Functional Requirements

### 6.1 Representation Registry Kind

**FR-001: `"representation"` registry kind**

- Description: The evaluator registry gains a new kind string
  `"representation"`. Representations are registered via
  `builtins.register("representation", struct(...))` from within `.mlody` files
  (identical pattern to `"location"` and `"type"`).
- Business Rules:
  - Representations are immutable structs once registered.
  - Duplicate registration of the same name must follow existing registry
    semantics (error or overwrite — consistent with how other kinds behave).
- Priority: Must Have
- Dependencies: Existing evaluator registry mechanism.

### 6.2 `representation.mlody` Standard File

**FR-002: New `mlody/common/representation.mlody`**

- Description: A new standard Starlark file that declares the built-in
  representation kinds and injects them into the sandbox. Initially contains
  only `json()`.
- Contents (indicative):

  ```starlark
  load("//mlody/core/rule.mlody", "rule")
  load("//mlody/common/attrs.mlody", "attr")

  def _representation_impl(ctx):
      rep_struct = struct(
          kind="representation",
          name=ctx.attr.name,
      )
      builtins.register("representation", rep_struct)
      builtins.inject(ctx.attr.name, rep_struct)
      return rep_struct

  representation = rule(
      implementation=_representation_impl,
      kind="representation",
      attrs={
          "name": attr(type="string"),
      },
  )

  representation(name="json")
  ```

- Business Rules:
  - `json` is injected as a bare struct (not a factory), i.e.
    `builtins.inject("json", json_struct)`. `json()` invocations in user files
    are therefore `json` references, not calls — OR `json` is injected as a
    zero-argument callable that returns the struct. Either is acceptable; the
    chosen interface must be consistent with how `json()` is written in user
    files (the downloader shows `representation=json()` with parentheses, so
    `json` must be a callable or a zero-argument factory).
  - No arguments are accepted by `json()` in this phase. Passing any keyword
    argument must raise a `TypeError`.
  - The `schema` field is not present in this phase.
- Priority: Must Have
- Dependencies: FR-001.

### 6.3 Attr-Type `"representation_ref"`

**FR-003: `"representation_ref"` in `_validate_attr_value()`**

- Description: The `attrs.mlody` validation function `_validate_attr_value()` is
  extended with a new branch for `type_ref == "representation_ref"`. It accepts
  a struct with `kind="representation"` or `None`; rejects anything else.
- Inputs: Any Starlark value passed as `representation=...` in a `value()` call.
- Processing: Check `type(value) == "struct"` and
  `python.getattr(value, "kind", None) == "representation"`. If not, raise
  `TypeError` with a message naming expected kind `"representation"` and the
  actual value.
- Business Rules:
  - `None` is the implicit default (representation is optional); the attr
    definition uses `mandatory=False, default=None`.
  - A string name (lazy reference) is NOT supported in this phase — unlike
    `type_ref` and `location_ref`. Representation must be an inline struct.
  - This constraint may be relaxed in a future phase when named representations
    become useful.
- Priority: Must Have
- Dependencies: None (pure Starlark validation).

### 6.4 `representation` Attribute on `value()`

**FR-004: Add `representation` to the `value()` rule**

- Description: The `value()` rule in `values.mlody` gains a `representation`
  attribute with `type="representation_ref"` and `mandatory=False`.
- Processing in `_value_impl`:
  - Read `ctx.attr.representation` (may be `None`).
  - No resolution step needed (unlike `location` which calls
    `_resolve_location_ref`): representation structs are already fully formed
    inline.
  - Store directly on the output `value_struct`:
    `representation=ctx.attr.representation`.
- Business Rules:
  - When `representation` is omitted, the field is present on the struct with
    value `None`.
  - When a struct is passed, it must already have passed `_validate_attr_value`
    with `type_ref="representation_ref"` before `_value_impl` is called
    (enforced by `rule.mlody`'s `_validate_args`).
- Priority: Must Have
- Dependencies: FR-001, FR-002, FR-003.

### 6.5 Propagation Through Task/Action Port Unification

**FR-005: `representation` survives `_merge_value_structs`**

- Description: The `_merge_value_structs` function in `task.mlody` merges
  task-level and action-level value structs by union-ing their fields. The
  `representation` field must be included in this merge with the same semantics
  as `type` and `location`: if both task and action specify `representation`,
  they must be equal; if only one specifies it, the other's `None` is overridden
  by the present value.
- Business Rules:
  - A conflict (`representation` set to different non-`None` structs on task vs.
    action) must raise a `ValueError` naming the conflicting field.
  - `_field_values_compatible` may need to recognise `"representation"` as a
    field that requires structural equality (same `kind` and `name`), analogous
    to how `"type"` and `"location"` are handled.
- Priority: Must Have
- Dependencies: FR-004.

### 6.6 Scoped Value Registration

**FR-006: `representation` on task-scoped values**

- Description: The `_register_scoped_value` helper inside `_task_impl` builds a
  `Struct(kind="value", ...)` for each port. This helper must include
  `representation` when constructing the scoped struct.
- Business Rules:
  - Copy `representation` from the source value struct (may be `None`).
  - Consistent with how `type`, `location`, and `source` are propagated.
- Priority: Must Have
- Dependencies: FR-004, FR-005.

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-001:** Adding a `representation` attribute to `value()` must not
  measurably increase workspace load time. Struct construction and validation
  are O(1) operations.

### 7.2 Scalability Requirements

- **NFR-002:** The representation registry follows the same internal storage as
  all other kinds; no special scaling consideration applies.

### 7.3 Availability & Reliability

- **NFR-003:** The feature is purely additive. Files that omit `representation`
  must load identically to today. No existing test may regress.

### 7.4 Security Requirements

- **NFR-004:** No security considerations beyond those already applying to the
  `.mlody` sandbox (no file I/O, no network calls).

### 7.5 Usability Requirements

- **NFR-005:** A `TypeError` for a wrong `representation` value must name the
  expected kind (`"representation"`), the actual kind received, and the
  attribute name.
- **NFR-006:** `json` must be available in `.mlody` files without an explicit
  `load()` — injected into the sandbox, consistent with `posix`, `s3`, etc.

### 7.6 Maintainability Requirements

- **NFR-007:** `representation.mlody` must follow the same structure as
  `locations.mlody`: a `rule`-based implementation, a `rule()` call to define
  the `representation` rule, followed by concrete representation declarations.
- **NFR-008:** All new code paths must be covered by tests in
  `mlody/common/values_test.py` (or a new `representation_test.py`): struct
  field present with `json()`, field `None` when omitted, `TypeError` on wrong
  kind, propagation through task port unification.

### 7.7 Compatibility Requirements

- **NFR-009:** The `representation` field must be present on all `value` structs
  after this change (defaulting to `None`), so that downstream code can always
  read `python.getattr(v, "representation", None)` without branching on struct
  age.
- **NFR-010:** The `repr` helper in `types.mlody` (used for typedef
  `representations=[repr(...)]`) is unrelated to this feature and must not be
  renamed or altered.

---

## 8. Data Requirements

### 8.1 Data Entities

- **Representation struct:** An immutable Starlark struct with at minimum
  `kind="representation"` and `name` (e.g. `"json"`). Additional fields may be
  added in future phases (e.g. `schema`).
- **Value struct (updated):** Gains a `representation` field (a representation
  struct or `None`).

### 8.2 Data Quality Requirements

- A representation struct must have `kind="representation"` — validated by
  `_validate_attr_value` before it reaches `_value_impl`.
- The `name` field on a representation struct must be a non-empty string.

### 8.3 Data Retention & Archival

- Not applicable. Representation structs are in-memory Starlark values for the
  lifetime of a workspace evaluation.

### 8.4 Data Privacy & Compliance

- No data privacy implications. Representation metadata is structural/schema
  information, not user data.

---

## 9. Integration Requirements

### 9.1 Internal Module Integration

| Module                              | Change required                                                                          |
| ----------------------------------- | ---------------------------------------------------------------------------------------- |
| `mlody/common/representation.mlody` | New file: declares `representation` rule and `json` built-in                             |
| `mlody/common/attrs.mlody`          | Add `"representation_ref"` branch in `_validate_attr_value()`                            |
| `mlody/common/values.mlody`         | Add `representation` attr on `value()` rule; propagate in impl                           |
| `mlody/common/task.mlody`           | Extend `_merge_value_structs` and `_register_scoped_value` for `representation`          |
| Evaluator sandbox setup             | Load `representation.mlody` during sandbox initialisation (same as other standard files) |

### 9.2 API Requirements

- No HTTP APIs. All changes are internal to the `.mlody` DSL evaluation path.

---

## 10. User Interface Requirements

### 10.1 DSL Syntax

The `representation` keyword argument is available on `value()` in all usage
sites:

```starlark
# Top-level value declaration
value(
    name           = "model_info",
    type           = top(),
    representation = json(),
    location       = posix(path="model_info.json"),
)

# Inside typedef fields (record type)
typedef(
    name = "yolo26",
    base = record(fields=[
        value(name="model_info", type=top(), representation=json(), location=posix(path="model_info.json")),
    ]),
)

# Inside task/action ports
action(
    name    = "downloader-action",
    outputs = [
        value(name="model", type=":hf-model", representation=json(), location=posix(path="...")),
    ],
    ...
)
```

### 10.2 Error Message Standards

Error messages follow the existing pattern in the codebase:

```
Attribute 'representation' expects a representation struct (kind='representation'),
got a location struct (kind='location')
```

---

## 11. Reporting & Analytics Requirements

Not applicable for this feature.

---

## 12. Security & Compliance Requirements

### 12.1 Authentication & Authorization

- Not applicable. No network or file-system access introduced.

### 12.2 Data Security

- Representation structs contain only schema/format metadata. No sensitive data.

### 12.3 Compliance

- No regulatory compliance requirements identified for this feature.

---

## 13. Infrastructure & Deployment Requirements

### 13.1 File Layout

```
mlody/common/
├── representation.mlody      # NEW: declares representation rule and json()
├── values.mlody              # MODIFIED: add representation attr
├── attrs.mlody               # MODIFIED: add "representation_ref" type
└── task.mlody                # MODIFIED: propagate representation in merge/scoping
```

### 13.2 Deployment

- No new binaries or services. The feature is a library addition within the
  existing `mlody` Python package, surfaced through the existing evaluator.

### 13.3 Disaster Recovery

- Not applicable. All changes are to in-memory evaluation logic; no persistent
  state is introduced.

---

## 14. Testing & Quality Assurance Requirements

### 14.1 Testing Scope

| Test type                       | Coverage                                                                          |
| ------------------------------- | --------------------------------------------------------------------------------- |
| Unit: `representation.mlody`    | `json()` returns struct with `kind="representation"`, `name="json"`               |
| Unit: `_validate_attr_value`    | `"representation_ref"` accepts representation struct; rejects wrong kinds         |
| Unit: `value()` rule            | Struct has `representation` field when supplied; `None` when omitted              |
| Unit: port unification          | `representation` propagates through `_merge_value_structs`; conflict raises error |
| Unit: scoped value registration | Task-scoped values carry `representation` from source port                        |
| Integration: workspace load     | `.mlody` file with `representation=json()` loads and resolves correctly           |
| Regression                      | All existing `//mlody/...` tests pass unchanged                                   |

### 14.2 Acceptance Criteria

- `ws.resolve(":model_info").representation.name == "json"` for a value declared
  with `representation=json()`.
- `ws.resolve(":model_info").representation` is `None` for a value without
  `representation`.
- Passing a `location` struct as `representation` raises `TypeError`.
- All existing tests under `bazel test //mlody/...` pass without modification.
- Lint passes: `bazel build --config=lint //mlody/...`.

---

## 15. Training & Documentation Requirements

### 15.1 User Documentation

- A brief note in `mlody/common/representation.mlody` module docstring
  explaining the purpose of representations and the `json()` built-in.
- Inline comment in `values.mlody` explaining the `representation` attribute.

### 15.2 Technical Documentation

- Docstring on `_representation_impl` describing the struct shape and registry
  kind.
- Comment in `_validate_attr_value` for the `"representation_ref"` branch.

---

## 16. Risks & Mitigation Strategies

| Risk ID | Description                                                                                                                                | Impact | Probability            | Mitigation                                                                                                                                                 | Owner |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------ | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| R-001   | `repr` name collision: `types.mlody` exports `repr()` for typedef representations; `json` for value representations is a different concept | Medium | Low                    | Keep names distinct; never rename `repr` in `types.mlody`; document the distinction clearly                                                                | Dev   |
| R-002   | `_merge_value_structs` in `task.mlody` filters by known fields and silently drops `representation`                                         | Medium | Medium                 | Audit the merge logic; add `representation` to the explicitly propagated field set                                                                         | Dev   |
| R-003   | Sandbox injection order: `representation.mlody` must be loaded before `values.mlody` uses `json`                                           | Low    | Low                    | Enforce load order in sandbox setup; add an integration test that catches missing `json`                                                                   | Dev   |
| R-004   | Future `json(schema=...)` breaks the bare-struct approach                                                                                  | Low    | Medium                 | Design `json` as a zero-arg callable (factory returning the same struct) rather than a plain struct, so future args can be added without a breaking change | Dev   |
| R-005   | `default` field on `value()` is any Starlark value with no type checking                                                                   | Low    | High (already present) | Flagged as future concern; no change required in this phase — see OQ-1                                                                                     | Dev   |

---

## 17. Dependencies

| Dependency                             | Type     | Status | Impact if Delayed                                  | Owner |
| -------------------------------------- | -------- | ------ | -------------------------------------------------- | ----- |
| Evaluator registry (existing)          | Internal | Stable | Blocking; representation registration relies on it | Dev   |
| `attrs.mlody` `_validate_attr_value()` | Internal | Stable | Blocking; `"representation_ref"` branch needed     | Dev   |
| `rule.mlody` `_validate_args()`        | Internal | Stable | Attr validation runs before impl is called         | Dev   |
| `values.mlody` `value()` rule          | Internal | Stable | Core change target                                 | Dev   |
| `task.mlody` port merge logic          | Internal | Stable | Representation propagation depends on this         | Dev   |

---

## 18. Open Questions & Action Items

| ID   | Question / Action                                                                                                                                                                                        | Owner | Target Date | Status   |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ----------- | -------- |
| OQ-1 | `default` field on `value()` is currently any Starlark value with no type checking — should it be validated against the declared `type`? Flag as a future concern; no change required in this phase.     | Dev   | Future      | Deferred |
| OQ-2 | Should `json` be a bare struct (reference, no parens) or a zero-arg callable (factory, with parens)? Current downloader uses `json()` with parens — factory approach preferred for future extensibility. | Dev   | Phase 1     | Open     |
| OQ-3 | Should string names (e.g. `representation="json"`) be supported as lazy references, analogous to `type_ref` and `location_ref`? Not needed for this phase but may be useful for readability.             | Dev   | Future      | Deferred |
| OQ-4 | When will `json(schema=...)` be introduced? Schema specification for JSON representation is explicitly deferred.                                                                                         | Arch  | Future      | Deferred |
| OQ-5 | Should `representation.mlody` be auto-loaded as part of the standard sandbox (like `attrs.mlody`) or explicitly `load()`-ed by `values.mlody`?                                                           | Dev   | Phase 1     | Open     |
| OQ-6 | Should the `representation` field participate in the `_validate_required_value_fields` check in `task.mlody`? (Currently only `type` and `location` are required.)                                       | Dev   | Phase 1     | Open     |

---

## 19. Revision History

| Version | Date       | Author                  | Changes                                                     |
| ------- | ---------- | ----------------------- | ----------------------------------------------------------- |
| 1.0     | 2026-04-07 | Requirements Analyst AI | Initial draft for value representation feature (issue #460) |

---

## Appendices

### Appendix A: Glossary

| Term                 | Definition                                                                                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Representation       | A DSL struct (kind="representation") describing the serialisation format of a value's data (e.g. JSON)                                  |
| `json()`             | The first concrete representation; a bare marker with no arguments in this phase; schema deferred                                       |
| `representation_ref` | An attr-type that accepts a representation struct or `None`                                                                             |
| Value struct         | A Starlark struct with `kind="value"` produced by the `value()` rule                                                                    |
| Port unification     | The `_merge_value_structs` / `_unify_ports` logic in `task.mlody` that merges task-level and action-level value declarations            |
| `repr` (types.mlody) | An unrelated concept: a descriptor for alternate input representations of a typedef (coercion). Not the same as a value representation. |

### Appendix B: Struct Shapes

**Representation struct (after this change):**

```
Struct(
    kind           = "representation",   # always "representation"
    name           = "json",             # or future formats
    # schema field: TBD for a later phase
)
```

**Value struct (after this change):**

```
Struct(
    kind           = "value",
    name           = <string>,
    type           = <type struct | None>,
    location       = <location struct | None>,
    representation = <representation struct | None>,   # NEW
    default        = <any | None>,
    source         = <string | None>,
    _lineage       = [],
)
```

### Appendix C: Prior Art — Location Pattern

The `representation` feature mirrors the `location` pattern exactly:

| Aspect           | `location`                         | `representation`                                    |
| ---------------- | ---------------------------------- | --------------------------------------------------- |
| Registry kind    | `"location"`                       | `"representation"`                                  |
| Attr-type string | `"location_ref"`                   | `"representation_ref"`                              |
| Standard file    | `mlody/common/locations.mlody`     | `mlody/common/representation.mlody`                 |
| Injected symbols | `posix`, `s3`, `git_repository`, … | `json` (others deferred)                            |
| Validation       | `_resolve_location_ref()`          | Inline in `_value_impl` (no resolution step needed) |
| Field on value() | `location=`                        | `representation=`                                   |

---

**End of Requirements Document**
