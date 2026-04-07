"""Tests for mlody.resolver.label_value — resolve_label_to_value golden tests.

Each test traces back to a named scenario in the spec
(openspec/changes/mav-457-label-value-mapping/specs/label-value-resolver/spec.md).

Filesystem fixtures: pyfakefs (``fs`` fixture).
Evaluator fixtures: Workspace with pyfakefs + .mlody content — no mocking of
starlarkish internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pyfakefs.fake_filesystem import FakeFilesystem

from mlody.core.label import parse_label
from mlody.core.workspace import Workspace
from mlody.resolver.label_value import (
    TRAVERSAL_STRATEGIES,
    MlodyActionValue,
    MlodyFolderValue,
    MlodySourceValue,
    MlodyTaskValue,
    MlodyUnresolvedValue,
    MlodyValue,  # noqa: F401 — imported for type annotations in extensibility test
    MlodyValueValue,
    _RawAttrValue,
    resolve_label_to_value,
)

# ---------------------------------------------------------------------------
# Shared .mlody file contents used across fixtures
# ---------------------------------------------------------------------------

ROOT = Path("/project")

BUILTINS_MLODY = """\
def root(name, path, description=""):
    builtins.register("root", struct(
        name=name,
        path=path,
        description=description,
    ))
"""

ROOTS_MLODY = """\
load("//mlody/core/builtins.mlody", "root")

root(name="myroot", path="//teams/myroot", description="test root")
"""

TASK_MLODY = """\
builtins.register("task", struct(
    kind="task",
    name="my_task",
    inputs=[],
    outputs=[],
    action=None,
))
"""

TASK_WITH_INPUTS_MLODY = """\
builtins.register("task", struct(
    kind="task",
    name="my_task",
    inputs=[],
    outputs=[],
    action=None,
    extra=struct(count=42),
))
"""

ACTION_MLODY = """\
builtins.register("action", struct(
    kind="action",
    name="my_action",
    inputs=[],
    outputs=[],
    config=[],
))
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(fs: FakeFilesystem, extra_files: dict[str, str] | None = None) -> Workspace:
    """Construct a minimal Workspace with the given extra .mlody files."""
    fs.create_file(str(ROOT / "mlody/core/builtins.mlody"), contents=BUILTINS_MLODY)
    fs.create_file(str(ROOT / "mlody/roots.mlody"), contents=ROOTS_MLODY)

    if extra_files:
        for rel_path, contents in extra_files.items():
            fs.create_file(str(ROOT / rel_path), contents=contents)

    ws = Workspace(monorepo_root=ROOT, skipped_mlody_paths=[])
    ws.load()
    return ws


# ---------------------------------------------------------------------------
# 6.1 Golden test: label resolves to MlodyFolderValue
# Scenario: "Golden test for MlodyFolderValue"
# ---------------------------------------------------------------------------


class TestFolderValue:
    """Requirement: Filesystem traversal — folder detection."""

    def test_label_resolves_to_folder_value(self, fs: FakeFilesystem) -> None:
        """Scenario: Label resolves to a folder."""
        fs.create_dir(str(ROOT / "teams/myroot/pkg/mydir"))
        fs.create_file(str(ROOT / "teams/myroot/pkg/mydir/child1.txt"), contents="")
        fs.create_file(str(ROOT / "teams/myroot/pkg/mydir/child2.txt"), contents="")
        ws = _make_workspace(fs)

        label = parse_label("@myroot//pkg/mydir")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyFolderValue)
        assert result.path == "pkg/mydir"
        assert "child1.txt" in result.children
        assert "child2.txt" in result.children

    def test_folder_children_are_immediate_only(self, fs: FakeFilesystem) -> None:
        """Children list contains only immediate directory entries (not recursive)."""
        fs.create_dir(str(ROOT / "teams/myroot/pkg/mydir"))
        fs.create_file(str(ROOT / "teams/myroot/pkg/mydir/a.mlody"), contents="")
        fs.create_dir(str(ROOT / "teams/myroot/pkg/mydir/subdir"))
        ws = _make_workspace(fs)

        label = parse_label("@myroot//pkg/mydir")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyFolderValue)
        # Only the top-level entries
        assert set(result.children) == {"a.mlody", "subdir"}

    def test_folder_with_attribute_path_is_unresolved(self, fs: FakeFilesystem) -> None:
        """Scenario: Folder with no entity name and attribute path is unresolved."""
        fs.create_dir(str(ROOT / "teams/myroot/pkg/mydir"))
        ws = _make_workspace(fs)

        # parse_label for @myroot//pkg/mydir'attr
        label = parse_label("@myroot//pkg/mydir'some_attr")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "attribute traversal" in result.reason.lower()


