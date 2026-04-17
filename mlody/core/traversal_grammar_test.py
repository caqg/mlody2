"""Tests for mlody.core.traversal_grammar — PathSegment AST and EBNF constant.

Each test traces back to a named scenario in the spec:
  openspec/changes/mlody-label-traversal/specs/traversal-grammar/spec.md
"""

from __future__ import annotations

import pytest

from mlody.core.traversal_grammar import (
    TRAVERSAL_GRAMMAR_EBNF,
    FieldSegment,
    IndexSegment,
    KeySegment,
    PathExpression,
    PathSegment,
    RecursiveDescentSegment,
    TraversalParseError,
    WildcardSegment,
)


# ---------------------------------------------------------------------------
# TraversalParseError
# Scenario: "TraversalParseError carries structured fields"
# ---------------------------------------------------------------------------


class TestTraversalParseError:
    """Requirement: TraversalParseError exception type."""

    def test_structured_fields_are_accessible(self) -> None:
        """Scenario: TraversalParseError carries structured fields."""
        exc = TraversalParseError("unexpected token", "[bad]", 1)
        assert exc.input_expr == "[bad]"
        assert exc.position == 1
        assert "[bad]" in str(exc)

    def test_message_is_in_str_repr(self) -> None:
        """The message is included in the string representation."""
        exc = TraversalParseError("bad token here", "xyz", 2)
        assert "bad token here" in str(exc)
        assert "xyz" in str(exc)
        assert "2" in str(exc)

    def test_is_exception_subclass(self) -> None:
        """TraversalParseError must be an Exception subclass."""
        exc = TraversalParseError("msg", "expr", 0)
        assert isinstance(exc, Exception)


# ---------------------------------------------------------------------------
# PathSegment str serialisation
# ---------------------------------------------------------------------------


class TestFieldSegment:
    """Requirement: FieldSegment str serialisation."""

    def test_str_returns_dot_name(self) -> None:
        """Scenario: FieldSegment str serialisation."""
        assert str(FieldSegment(name="loss")) == ".loss"

    def test_is_path_segment(self) -> None:
        """FieldSegment is a PathSegment subclass."""
        assert isinstance(FieldSegment(name="x"), PathSegment)

    def test_frozen_and_hashable(self) -> None:
        """Scenario: PathSegment subclasses are frozen and hashable."""
        s = FieldSegment(name="a")
        result = {s}
        assert s in result

    def test_frozen_raises_on_mutation(self) -> None:
        """Frozen dataclass should reject field assignment."""
        s = FieldSegment(name="a")
        with pytest.raises((AttributeError, TypeError)):
            s.name = "b"  # type: ignore[misc]


class TestIndexSegment:
    """Requirement: IndexSegment str serialisation."""

    def test_str_positive_index(self) -> None:
        """Scenario: IndexSegment str serialisation."""
        assert str(IndexSegment(index=2)) == "[2]"

    def test_str_negative_index(self) -> None:
        """Scenario: IndexSegment negative index str serialisation."""
        assert str(IndexSegment(index=-1)) == "[-1]"

    def test_str_zero(self) -> None:
        assert str(IndexSegment(index=0)) == "[0]"

    def test_is_path_segment(self) -> None:
        assert isinstance(IndexSegment(index=0), PathSegment)

    def test_frozen_and_hashable(self) -> None:
        s = IndexSegment(index=5)
        assert s in {s}


class TestKeySegment:
    """Requirement: KeySegment str serialisation."""

    def test_str_returns_bracket_quoted_key(self) -> None:
        """Scenario: KeySegment str serialisation."""
        assert str(KeySegment(key="f1_score")) == '["f1_score"]'

    def test_is_path_segment(self) -> None:
        assert isinstance(KeySegment(key="k"), PathSegment)

    def test_frozen_and_hashable(self) -> None:
        s = KeySegment(key="k")
        assert s in {s}


class TestWildcardSegment:
    """Requirement: WildcardSegment str serialisation."""

    def test_str_returns_bracket_star(self) -> None:
        """Scenario: WildcardSegment str serialisation."""
        assert str(WildcardSegment()) == "[*]"

    def test_is_path_segment(self) -> None:
        assert isinstance(WildcardSegment(), PathSegment)

    def test_frozen_and_hashable(self) -> None:
        s = WildcardSegment()
        assert s in {s}

    def test_equality(self) -> None:
        """Two WildcardSegment instances with no fields are equal."""
        assert WildcardSegment() == WildcardSegment()


