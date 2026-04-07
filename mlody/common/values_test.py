"""Integration tests for mlody/common/values.mlody."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from starlarkish.evaluator.evaluator import Evaluator
from starlarkish.evaluator.testing import InMemoryFS

_THIS_DIR = Path(__file__).parent
_RULE_MLODY = (_THIS_DIR.parent / "core" / "rule.mlody").read_text()
_ATTRS_MLODY = (_THIS_DIR / "attrs.mlody").read_text()
_TYPES_MLODY = (_THIS_DIR / "types.mlody").read_text()
_LOCATIONS_MLODY = (_THIS_DIR / "locations.mlody").read_text()
_REPRESENTATION_MLODY = (_THIS_DIR / "representation.mlody").read_text()
_VALUES_MLODY = (_THIS_DIR / "values.mlody").read_text()

_BASE_FILES: dict[str, str] = {
    "mlody/core/rule.mlody": _RULE_MLODY,
    "mlody/common/attrs.mlody": _ATTRS_MLODY,
    "mlody/common/types.mlody": _TYPES_MLODY,
    "mlody/common/locations.mlody": _LOCATIONS_MLODY,
    "mlody/common/representation.mlody": _REPRESENTATION_MLODY,
    "mlody/common/values.mlody": _VALUES_MLODY,
}


def _eval(extra_mlody: str) -> Evaluator:
    script = (
        'load("//mlody/common/types.mlody")\n'
        'load("//mlody/common/locations.mlody")\n'
        'load("//mlody/common/representation.mlody")\n'
        'load("//mlody/common/values.mlody")\n'
        + dedent(extra_mlody)
    )
    files = dict(_BASE_FILES)
    files["test.mlody"] = script
    with InMemoryFS(files, root="/project") as root:
        ev = Evaluator(root)
        ev.eval_file(root / "test.mlody")
    return ev


def _result(ev: Evaluator) -> object:
    return ev._module_globals[ev.root_path / "test.mlody"]["result"]


# ---------------------------------------------------------------------------
# TC-001: value() with direct structs registers with kind="value"
# ---------------------------------------------------------------------------


def test_value_with_direct_structs_registers_correctly() -> None:
    """TC-001: value(name='x', type=integer(), location=s3()) → kind='value'."""
    ev = _eval('value(name="x", type=integer(), location=s3())')
    assert "x" in ev._values_by_name
    v = ev._values_by_name["x"]
    assert v.kind == "value"
    assert v.name == "x"


def test_value_stores_type_and_location_name() -> None:
    """TC-001: value struct holds .type.name and .location.name."""
    ev = _eval('value(name="x", type=integer(), location=s3())')
    v = ev._values_by_name["x"]
    assert v.type.name == "integer"
    assert v.location.name == "s3"


# ---------------------------------------------------------------------------
# TC-002: string label for type is resolved
# ---------------------------------------------------------------------------


def test_value_string_type_label_resolves_to_type_struct() -> None:
    """TC-002: type='integer' (string) resolves to the integer type struct."""
    ev = _eval('value(name="y", type="integer", location=s3())')
    v = ev._values_by_name["y"]
    assert v.type.kind == "type"
    assert v.type.name == "integer"


# ---------------------------------------------------------------------------
# TC-003: string label for location is resolved
# ---------------------------------------------------------------------------


def test_value_string_location_label_resolves_to_location_struct() -> None:
    """TC-003: location='s3' (string) resolves to the s3 location struct."""
    ev = _eval('value(name="z", type=integer(), location="s3")')
    v = ev._values_by_name["z"]
    assert v.location.kind == "location"
    assert v.location.name == "s3"


# ---------------------------------------------------------------------------
# TC-004: constrained type struct is stored
# ---------------------------------------------------------------------------


def test_value_stores_constrained_type_struct() -> None:
    """TC-004: type=integer(max=100) stores the constrained struct."""
    ev = _eval('value(name="bounded", type=integer(max=100), location=s3())')
    v = ev._values_by_name["bounded"]
    assert v.type.kind == "type"
    assert v.type.attributes.get("max") == 100


# ---------------------------------------------------------------------------
# TC-005: constrained location struct is stored
# ---------------------------------------------------------------------------


def test_value_stores_constrained_location_struct() -> None:
    """TC-005: location=s3(bucket='prod') stores the constrained struct."""
    ev = _eval('value(name="prod_data", type=integer(), location=s3(bucket="prod"))')
    v = ev._values_by_name["prod_data"]
    assert v.location.kind == "location"
    assert v.location.attributes.get("bucket") == "prod"


# ---------------------------------------------------------------------------
# TC-006: unknown type string raises NameError
# ---------------------------------------------------------------------------


def test_value_unknown_type_string_raises_name_error() -> None:
    """TC-006: type='nonexistent' raises NameError."""
    with pytest.raises(NameError):
        _eval('value(name="bad", type="nonexistent", location=s3())')


# ---------------------------------------------------------------------------
# TC-007: unknown location string raises NameError
# ---------------------------------------------------------------------------


def test_value_unknown_location_string_raises_name_error() -> None:
    """TC-007: location='nonexistent' raises NameError."""
    with pytest.raises(NameError):
        _eval('value(name="bad", type=integer(), location="nonexistent")')


# ---------------------------------------------------------------------------
# TC-008: wrong type for type attr raises TypeError
# ---------------------------------------------------------------------------


def test_value_location_struct_as_type_raises_type_error() -> None:
    """TC-008: passing a location struct as type raises TypeError."""
    with pytest.raises(TypeError):
        _eval('value(name="bad", type=s3(), location=s3())')


# ---------------------------------------------------------------------------
# TC-009: wrong type for location attr raises TypeError
# ---------------------------------------------------------------------------


def test_value_type_struct_as_location_raises_type_error() -> None:
    """TC-009: passing a type struct as location raises TypeError."""
    with pytest.raises(TypeError):
        _eval('value(name="bad", type=integer(), location=integer())')


# ---------------------------------------------------------------------------
# TC-010: freshly registered value has an empty _lineage list
# ---------------------------------------------------------------------------


def test_value_has_empty_lineage_on_creation() -> None:
    """TC-010: a new value has _lineage == []."""
    ev = _eval('value(name="v", type=integer(), location=s3())')
    v = ev._values_by_name["v"]
    assert v._lineage == []


def test_value_lineage_is_a_list() -> None:
    """TC-010: _lineage is a list, not None or missing."""
    ev = _eval('value(name="v", type=integer(), location=s3())')
    v = ev._values_by_name["v"]
    assert isinstance(v._lineage, list)


# ---------------------------------------------------------------------------
# TC-011: value() allows partial declarations (type/location optional)
# ---------------------------------------------------------------------------


def test_value_allows_missing_location() -> None:
    ev = _eval('value(name="v", type=integer())')
    v = ev._values_by_name["v"]
    assert v.type.kind == "type"
    assert v.location is None


def test_value_allows_missing_type() -> None:
    ev = _eval('value(name="v", location=s3())')
    v = ev._values_by_name["v"]
    assert v.type is None
    assert v.location.kind == "location"


# ---------------------------------------------------------------------------
# TC-012: value() accepts optional default of any Starlark builtin type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("1", 1),
        ("3.14", 3.14),
        ('"hello"', "hello"),
        ("True", True),
        ("[1, 2, 3]", [1, 2, 3]),
    ],
)
def test_value_stores_default_builtin_types(expr: str, expected: object) -> None:
    ev = _eval(f'value(name="v", type=integer(), location=s3(), default={expr})')
    v = ev._values_by_name["v"]
    assert v.default == expected


def test_value_stores_dict_literal_default() -> None:
    ev = _eval('value(name="v", type=integer(), location=s3(), default={"k": "v"})')
    v = ev._values_by_name["v"]
    # In starlarkish, dict literals are represented as Struct values.
    assert getattr(v.default, "k", None) == "v"


def test_value_stores_tuple_literal_default() -> None:
    ev = _eval('value(name="v", type=integer(), location=s3(), default=(1, 2))')
    v = ev._values_by_name["v"]
    # Tuples are normalized to list in runtime values.
    assert v.default == [1, 2]


# ---------------------------------------------------------------------------
# TC-013 (5.1): value() with representation=json() carries representation struct
# ---------------------------------------------------------------------------


def test_value_with_representation_json_carries_representation_struct() -> None:
    """5.1: value(representation=json()) → result.representation.kind == 'representation'
    and result.representation.name == 'json'.
    """
    ev = _eval('value(name="x", type=integer(), location=s3(), representation=json())')
    v = ev._values_by_name["x"]
    assert v.representation is not None
    assert v.representation.kind == "representation"
    assert v.representation.name == "json"


# ---------------------------------------------------------------------------
# TC-014 (5.2): value() without representation has representation=None
# ---------------------------------------------------------------------------


def test_value_without_representation_has_representation_none() -> None:
    """5.2: value() without representation attr → result.representation is None."""
    ev = _eval('value(name="x", type=integer(), location=s3())')
    v = ev._values_by_name["x"]
    assert v.representation is None


# ---------------------------------------------------------------------------
# TC-015 (5.3): value() with wrong-kind representation raises TypeError
# ---------------------------------------------------------------------------


def test_value_with_wrong_kind_representation_raises_type_error() -> None:
    """5.3: value(representation=posix()) raises TypeError naming kind 'representation'."""
    with pytest.raises(TypeError, match="representation"):
        _eval('value(name="x", type=integer(), location=s3(), representation=posix())')
