# Requirements: mlody/common/types.mlody extensions

**Status:** Draft **Date:** 2026-03-25

---

## 1. Multiple Representations

### 1.1 Overview

A `typedef` may declare multiple named **representations** — alternative forms
in which a value of that type may be expressed. Representations belong to the
type; fields inherit representations through their declared type.

### 1.2 Canonical representation

- Exactly one representation is designated canonical.
- The canonical form is the authoritative storage and comparison form.
- The `canonical` callable on the `typedef` normalises an incoming value in
  canonical type into canonical form (e.g. trim whitespace, lowercase). If
  omitted, the value is stored as-is.
- All values, regardless of how they arrive, are coerced to canonical form
  before being stored or compared.

### 1.3 Alternative representations

Each alternative representation is described by a `repr()` descriptor:

| Field            | Type        | Description                                         |
| ---------------- | ----------- | --------------------------------------------------- |
| `name`           | `string`    | Unique name within the type (e.g. `"parsed"`)       |
| `type`           | type struct | The type that validates this representation's shape |
| `to_canonical`   | `callable`  | Converts a value in this repr to canonical form     |
| `from_canonical` | `callable`  | Converts a canonical value back to this repr        |

A new `repr()` factory will be added to `types.mlody` (or `attrs.mlody`) for
constructing representation descriptors.

### 1.4 Coercion

Automatic coercion applies at validation time:

1. Check whether the incoming value matches the canonical type. If so, apply
   `canonical` (if defined) and store the result.
2. Otherwise, try each alternative representation's type validator in
   declaration order.
3. On the first match, call `to_canonical` and use the result as the stored
   value.
4. If no representation matches, raise `TypeError` with a message listing all
   accepted forms.

Coercion is automatic for now. If ambiguous matching becomes a problem, this
will be revisited.

### 1.5 Equality

Two values of the same type are equal if and only if their canonical
representations are equal. This falls out naturally from always storing
canonical form.

### 1.6 Example — email

```starlark
typedef(
    name = "email",
    base = string(pattern = r".+@.+"),
    canonical = lambda s: s.strip().lower(),
    representations = [
        repr(
            name = "parsed",
            type = map(fields = [
                field(name = "user",   type = string()),
                field(name = "domain", type = string()),
            ]),
            to_canonical   = lambda d: (d["user"] + "@" + d["domain"]).strip().lower(),
            from_canonical = lambda s: {"user": s.split("@")[0], "domain": s.split("@")[1]},
        ),
    ],
)
```

All three of the following produce the same canonical value `"user@domain.com"`
and therefore compare equal:

- `"  User@Domain.COM  "` — string, normalized by `canonical`
- `"user@domain.com"` — already canonical
- `{user: "User", domain: "Domain.COM"}` — struct, converted by `to_canonical`

---

## 2. Bool Type Completion

### 2.1 Accepted inputs and canonical mapping

| Input value                                         | Canonical result |
| --------------------------------------------------- | ---------------- |
| `True`, `"true"`, `"yes"`, `"1"`, integer `1`       | `True`           |
| `False`, `"false"`, `"no"`, `"0"`, integer `0`      | `False`          |
| Any other value (including integers other than 0/1) | **Error**        |

String matching is **case-insensitive** (`"YES"`, `"Yes"`, `"yes"` are all
valid).

### 2.2 Canonical form

The canonical form is Python `bool` (`True` / `False`). Bool is **not** a
subtype of `integer`; it remains a subtype of `scalar`.

### 2.3 Implementation

The bare `typedef(name="bool", base=scalar())` must be completed with:

- A `predicate` that accepts only the values listed in 2.1 and rejects
  everything else.
- A `canonical` callable that maps accepted values to `True` or `False`.

`_validate_bool` in `attrs.mlody` must be updated to accept the expanded input
set. After coercion, stored values are always Python `bool`, so downstream
validators are unchanged.

The accepted set is fixed in the primitive declaration. Users who need different
truthy semantics must define their own type.

---

## 3. Struct Typedef

### 3.1 Goal

The commented-out `typedef(name="struct", ...)` must be uncommented and made
functional.

### 3.2 Shape

`struct` is an aggregate type whose validator delegates to `_validate_map`,
which already supports per-field typing, required fields, and strict mode. No
new validator logic is needed.

### 3.3 Blocked typedefs

Once `struct` is functional, `mlody-folder` and `mlody-source` must also be
uncommented and completed. Their precise field schemas are deferred to a
subsequent design step.

---

## 4. Bug Fixes

### 4.1 `is False` / `is True` comparisons

The `is` operator does not exist in Starlark. All occurrences of `is False` and
`is True` must be replaced with `== False` and `== True` respectively.

Files to audit: `types.mlody`, `attrs.mlody`. Run `grep 'is False\|is True'`
across both files to get the full list.

### 4.2 `is None` / `is not None` comparisons

Per the mlody coding standard, `is None` and `is not None` must be replaced with
`== None` and `!= None`. Audit alongside 4.1.