class TestRecursiveDescentSegment:
    """Requirement: RecursiveDescentSegment str serialisation."""

    def test_str_returns_double_dot(self) -> None:
        """Scenario: RecursiveDescentSegment str serialisation."""
        assert str(RecursiveDescentSegment()) == ".."

    def test_is_path_segment(self) -> None:
        assert isinstance(RecursiveDescentSegment(), PathSegment)

    def test_frozen_and_hashable(self) -> None:
        s = RecursiveDescentSegment()
        assert s in {s}

    def test_equality(self) -> None:
        assert RecursiveDescentSegment() == RecursiveDescentSegment()


# ---------------------------------------------------------------------------
# PathExpression
# Scenario: "PathExpression str of empty expression"
# Scenario: "PathExpression str of mixed segments"
# Scenario: "PathExpression len and iteration"
# ---------------------------------------------------------------------------


class TestPathExpression:
    """Requirement: PathExpression as ordered segment sequence."""

    def test_str_empty_expression(self) -> None:
        """Scenario: PathExpression str of empty expression."""
        assert str(PathExpression(segments=())) == ""

    def test_str_mixed_segments(self) -> None:
        """Scenario: PathExpression str of mixed segments."""
        expr = PathExpression(
            segments=(FieldSegment("config"), IndexSegment(0), FieldSegment("lr"))
        )
        assert str(expr) == ".config[0].lr"

    def test_len(self) -> None:
        """Scenario: PathExpression len and iteration."""
        segments = (FieldSegment("a"), IndexSegment(1), WildcardSegment())
        expr = PathExpression(segments=segments)
        assert len(expr) == 3

    def test_iteration_yields_segments_in_order(self) -> None:
        """Scenario: PathExpression len and iteration — iteration part."""
        segments = (FieldSegment("a"), IndexSegment(1), WildcardSegment())
        expr = PathExpression(segments=segments)
        assert list(expr) == list(segments)

    def test_getitem_integer(self) -> None:
        """__getitem__ supports integer indexing."""
        expr = PathExpression(
            segments=(FieldSegment("a"), IndexSegment(2), KeySegment("k"))
        )
        assert expr[0] == FieldSegment("a")
        assert expr[1] == IndexSegment(2)
        assert expr[-1] == KeySegment("k")

    def test_getitem_slice(self) -> None:
        """__getitem__ supports slice access."""
        expr = PathExpression(
            segments=(FieldSegment("a"), IndexSegment(2), KeySegment("k"))
        )
        sliced = expr[0:2]
        assert sliced == (FieldSegment("a"), IndexSegment(2))

    def test_frozen_and_hashable(self) -> None:
        """PathExpression is frozen and hashable (usable as dict key)."""
        expr = PathExpression(segments=(FieldSegment("x"),))
        d = {expr: "hello"}
        assert d[expr] == "hello"

    def test_str_single_field(self) -> None:
        expr = PathExpression(segments=(FieldSegment("loss"),))
        assert str(expr) == ".loss"

    def test_str_wildcard_then_field(self) -> None:
        expr = PathExpression(segments=(WildcardSegment(), FieldSegment("loss")))
        assert str(expr) == "[*].loss"

    def test_str_recursive_descent_then_field(self) -> None:
        expr = PathExpression(
            segments=(RecursiveDescentSegment(), FieldSegment("loss"))
        )
        assert str(expr) == "...loss"

    def test_str_key_segment(self) -> None:
        expr = PathExpression(segments=(KeySegment("f1"),))
        assert str(expr) == '["f1"]'

    def test_equality(self) -> None:
        """Two PathExpressions with identical segments are equal."""
        e1 = PathExpression(segments=(FieldSegment("a"), IndexSegment(0)))
        e2 = PathExpression(segments=(FieldSegment("a"), IndexSegment(0)))
        assert e1 == e2


# ---------------------------------------------------------------------------
# EBNF constant
# Scenario: "EBNF constant is importable"
# ---------------------------------------------------------------------------


class TestEBNFConstant:
    """Requirement: EBNF grammar specification."""

    def test_ebnf_is_non_empty_string(self) -> None:
        """Scenario: EBNF constant is importable and non-empty."""
        assert isinstance(TRAVERSAL_GRAMMAR_EBNF, str)
        assert len(TRAVERSAL_GRAMMAR_EBNF) > 0

    def test_ebnf_contains_key_grammar_symbols(self) -> None:
        """The EBNF string references the main non-terminal names."""
        assert "traversal_expr" in TRAVERSAL_GRAMMAR_EBNF
        assert "field_seg" in TRAVERSAL_GRAMMAR_EBNF
        assert "bracket_seg" in TRAVERSAL_GRAMMAR_EBNF
        assert "recursive_seg" in TRAVERSAL_GRAMMAR_EBNF
