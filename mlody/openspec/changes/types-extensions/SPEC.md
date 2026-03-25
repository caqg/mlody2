# SPEC: types.mlody Extensions

**Version:** 1.0 **Date:** 2026-03-25 **Architect:** @vitruvius **Status:**
Draft **Requirements:** `mlody/common/types.REQUIREMENTS.md` **Design:**
`mlody/openspec/changes/types-extensions/design.md`

---

## Executive Summary

This change delivers four independent but co-located improvements to the mlody
type system:

1. **Multiple representations** — `typedef` gains a `representations=` parameter
   and a new `repr()` descriptor factory. At validation time the type
   automatically tries the canonical form first, then each alternative repr in
   declaration order, coercing to canonical before storing.

2. **Bool type completion** — `typedef(name="bool", ...)` is expanded with an
   explicit `predicate` (gates the accepted input set) and `canonical`
   (normalises to Python `bool`). Accepted inputs: `True`/`False`, `"true"` /
   `"false"` / `"yes"` / `"no"` / `"1"` / `"0"` (case-insensitive), integer `1`
   / `0`.

3. **Struct typedef** — the commented-out `typedef(name="struct", ...)` is
   uncommented and wired to `_validate_map`. `mlody-folder` and `mlody-source`
   are also uncommented with provisional field schemas, unblocking any consumer
   that references these type names.

4. **Bug fixes** — three `is False` comparisons (one in `types.mlody`, two in
   `attrs.mlody`) are replaced with `== False`, as required by the Starlark
   compatibility standard documented in `mlody/CLAUDE.md`.

No new files, no new Python modules, no Bazel target changes.

---

## Architecture Overview

```
mlody/common/types.mlody       (MODIFIED — primary)
mlody/common/attrs.mlody       (MODIFIED — bug fixes only)
mlody/common/types_test.py     (MODIFIED — new tests added)
```

Data flow for coercion (new):

```
User writes: email_address("  USER@DOMAIN.COM  ")   # string form
             email_address({"user": "U", "domain": "D.COM"})  # struct form

typedef(..., representations=[repr(name="parsed", type=map(...), to_canonical=...)])
  -> _type_impl builds _repr_validator closure and passes it as extra_validator
  -> extend_attrs composes it into the type's validator chain

At validation time (_repr_validator):
  1. Try base validator (string with pattern) on value
     - If passes: apply canonical() -> "user@domain.com" -> store
  2. For each repr in order:
     Try repr.type.validator(value)
     - If passes: call repr.to_canonical(value) -> canonical string -> store
  3. If none match: raise TypeError listing all accepted forms
```

---

## Technical Stack

- Starlark (`.mlody` files) — no Python changes
- No new third-party libraries
- Bazel rules: no changes to `BUILD.bazel` — all affected files are already
  declared as `data` deps of the existing library targets
- Test runner: `bazel test //mlody/common:types_test`

---

## Detailed Component Specifications

### 1. `mlody/common/types.mlody`

#### 1.1 `repr()` factory (NEW)

A new top-level function `repr()` constructs a representation descriptor
`Struct`. It is a pure data constructor with no side effects.

```starlark
def repr(*, name, type, to_canonical, from_canonical=None):
    """Construct a representation descriptor for use in typedef(representations=[...])."""
    return Struct(
        kind="repr",
        name=name,
        type=type,
        to_canonical=to_canonical,
        from_canonical=from_canonical,
    )
```

Fields:

| Field            | Type        | Required | Description                                              |
| ---------------- | ----------- | -------- | -------------------------------------------------------- |
| `name`           | `string`    | yes      | Unique name within the type (e.g. `"parsed"`)            |
| `type`           | type struct | yes      | Type struct whose validator matches this repr's shape    |
| `to_canonical`   | callable    | yes      | Converts a value in this repr to canonical form          |
| `from_canonical` | callable    | no       | Converts canonical back to this repr (for serialisation) |

