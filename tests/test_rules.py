"""Tests for `Rules`."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from neo4j_guard._rules import READ_CLAUSES, WRITE_CLAUSES, ModeName, Rules


def test_read_mode_uses_read_defaults() -> None:
    assert Rules.build("read", [], []).allowed_clauses == READ_CLAUSES


def test_write_mode_uses_write_defaults() -> None:
    assert Rules.build("write", [], []).allowed_clauses == WRITE_CLAUSES


@pytest.mark.parametrize(("alias", "mode"), [("readonly", "read"), ("readwrite", "write")])
def test_mode_aliases_match_their_modes(alias: ModeName, mode: ModeName) -> None:
    assert Rules.build(alias, [], []) == Rules.build(mode, [], [])


def test_allow_adds_clause_ignoring_case_and_spacing() -> None:
    assert "LOAD CSV" in Rules.build("read", ["load  csv"], []).allowed_clauses


def test_disallow_removes_clause() -> None:
    assert "MERGE" not in Rules.build("write", [], ["merge"]).allowed_clauses


@pytest.mark.parametrize(("entry", "clause"), [("DETACH DELETE", "DELETE"), ("optional match", "MATCH")])
def test_clause_aliases_name_their_clause(entry: str, clause: str) -> None:
    assert clause not in Rules.build("write", [], [entry]).allowed_clauses


def test_disallow_wins_over_allow_for_clauses() -> None:
    assert "DELETE" not in Rules.build("write", ["DELETE"], ["DELETE"]).allowed_clauses


def test_builtin_procedures_and_functions_are_allowed() -> None:
    rules = Rules.build("read", [], [])
    assert rules.allows_procedure("db.labels")
    assert rules.allows_procedure("db.schema.visualization")
    assert rules.allows_function("toUpper")
    assert rules.allows_function("datetime.truncate")


def test_other_procedures_and_functions_are_blocked() -> None:
    rules = Rules.build("read", [], [])
    assert not rules.allows_procedure("apoc.load.json")
    assert not rules.allows_function("apoc.cypher.runFirstColumnSingle")


def test_allow_accepts_names_and_wildcards() -> None:
    assert Rules.build("read", ["apoc.load.json"], []).allows_procedure("apoc.load.json")
    assert Rules.build("read", ["apoc.meta.*"], []).allows_function("apoc.meta.type")


def test_names_match_case_insensitively() -> None:
    assert Rules.build("read", ["apoc.load.json"], []).allows_procedure("APOC.LOAD.JSON")
    assert not Rules.build("read", [], ["DB.LABELS"]).allows_procedure("db.labels")


def test_disallow_wins_over_allow_for_names() -> None:
    rules = Rules.build("read", ["apoc.*"], ["apoc.load.*"])
    assert rules.allows_procedure("apoc.meta.data")
    assert not rules.allows_procedure("apoc.load.json")


def test_plain_name_blocks_label() -> None:
    assert Rules.build("read", [], ["Employee"]).blocked_labels == {"Employee"}


def test_dotted_name_blocks_property() -> None:
    rules = Rules.build("read", [], ["Employee.salary"])
    assert rules.blocked_properties == {("Employee", "salary")}
    assert rules.blocked_keys == {"salary"}


def test_entries_ignore_surrounding_spaces() -> None:
    rules = Rules.build("read", [" apoc.load.json "], [" Employee ", "Person.ssn "])
    assert rules.allows_procedure("apoc.load.json")
    assert rules.blocked_labels == {"Employee"}
    assert rules.blocked_properties == {("Person", "ssn")}


@pytest.mark.parametrize("name", ["db.labels", "db.nameFromElementId", "apoc.when"])
def test_procedure_and_function_names_are_not_properties(name: str) -> None:
    assert not Rules.build("read", [], [name]).blocked_properties


def test_wildcards_block_neither_labels_nor_properties() -> None:
    rules = Rules.build("read", [], ["apoc.*", "Emp*"])
    assert not rules.blocked_labels
    assert not rules.blocked_properties


def test_properties_function_is_blocked_while_properties_are_blocked() -> None:
    assert Rules.build("read", [], []).allows_function("properties")
    assert not Rules.build("read", [], ["Employee.salary"]).allows_function("properties")


@pytest.mark.parametrize("mode", ["admin", "READ", ""])
def test_rejects_unknown_mode(mode: str) -> None:
    with pytest.raises(ValueError, match="mode must be"):
        Rules.build(mode, [], [])  # type: ignore[arg-type]


@pytest.mark.parametrize(("allow", "disallow"), [("apoc.load.json", []), ([], "Employee")])
def test_rejects_single_string(allow: Iterable[str], disallow: Iterable[str]) -> None:
    with pytest.raises(TypeError, match="must be lists of strings"):
        Rules.build("read", allow, disallow)


@pytest.mark.parametrize("entry", ["", "   ", None, 1])
def test_rejects_invalid_entry(entry: object) -> None:
    with pytest.raises(ValueError, match="must be non-empty strings"):
        Rules.build("read", [entry], [])  # type: ignore[list-item]