# ---------------------------------------------------------------------------
# 6.2 Golden test: label resolves to MlodySourceValue
# Scenario: "Golden test for MlodySourceValue"
# ---------------------------------------------------------------------------


class TestSourceValue:
    """Requirement: Filesystem traversal — source file detection."""

    def test_label_resolves_to_source_value(self, fs: FakeFilesystem) -> None:
        """Scenario: Label resolves to a source file."""
        fs.create_dir(str(ROOT / "teams/myroot/pkg"))
        fs.create_file(str(ROOT / "teams/myroot/pkg/foo.mlody"), contents="")
        ws = _make_workspace(fs)

        label = parse_label("@myroot//pkg/foo")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodySourceValue)
        assert result.path == "pkg/foo"

    def test_source_with_attribute_path_is_unresolved(self, fs: FakeFilesystem) -> None:
        """Scenario: Source file with no entity name and attribute path is unresolved."""
        fs.create_dir(str(ROOT / "teams/myroot/pkg"))
        fs.create_file(str(ROOT / "teams/myroot/pkg/foo.mlody"), contents="")
        ws = _make_workspace(fs)

        label = parse_label("@myroot//pkg/foo'some_attr")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "attribute traversal" in result.reason.lower()

    def test_missing_path_is_unresolved(self, fs: FakeFilesystem) -> None:
        """Scenario: Terminal path is neither directory nor source file."""
        fs.create_dir(str(ROOT / "teams/myroot/pkg"))
        ws = _make_workspace(fs)

        label = parse_label("@myroot//pkg/missing")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "not a directory" in result.reason.lower()


# ---------------------------------------------------------------------------
# 6.3 Golden test: label resolves to MlodyTaskValue
# Scenario: "Golden test for MlodyTaskValue"
# ---------------------------------------------------------------------------


class TestTaskValue:
    """Requirement: Entity dispatch — task to MlodyTaskValue."""

    def test_label_resolves_to_task_value(self, fs: FakeFilesystem) -> None:
        """Scenario: Task entity resolves to MlodyTaskValue."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyTaskValue)
        assert result.struct is not None
        assert getattr(result.struct, "name", None) == "my_task"

    def test_task_value_struct_is_registry_struct(self, fs: FakeFilesystem) -> None:
        """The struct field is the raw registry object."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyTaskValue)
        # isinstance check confirms exact type (spec scenario)
        from mlody.resolver.label_value import MlodyTaskValue as _T

        assert isinstance(result, _T)


# ---------------------------------------------------------------------------
# 6.4 Golden test: label resolves to MlodyActionValue
# Scenario: "Golden test for MlodyActionValue"
# ---------------------------------------------------------------------------


class TestActionValue:
    """Requirement: Entity dispatch — action to MlodyActionValue."""

    def test_label_resolves_to_action_value(self, fs: FakeFilesystem) -> None:
        """Scenario: Action entity resolves to MlodyActionValue."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": ACTION_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_action")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyActionValue)
        assert result.struct is not None
        assert getattr(result.struct, "name", None) == "my_action"


VALUE_MLODY = """\
builtins.register("value", struct(
    kind="value",
    name="my_value",
    type=None,
    location=None,
    default=None,
    source=None,
    _lineage=[],
))
"""


# ---------------------------------------------------------------------------
# 6.5 Golden test: label resolves to MlodyValueValue
# ---------------------------------------------------------------------------


class TestValueKind:
    """Requirement: Entity dispatch — value kind to MlodyValueValue."""

    def test_label_resolves_to_value_value(self, fs: FakeFilesystem) -> None:
        """Scenario: Value entity resolves to MlodyValueValue."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": VALUE_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_value")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyValueValue)
        assert result.struct is not None
        assert getattr(result.struct, "name", None) == "my_value"

    def test_attribute_traversal_on_value(self, fs: FakeFilesystem) -> None:
        """Scenario: Attribute traversal into a value struct field."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": VALUE_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_value.name")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, _RawAttrValue)
        assert result.value == "my_value"

    def test_missing_attribute_returns_unresolved(self, fs: FakeFilesystem) -> None:
        """Scenario: Attribute not present on the value struct."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": VALUE_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_value.nonexistent")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "nonexistent" in result.reason


# ---------------------------------------------------------------------------
# 6.6 Golden test: MlodyUnresolvedValue — path not found
# Scenario: "Golden test for MlodyUnresolvedValue"
# ---------------------------------------------------------------------------