`from_canonical` is stored for future use but not called by the coercion engine
in this change.

#### 1.2 `"repr_list"` type check in `_validate_attr_value` (NEW, in `attrs.mlody`)

A new branch in `_validate_attr_value` handles `type_ref == "repr_list"`:

```starlark
if type_ref == "repr_list":
    if not isinstance(value, list):
        raise TypeError(f"Attribute {attr_name!r} expects a list of repr() specs")
    for item in value:
        if type(item) != "struct" or python.getattr(item, "kind", None) != "repr":
            raise TypeError(f"Attribute {attr_name!r}: each item must be a repr() spec")
    return
```

This follows the identical pattern used for `"field_list"` on line 88–94 of
`attrs.mlody`.

#### 1.3 `typedef` rule — new `representations` attribute

The `rule()` declaration for `typedef` gains one new optional attribute:

```starlark
"representations": attr(type="repr_list", mandatory=False, default=None),
```

This attribute is adjacent to `fields` in the attribute dict for readability.

#### 1.4 `_type_impl` — coercion branch (NEW)

After the existing `fields` and `list base` branches but before the
`base_type == None` bootstrap block, a new branch handles `representations`:

```starlark
representations = python.getattr(ctx.attr, "representations", None)
```

This value is captured in the closure described below and used only when
`representations != None`.

The coercion validator is built as a closure `_repr_validator` inside
`_type_impl`. It is passed as `extra_validator` to `extend_attrs`, composing it
on top of the existing base validator chain:

```starlark
if representations != None:
    captured_reprs = representations
    captured_canonical = python.getattr(ctx.attr, "canonical", None)

    def _repr_validator(value):
        # Step 1: try canonical form (base type already validated before us)
        # If we are here the base validator has already passed, so just apply canonical.
        if captured_canonical != None:
            return True  # canonical is applied externally; value already accepted
        return True

    # ... (full algorithm below)
```

Wait — the composition model matters here. The `composed_validator` in
`extend_attrs` calls `generic_validate` first (or the base validator), then
calls `extra_validator`. If the value already passes the base type, it is
accepted by step 1. If the base type rejects it, `composed_validator` raises
_before_ the `extra_validator` is reached.

The coercion requirement is that the _incoming_ value may not match the base
type but may match a repr's type. This means coercion must _replace_ the
standard validator composition, not extend it.

Therefore, when `representations != None` is present, `_type_impl` builds a
standalone validator that encodes the full algorithm (canonical-type check +
repr fallback), bypasses `extend_attrs`, and sets the validator directly on the
type struct:

```starlark
if representations != None:
    captured_reprs = representations
    captured_base_validator = python.getattr(base_type, "validator", None)
    captured_canonical = python.getattr(ctx.attr, "canonical", None)
    captured_predicate = python.getattr(ctx.attr, "predicate", None)
    accepted_forms = ["canonical (" + ctx.attr.name + ")"] + [r.name for r in captured_reprs]

    def _coercion_validator(value):
        # Step 1: try canonical type
        try:
            if captured_base_validator != None:
                captured_base_validator(value)
            # Passes canonical check — optionally apply predicate
            if captured_predicate != None:
                res = captured_predicate(value)
                if res == False:
                    raise ValueError("predicate rejected value")
            return True
        except (TypeError, ValueError):
            pass

        # Step 2: try each repr in order
        for r in captured_reprs:
            repr_validator = python.getattr(r.type, "validator", None)
            try:
                if repr_validator != None:
                    repr_validator(value)
                # Matched this repr — coerce to canonical
                canonical_value = r.to_canonical(value)
                # Note: the canonical value is returned to signal success;
                # the runtime stores whatever the validator returns when truthy.
                # Return True for now; see §1.4 note on return value.
                return True
            except (TypeError, ValueError):
                pass

        # Step 3: nothing matched
        raise TypeError(
            f"Value {value!r} does not match any accepted form for type "
            f"{ctx.attr.name!r}. Accepted: {accepted_forms}"
        )

    new_type_struct = extend_attrs(
        base=base_type,
        type_name=ctx.attr.name,
        extra_validator=_coercion_validator,
        **other_fields,
    )
    builtins.register(ctx.kind, new_type_struct)
    builtins.inject(ctx.attr.name, _make_factory(new_type_struct))
    return {}
```

