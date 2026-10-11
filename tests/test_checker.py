"""Tests for `check_query` and `check_record`."""

from __future__ import annotations

from typing import Any

import neo4j
import pytest
from neo4j.graph import Graph, Node, Path

from neo4j_guard import GuardError
from neo4j_guard._checker import CLAUSE_RULES, check_query, check_record
from neo4j_guard._rules import CLAUSES, Rules

# Rules shared by the tests below.
READ = Rules.build("read", [], [])
WRITE = Rules.build("write", [], [])
SENSITIVE = Rules.build("read", [], ["Employee", "Person.ssn"])


def node(*labels: str, **properties: Any) -> Node:
    """Returns a node like the driver returns from a query."""
    return Node(Graph(), "4:test:0", 0, labels, properties)


def test_every_clause_rule_maps_to_a_known_clause() -> None:
    assert set(CLAUSE_RULES.values()) == CLAUSES


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:Person {name: $name})-[:KNOWS*1..3]->(m) WHERE m.age > 30 RETURN m.name LIMIT 10",
        "OPTIONAL MATCH p = shortestPath((a:Person)-[*]-(b:Person)) RETURN p",
        "MATCH (a) CALL (a) { MATCH (a)--(c) RETURN count(c) AS c } RETURN a, c",
        "MATCH (a) CALL { WITH a MATCH (a)--(c) RETURN count(c) AS c } RETURN a, c",
        "MATCH (n) WHERE EXISTS { (n)-->() } RETURN n { .name }, COUNT { (n)--() } AS degree",
        "CALL db.labels() YIELD label RETURN label",
        "UNWIND [1, 2] AS x WITH x WHERE x > 1 RETURN x UNION MATCH (n) RETURN n.x AS x",
        "MATCH (n) RETURN datetime.truncate('day', datetime()), toUpper(n.name), [1, 2][0]",
    ],
)
def test_query_allows_read_queries(query: str) -> None:
    check_query(query, READ)


def test_query_allows_write_clauses_in_write_mode() -> None:
    check_query("MERGE (a:Person {name: $name}) SET a.seen = true REMOVE a.old CREATE (a)-[:KNOWS]->()", WRITE)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("CREATE (n)", "CREATE is not allowed in read mode"),
        ("MATCH (n) SET n.x = 1", "SET is not allowed in read mode"),
        ("LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row", "LOAD CSV is not allowed in read mode"),
        ("USE system MATCH (n) RETURN n", "USE is not allowed in read mode"),
    ],
)
def test_query_blocks_clauses_outside_mode(query: str, message: str) -> None:
    with pytest.raises(GuardError, match=message):
        check_query(query, READ)


def test_query_blocks_delete_in_write_mode() -> None:
    with pytest.raises(GuardError, match="DELETE is not allowed in write mode"):
        check_query("MATCH (n) DETACH DELETE n", WRITE)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) CALL (n) { CREATE (m) } RETURN n",
        "MATCH (n) RETURN COUNT { MATCH (n) CREATE () RETURN 1 }",
        "WHEN true THEN MATCH (n) RETURN n ELSE CREATE (n) RETURN n",
        "MATCH (n) RETURN n; CREATE (m)",
    ],
)
def test_query_blocks_nested_clauses(query: str) -> None:
    with pytest.raises(GuardError, match="CREATE is not allowed"):
        check_query(query, READ)


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("CALL apoc.load.json('http://169.254.169.254/') YIELD value RETURN value", "apoc.load.json"),
        ("CALL `apoc`.`load`.json('x') YIELD value RETURN value", "apoc.load.json"),
        ("CALL apoc . load . json('x') YIELD value RETURN value", "apoc.load.json"),
        ("CALL dbms.killQuery('1')", "dbms.killQuery"),
    ],
)
def test_query_blocks_procedures(query: str, name: str) -> None:
    with pytest.raises(GuardError, match=f"procedure {name} is not allowed"):
        check_query(query, READ)


def test_query_blocks_functions() -> None:
    with pytest.raises(GuardError, match=r"function apoc.cypher.runFirstColumnSingle\(\) is not allowed"):
        check_query("RETURN apoc.cypher.runFirstColumnSingle('CREATE (n) RETURN n', {})", READ)


