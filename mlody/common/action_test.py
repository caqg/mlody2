"""Integration tests for mlody/common/action.mlody."""
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
_VALUES_MLODY = (_THIS_DIR / "values.mlody").read_text()
_ACTION_MLODY = (_THIS_DIR / "action.mlody").read_text()

_BASE_FILES: dict[str, str] = {
    "mlody/core/rule.mlody": _RULE_MLODY,
    "mlody/common/attrs.mlody": _ATTRS_MLODY,
    "mlody/common/types.mlody": _TYPES_MLODY,
    "mlody/common/locations.mlody": _LOCATIONS_MLODY,
    "mlody/common/values.mlody": _VALUES_MLODY,
    "mlody/common/action.mlody": _ACTION_MLODY,
}

_PREAMBLE = (
    'load("//mlody/common/types.mlody")\n'
    'load("//mlody/common/locations.mlody")\n'
    'load("//mlody/common/values.mlody")\n'
    'load("//mlody/common/action.mlody")\n'
)


def _eval(extra_mlody: str) -> Evaluator:
    script = _PREAMBLE + dedent(extra_mlody)
    files = dict(_BASE_FILES)
    files["test.mlody"] = script
    with InMemoryFS(files, root="/project") as root:
        ev = Evaluator(root)
        ev.eval_file(root / "test.mlody")
        ev.resolve()
    return ev


# ---------------------------------------------------------------------------
# TC-001: action() registers with kind="action"
# ---------------------------------------------------------------------------


def test_action_registers_with_kind_action() -> None:
    ev = _eval(
        'value(name="inp", type=integer(), location=s3())\n'
        'value(name="out", type=integer(), location=s3())\n'
        'action(name="my_action", inputs=["inp"], outputs=["out"], implementation=["//mlody/common:action_lib"])\n'
    )
    assert "my_action" in ev._actions_by_name
    a = ev._actions_by_name["my_action"]
    assert a.kind == "action"
    assert a.name == "my_action"


# ---------------------------------------------------------------------------
# TC-002: action stores inputs and outputs
# ---------------------------------------------------------------------------


def test_action_stores_inputs_and_outputs() -> None:
    ev = _eval(
        'value(name="inp", type=integer(), location=s3())\n'
        'value(name="out", type=string(), location=s3())\n'
        'action(name="a", inputs=["inp"], outputs=["out"], implementation=["//mlody/common:action_lib"])\n'
    )
    a = ev._actions_by_name["a"]
    assert a.inputs[0].name == "inp"
    assert a.outputs[0].name == "out"


# ---------------------------------------------------------------------------
# TC-003: string value label in inputs resolves
# ---------------------------------------------------------------------------


def test_action_string_value_label_resolves() -> None:
    ev = _eval(
        'value(name="my_val", type=integer(), location=s3())\n'
        'action(name="a", inputs=["my_val"], outputs=[], implementation=["//mlody/common:action_lib"])\n'
    )
    a = ev._actions_by_name["a"]
    assert a.inputs[0].name == "my_val"
    assert a.inputs[0].kind == "value"


# ---------------------------------------------------------------------------
# TC-004: empty inputs and outputs allowed
# ---------------------------------------------------------------------------


def test_action_empty_inputs_and_outputs_allowed() -> None:
    ev = _eval('action(name="empty", inputs=[], outputs=[], implementation=["//mlody/common:action_lib"])\n')
    a = ev._actions_by_name["empty"]
    assert a.inputs == []
    assert a.outputs == []


# ---------------------------------------------------------------------------
# TC-005: implementation is mandatory
# ---------------------------------------------------------------------------


def test_action_implementation_is_mandatory() -> None:
    with pytest.raises(ValueError, match="Missing mandatory argument"):
        _eval('action(name="a", inputs=[], outputs=[])\n')


# ---------------------------------------------------------------------------
# TC-006: config stores value refs when provided
# ---------------------------------------------------------------------------


def test_action_config_value_refs_stored() -> None:
    ev = _eval(
        'value(name="cfg", type=integer(), location=s3())\n'
        'action(name="a", inputs=[], outputs=[], config=["cfg"], implementation=["//mlody/common:action_lib"])\n'
    )
    a = ev._actions_by_name["a"]
    assert len(a.config) == 1
    assert a.config[0].name == "cfg"


# ---------------------------------------------------------------------------
# TC-007: unknown value label raises NameError
# ---------------------------------------------------------------------------


def test_action_unknown_value_label_raises_name_error() -> None:
    with pytest.raises(NameError):
        _eval('action(name="a", inputs=["nonexistent"], outputs=[], implementation=["//mlody/common:action_lib"])\n')