**Note on return value:** The current validator contract returns `True` on
success and raises on failure. The coerced value is not threaded through the
validator; callers that need the canonical form must call
`type_struct.canonical` explicitly, or the DSL layer applies `canonical` after
validation. This is consistent with how `canonical` is stored and used today
(see `test_canonical_stored_on_type_struct` in `types_test.py`). The coercion
validator's responsibility is _acceptance_, not transformation.

#### 1.5 Bool typedef expansion

Replace:

```starlark
typedef(name="bool", base=scalar())
```

With:

```starlark
_BOOL_TRUTHY_STRINGS = {"true", "yes", "1"}
_BOOL_FALSY_STRINGS  = {"false", "no", "0"}

def _is_accepted_bool(v):
    if isinstance(v, _builtin_bool):
        return True
    if isinstance(v, str) and v.lower() in _BOOL_TRUTHY_STRINGS:
        return True
    if isinstance(v, str) and v.lower() in _BOOL_FALSY_STRINGS:
        return True
    if isinstance(v, int) and not isinstance(v, _builtin_bool) and v in (0, 1):
        return True
    return False

def _coerce_bool(v):
    if isinstance(v, _builtin_bool):
        return v
    if isinstance(v, str):
        return v.lower() in _BOOL_TRUTHY_STRINGS
    if isinstance(v, int):
        return v == 1
    return False  # unreachable if predicate passed

typedef(
    name     = "bool",
    base     = scalar(),
    predicate = _is_accepted_bool,
    canonical = _coerce_bool,
)
```

These two helpers are module-level private functions (no `_builtin_` prefix;
they are DSL-level helpers, not host-escape references).

`_BOOL_TRUTHY_STRINGS` and `_BOOL_FALSY_STRINGS` must be declared before
`_is_accepted_bool` is defined; both must appear before the
`typedef(name="bool", ...)` call.

`_builtin_bool` is already saved at the top of `attrs.mlody` and imported by
`types.mlody` via the existing `load()` statement — it is available in scope.

**Relationship to `_validate_bool` in `attrs.mlody`:** `_validate_bool` is the
raw primitive gate used by `generic_validate` for the `"bool"` kind. It checks
`isinstance(v, _builtin_bool)`. After the `canonical` function normalises any
accepted input to Python `bool`, `_validate_bool` correctly accepts the
normalised value. `_validate_bool` does not need to change.

The `predicate` on the `typedef` runs _after_ the base validator in the current
composition model. Since `base=scalar()` is abstract and its validator is a
no-op, the predicate effectively runs first. The predicate must accept Python
`bool` (which passes `isinstance(v, _builtin_bool)`), strings, and the integers
0/1. This is satisfied by `_is_accepted_bool`.

#### 1.6 Struct typedef (uncommented)

Replace the commented block with:

```starlark
typedef(
    name = "struct",
    base = aggregate(),
    attrs = {
        "fields": attr(type="field_list", mandatory=False),
        "strict":  attr(type="bool",       mandatory=False),
    },
)
```

The `struct` typedef follows the exact same shape as the existing `map` typedef.
Its validator delegates to `_validate_map` via the `fields`-path branch in
`_type_impl` (which is already used when `typedef(fields=[...])` is called
directly). No new validator function is needed.

To make `struct(fields=[...])` invoke `_validate_map`, the `_type_impl`
function's fields-branch must also activate when `base_type` is `struct()`. The
existing check is:

```starlark
fields = python.getattr(ctx.attr, "fields", None)
if fields != None:
    ...
```

