# mlody/common: User Guide

`mlody/common` provides reusable DSL building blocks for two things:
- Data **types** (`types.mlody`)
- Data **locations** (`locations.mlody`)

Think of these as declarative descriptors used for validation and compatibility checks.

## Quick start

```starlark
load("//mlody/common/types.mlody")
load("//mlody/common/locations.mlody")
```

After loading, built-in factories are available in scope.

## Defining and using types

Built-in type families include:
- Scalars: `integer`, `bool`, `float`, `string`
- Aggregates: `vector`, `tuple`, `map`

Use factory kwargs to configure constraints:

```starlark
age = integer(min=0, max=150)
username = string(min_length=3, pattern="[a-z_][a-z0-9_]*")
scores = map(value_type=integer())
```

Each type struct has a `validator(value)` callable.

### Create custom named types with `typedef`

```starlark
load("//mlody/common/types.mlody")

typedef(
    name = "positive_integer",
    base = integer(min=0),
)

typedef(
    name = "even_natural_number",
    base = integer(min=1),
    predicate = lambda v: v % 2 == 0,
)
```

Common `typedef(...)` parameters:
- `name`: required type name
- `base`: parent type (optional for bootstrap primitives)
- `attrs`: extra typed attributes on the type definition
- `predicate`: extra validator predicate
- `fields`: per-key map schema (record-like type)
- `strict`: for `fields` mode, reject unknown keys
- `canonical`: optional canonicalization callable
- `abstract`: mark as classification-only anchor

### Record-like map types with `field(...)`

```starlark
typedef(
    name = "point2d",
    fields = [
        field(name="x", type=float()),
        field(name="y", type=float()),
    ],
)

typedef(
    name = "named_point",
    fields = [
        field(name="x", type=float()),
        field(name="y", type=float()),
        field(name="label", type=string(), mandatory=False),
    ],
    strict = True,
)
```

### Positional tuple schema via list base

```starlark
typedef(
    name = "latlon",
    base = [float(), float()],
)
```

### Narrowing via factory calls

Every registered type gets an injected factory named after the type:

```starlark
typedef(name="age", base=integer(min=0, max=150))
teen_age = age(max=19)
```

Factory behavior:
- Unknown kwargs raise `TypeError`
- Kwarg values are type-checked
- Returned value is an anonymous derived type struct
- Calling with no kwargs returns the base type struct

## Defining and using locations

`locations.mlody` defines location descriptors (where values live), not transport/execution logic.

Built-ins:
- `s3(bucket=..., prefix=..., region=...)`
- `posix(path=...)`

Examples:

```starlark
any_s3 = s3()
team_bucket = s3(bucket="team-prod", region="us-east-1")
local_data = posix(path="/data/runs")
```

### Create custom location kinds with `location`

```starlark
load("//mlody/common/locations.mlody")

location(
    name = "team_s3",
    base = s3(bucket="team-prod"),
)

location(
    name = "restricted_posix",
    base = posix(),
    predicate = lambda v: v != "/tmp",
)
```

Common `location(...)` parameters:
- `name`: required location kind name
- `base`: parent location kind
- `attrs`: extra typed attributes
- `predicate`: extra validator predicate
- `abstract`: mark as non-concrete

## Notes and conventions

- Type/location definitions are registered globally and factories are injected into scope.
- Child definitions inherit parent attributes.
- Redeclaring inherited attributes in `attrs` causes a conflict error.
- `attrs.mlody` provides shared primitives (`attr`, `field`, `extend_attrs`) used by both systems.
