# Tasks: types-extensions

## Task 1 — Bug fixes

Replace `is False` with `== False` at three locations:

- `mlody/common/types.mlody` line 19
- `mlody/common/attrs.mlody` lines 340 and 361

Also replace any `is None` / `is not None` with `== None` / `!= None` found in
the same two files.

Status: [x]

---

## Task 2 — Struct typedef

- Add `"struct"` to `_PRIMITIVE_KINDS` in `mlody/common/attrs.mlody`
- Add `"struct"` dispatch branch to `generic_validate` delegating to
  `_validate_map`
- Add `"repr_list"` branch to `_validate_attr_value` (validates a list of
  `repr()` descriptors)
- Uncomment and complete `typedef(name="struct", ...)` in
  `mlody/common/types.mlody`
- Uncomment `mlody-folder` and `mlody-source` typedefs (field schemas TBD; leave
  as stubs if not yet specified)
- Add 6 struct tests

Status: [x]

---

## Task 3 — Bool completion

- Add `_BOOL_TRUTHY_STRINGS`, `_BOOL_FALSY_STRINGS`, `_is_accepted_bool`, and
  `_coerce_bool` helpers to `mlody/common/types.mlody`
- Expand `typedef(name="bool", ...)` with `predicate` + `canonical` on
  `base=scalar()` (not `_validate_bool` — see design.md §1.5 for rationale)
- Update one existing test whose assertion changes under the new bool behaviour
- Add 7 new bool tests covering all accepted truthy/falsy inputs and rejection
  of out-of-range integers

Status: [x]

---

## Task 4 — Multiple representations

- Add `repr()` factory function to `mlody/common/types.mlody` (fields: `name`,
  `type`, `to_canonical`, `from_canonical`)
- Add `representations` attribute to the `typedef` rule declaration
- Add `representations != None` coercion branch in `_type_impl`: try canonical
  type first (applying `canonical` callable if defined), then try each repr in
  order, raise `TypeError` if none match
- Add 5 new repr tests including the email example from REQUIREMENTS.md

Status: [x]

---

## Task 5 — Lint pass

Run `bazel build --config=lint //mlody/common/...` and fix all warnings/errors.

Status: [x]