This is already correct — `struct` is declared with `attrs={"fields": ...}` so
the `fields` kwarg flows through `ctx.attr.fields` when the struct factory is
called as `struct(fields=[...])`. However, for
`typedef(name="...", base=struct(fields=[...]))` the fields come from
`base_type.attributes` rather than `ctx.attr.fields`. The existing
`extend_attrs` machinery propagates `attributes` through the chain. The
`composed_validator` in `extend_attrs` calls
`generic_validate("map", merged_attrs, value)` when `_root_kind == "map"`.

Therefore `struct` must carry `_root_kind = "map"` to route through
`_validate_map`. This is achieved by adding `"struct"` to `_PRIMITIVE_KINDS` in
`attrs.mlody` and adding a `"struct"` branch to `generic_validate`:

```starlark
_PRIMITIVE_KINDS = {"integer", "string", "bool", "float", "vector", "tuple", "map", "struct"}
```

And in `generic_validate`:

```starlark
elif kind == "struct":
    _validate_map(attrs, value)
```

And in `_type_impl`, the `_root_kind` assignment:

```starlark
if ctx.attr.name in _PRIMITIVE_KINDS:
    other_fields['_root_kind'] = ctx.attr.name
```

This already fires for `"struct"` once it is added to `_PRIMITIVE_KINDS`.

#### 1.7 `mlody-folder` and `mlody-source` (uncommented, provisional)

```starlark
# typedef(name="mlody-folder") — provisional; field schema deferred to next design step
typedef(
    name = "mlody-folder",
    description = "A file system folder containing mlody source files or other mlody folders.",
    base = struct(fields = [
        field(name = "subfolders", type = vector()),   # element_type=mlody-folder deferred
        field(name = "files",      type = vector()),   # element_type=mlody-source deferred
    ]),
)

# typedef(name="mlody-source") — provisional; field schema deferred to next design step
typedef(
    name = "mlody-source",
    description = "A mlody source file containing mlody entities.",
    base = struct(fields = [
        field(name = "entities", type = vector()),     # element_type TBD
    ]),
)
```

Self-referential types (`mlody-folder` containing `mlody-folder` children) are
not supported in the current type system — the type must be registered before it
can be referenced. The provisional schema uses bare `vector()` (no element type)
to avoid this. The next design step will introduce forward references or lazy
resolution.

#### 1.8 Bug fix — `is False` in `types.mlody`

Line 19 of `types.mlody`:

Before:

```starlark
if res is False:
```

After:

```starlark
if res == False:
```

### 2. `mlody/common/attrs.mlody`

#### 2.1 Bug fix — `is False` (two occurrences)

Line 340:

```starlark
# Before:
if res is False:
    raise ValueError("rejected by extra_validator")
# After:
if res == False:
    raise ValueError("rejected by extra_validator")
```

Line 361:

```starlark
# Before:
if res is False:
    raise ValueError("rejected by user validator")
# After:
if res == False:
    raise ValueError("rejected by user validator")
```

#### 2.2 New `"repr_list"` branch in `_validate_attr_value`

Insert immediately after the `"field_list"` branch (after line 94):

```starlark
if type_ref == "repr_list":
    if not isinstance(value, list):
        raise TypeError(f"Attribute {attr_name!r} expects a list of repr() specs")
    for item in value:
        if type(item) != "struct" or python.getattr(item, "kind", None) != "repr":
            raise TypeError(
                f"Attribute {attr_name!r}: each item must be a repr() spec, "
                f"got {type(item)!r}"
            )
    return
```

#### 2.3 `"struct"` added to `_PRIMITIVE_KINDS`

```starlark
_PRIMITIVE_KINDS = {"integer", "string", "bool", "float", "vector", "tuple", "map", "struct"}
```

#### 2.4 `"struct"` branch in `generic_validate`

```starlark
elif kind == "struct":
    _validate_map(attrs, value)
```

### 3. `mlody/common/types_test.py`

