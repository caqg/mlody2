"""Helpers for typed virtual value traversal.

Virtual values are ``Struct(kind="value")`` instances whose location has
``type == "virtual"`` and a ``materializer`` callable. This module provides
shared helpers for forcing such values and for traversing declared type
attributes without dropping to raw Python data.
"""

from __future__ import annotations

from typing import Callable

from starlarkish.core.struct import Struct


_SENTINEL = object()


def force_virtual_value(value: object) -> object:
    """Materialize a virtual value Struct; return all other inputs unchanged."""
    if not isinstance(value, Struct):
        return value
    if getattr(value, "kind", None) != "value":
        return value
    loc = getattr(value, "location", None)
    if loc is None or getattr(loc, "type", None) != "virtual":
        return value
    materializer = getattr(loc, "materializer", None)
    if materializer is None:
        return value
    return materializer(value)


def step_object(obj: object, segment: str) -> object:
    """Traverse one segment on a materialized object.

    Lists are traversed by matching an element's ``name`` field.
    Everything else uses ``getattr``.
    """
    if isinstance(obj, list):
        for item in obj:
            if getattr(item, "name", None) == segment:
                return item
        raise KeyError(segment)
    return getattr(obj, segment)


def is_record_type(value_type: object) -> bool:
    """Return True when the type represents a record/map-style schema."""
    return (
        getattr(value_type, "kind", None) == "record"
        or getattr(value_type, "_root_kind", None) == "record"
    )


def lookup_record_field(value_type: object, segment: str) -> object | None:
    """Return the declared record field spec for ``segment``, if any."""
    direct_fields = getattr(value_type, "fields", None)
    attrs = getattr(value_type, "attributes", None)
    attrs_fields = attrs.get("fields") if isinstance(attrs, dict) else None
    for field_obj in list(direct_fields or attrs_fields or []):
        if getattr(field_obj, "name", None) == segment:
            return field_obj
    return None


def lookup_virtual_attribute(value_type: object, segment: str) -> object | None:
    """Return the declared virtual attribute spec for ``segment``, if any."""
    direct = getattr(value_type, "virtual_attributes", None)
    attrs = getattr(value_type, "attributes", None)
    attrs_virtual = attrs.get("virtual_attributes") if isinstance(attrs, dict) else None
    for attr_obj in list(direct or attrs_virtual or []):
        if getattr(attr_obj, "name", None) == segment:
            return attr_obj
    return None


def lookup_declared_attribute(value_type: object, segment: str) -> object | None:
    """Return the declared virtual attr or record field spec for ``segment``."""
    virtual_attr = lookup_virtual_attribute(value_type, segment)
    if virtual_attr is not None:
        return virtual_attr
    if is_record_type(value_type):
        return lookup_record_field(value_type, segment)
    return None


def make_virtual_value(
    *,
    value_type: object,
    label: str,
    materializer: Callable[[object], object],
    name: str | None = None,
) -> Struct:
    """Construct a typed virtual value Struct."""
    virtual_loc = Struct(
        kind="location",
        type="virtual",
        name="virtual",
        materializer=materializer,
    )
    fields: dict[str, object] = {
        "kind": "value",
        "type": value_type,
        "location": virtual_loc,
        "label": label,
        "_lineage": [],
    }
    if name is not None:
        fields["name"] = name
    return Struct(**fields)


def traverse_virtual_value(value: Struct, path: tuple[str, ...], label: str) -> Struct:
    """Traverse declared attributes on a virtual value, returning a child value."""
    current = value
    for segment in path:
        current_type = getattr(current, "type", None)
        attr_spec = lookup_declared_attribute(current_type, segment)
        if attr_spec is None:
            raise AttributeError(segment)
        child_type = getattr(attr_spec, "type", _SENTINEL)
        if child_type is _SENTINEL or child_type is None:
            raise AttributeError(segment)

        parent = current

        def _materializer(_v: object, *, _parent: Struct = parent, _segment: str = segment) -> object:
            parent_value = force_virtual_value(_parent)
            return step_object(parent_value, _segment)

        current = make_virtual_value(
            value_type=child_type,
            label=label,
            materializer=_materializer,
            name=getattr(attr_spec, "name", segment),
        )
    return current
