"""Frozen dataclasses representing a parsed mlody label."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntitySpec:
    """The entity component of a label (path, wildcard flag, optional name)."""

    root: str | None
    path: str | None
    wildcard: bool
    name: str | None


@dataclass(frozen=True)
class Label:
    """A fully-parsed mlody label.

    None on any field means the corresponding component was absent from
    the original label string, not that it was empty.  The distinction
    matters: workspace=None means "current workspace", workspace="" is
    invalid and should never be produced by the parser.
    """

    workspace: str | None
    workspace_query: str | None
    entity: EntitySpec | None
    entity_query: str | None
    # None means no "'" separator was present; an empty tuple is not used.
    attribute_path: tuple[str, ...] | None
    attribute_query: str | None