@pytest.mark.parametrize(
    "query",
    ["SHOW USERS", "CREATE INDEX FOR (n:Person) ON (n.name)", "TERMINATE TRANSACTIONS 'neo4j-transaction-1'"],
)
def test_query_blocks_commands(query: str) -> None:
    with pytest.raises(GuardError, match="only regular query clauses are"):
        check_query(query, WRITE)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("MATCH (e:Employee) RETURN e.name", "label or relationship type Employee is not allowed"),
        ("MATCH (e) WHERE e:Person|Employee RETURN e.name", "label or relationship type Employee"),
        ("MATCH (e:`Employee`) RETURN e.name", "label or relationship type Employee"),
        ("MATCH (p:$($label)) RETURN p.name", "dynamic labels are not allowed"),
        ("MATCH (p:Person) RETURN p.ssn", "property ssn is not allowed"),
        ("MATCH (p) WHERE p.`ssn` STARTS WITH '1' RETURN p.name", "property ssn"),
        ("MATCH (p:Person) RETURN p {.name, .ssn}", "property ssn"),
        ("MATCH (p) RETURN p {.*}", r"map projection \.\* is not allowed"),
        ("MATCH (p) RETURN {k: v IN p | k: v}", "map comprehension over a map is not allowed"),
        ("MATCH (p) RETURN properties(p)", r"function properties\(\) is not allowed"),
        ("MATCH (p) RETURN p[$key]", "dynamic property access"),
    ],
)
def test_query_blocks_disallowed_labels_and_properties(query: str, message: str) -> None:
    with pytest.raises(GuardError, match=message):
        check_query(query, SENSITIVE)


def test_query_allows_other_labels_and_properties() -> None:
    check_query("MATCH (p:Person)-[:KNOWS]->(e:employee) RETURN p.name, e, [1, 2][0]", SENSITIVE)


@pytest.mark.parametrize("query", ["MATCH (n RETURN n", "EXPLAINMATCH (n) RETURN n", "MATCH (n) RETURN 'x"])
def test_query_blocks_invalid_cypher(query: str) -> None:
    with pytest.raises(GuardError, match="invalid Cypher at line 1"):
        check_query(query, READ)


@pytest.mark.parametrize("prefix", ["EXPLAIN", "explain", " PROFILE"])
def test_query_checks_the_query_after_explain_and_profile(prefix: str) -> None:
    check_query(f"{prefix} MATCH (n) RETURN n", READ)
    with pytest.raises(GuardError, match="CREATE is not allowed"):
        check_query(f"{prefix} CREATE (n)", READ)


def test_query_blocks_deep_nesting() -> None:
    with pytest.raises(GuardError, match="nested too deeply"):
        check_query("RETURN " + "(" * 5000 + "1" + ")" * 5000, READ)


def test_record_allows_anything_without_label_or_property_rules() -> None:
    check_record(neo4j.Record({"e": node("Employee", salary=1)}), READ)


def test_record_blocks_node_with_disallowed_label() -> None:
    with pytest.raises(GuardError, match="contains Employee"):
        check_record(neo4j.Record({"n": node("Employee")}), SENSITIVE)


def test_record_blocks_relationship_with_disallowed_type() -> None:
    graph = Graph()
    knows = graph.relationship_type("KNOWS")(graph, "5:test:0", 0, {})
    with pytest.raises(GuardError, match="contains KNOWS"):
        check_record(neo4j.Record({"r": knows}), Rules.build("read", [], ["KNOWS"]))


def test_record_blocks_node_with_disallowed_property() -> None:
    with pytest.raises(GuardError, match="Person with property ssn"):
        check_record(neo4j.Record({"p": node("Person", name="Ada", ssn="123")}), SENSITIVE)


def test_record_allows_same_property_on_other_label() -> None:
    check_record(neo4j.Record({"c": node("Company", ssn="123")}), SENSITIVE)


def test_record_allows_values_without_blocked_data() -> None:
    record = neo4j.Record({"ssn": "123", "people": [{"name": "Ada"}], "path": Path(node("Company", name="Acme"))})
    check_record(record, SENSITIVE)


def test_record_blocks_nodes_nested_in_lists_and_maps() -> None:
    with pytest.raises(GuardError, match="ssn"):
        check_record(neo4j.Record({"x": [{"person": node("Person", ssn="123")}]}), SENSITIVE)


def test_record_blocks_nodes_in_paths() -> None:
    with pytest.raises(GuardError, match="ssn"):
        check_record(neo4j.Record({"p": Path(node("Person", ssn="123"))}), SENSITIVE)