New test functions are appended to the existing file. All follow the `_eval()`
helper pattern already established in the file.

#### 3.1 Repr / multiple representations

| Test function                                  | Scenario                                          | Expected                                            |
| ---------------------------------------------- | ------------------------------------------------- | --------------------------------------------------- |
| `test_repr_factory_creates_struct`             | `repr(name="p", type=map(...), to_canonical=...)` | `kind == "repr"`, fields present                    |
| `test_typedef_representations_string_accepted` | String input to repr-aware email type             | Accepted; validator returns True                    |
| `test_typedef_representations_struct_coerced`  | Dict input coerced via `to_canonical`             | Accepted                                            |
| `test_typedef_representations_invalid_raises`  | Input matching neither canonical nor any repr     | `TypeError` raised, message mentions accepted forms |
| `test_typedef_representations_order`           | Two reprs; input matches second                   | Second repr's `to_canonical` is tried               |

#### 3.2 Bool type completion

| Test function                                   | Scenario                                    | Expected                        |
| ----------------------------------------------- | ------------------------------------------- | ------------------------------- |
| `test_bool_accepts_python_true_false`           | `True`, `False`                             | Accepted                        |
| `test_bool_accepts_truthy_strings`              | `"true"`, `"yes"`, `"1"`, `"TRUE"`, `"Yes"` | All accepted                    |
| `test_bool_accepts_falsy_strings`               | `"false"`, `"no"`, `"0"`, `"FALSE"`         | All accepted                    |
| `test_bool_accepts_int_zero_one`                | `0`, `1`                                    | Accepted                        |
| `test_bool_rejects_other_int`                   | `2`, `-1`, `42`                             | `TypeError`                     |
| `test_bool_rejects_other_string`                | `"maybe"`, `"y"`, `""`                      | `TypeError`                     |
| `test_bool_canonical_normalises_to_python_bool` | `"yes"` -> `True`, `0` -> `False`           | `canonical("yes") is True` etc. |

Note: the existing test `test_primitive_validators_unchanged_after_hierarchy`
contains `r.bool_t.validator(True)` (pass) and `r.bool_t.validator(1)` (raises
`TypeError`). After this change, `bool().validator(1)` must still raise
`TypeError` because `_validate_bool` in `attrs.mlody` (the raw primitive gate)
accepts only Python `bool`. The `predicate` on the `typedef` gates the _input_
set, but the base validator (`_validate_bool`) runs inside `composed_validator`
before the `extra_validator` (predicate). This means `bool().validator(1)` would
still reject `1` at the base-validator stage.

**Resolution:** The `typedef(name="bool")` must use `base=scalar()` (abstract,
no-op validator) rather than a base that delegates to `_validate_bool`. The
`predicate` then becomes the sole gate, and it accepts the expanded input set.
`_validate_bool` remains the Tier-1 primitive validator used only for direct
attr-type checks (e.g. `attr(type="bool")` in `rule()` declarations), not for
end-user value validation via the registered typedef.

The existing test `r.bool_t.validator(1)` raising `TypeError` will change — `1`
will now be accepted by the extended `bool` typedef. The test must be updated to
reflect the new behaviour: `1` is accepted (canonical form: `False`... wait, `1`
maps to `True`). The test must be updated to assert that `1` is now accepted and
that `2` is rejected instead.

Test update required in `test_primitive_validators_unchanged_after_hierarchy`:

```python
# Old (will break):
with pytest.raises(TypeError):
    r.bool_t.validator(1)

# New:
assert r.bool_t.validator(1)    # integer 1 is now accepted
with pytest.raises(TypeError):
    r.bool_t.validator(2)       # integer 2 is still rejected
```

#### 3.3 Struct typedef

