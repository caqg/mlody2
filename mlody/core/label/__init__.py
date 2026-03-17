"""mlody.core.label — label parsing types and utilities."""

from mlody.core.label.errors import AttributeParseError as AttributeParseError
from mlody.core.label.errors import EntityParseError as EntityParseError
from mlody.core.label.errors import LabelParseError as LabelParseError
from mlody.core.label.errors import WorkspaceParseError as WorkspaceParseError
from mlody.core.label.label import EntitySpec as EntitySpec
from mlody.core.label.label import Label as Label
from mlody.core.label.parser import parse_label as parse_label