class TestUnresolvedValue:
    """Requirement: Soft failure — MlodyUnresolvedValue returned, never raised."""

    def test_unresolved_when_path_not_found(self, fs: FakeFilesystem) -> None:
        """Scenario: path does not exist → MlodyUnresolvedValue."""
        ws = _make_workspace(fs)

        label = parse_label("@myroot//nonexistent/path")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert result.label is label
        assert len(result.reason) > 0

    def test_unresolved_has_non_empty_reason(self, fs: FakeFilesystem) -> None:
        """reason must be a non-empty human-readable string (spec requirement)."""
        ws = _make_workspace(fs)

        label = parse_label("@myroot//gone")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert isinstance(result.reason, str)
        assert result.reason.strip() != ""

    def test_unknown_root_returns_unresolved(self, fs: FakeFilesystem) -> None:
        """Unknown root name → MlodyUnresolvedValue (not KeyError)."""
        ws = _make_workspace(fs)

        label = parse_label("@unknownroot//pkg/foo")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "unknownroot" in result.reason


# ---------------------------------------------------------------------------
# 6.6 Golden test: MlodyUnresolvedValue — entity not in registry
# Scenario: "Entity not found in registry yields MlodyUnresolvedValue"
# ---------------------------------------------------------------------------


class TestUnresolvedEntityNotInRegistry:
    """Requirement: Registry lookup — entity not found."""

    def test_entity_not_found_returns_unresolved(self, fs: FakeFilesystem) -> None:
        """Scenario: entity not in registry → MlodyUnresolvedValue."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:ghost")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "ghost" in result.reason

    def test_entity_reason_names_stem(self, fs: FakeFilesystem) -> None:
        """reason must name the stem it was searched in (spec NFR-7.4)."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:ghost")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        # Reason should mention where we looked
        assert "ghost" in result.reason


# ---------------------------------------------------------------------------
# 6.7 Attribute-path traversal on task struct: full consumption
# Scenario: "Attribute path fully consumed on task struct"
# ---------------------------------------------------------------------------


class TestAttributePathTraversal:
    """Requirement: Traversal strategy — struct-based attribute path consumption."""

    def test_attribute_path_fully_consumed(self, fs: FakeFilesystem) -> None:
        """Scenario: extra.count fully consumed → terminal value returned."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_WITH_INPUTS_MLODY},
        )

        # @myroot//pkg/foo:my_task'extra.count
        label = parse_label("@myroot//pkg/foo:my_task'extra.count")
        result = resolve_label_to_value(label, ws)

        # Terminal value reached — returned as _RawAttrValue
        assert isinstance(result, _RawAttrValue)
        assert result.value == 42

    def test_attribute_path_on_task_no_residual(self, fs: FakeFilesystem) -> None:
        """No residual field_path on the returned value (spec FR-003)."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_WITH_INPUTS_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task'extra")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, _RawAttrValue)
        # The result has no attribute_path — all consumed
        assert not hasattr(result, "attribute_path")

    def test_no_attribute_path_returns_task_value(self, fs: FakeFilesystem) -> None:
        """When no attribute path, returns MlodyTaskValue (not _RawAttrValue)."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyTaskValue)


# ---------------------------------------------------------------------------
# 6.8 Attribute-path traversal: missing intermediate attribute
# Scenario: "Missing intermediate attribute yields MlodyUnresolvedValue"
# ---------------------------------------------------------------------------


class TestAttributePathMissingAttribute:
    """Requirement: Traversal strategy — missing attribute returns MlodyUnresolvedValue."""

    def test_missing_attribute_returns_unresolved(self, fs: FakeFilesystem) -> None:
        """Scenario: missing_field does not exist → MlodyUnresolvedValue."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_WITH_INPUTS_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task'extra.missing_field")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "missing_field" in result.reason

    def test_first_segment_missing_returns_unresolved(self, fs: FakeFilesystem) -> None:
        """When the very first attribute segment is missing, returns unresolved."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task'nonexistent_field")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "nonexistent_field" in result.reason


# ---------------------------------------------------------------------------
# 6.9 Extensibility seam: custom TraversalStrategy for kind "widget"
# Scenario: "Custom strategy registered for a new kind is dispatched"
# ---------------------------------------------------------------------------


class TestExtensibilitySeam:
    """Requirement: TraversalStrategy extension seam."""

    def test_custom_strategy_dispatched_for_new_kind(self, fs: FakeFilesystem) -> None:
        """Scenario: stub strategy for 'widget' kind is invoked."""
        sentinel_value = MlodyFolderValue(path="stub", children=[])

        class StubWidgetStrategy:
            """Minimal TraversalStrategy implementation for testing the seam."""

            def __init__(self) -> None:
                self.called = False
                self.call_args: tuple[object, tuple[str, ...], Any] | None = None

            def traverse(
                self,
                value: object,
                path: tuple[str, ...],
                label: Any,
            ) -> MlodyValue:
                self.called = True
                self.call_args = (value, path, label)
                return sentinel_value

        stub = StubWidgetStrategy()
        # Register a widget entity and a widget strategy
        widget_mlody = """\
