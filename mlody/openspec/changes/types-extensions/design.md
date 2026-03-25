# Design: types.mlody Extensions

**Version:** 1.0 **Date:** 2026-03-25 **Architect:** @vitruvius **Status:**
Draft **Requirements:** `mlody/common/types.REQUIREMENTS.md`

---

## Problem Statement

`mlody/common/types.mlody` and `attrs.mlody` have four gaps that block progress
on the mlody type system:

1. **Multiple representations** — a `typedef` has no way to declare alternative
   input shapes that coerce to the canonical form. The email example (string or
   `{user, domain}` dict) is representative. The `repr()` factory, a
   `representations` field on `typedef`, and coercion at validation time are all
   missing.

2. **Bool type is incomplete** — `typedef(name="bool", base=scalar())` accepts
   only Python `bool`. The expanded set of truthy/falsy strings and integers
   (`"yes"`, `"1"`, `1`, etc.) is not handled, and there is no canonical
   normalisation to Python `bool`.

3. **Struct typedef is commented out** — the `struct` aggregate type and its
   dependents (`mlody-folder`, `mlody-source`) are blocked behind incomplete
   syntax. `struct` must be uncommented and wired to `_validate_map`; the two
   mlody built-in typedefs follow once `struct` is functional.

4. **`is` comparisons are invalid Starlark** — `is False`, `is True`, and
   `is None` / `is not None` are Python idioms that do not exist in Starlark.
   Three occurrences exist across `types.mlody` and `attrs.mlody` and must be
   replaced.

---

## Design Decisions

### D-1: `repr()` lives in `types.mlody` alongside `typedef`

`repr()` is a descriptor used only at `typedef` declaration time. Placing it in
`types.mlody` keeps the authoring surface in one file and avoids a new export
from `attrs.mlody`. It is implemented as a simple `Struct` constructor (no
registry, no side effects).

### D-2: Coercion lives inside the type's `validator` closure

The coercion algorithm (try canonical type → try each repr's type in order) is
encoded in a new `_make_repr_validator` closure inside `_type_impl`, composed
with the existing `extend_attrs` / `_map_validator` paths. This reuses the
existing validator composition machinery without changing its contract.

### D-3: `representations` is a new optional `typedef` attribute

`typedef`'s `rule()` declaration gains one new attribute:
`representations: attr(type="repr_list", mandatory=False, default=None)`. A
lightweight `"repr_list"` type check is added to `_validate_attr_value` (list of
`repr` structs). This follows the existing `"field_list"` pattern exactly.

### D-4: Bool coercion is expressed as `predicate` + `canonical` on the bare `typedef`

The `typedef(name="bool", ...)` declaration is expanded in-place in
`types.mlody` with an explicit `predicate` and `canonical`. No changes to the
`_validate_bool` generic validator in `attrs.mlody` are needed: after coercion
the stored value is always a Python `bool`, and `_validate_bool` already accepts
Python `bool` only — which is correct for post-coercion validation.
`_validate_bool` is the raw primitive gate; it is intentionally kept strict.

### D-5: `struct` reuses `_validate_map` unchanged

`struct` is an aggregate type whose shape is defined by `fields`. The
`_validate_map` function already handles per-field typing, required fields, and
strict mode. The `struct` typedef delegates to `_validate_map` the same way the
existing `typedef(fields=[...])` path does. No new validator logic is needed.

### D-6: `mlody-folder` and `mlody-source` schemas are deferred

The requirements explicitly defer their precise schemas. The two typedefs are
uncommented with placeholder field lists that satisfy the parser but are clearly
marked as provisional. This unblocks any consumer that checks for the registered
type name without actually exercising the validator.

### D-7: Bug fixes are mechanical — no design risk

All three `is False` occurrences and the absence of `is None` / `is not None` in
both files are straightforward text replacements. The mlody CLAUDE.md already
documents the correct Starlark idioms (`== None`, `!= None`).

---

## Architecture Sketch

### Changes by file

```
mlody/common/types.mlody       (MODIFIED — primary change)
  _repr_validator()            NEW — factory: repr coercion validator
  repr()                       NEW — repr descriptor constructor
  typedef rule attrs           +representations field
  _type_impl                   +coercion branch for representations
  typedef(name="bool", ...)    expanded with predicate + canonical
  typedef(name="struct", ...)  uncommented + wired
  typedef(name="mlody-folder") uncommented + provisional schema
  typedef(name="mlody-source") uncommented + provisional schema
  is False (line 19)           -> == False

mlody/common/attrs.mlody       (MODIFIED — bug fixes only)
  is False (line 340)          -> == False
  is False (line 361)          -> == False
```

No new files. No new Python modules. No Bazel target changes.

### Coercion flow (new)

```
typedef(..., representations=[repr(...)])
  -> _type_impl captures representations
  -> builds _repr_validator closure:
       1. try canonical type validator (base validator)
          if passes -> apply canonical() if present -> return value
       2. for each repr in representations:
          try repr.type.validator(value)
          if passes -> return repr.to_canonical(value)
       3. raise TypeError listing accepted forms
  -> extends the type struct with this closure as extra_validator
     (composed on top of the existing base validator chain via extend_attrs)
```

### Bool coercion (expanded)

```
_BOOL_TRUTHY = {True, "true", "yes", "1", 1}   # (case-insensitive for strings)
_BOOL_FALSY  = {False, "false", "no", "0", 0}

predicate  = lambda v: _is_accepted_bool(v)
canonical  = lambda v: _coerce_bool(v)          # -> Python bool
```

The `predicate` is the gate; `canonical` is the normaliser. Together they plug
directly into the existing `predicate=` and `canonical=` parameters of
`typedef`.

---

## Constraints and Risks

| Risk                                                                | Mitigation                                                                                                                                                                                                                 |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repr` shadows Python builtin `repr`                                | In Starlark there is no builtin `repr` in the mlody sandbox (only `str` is used for debug output). No conflict.                                                                                                            |
| Coercion order ambiguity                                            | Representations are tried in declaration order. The canonical type is always tried first. This is deterministic and documented.                                                                                            |
| Bool predicate accepting integer `1`/`0` but not other integers     | The accepted set is fixed per requirements §2.1; integers other than 0/1 are rejected.                                                                                                                                     |
| `mlody-folder` / `mlody-source` provisional schemas may need rework | They are marked `# provisional` in the source. The next design step refines them.                                                                                                                                          |
| `is False` -> `== False` correctness                                | In both `.mlody` files the pattern is `res is False` where `res` is the return value of a predicate call. Starlark lambdas returning `False` produce a value that `== False` matches identically. Semantically equivalent. |

---

## Open Questions

None. All requirements are resolved. The provisional schemas for `mlody-folder`
and `mlody-source` are explicitly deferred by the requirements document itself
(§3.3).
