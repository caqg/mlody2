"""Tests for mlody.core.label.parser — parse_label() acceptance scenarios.

Every test class traces to a named requirement in
openspec/changes/mlody-label-parsing-3-parser/specs/label-parser/spec.md.
"""

from __future__ import annotations

import pytest

from mlody.core.label.errors import (
    AttributeParseError,
    EntityParseError,
    LabelParseError,
    WorkspaceParseError,
)
from mlody.core.label.parser import parse_label


class TestEmptyLabelRejection:
    """Requirement: parse_label raises LabelParseError on empty input."""

    def test_empty_string_raises_label_parse_error(self) -> None:
        # Scenario: Empty label raises LabelParseError
        with pytest.raises(LabelParseError):
            parse_label("")


class TestDisambiguationRule1:
    """Requirement: Rule 1 — pipe present splits workspace from entity+attr."""

    def test_pipe_with_empty_workspace_and_entity(self) -> None:
        # Scenario: "|//foo/bar" -> workspace=None, entity.path="foo/bar"
        result = parse_label("|//foo/bar")
        assert result.workspace is None
        assert result.entity is not None
        assert result.entity.path == "foo/bar"
        assert result.attribute_path is None

    def test_pipe_with_sha_like_workspace(self) -> None:
        result = parse_label("deadbeef|//foo/bar")
        assert result.workspace == "deadbeef"
        assert result.entity is not None
        assert result.entity.path == "foo/bar"

    def test_pipe_with_branch_workspace(self) -> None:
        result = parse_label("my-branch|//foo/bar")
        assert result.workspace == "my-branch"
        assert result.entity is not None
        assert result.entity.path == "foo/bar"

    def test_pipe_with_entity_and_attribute(self) -> None:
        # Scenario: Full label with all three parts
        result = parse_label("my-branch|//foo/bar:task-a'outputs.model")
        assert result.workspace == "my-branch"
        assert result.entity is not None
        assert result.entity.path == "foo/bar"
        assert result.entity.name == "task-a"
        assert result.attribute_path == ("outputs", "model")


class TestDisambiguationRule2:
    """Requirement: Rule 2 — no pipe, starts with '//' or '@'."""

    def test_double_slash_gives_entity_only(self) -> None:
        # Scenario: Rule 2 — no pipe, starts with "//"
        result = parse_label("//foo/bar")
        assert result.workspace is None
        assert result.entity is not None
        assert result.entity.path == "foo/bar"
        assert result.attribute_path is None

    def test_at_root_gives_entity_only(self) -> None:
        # Scenario: Rule 2 — no pipe, starts with "@"
        result = parse_label("@root//foo/bar")
        assert result.workspace is None
        assert result.entity is not None
        assert result.entity.root == "root"
        assert result.entity.path == "foo/bar"

    def test_double_slash_with_attribute(self) -> None:
        result = parse_label("//foo/bar'outputs.model")
        assert result.workspace is None
        assert result.entity is not None
        assert result.entity.path == "foo/bar"
        assert result.attribute_path == ("outputs", "model")


class TestDisambiguationRule3:
    """Requirement: Rule 3 — no pipe, no '//'/'@'."""

    def test_tick_prefix_gives_cwd_attribute(self) -> None:
        # Scenario: CWD attribute access (no workspace, no entity)
        result = parse_label("'info")
        assert result.workspace is None
        assert result.entity is None
        assert result.attribute_path == ("info",)

    def test_branch_tick_attr_gives_workspace_and_attribute(self) -> None:
        # Scenario: Rule 3 — with "'" -> workspace+attribute
        result = parse_label("my-branch'info")
        assert result.workspace == "my-branch"
        assert result.entity is None
        assert result.attribute_path == ("info",)

    def test_workspace_only_no_tick(self) -> None:
        # Scenario: Workspace-only label
        result = parse_label("my-branch")
        assert result.workspace == "my-branch"
        assert result.entity is None
        assert result.attribute_path is None


class TestWorkspaceQueryCapture:
    """Requirement: Workspace query is captured from workspace spec."""

    def test_workspace_query_captured(self) -> None:
        # Scenario: Workspace query captured
        result = parse_label("my-branch[git:author=mav]|//foo")
        assert result.workspace == "my-branch"
        assert result.workspace_query == "git:author=mav"
        assert result.entity is not None
        assert result.entity.path == "foo"

    def test_cwd_workspace_query_only(self) -> None:
        # Scenario: [bar]|entity — empty workspace name with query → CWD workspace
        result = parse_label("[bar]|@common//sandbox/...")
        assert result.workspace is None
        assert result.workspace_query == "bar"
        assert result.entity is not None
        assert result.entity.root == "common"
        assert result.entity.path == "sandbox"
        assert result.entity.wildcard is True

    def test_branch_with_entity_query(self) -> None:
        # Scenario: bar|entity[query] — branch workspace with entity query
        result = parse_label("bar|@common//sandbox/...[foo]")
        assert result.workspace == "bar"
        assert result.workspace_query is None
        assert result.entity is not None
        assert result.entity.root == "common"
        assert result.entity.path == "sandbox"
        assert result.entity.wildcard is True
        assert result.entity_query == "foo"

    def test_cwd_workspace_query_with_entity_query(self) -> None:
        # Scenario: [bar]|entity[foo] — both workspace query and entity query
        result = parse_label("[bar]|@common//sandbox/...[foo]")
        assert result.workspace is None
        assert result.workspace_query == "bar"
        assert result.entity is not None
        assert result.entity.root == "common"
        assert result.entity.path == "sandbox"
        assert result.entity.wildcard is True
        assert result.entity_query == "foo"

    def test_unclosed_workspace_bracket_raises(self) -> None:
        # Scenario: Unclosed workspace query raises WorkspaceParseError
        # Note: the "[" has no "|" after it so the bracket scan treats
        # "my-branch[git:author=mav" as workspace fragment
        with pytest.raises(WorkspaceParseError):
            parse_label("my-branch[git:author=mav|//foo")


