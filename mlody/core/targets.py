"""Target address parsing and resolution for Bazel-style target references."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TargetAddress:
    """Parsed Bazel-style target address.

    Format: @ROOT//package/path:target_name.field.subfield
    """

    root: str | None
    package_path: str | None
    target_name: str
    field_path: tuple[str, ...]


def parse_target(raw: str) -> TargetAddress:
    """Parse a target string into a TargetAddress.

    Supported formats:
        @ROOT//package/path:target_name.field.subfield
        //package/path:target_name
        :target_name.field

    Raises:
        ValueError: On malformed input.
    """
    if not raw:
        msg = "Target string is empty"
        raise ValueError(msg)

    # TODO(mlody-label-parsing): remove :name shorthand once callers migrate
    # to the full label grammar. The core parser does not support bare ':name'
    # forms (no // prefix), so handle it here directly.
    if raw.startswith(":"):
        rest = raw[1:]
        if "." in rest:
            dot_idx = rest.index(".")
            target_name = rest[:dot_idx]
            field_path: tuple[str, ...] = tuple(rest[dot_idx + 1 :].split("."))
        else:
            target_name = rest
            field_path = ()
        if not target_name:
            raise ValueError(f"Target name is empty in {raw!r}")
        return TargetAddress(
            root=None,
            package_path=None,
            target_name=target_name,
            field_path=field_path,
        )

    # Delegate all other forms to the canonical label parser.
    # TODO(mlody-label-parsing): replace callers with Label directly
    #   and delete this wrapper.
    from mlody.core.label import parse_label as _core_parse_label  # noqa: PLC0415
    from mlody.core.label.errors import LabelParseError as _LabelParseError  # noqa: PLC0415

    try:
        lbl = _core_parse_label(raw)
    except _LabelParseError as exc:
        raise ValueError(str(exc)) from exc

    if lbl.entity is None:
        raise ValueError(f"Target string has no entity spec: {raw!r}")

    entity = lbl.entity

    if entity.name is None:
        raise ValueError(f"Invalid target syntax: missing ':' separator in {raw!r}")

    # Split entity.name on '.' to recover target_name + field_path.
    # The core parser stores the full ':suffix' verbatim in entity.name;
    # TargetAddress splits on '.' to separate target_name from field traversal.
    name_parts = entity.name.split(".")
    target_name = name_parts[0]
    field_path = tuple(name_parts[1:])

    if not target_name:
        raise ValueError(f"Target name is empty in {raw!r}")

    return TargetAddress(
        root=entity.root,
        package_path=entity.path,
        target_name=target_name,
        field_path=field_path,
    )


def resolve_target_value(
    address: TargetAddress,
    roots: dict[str, Any],
) -> object:
    """Resolve a TargetAddress against a roots dictionary.

    Traverses roots dict -> root object -> target_name -> field_path
    using getattr() for Struct field access.

    Raises:
        KeyError: If root or target is not found.
        AttributeError: If a field in field_path does not exist.
    """
    if address.root is None:
        msg = f"No root specified in target address; available roots: {sorted(roots)}"
        raise KeyError(msg)

    if address.root not in roots:
        msg = f"Root {address.root!r} not found; available roots: {sorted(roots)}"
        raise KeyError(msg)

    obj: object = roots[address.root]

    # Navigate to target_name via getattr
    obj = getattr(obj, address.target_name)

    # Traverse field_path
    for field in address.field_path:
        obj = getattr(obj, field)

    return obj