| Test function                                | Scenario                                     | Expected                                |
| -------------------------------------------- | -------------------------------------------- | --------------------------------------- |
| `test_struct_typedef_registered`             | After loading types.mlody                    | `"struct"` in `ev._types_by_name`       |
| `test_struct_validates_dict_with_fields`     | `struct(fields=[...]).validator({"x": 1.0})` | Accepted                                |
| `test_struct_rejects_missing_required_field` | Missing required field                       | `ValueError`                            |
| `test_struct_strict_rejects_extra_key`       | Extra key with `strict=True`                 | `ValueError`                            |
| `test_mlody_folder_typedef_registered`       | After loading                                | `"mlody-folder"` in `ev._types_by_name` |
| `test_mlody_source_typedef_registered`       | After loading                                | `"mlody-source"` in `ev._types_by_name` |

#### 3.4 Bug fix regression

No dedicated test — the `is False` -> `== False` fix is a Starlark compatibility
correction. The existing test suite exercises all three paths; if the evaluator
starts rejecting `is False` syntax in future, the existing tests would catch
regressions.

---

## Data Architecture

No persistent storage. All changes are in-memory type structs built at evaluator
load time. The `repr` descriptor is an anonymous `Struct` held in the
`representations` list on the type struct. It is not registered in
`ev._types_by_name`.

---

## Security and Authentication

Not applicable.

---

## Implementation Plan

### Phase 1 — Bug fixes (no design risk, no dependencies)

1. In `attrs.mlody`: replace `is False` with `== False` at lines 340 and 361.
2. In `types.mlody`: replace `is False` with `== False` at line 19.

Run `bazel test //mlody/common:types_test` — all existing tests must pass.

### Phase 2 — Struct typedef (depends on Phase 1)

3. In `attrs.mlody`: add `"struct"` to `_PRIMITIVE_KINDS`.
4. In `attrs.mlody`: add `"struct"` branch to `generic_validate`.
5. In `attrs.mlody`: add `"repr_list"` branch to `_validate_attr_value`.
6. In `types.mlody`: uncomment `typedef(name="struct", ...)` with the `fields` +
   `strict` attrs.
7. In `types.mlody`: uncomment `mlody-folder` and `mlody-source` with
   provisional schemas.

Run `bazel test //mlody/common:types_test` — existing tests pass; add struct
tests.

### Phase 3 — Bool type completion (depends on Phase 2)

8. In `types.mlody`: add `_BOOL_TRUTHY_STRINGS`, `_BOOL_FALSY_STRINGS`,
   `_is_accepted_bool`, `_coerce_bool`.
9. In `types.mlody`: expand `typedef(name="bool", ...)` with `predicate` and
   `canonical`.
10. In `types_test.py`: update the `bool` assertion in
    `test_primitive_validators_unchanged_after_hierarchy` (integer `1` now
    accepted; test integer `2` rejected instead).
11. Add the new bool tests from §3.2.

Run `bazel test //mlody/common:types_test` — all tests pass.

### Phase 4 — Multiple representations (depends on Phase 2)

12. In `types.mlody`: add the `repr()` function.
13. In `types.mlody`: add `"representations"` attribute to the `typedef` rule.
14. In `types.mlody`: add the `representations != None` branch in `_type_impl`.
15. In `types_test.py`: add the repr tests from §3.1.

Run `bazel test //mlody/common:types_test` — all tests pass.

### Phase 5 — Lint and type check

16. `bazel build --config=lint //mlody/common:...` — zero errors.

### Dependency order

```
Phase 1 (bug fixes)
  -> Phase 2 (struct)
       -> Phase 3 (bool)   [independent of struct, but struct tests run first]
       -> Phase 4 (repr)   [depends on "repr_list" check added in Phase 2]
  -> Phase 5 (lint)        [after all changes]
```

Phases 3 and 4 are independent of each other and can be implemented in either
order after Phase 2.

### Estimated complexity