class TestEntitySpecFull:
    """Requirement: Entity spec is fully parsed from entity fragment."""

    def test_root_path_and_name(self) -> None:
        # Scenario: Root, path, and name
        result = parse_label("@planning//foo/bar:task-a")
        assert result.entity is not None
        assert result.entity.root == "planning"
        assert result.entity.path == "foo/bar"
        assert result.entity.name == "task-a"
        assert result.entity.wildcard is False

    def test_root_and_path_no_name(self) -> None:
        result = parse_label("@planning//foo/bar")
        assert result.entity is not None
        assert result.entity.root == "planning"
        assert result.entity.path == "foo/bar"
        assert result.entity.name is None

    def test_path_and_name_no_root(self) -> None:
        result = parse_label("//foo/bar:task-a")
        assert result.entity is not None
        assert result.entity.root is None
        assert result.entity.path == "foo/bar"
        assert result.entity.name == "task-a"

    def test_path_only(self) -> None:
        result = parse_label("//foo/bar")
        assert result.entity is not None
        assert result.entity.path == "foo/bar"
        assert result.entity.name is None

    def test_at_root_without_double_slash_raises(self) -> None:
        # Scenario: Missing "//" after "@" raises EntityParseError
        with pytest.raises(EntityParseError):
            parse_label("@root-only")

    def test_empty_path_raises(self) -> None:
        # Scenario: Empty path raises EntityParseError
        with pytest.raises(EntityParseError):
            parse_label("//")

    def test_empty_name_raises(self) -> None:
        # "//foo:" — colon present but name is empty
        with pytest.raises(EntityParseError):
            parse_label("//foo:")


class TestEntitySpecWildcard:
    """Requirement: Wildcard path is parsed from entity fragment."""

    def test_wildcard_strips_and_sets_flag(self) -> None:
        # Scenario: Wildcard path — //foo/... -> path="foo", wildcard=True
        result = parse_label("//foo/...")
        assert result.entity is not None
        assert result.entity.path == "foo"
        assert result.entity.wildcard is True

    def test_root_wildcard_no_path_allowed(self) -> None:
        # Scenario: //... is valid — wildcard with no path prefix means "search everywhere"
        result = parse_label("//...")
        assert result.entity is not None
        assert result.entity.path is None
        assert result.entity.wildcard is True

    def test_root_wildcard_with_root_name_and_attr_path(self) -> None:
        # Scenario: @common//...:downloader'outputs.model
        result = parse_label("@common//...:downloader'outputs.model")
        assert result.entity is not None
        assert result.entity.root == "common"
        assert result.entity.path is None
        assert result.entity.wildcard is True
        assert result.entity.name == "downloader"
        assert result.attribute_path == ("outputs", "model")


class TestEntitySpecQuery:
    """Requirement: Entity query is captured and stored on Label."""

    def test_entity_query_captured(self) -> None:
        # Scenario: Entity query captured
        result = parse_label("//foo/bar[kind=action]")
        assert result.entity_query == "kind=action"
        assert result.entity is not None
        assert result.entity.path == "foo/bar"

    def test_unclosed_entity_bracket_raises(self) -> None:
        # Scenario: Unclosed entity query raises EntityParseError
        with pytest.raises(EntityParseError):
            parse_label("//foo/bar[kind=action")


class TestAttributePath:
    """Requirement: Attribute path is parsed after the tick (')."""

    def test_single_segment(self) -> None:
        result = parse_label("'info")
        assert result.attribute_path == ("info",)

    def test_multi_segment(self) -> None:
        # Scenario: Multi-segment attribute path
        result = parse_label("'outputs.model")
        assert result.attribute_path == ("outputs", "model")

    def test_attribute_query_captured(self) -> None:
        # Scenario: Attribute query captured
        result = parse_label("'info[git:author=mav]")
        assert result.attribute_path == ("info",)
        assert result.attribute_query == "git:author=mav"

    def test_trailing_dot_raises(self) -> None:
        # Scenario: Trailing dot raises AttributeParseError
        with pytest.raises(AttributeParseError):
            parse_label("'outputs.")

    def test_unclosed_attribute_bracket_raises(self) -> None:
        # Scenario: Unclosed attribute query raises AttributeParseError
        with pytest.raises(AttributeParseError):
            parse_label("'info[git:author=mav")