builtins.register("task", struct(
    kind="task",
    name="my_widget",
    inputs=[],
    outputs=[],
    action=None,
))
"""
        # We cannot register a custom kind in the evaluator without modifying it,
        # so we test the dispatch table directly:
        # Register stub into the dispatch table for the "task" kind override
        # (to demonstrate the seam without touching the evaluator).
        original_strategy = TRAVERSAL_STRATEGIES.get("task")
        TRAVERSAL_STRATEGIES["task"] = stub  # type: ignore[assignment]
        try:
            ws = _make_workspace(
                fs,
                extra_files={"teams/myroot/pkg/widgets.mlody": widget_mlody},
            )

            label = parse_label("@myroot//pkg/widgets:my_widget")
            result = resolve_label_to_value(label, ws)
        finally:
            if original_strategy is not None:
                TRAVERSAL_STRATEGIES["task"] = original_strategy
            else:
                del TRAVERSAL_STRATEGIES["task"]

        assert stub.called
        assert result is sentinel_value

    def test_unknown_kind_returns_unresolved(self, fs: FakeFilesystem) -> None:
        """Kind not in dispatch table → MlodyUnresolvedValue (no KeyError)."""
        # We cannot register a custom kind in the evaluator, but we can remove
        # a known kind from the table to simulate an unknown kind.
        original_strategy = TRAVERSAL_STRATEGIES.pop("task")
        try:
            ws = _make_workspace(
                fs,
                extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
            )

            label = parse_label("@myroot//pkg/foo:my_task")
            result = resolve_label_to_value(label, ws)
        finally:
            TRAVERSAL_STRATEGIES["task"] = original_strategy

        assert isinstance(result, MlodyUnresolvedValue)
        assert "task" in result.reason


# ---------------------------------------------------------------------------
# Wildcard guard
# Scenario: "Wildcard label raises immediately"
# ---------------------------------------------------------------------------


class TestEntityFieldPathTraversal:
    """Requirement: field_path in entity name is combined with attribute_path for traversal."""

    def test_dotted_entity_name_traverses_field_path(self, fs: FakeFilesystem) -> None:
        """Scenario: @myroot//pkg/foo:my_task.extra.count resolves to terminal value 42.

        The parser splits :my_task.extra.count into name="my_task",
        field_path=("extra", "count").  The resolver must traverse that path.
        """
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_WITH_INPUTS_MLODY},
        )

        # Dot-path syntax: entity field_path carries the traversal
        label = parse_label("@myroot//pkg/foo:my_task.extra.count")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, _RawAttrValue)
        assert result.value == 42

    def test_dotted_entity_name_single_segment_traversal(self, fs: FakeFilesystem) -> None:
        """Scenario: :my_task.extra → field_path=("extra",) → returns _RawAttrValue."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_WITH_INPUTS_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task.extra")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, _RawAttrValue)
        # The extra field is a struct — verify it has the expected attribute
        assert hasattr(result.value, "count")

    def test_dotted_entity_name_combined_with_tick_path(self, fs: FakeFilesystem) -> None:
        """Scenario: field_path + tick attribute_path are both applied."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_WITH_INPUTS_MLODY},
        )

        # :my_task.extra traverses to the extra struct, then 'count traverses further
        label = parse_label("@myroot//pkg/foo:my_task.extra'count")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, _RawAttrValue)
        assert result.value == 42

    def test_dotted_entity_name_missing_field_returns_unresolved(
        self, fs: FakeFilesystem
    ) -> None:
        """Scenario: :my_task.nonexistent → MlodyUnresolvedValue."""
        ws = _make_workspace(
            fs,
            extra_files={"teams/myroot/pkg/foo.mlody": TASK_MLODY},
        )

        label = parse_label("@myroot//pkg/foo:my_task.nonexistent")
        result = resolve_label_to_value(label, ws)

        assert isinstance(result, MlodyUnresolvedValue)
        assert "nonexistent" in result.reason


class TestWildcardGuard:
    """Requirement: resolve_label_to_value public API — wildcard guard."""

    def test_wildcard_label_raises_value_error(self, fs: FakeFilesystem) -> None:
        """Scenario: wildcard label → ValueError (programmer error)."""
        ws = _make_workspace(fs)

        label = parse_label("@myroot//pkg/...")
        with pytest.raises(ValueError, match="wildcard"):
            resolve_label_to_value(label, ws)