| Phase                            | Scope                                               | Effort  |
| -------------------------------- | --------------------------------------------------- | ------- |
| Phase 1 — bug fixes              | 3 lines across 2 files                              | Trivial |
| Phase 2 — struct typedef         | ~20 lines in attrs.mlody + ~15 lines in types.mlody | Small   |
| Phase 3 — bool completion        | ~30 lines in types.mlody + test updates             | Small   |
| Phase 4 — repr / representations | ~60 lines in types.mlody + ~40 lines tests          | Medium  |
| Phase 5 — lint pass              | Zero new code                                       | Trivial |

### BUILD.bazel changes required

None. The affected files (`types.mlody`, `attrs.mlody`) are already declared as
`data` dependencies of the existing library target in
`mlody/common/BUILD.bazel`. The test target already exists as
`//mlody/common:types_test`. No new targets, no Gazelle run needed.

---

## Testing Strategy

All tests live in `mlody/common/types_test.py`. The test infrastructure
(`_eval()` helper, `InMemoryFS`, `_BASE_FILES`) is already established and
requires no changes.

### Run commands

```sh
bazel test //mlody/common:types_test                # unit tests
bazel build --config=lint //mlody/common:types_test # lint
```

### Existing tests that must stay green (no modification except the one noted)

All 32+ existing tests must pass. The one required update is:

- `test_primitive_validators_unchanged_after_hierarchy`: change the `bool`
  sub-assertion from `validator(1)` raises `TypeError` to `validator(1)` passes
  and `validator(2)` raises `TypeError` (see §3.2).

### New tests

- 5 tests for repr / multiple representations (§3.1)
- 7 tests for bool completion (§3.2)
- 6 tests for struct typedef (§3.3)

Total new tests: 18

---

## Non-Functional Requirements

### Correctness

Coercion is deterministic (canonical type checked first, then reprs in
declaration order). Ambiguous inputs are impossible by construction: any value
that passes the canonical type validator is accepted by step 1, regardless of
whether it would also match a repr.

### Backward compatibility

All existing `.mlody` files that do not use `representations=` are unaffected.
The `repr()` function name is new and does not shadow any existing symbol in the
mlody sandbox (`repr` is not in `SAFE_BUILTINS` per `evaluator.py`). The bool
expansion is additive — previously valid inputs (`True`, `False`) remain valid.

### Performance

The coercion validator tries each repr's validator in sequence. For types with
zero representations this path is never entered. For types with N
representations the worst-case cost is O(N) validator calls per value
validation. In practice N will be small (1–3). No caching or index is needed.

---

## Risks and Mitigation

| Risk                                                                                                                                         | Mitigation                                                                                                                                                                            |
| -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R-001: `repr` name conflicts with a future Starlark builtin                                                                                  | `repr` is not in the current mlody sandbox; if it is added later, rename to `representation()` at that point.                                                                         |
| R-002: Bool predicate runs after abstract scalar validator (no-op), but `_validate_bool` Tier-1 check still gates `attr(type="bool")` fields | These are two separate paths. The typedef validator is used for value validation; `_PRIMITIVE_VALIDATORS["bool"]` is used for attr-type checking. They are intentionally independent. |
| R-003: Self-referential types (`mlody-folder` contains `mlody-folder`) not representable                                                     | Provisional schemas use bare `vector()`. Documented clearly with `# provisional` comments. Next design step introduces forward references.                                            |
| R-004: `try/except` in `_coercion_validator` catches `TypeError`/`ValueError` — may mask bugs                                                | Each `except` block catches only the two error types raised by validators per the established contract. Any other exception type propagates normally.                                 |

---

## Future Considerations

- **Forward type references** — `mlody-folder` needs `element_type=mlody-folder`
  which requires lazy resolution. Separate design step.
- **`from_canonical`** — stored on `repr` structs but not called. A future
  serialisation layer will call this to round-trip values back to their repr
  form.
- **Repr disambiguation** — if two reprs accept the same value, the first one
  wins. If explicit disambiguation is ever needed, add a `priority` field to
  `repr()`.
- **`struct` strict-by-default option** — could be added as a module-level
  default later without breaking existing usage.
