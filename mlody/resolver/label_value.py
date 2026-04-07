"""Label → MlodyValue resolution step.

This module adds the third step of the mlody show pipeline:

    parse_label(target_str)          → Label           [existing]
    workspace.expand_wildcard_label  → [Label, ...]    [existing]
    resolve_label_to_value(          → MlodyValue      [this module]
        label, workspace)

Public entry point: ``resolve_label_to_value``.
Re-exported from ``mlody.resolver``.

Extension seam (design D-3):
    The dispatch table ``TRAVERSAL_STRATEGIES`` maps kind strings to
    ``TraversalStrategy`` instances.  Adding a callable-based strategy for a
    future kind (e.g. one that lazily derives a value from the Workspace rather
    than from a static Struct field) requires only:
      1. Implement a class conforming to ``TraversalStrategy``.
      2. Add one entry to ``TRAVERSAL_STRATEGIES``.
    No changes to ``resolve_label_to_value`` or ``show`` are needed.

See also: design.md §D-3, §D-6.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mlody.core.label.label import Label
    from mlody.core.workspace import Workspace


# ---------------------------------------------------------------------------
# Value type hierarchy  (tasks 1.1 – 1.6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MlodyValue:
    """Base class for all resolved mlody values."""


@dataclass(frozen=True)
class MlodyWorkspaceValue(MlodyValue):
    """The workspace itself (label has no entity spec).

    ``name`` is the workspace name (``label.workspace``), or ``None`` for CWD.
    ``root`` is the absolute filesystem path of the monorepo root.
    """

    name: str | None
    root: str


@dataclass(frozen=True)
class MlodyFolderValue(MlodyValue):
    """A directory on disk under the workspace.

    ``path`` is workspace-relative (without leading slash), matching the label.
    ``children`` contains the names of the immediate directory entries.
    """

    path: str
    children: list[str]  # pyright: ignore[reportMutableClassVariable]


@dataclass(frozen=True)
class MlodySourceValue(MlodyValue):
    """A ``.mlody`` source file on disk.

    ``path`` is the workspace-relative path **without** the ``.mlody`` suffix,
    matching the label exactly.
    """

    path: str


@dataclass(frozen=True)
class MlodyTaskValue(MlodyValue):
    """Opaque wrapper around a task registry Struct."""

    struct: object


@dataclass(frozen=True)
class MlodyActionValue(MlodyValue):
    """Opaque wrapper around an action registry Struct."""

    struct: object


@dataclass(frozen=True)
class MlodyValueValue(MlodyValue):
    """Opaque wrapper around a value registry Struct."""

    struct: object


@dataclass(frozen=True)
class MlodyUnresolvedValue(MlodyValue):
    """Soft-failure sentinel.

    Returned (never raised) when any resolution step cannot proceed.
    ``reason`` is a human-readable string naming the failed step.
    """

    label: "Label"
    reason: str


# ---------------------------------------------------------------------------
# Traversal strategy protocol  (task 2.1)
# ---------------------------------------------------------------------------


class TraversalStrategy(Protocol):
    """Contract for attribute-path traversal per entity kind.

    v1 ships ``StructTraversalStrategy`` for task and action.
    Future callable-based strategies (e.g. lazy workspace-info computation)
    implement this protocol without touching ``resolve_label_to_value``.
    """

    def traverse(
        self,
        value: object,
        path: tuple[str, ...],
        label: "Label",
    ) -> MlodyValue: ...


# ---------------------------------------------------------------------------
# Struct-based traversal strategy  (task 2.2)
# ---------------------------------------------------------------------------


def _wrap_struct(kind: str, struct: object) -> MlodyValue:
    """Wrap a registry struct in its typed MlodyValue subclass."""
    if kind == "task":
        return MlodyTaskValue(struct=struct)
    if kind == "action":
        return MlodyActionValue(struct=struct)
    if kind == "value":
        return MlodyValueValue(struct=struct)
    # Future kinds added to the dispatch table will provide their own wrapper;
    # this function is called after kind dispatch, so this branch is unreachable
    # for registered kinds.
    return MlodyUnresolvedValue(
        label=_SENTINEL_LABEL,  # replaced by callers that know the label
        reason=f"no wrapper defined for kind {kind!r}",
    )


class StructTraversalStrategy:
    """Attribute-path traversal via getattr on a Starlark Struct.

    Walks each segment in ``path`` via ``getattr``.  Returns
    ``MlodyUnresolvedValue`` immediately on the first ``AttributeError``
    rather than propagating the exception to callers (design R-001).

    The terminal value is returned as-is (the raw Python object); callers are
    responsible for wrapping it if a typed ``MlodyValue`` is desired.
    """

    def __init__(self, kind: str) -> None:
        self._kind = kind

    def traverse(
        self,
        value: object,
        path: tuple[str, ...],
        label: "Label",
    ) -> MlodyValue:
        if not path:
            return _wrap_struct(self._kind, value)

        obj: object = value
        for i, segment in enumerate(path):
            try:
                obj = getattr(obj, segment)
            except AttributeError:
                traversed = ".".join(path[:i])
                parent = f" on '{traversed}'" if traversed else ""
                return MlodyUnresolvedValue(
                    label=label,
                    reason=(
                        f"attribute '{segment}' not found"
                        f"{parent} (label: {label!r})"
                    ),
                )
        # Terminal value reached — if it's a known entity kind (e.g. the .action
        # field on a task struct is itself an action), wrap it properly so the
        # caller gets a typed MlodyValue rather than a raw dump.
        terminal_kind = getattr(obj, "kind", None)
        if isinstance(terminal_kind, str) and terminal_kind in TRAVERSAL_STRATEGIES:
            return _wrap_struct(terminal_kind, obj)
        return _RawAttrValue(value=obj, label=label)


@dataclass(frozen=True)
class _RawAttrValue(MlodyValue):
    """Internal: terminal value reached after attribute-path traversal."""

    value: object
    label: "Label"


# A sentinel label used only in error messages from _wrap_struct above.
# It is never returned to callers because _wrap_struct is called only when
# kind is in the dispatch table (which provides typed wrappers).
class _SentinelEntitySpec:
    root = None
    path = None
    wildcard = False
    name = None
    field_path = None


class _SentinelLabel:
    workspace = None
    workspace_query = None
    entity = _SentinelEntitySpec()
    entity_query = None
    attribute_path = None
    attribute_query = None

    def __repr__(self) -> str:
        return "<sentinel>"


_SENTINEL_LABEL: "Label" = _SentinelLabel()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Dispatch table  (task 2.3)
# ---------------------------------------------------------------------------

TRAVERSAL_STRATEGIES: dict[str, TraversalStrategy] = {
    "task": StructTraversalStrategy("task"),
    "action": StructTraversalStrategy("action"),
    "value": StructTraversalStrategy("value"),
}


# ---------------------------------------------------------------------------
# Entity lookup  (task 3.2)
# ---------------------------------------------------------------------------


def _lookup_entity(
    workspace: "Workspace",
    stem: str,
    name: str,
) -> tuple[str, object] | None:
    """Scan ``workspace.evaluator.all`` for ``(kind, stem, name)``.

    Returns ``(kind, struct)`` on the first match, or ``None`` if not found.

    The registry key shape ``(kind, stem, name)`` is documented in
    ``starlarkish/evaluator/evaluator.py`` and used by ``workspace.resolve()``.
    Coupling note: see design.md §R-002 for the accepted trade-off.
    """
    for key, value in workspace.evaluator.all.items():
        if (
            isinstance(key, tuple)
            and len(key) == 3
            and key[1] == stem
            and key[2] == name
        ):
            return (key[0], value)
    return None


# ---------------------------------------------------------------------------
# Resolver  (tasks 3.1, 3.3, 3.4)
# ---------------------------------------------------------------------------


def resolve_label_to_value(label: "Label", workspace: "Workspace") -> MlodyValue:
    """Resolve a concrete ``Label`` to a typed ``MlodyValue``.

    Accepts only non-wildcard labels.  Wildcard expansion is the caller's
    responsibility and MUST happen before calling this function.

    Resolution pipeline (design §Resolution Pipeline):
    1. Derive absolute path from workspace root + root path + entity path.
    2. Terminal filesystem check: directory → MlodyFolderValue;
       ``<path>.mlody`` → MlodySourceValue or entity lookup.
    3. Entity name present: scan evaluator registry; dispatch to strategy table.
    4. Attribute path present on folder/source: MlodyUnresolvedValue.
    5. Any step fails: MlodyUnresolvedValue with step-specific reason.

    Raises:
        ValueError: if ``label.entity`` is a wildcard (programmer error).
    """
    # Guard: wildcard labels must be expanded before calling this function.
    if label.entity is not None and label.entity.wildcard:
        raise ValueError(
            f"resolve_label_to_value received a wildcard label {label!r}; "
            "expand wildcards before calling this function"
        )

    # -----------------------------------------------------------------------
    # Workspace-level label: no entity spec
    # -----------------------------------------------------------------------
    # When no entity is specified, the attribute path (if present) is treated
    # as a filesystem path relative to the monorepo root ("root substitution"):
    #   'info  →  <monorepo_root>/info  →  MlodyFolderValue or MlodySourceValue
    # A bare workspace label with no path at all → MlodyWorkspaceValue.
    if label.entity is None:
        if label.attribute_path is not None:
            # Workspace-level attribute label (e.g. 'info, 'info.branch).
            # Traverse workspace attributes directly — do not treat the path as
            # a filesystem path.
            obj: object = workspace
            for segment in label.attribute_path:
                try:
                    obj = getattr(obj, segment)
                except AttributeError:
                    return MlodyUnresolvedValue(
                        label=label,
                        reason=(
                            f"workspace has no attribute '{segment}' "
                            f"(label: {label!r})"
                        ),
                    )
            return _RawAttrValue(value=obj, label=label)
        return MlodyWorkspaceValue(
            name=label.workspace,
            root=str(workspace._monorepo_root),  # noqa: SLF001
        )

    # -----------------------------------------------------------------------
    # Step 1: derive absolute path
    # -----------------------------------------------------------------------
    entity_path: str = ""
    if label.entity is not None and label.entity.path:
        entity_path = label.entity.path.lstrip("/").rstrip("/")

    root_path: str = ""
    if label.entity is not None and label.entity.root is not None:
        root_info = workspace.root_infos.get(label.entity.root)
        if root_info is None:
            return MlodyUnresolvedValue(
                label=label,
                reason=(
                    f"root '{label.entity.root}' not found in workspace; "
                    f"available roots: {sorted(workspace.root_infos.keys())}"
                ),
            )
        root_path = root_info.path.lstrip("/").rstrip("/")

    # Bare root reference (@lexica with no path/name) → MlodyFolderValue for the
    # root directory, since a named root maps to a folder on disk.
    if label.entity is not None and label.entity.path is None and label.entity.name is None:
        root_abs = workspace._monorepo_root / root_path if root_path else workspace._monorepo_root  # noqa: SLF001
        children = sorted(os.listdir(root_abs))
        return MlodyFolderValue(path=root_path, children=children)

    # Build the absolute path: monorepo_root / root_path / entity_path
    abs_path = workspace._monorepo_root  # noqa: SLF001 — accepted per design R-002
    if root_path:
        abs_path = abs_path / root_path
    if entity_path:
        abs_path = abs_path / entity_path

    # -----------------------------------------------------------------------
    # Step 2: terminal filesystem classification
    # -----------------------------------------------------------------------
    entity_name: str | None = None
    if label.entity is not None:
        entity_name = label.entity.name

    attr_path: tuple[str, ...] | None = label.attribute_path

    if abs_path.is_dir():
        # Folder — entity name on a folder is not supported in v1
        if entity_name is not None:
            return MlodyUnresolvedValue(
                label=label,
                reason=(
                    f"entity name '{entity_name}' specified on a folder "
                    f"'{entity_path}'; use a .mlody source file path to address entities"
                ),
            )
        if attr_path is not None:
            return MlodyUnresolvedValue(
                label=label,
                reason=(
                    f"attribute traversal is not supported for folder values "
                    f"(label: {label!r})"
                ),
            )
        children = sorted(os.listdir(abs_path))
        return MlodyFolderValue(path=entity_path, children=children)

    # Check for a .mlody source file (suffix never in the label)
    mlody_path = abs_path.parent / (abs_path.name + ".mlody")

    if mlody_path.exists():
        # Source file found. If no entity name, return MlodySourceValue.
        if entity_name is None:
            if attr_path is not None:
                return MlodyUnresolvedValue(
                    label=label,
                    reason=(
                        f"attribute traversal is not supported for source-file values "
                        f"(label: {label!r})"
                    ),
                )
            return MlodySourceValue(path=entity_path)

        # -----------------------------------------------------------------------
        # Step 3: entity lookup
        # -----------------------------------------------------------------------
        # Derive stem: root_path / entity_path (mirrors evaluator._register logic)
        stem_parts: list[str] = []
        if root_path:
            stem_parts.append(root_path)
        if entity_path:
            stem_parts.append(entity_path)
        stem = "/".join(stem_parts)

        lookup = _lookup_entity(workspace, stem, entity_name)
        if lookup is None:
            return MlodyUnresolvedValue(
                label=label,
                reason=(
                    f"entity '{entity_name}' not found in registry "
                    f"(stem: '{stem}', label: {label!r})"
                ),
            )

        kind, struct = lookup

        # -----------------------------------------------------------------------
        # Step 4 / 5: attribute-path traversal via dispatch table
        # -----------------------------------------------------------------------
        strategy = TRAVERSAL_STRATEGIES.get(kind)
        if strategy is None:
            return MlodyUnresolvedValue(
                label=label,
                reason=(
                    f"kind '{kind}' is not supported by the label-value resolver "
                    f"(label: {label!r})"
                ),
            )

        # Combine entity field_path (from the colon section, e.g. ":task.out.w")
        # with the tick attribute_path (from "'out.w") into one traversal sequence.
        entity_field_path: tuple[str, ...] = (
            label.entity.field_path if label.entity and label.entity.field_path else ()
        )
        attr_path_tuple: tuple[str, ...] = attr_path if attr_path is not None else ()
        resolved_path: tuple[str, ...] = entity_field_path + attr_path_tuple
        return strategy.traverse(struct, resolved_path, label)

    # Neither a directory nor a .mlody source file
    return MlodyUnresolvedValue(
        label=label,
        reason=(
            f"path '{entity_path}' is not a directory or .mlody source file "
            f"under '{workspace._monorepo_root}' (label: {label!r})"  # noqa: SLF001
        ),
    )
