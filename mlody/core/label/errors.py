"""Typed exception hierarchy for label parsing failures."""

from __future__ import annotations


class LabelParseError(ValueError):
    """A label string has invalid syntax.

    Inherits from ``ValueError`` because label parsing is an invalid-argument
    error — the caller passed a malformed string.  Callers doing
    ``except ValueError`` will catch this without modification.

    NOTE: This class previously lived in ``mlody.resolver.errors`` as a
    subclass of ``WorkspaceResolutionError``.  The move to ``mlody.core`` is
    intentional: label parsing is used by the resolver, LSP, and CLI — none of
    the latter two should import ``mlody.resolver``.  The re-export in
    ``mlody/resolver/errors.py`` preserves the old import path.  As a
    consequence, ``issubclass(LabelParseError, WorkspaceResolutionError)`` is
    now ``False``; the one test assertion that relied on this relationship needs
    updating (see ``mlody/resolver/errors_test.py::TestWorkspaceResolutionErrorHierarchy``).
    """

    label: str
    reason: str

    def __init__(self, label: str, reason: str) -> None:
        self.label = label
        self.reason = reason
        super().__init__(f"Cannot parse label {label!r}: {reason}")


class WorkspaceParseError(LabelParseError):
    """The workspace section (before ``|``) of a label has invalid syntax."""

    workspace_fragment: str

    def __init__(
        self, label: str, reason: str, workspace_fragment: str
    ) -> None:
        self.workspace_fragment = workspace_fragment
        super().__init__(label, reason)


class EntityParseError(LabelParseError):
    """The entity section (after ``|``) of a label has invalid syntax."""

    entity_fragment: str

    def __init__(self, label: str, reason: str, entity_fragment: str) -> None:
        self.entity_fragment = entity_fragment
        super().__init__(label, reason)


class AttributeParseError(LabelParseError):
    """The attribute section (after ``:``) of a label has invalid syntax."""

    attribute_fragment: str

    def __init__(
        self, label: str, reason: str, attribute_fragment: str
    ) -> None:
        self.attribute_fragment = attribute_fragment
        super().__init__(label, reason)
