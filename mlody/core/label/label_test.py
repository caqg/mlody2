"""Tests for mlody.core.label.label — EntitySpec and Label frozen dataclasses."""

from __future__ import annotations

import dataclasses

import pytest

from mlody.core.label.label import EntitySpec, Label


# ---------------------------------------------------------------------------
# TestEntitySpec
# ---------------------------------------------------------------------------


class TestEntitySpec:
    """Requirement: EntitySpec is a frozen dataclass with five typed fields."""

    def test_entity_spec_fields_populated(self) -> None:
        e = EntitySpec(
            root="planning",
            path="foo/bar",
            wildcard=False,
            name="task-a",
            field_path=None,
        )
        assert e.root == "planning"
        assert e.path == "foo/bar"
        assert e.wildcard is False
        assert e.name == "task-a"
        assert e.field_path is None

    def test_entity_spec_optional_fields_accept_none(self) -> None:
        e = EntitySpec(root=None, path="foo/bar", wildcard=False, name=None, field_path=None)
        assert e.root is None
        assert e.name is None
        assert e.field_path is None

    def test_entity_spec_field_path_tuple(self) -> None:
        # Requirement: field_path stores the dot-separated path after entity name
        e = EntitySpec(
            root="lexica",
            path="diamond",
            wildcard=False,
            name="pretrain",
            field_path=("outputs", "backbone_weights"),
        )
        assert e.field_path == ("outputs", "backbone_weights")

    def test_entity_spec_wildcard_with_no_name(self) -> None:
        e = EntitySpec(root=None, path="foo", wildcard=True, name=None, field_path=None)
        assert e.wildcard is True
        assert e.name is None

    def test_entity_spec_mutation_raises_frozen_instance_error(self) -> None:
        e = EntitySpec(root="r", path="p", wildcard=False, name="n", field_path=None)
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.root = "other"  # type: ignore[misc]

    def test_entity_spec_equal_instances(self) -> None:
        a = EntitySpec(root="r", path="p", wildcard=False, name="n", field_path=None)
        b = EntitySpec(root="r", path="p", wildcard=False, name="n", field_path=None)
        assert a == b

    def test_entity_spec_equal_instances_share_hash(self) -> None:
        a = EntitySpec(root="r", path="p", wildcard=True, name=None, field_path=None)
        b = EntitySpec(root="r", path="p", wildcard=True, name=None, field_path=None)
        assert hash(a) == hash(b)

    def test_entity_spec_usable_as_dict_key(self) -> None:
        e = EntitySpec(root=None, path="foo/bar", wildcard=False, name="x", field_path=None)
        d = {e: "value"}
        assert d[e] == "value"


# ---------------------------------------------------------------------------
# TestLabel
# ---------------------------------------------------------------------------


class TestLabel:
    """Requirement: Label is a frozen dataclass with six typed fields."""

    def test_label_all_fields_populated(self) -> None:
        entity = EntitySpec(root=None, path="foo/bar", wildcard=False, name="task-a", field_path=None)
        lbl = Label(
            workspace="main",
            workspace_query=None,
            entity=entity,
            entity_query='kind="action"',
            attribute_path=("outputs", "model"),
            attribute_query=None,
        )
        assert lbl.workspace == "main"
        assert lbl.workspace_query is None
        assert lbl.entity == entity
        assert lbl.entity_query == 'kind="action"'
        assert lbl.attribute_path == ("outputs", "model")
        assert lbl.attribute_query is None

    def test_label_cwd_workspace_attribute_only(self) -> None:
        # Represents a label with no workspace or entity — only an attribute path.
        lbl = Label(
            workspace=None,
            workspace_query=None,
            entity=None,
            entity_query=None,
            attribute_path=("info",),
            attribute_query=None,
        )
        assert lbl.workspace is None
        assert lbl.entity is None
        assert lbl.attribute_path == ("info",)

    def test_label_mutation_raises_frozen_instance_error(self) -> None:
        lbl = Label(
            workspace="main",
            workspace_query=None,
            entity=None,
            entity_query=None,
            attribute_path=None,
            attribute_query=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            lbl.workspace = "other"  # type: ignore[misc]

    def test_label_equal_instances(self) -> None:
        entity = EntitySpec(root=None, path="p", wildcard=False, name="t", field_path=None)
        a = Label(
            workspace="ws",
            workspace_query=None,
            entity=entity,
            entity_query=None,
            attribute_path=None,
            attribute_query=None,
        )
        b = Label(
            workspace="ws",
            workspace_query=None,
            entity=entity,
            entity_query=None,
            attribute_path=None,
            attribute_query=None,
        )
        assert a == b

    def test_label_equal_instances_share_hash(self) -> None:
        a = Label(
            workspace=None,
            workspace_query=None,
            entity=None,
            entity_query=None,
            attribute_path=("x",),
            attribute_query=None,
        )
        b = Label(
            workspace=None,
            workspace_query=None,
            entity=None,
            entity_query=None,
            attribute_path=("x",),
            attribute_query=None,
        )
        assert hash(a) == hash(b)

    def test_label_nested_equality_via_entity_spec(self) -> None:
        # Two Labels containing separately-constructed but equal EntitySpecs must be equal.
        entity_a = EntitySpec(root="r", path="p", wildcard=False, name="n", field_path=None)
        entity_b = EntitySpec(root="r", path="p", wildcard=False, name="n", field_path=None)
        a = Label(
            workspace="ws",
            workspace_query=None,
            entity=entity_a,
            entity_query=None,
            attribute_path=None,
            attribute_query=None,
        )
        b = Label(
            workspace="ws",
            workspace_query=None,
            entity=entity_b,
            entity_query=None,
            attribute_path=None,
            attribute_query=None,
        )
        assert a == b

    def test_label_usable_as_dict_key(self) -> None:
        lbl = Label(
            workspace="main",
            workspace_query=None,
            entity=None,
            entity_query=None,
            attribute_path=("outputs",),
            attribute_query=None,
        )
        d = {lbl: 99}
        assert d[lbl] == 99
