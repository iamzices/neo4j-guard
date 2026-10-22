"""Tests for `guard`, the hooks it puts on the driver, and `check`."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import neo4j
import pytest
from neo4j.graph import Graph, Node

from neo4j_guard import GuardError, _guard, check, guard

PERSON = Node(Graph(), "4:test:0", 0, ["Person"], {"ssn": "123"})

# The arguments after the query and parameters that the driver passes to `Result._run`.
RUN_ARGS = (None,) * 6


@pytest.fixture
def driver() -> Iterator[neo4j.Driver]:
    """Returns a driver. It never connects, because nothing is sent."""
    with neo4j.GraphDatabase.driver("neo4j://localhost:7687") as driver:
        yield driver


@pytest.fixture
def async_driver() -> Iterator[neo4j.AsyncDriver]:
    """Returns an async driver. It never connects, because nothing is sent."""
    driver = neo4j.AsyncGraphDatabase.driver("neo4j://localhost:7687")
    yield driver
    asyncio.run(driver.close())


def result(driver: Any, *values: Any) -> Any:
    """Returns a fake result of the driver, with one record per value already received."""
    records = deque(neo4j.Record({"n": value}) for value in values)
    return SimpleNamespace(
        _connection=SimpleNamespace(pool=driver._pool),
        _on_closed=AsyncMock() if isinstance(driver, neo4j.AsyncDriver) else MagicMock(),
        _record_buffer=records,
        _attached=False,
        _out_of_scope=False,
        _consumed=False,
        _exception=None,
    )


async def collect(records: Any) -> list[neo4j.Record]:
    """Returns the records of an async iterator."""
    return [record async for record in records]


def test_guard_returns_the_same_driver(driver: neo4j.Driver) -> None:
    assert guard(driver) is driver


def test_guard_rejects_a_guarded_driver(driver: neo4j.Driver) -> None:
    guard(driver)
    with pytest.raises(ValueError, match="already guarded"):
        guard(driver, "write")


def test_run_sends_allowed_query(driver: neo4j.Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_guard, "_run", run := MagicMock())
    res = result(guard(driver))
    neo4j.Result._run(res, "MATCH (n) RETURN n", {}, *RUN_ARGS)
    run.assert_called_once_with(res, "MATCH (n) RETURN n", {}, *RUN_ARGS)


def test_run_blocks_query_and_closes_result(driver: neo4j.Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_guard, "_run", run := MagicMock())
    res = result(guard(driver))
    with pytest.raises(GuardError, match="CREATE is not allowed in read mode"):
        neo4j.Result._run(res, neo4j.Query("CREATE (n)"), {}, *RUN_ARGS)
    run.assert_not_called()
    res._on_closed.assert_called_once_with()


def test_run_ignores_unguarded_driver(driver: neo4j.Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_guard, "_run", run := MagicMock())
    neo4j.Result._run(result(driver), "CREATE (n)", {}, *RUN_ARGS)
    run.assert_called_once()


def test_iter_returns_allowed_records(driver: neo4j.Driver) -> None:
    res = result(guard(driver, disallow=["Person.ssn"]), 1, 2)
    assert [record["n"] for record in neo4j.Result.__iter__(res)] == [1, 2]


def test_iter_ignores_unguarded_driver(driver: neo4j.Driver) -> None:
    assert [record["n"] for record in neo4j.Result.__iter__(result(driver, PERSON))] == [PERSON]


def test_iter_blocks_records(driver: neo4j.Driver) -> None:
    records = neo4j.Result.__iter__(result(guard(driver, disallow=["Person.ssn"]), 1, PERSON))
    assert next(records)["n"] == 1
    with pytest.raises(GuardError, match="Person with property ssn"):
        next(records)


def test_buffer_blocks_records_that_fetch_returns(driver: neo4j.Driver) -> None:
    res = result(guard(driver, disallow=["Person.ssn"]), 1, PERSON)
    neo4j.Result._buffer(res, 1)
    with pytest.raises(GuardError, match="Person with property ssn"):
        neo4j.Result._buffer(res, 2)


def test_async_run_checks_queries(async_driver: neo4j.AsyncDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_guard, "_async_run", run := AsyncMock())
    res = result(guard(async_driver))
    with pytest.raises(GuardError, match="CREATE is not allowed in read mode"):
        asyncio.run(neo4j.AsyncResult._run(res, "CREATE (n)", {}, *RUN_ARGS))
    run.assert_not_called()
    res._on_closed.assert_awaited_once_with()
    asyncio.run(neo4j.AsyncResult._run(res, "MATCH (n) RETURN n", {}, *RUN_ARGS))
    run.assert_awaited_once_with(res, "MATCH (n) RETURN n", {}, *RUN_ARGS)


def test_async_iter_checks_records(async_driver: neo4j.AsyncDriver) -> None:
    driver = guard(async_driver, disallow=["Person.ssn"])
    assert [record["n"] for record in asyncio.run(collect(neo4j.AsyncResult.__aiter__(result(driver, 1))))] == [1]
    with pytest.raises(GuardError, match="Person with property ssn"):
        asyncio.run(collect(neo4j.AsyncResult.__aiter__(result(driver, PERSON))))


def test_async_buffer_checks_records(async_driver: neo4j.AsyncDriver) -> None:
    res = result(guard(async_driver, disallow=["Person.ssn"]), PERSON)
    with pytest.raises(GuardError, match="Person with property ssn"):
        asyncio.run(neo4j.AsyncResult._buffer(res, 1))


def test_check_allows_query() -> None:
    check("MATCH (n:Person) RETURN n.name")


def test_check_blocks_query() -> None:
    with pytest.raises(GuardError, match="CREATE is not allowed in read mode"):
        check("CREATE (:Person)")


def test_check_rejects_invalid_cypher() -> None:
    with pytest.raises(GuardError, match="invalid Cypher"):
        check("MATCH (n RETURN n")


def test_check_uses_mode() -> None:
    check("CREATE (:Person)", "write")
    check("CREATE (:Person)", "readwrite")


def test_check_uses_allow() -> None:
    check("CALL apoc.meta.schema()", allow=["apoc.meta.schema"])
    with pytest.raises(GuardError, match="procedure apoc.meta.schema is not allowed"):
        check("CALL apoc.meta.schema()")


def test_check_uses_disallow() -> None:
    with pytest.raises(GuardError, match="label or relationship type Employee is not allowed"):
        check("MATCH (n:Employee) RETURN n.name", disallow=["Employee"])
    with pytest.raises(GuardError, match="property ssn is not allowed"):
        check("MATCH (n:Person) RETURN n.ssn", disallow=["Person.ssn"])


def test_check_disallow_takes_precedence_over_allow() -> None:
    with pytest.raises(GuardError, match="DELETE is not allowed in write mode"):
        check("MATCH (n) DELETE n", "write", allow=["DELETE"], disallow=["DELETE"])


def test_check_rejects_invalid_settings() -> None:
    with pytest.raises(ValueError, match="mode must be"):
        check("MATCH (n) RETURN n", "admin")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="lists of strings"):
        check("MATCH (n) RETURN n", allow="DELETE")
