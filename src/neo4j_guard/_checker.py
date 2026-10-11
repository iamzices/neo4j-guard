"""Query and result checks against `Rules`, using a parser generated from Neo4j's Cypher grammar."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import neo4j
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener
from neo4j.graph import Node, Path, Relationship

from ._parser.Cypher25Lexer import Cypher25Lexer
from ._parser.Cypher25Parser import Cypher25Parser as P
from ._rules import Rules

# Clause name of each parser rule. A clause missing here is rejected as unknown.
CLAUSE_RULES = {
    P.CallClauseContext: "CALL",
    P.CreateClauseContext: "CREATE",
    P.DeleteClauseContext: "DELETE",
    P.FilterClauseContext: "FILTER",
    P.FinishClauseContext: "FINISH",
    P.ForListClauseContext: "FOR",
    P.ForeachClauseContext: "FOREACH",
    P.InsertClauseContext: "INSERT",
    P.LetClauseContext: "LET",
    P.LoadCSVClauseContext: "LOAD CSV",
    P.MatchClauseContext: "MATCH",
    P.MergeClauseContext: "MERGE",
    P.OrderBySkipLimitClauseContext: "ORDER BY",
    P.RemoveClauseContext: "REMOVE",
    P.ReturnClauseContext: "RETURN",
    P.SetClauseContext: "SET",
    P.SubqueryClauseContext: "CALL",
    P.UnwindClauseContext: "UNWIND",
    P.UseClauseContext: "USE",
    P.WithClauseContext: "WITH",
}

# Parser rules whose names are labels or relationship types.
_LABEL_RULES = (
    P.InsertNodeLabelExpressionContext,
    P.InsertRelationshipLabelExpressionContext,
    P.LabelNameContext,
    P.LabelOrRelTypeContext,
    P.LabelTypeContext,
    P.NodeLabelsIsContext,
    P.RelTypeContext,
)

# Parser rules for labels computed at runtime, like `n:$($label)`.
_DYNAMIC_LABEL_RULES = (P.DynamicAnyAllExpressionContext, P.DynamicExpressionContext)


class GuardError(Exception):
    """The error raised when a query or its result is blocked.

    The message describes what was blocked, so it can be returned to the agent.
    """


class _RaiseOnSyntaxError(ErrorListener):  # type: ignore[misc]
    """An ANTLR error listener that raises `GuardError` on syntax errors."""

    def syntaxError(self, recognizer: Any, symbol: Any, line: int, column: int, msg: str, e: Any) -> None:
        """Raises a `GuardError` with the position and message of the syntax error."""
        raise GuardError(f"invalid Cypher at line {line}, column {column}: {msg}")


def check_query(query: str, rules: Rules) -> None:
    """Checks a query against the rules without running it.

    Args:
        query: The Cypher query.
        rules: The rules to check against.

    Raises:
        GuardError: If the query is invalid or not allowed.
    """
    # The grammar has no EXPLAIN or PROFILE, so the query after them is checked instead.
    query = re.sub(r"^\s*(EXPLAIN|PROFILE)\b", "", query, flags=re.IGNORECASE)
    lexer = Cypher25Lexer(InputStream(query))
    parser = P(CommonTokenStream(lexer))
    for recognizer in (lexer, parser):
        recognizer.removeErrorListeners()
        recognizer.addErrorListener(_RaiseOnSyntaxError())
    try:
        tree = parser.statements()
    except RecursionError:
        raise GuardError("the query is nested too deeply") from None
    for node in _walk(tree):
        problem = _problem(node, rules)
        if problem is not None:
            raise GuardError(problem)


def check_record(record: neo4j.Record, rules: Rules) -> None:
    """Checks a returned record for blocked labels and properties.

    Args:
        record: The record the query returned.
        rules: The rules to check against.

    Raises:
        GuardError: If a returned node, relationship or path is blocked.
    """
    if rules.blocked_labels or rules.blocked_properties:
        for value in record.values():
            _check_value(value, rules)


def _problem(node: Any, rules: Rules) -> str | None:
    """Returns why a syntax tree node is not allowed, or None if it is."""
    kind = type(node)
    # Rejects admin, SHOW and TERMINATE commands, and any clause this version doesn't know.
    if kind is P.CommandContext or (kind is P.ClauseContext and type(node.getChild(0)) not in CLAUSE_RULES):
        return f"{_text(node)!r} is not allowed; only regular query clauses are"
    if kind in CLAUSE_RULES:
        clause = CLAUSE_RULES[kind]
        return None if clause in rules.allowed_clauses else f"{clause} is not allowed in {rules.mode} mode"
    if kind is P.ProcedureNameContext:
        name = _qualified_name(node)
        return None if rules.allows_procedure(name) else f"procedure {name} is not allowed"
    if kind is P.FunctionNameContext:
        name = _qualified_name(node)
        return None if rules.allows_function(name) else f"function {name}() is not allowed"
    if kind in _LABEL_RULES:
        for child in node.getChildren():
            if isinstance(child, P.SymbolicNameStringContext) and _name(child) in rules.blocked_labels:
                return f"label or relationship type {_name(child)} is not allowed"
        return None
    if kind in _DYNAMIC_LABEL_RULES and rules.blocked_labels:
        return "dynamic labels are not allowed while labels are blocked; name the labels instead"
    if kind is P.PropertyKeyNameContext and _name(node) in rules.blocked_keys:
        return f"property {_name(node)} is not allowed"
    if kind is P.IndexPostfixContext and rules.blocked_keys and not node.expression().getText().lstrip("-").isdigit():
        return f"dynamic property access {_text(node)} is not allowed while properties are blocked; use .key"
    if kind is P.MapProjectionElementContext and rules.blocked_keys and node.TIMES():
        return "map projection .* is not allowed while properties are blocked; list the keys you need"
    if kind is P.MapComprehensionForMapContext and rules.blocked_keys:
        return "map comprehension over a map is not allowed while properties are blocked; list the keys you need"
    return None


def _check_value(value: object, rules: Rules) -> None:
    """Raises a `GuardError` if a returned value contains a blocked label or property."""
    if isinstance(value, Path):
        for item in (*value.nodes, *value.relationships):
            _check_value(item, rules)
    elif isinstance(value, (Node, Relationship)):
        names = value.labels if isinstance(value, Node) else {value.type}
        for name in names:
            if name in rules.blocked_labels:
                raise GuardError(f"the result contains {name}, which is not allowed")
            for key in value:
                if (name, key) in rules.blocked_properties:
                    raise GuardError(
                        f"the result contains {name} with property {key}, which is not allowed;"
                        " return the properties you need (n.name) instead of whole nodes or relationships"
                    )
    elif isinstance(value, list):
        for item in value:
            _check_value(item, rules)
    elif isinstance(value, dict):
        for item in value.values():
            _check_value(item, rules)


def _walk(root: Any) -> Iterator[Any]:
    """Yields every node of a syntax tree."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(getattr(node, "children", None) or ())


def _name(ctx: Any) -> str:
    """Returns a symbolic name without its backtick escaping."""
    text = str(ctx.getText())
    return text[1:-1].replace("``", "`") if text.startswith("`") else text


def _qualified_name(ctx: Any) -> str:
    """Returns the full dotted name of a procedure or function."""
    return ".".join(map(_name, [*ctx.namespace().symbolicNameString(), ctx.symbolicNameString()]))


def _text(ctx: Any) -> str:
    """Returns the original query text of a syntax tree node."""
    return str(ctx.start.getInputStream().getText(ctx.start.start, ctx.stop.stop))