# ---------------------------------------------------------------------------
# TC-008: wrong type in inputs raises TypeError
# ---------------------------------------------------------------------------


def test_action_wrong_type_in_inputs_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _eval('action(name="a", inputs=[integer()], outputs=[], implementation=["//mlody/common:action_lib"])\n')


# ---------------------------------------------------------------------------
# TC-009: implementation rejects empty target list
# ---------------------------------------------------------------------------


def test_action_implementation_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one target"):
        _eval('action(name="a", inputs=[], outputs=[], implementation=[])\n')


# ---------------------------------------------------------------------------
# TC-010: implementation stores bazel target strings
# ---------------------------------------------------------------------------


def test_action_implementation_stores_bazel_targets() -> None:
    ev = _eval(
        'action(\n'
        '  name="a",\n'
        '  inputs=[],\n'
        '  outputs=[],\n'
        '  implementation=["//mlody/common/huggingface:model-download", "//mlody/cli:main"]\n'
        ')\n'
    )
    a = ev._actions_by_name["a"]
    assert a.implementation == [
        "//mlody/common/huggingface:model-download",
        "//mlody/cli:main",
    ]


# ---------------------------------------------------------------------------
# TC-011: implementation rejects non-string entries
# ---------------------------------------------------------------------------


def test_action_implementation_non_string_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _eval('action(name="a", inputs=[], outputs=[], implementation=[1])\n')


# ---------------------------------------------------------------------------
# TC-012: requirements default to empty list when omitted
# ---------------------------------------------------------------------------


def test_action_requirements_default_to_empty_list() -> None:
    ev = _eval(
        'action(name="a", inputs=[], outputs=[], implementation=["//mlody/common:action_lib"])\n'
    )
    a = ev._actions_by_name["a"]
    assert a.requirements == []


# ---------------------------------------------------------------------------
# TC-013: requirements stores declared resource requirements
# ---------------------------------------------------------------------------


def test_action_requirements_stored_for_supported_kinds() -> None:
    ev = _eval(
        'action(\n'
        '  name="a",\n'
        '  inputs=[],\n'
        '  outputs=[],\n'
        '  requirements=[\n'
        '    memory_requirement(amount=8, unit="GiB"),\n'
        '    disk_io_requirement(read_mbps=500, write_mbps=300),\n'
        '    network_requirement(bandwidth_mbps=1000),\n'
        '    cpu_requirement(count=4, type="x86_64"),\n'
        '    gpu_requirement(count=1, type="nvidia-l4"),\n'
        '  ],\n'
        '  implementation=["//mlody/common:action_lib"]\n'
        ')\n'
    )
    a = ev._actions_by_name["a"]
    assert len(a.requirements) == 5
    assert a.requirements[0].requirement == "memory"
    assert a.requirements[3].requirement == "cpu"
    assert a.requirements[3].type == "x86_64"
    assert a.requirements[4].requirement == "gpu"
    assert a.requirements[4].type == "nvidia-l4"


# ---------------------------------------------------------------------------
# TC-014: requirements rejects non-requirement elements
# ---------------------------------------------------------------------------


def test_action_requirements_rejects_non_requirement_structs() -> None:
    with pytest.raises(TypeError, match="struct\\(kind='requirement'\\)"):
        _eval(
            'action(\n'
            '  name="a",\n'
            '  inputs=[],\n'
            '  outputs=[],\n'
            '  requirements=[integer()],\n'
            '  implementation=["//mlody/common:action_lib"]\n'
            ')\n'
        )


# ---------------------------------------------------------------------------
# TC-015: cpu type defaults to "*" when omitted
# ---------------------------------------------------------------------------


def test_action_cpu_requirement_defaults_type_to_star() -> None:
    ev = _eval(
        'action(\n'
        '  name="a",\n'
        '  inputs=[],\n'
        '  outputs=[],\n'
        '  requirements=[cpu_requirement(count=2)],\n'
        '  implementation=["//mlody/common:action_lib"]\n'
        ')\n'
    )
    a = ev._actions_by_name["a"]
    assert len(a.requirements) == 1
    assert a.requirements[0].requirement == "cpu"
    assert a.requirements[0].type == "*"


def test_action_gpu_requirement_defaults_type_to_star() -> None:
    ev = _eval(
        'action(\n'
        '  name="a",\n'
        '  inputs=[],\n'
        '  outputs=[],\n'
        '  requirements=[gpu_requirement(count=1)],\n'
        '  implementation=["//mlody/common:action_lib"]\n'
        ')\n'
    )
    a = ev._actions_by_name["a"]
    assert len(a.requirements) == 1
    assert a.requirements[0].requirement == "gpu"
    assert a.requirements[0].type == "*"
