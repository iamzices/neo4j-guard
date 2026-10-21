"""`guard()`, and the hooks that check every query a guarded driver sends and every record it returns."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from itertools import islice
from typing import Any, TypeVar
from weakref import WeakKeyDictionary

import neo4j

from ._checker import GuardError, check_query, check_record
from ._rules import ModeName, Rules

Driver = TypeVar("Driver", neo4j.Driver, neo4j.AsyncDriver)

# The rules of each guarded driver, by its connection pool. Every connection points back to its pool.
_GUARDED: WeakKeyDictionary[Any, Rules] = WeakKeyDictionary()

# The driver methods the hooks replace. Every query and every record of every driver goes through them.
_run: Callable[..., None] = neo4j.Result._run
_iter = neo4j.Result.__iter__
_buffer: Callable[..., None] = neo4j.Result._buffer
_async_run: Callable[..., Awaitable[None]] = neo4j.AsyncResult._run
_async_iter = neo4j.AsyncResult.__aiter__
_async_buffer: Callable[..., Awaitable[None]] = neo4j.AsyncResult._buffer


def _rules_of(result: Any) -> Rules | None:
    """Returns the rules of the driver that created a result, or None if the driver is not guarded."""
    return _GUARDED.get(result._connection.pool)


def _guarded_run(self: Any, query: Any, *args: Any) -> None:
    """Checks a query before the driver sends it."""
    rules = _rules_of(self)
    if rules is not None:
        try:
            check_query(str(query), rules)
        except GuardError:
            self._on_closed()  # Lets the session release its connection, as for a failed query.
            raise
    _run(self, query, *args)


def _guarded_iter(self: Any) -> Iterator[neo4j.Record]:
    """Checks each record before the driver returns it."""
    rules = _rules_of(self)
    for record in _iter(self):
        if rules is not None:
            check_record(record, rules)
        yield record


def _guarded_buffer(self: Any, n: int | None = None) -> None:
    """Checks the buffered records that `fetch()`, `peek()` and `single()` return."""
    _buffer(self, n)
    rules = _rules_of(self)
    if rules is not None:
        for record in islice(self._record_buffer, n):
            check_record(record, rules)


async def _guarded_async_run(self: Any, query: Any, *args: Any) -> None:
    """Checks a query before the async driver sends it."""
    rules = _rules_of(self)
    if rules is not None:
        try:
            check_query(str(query), rules)
        except GuardError:
            await self._on_closed()  # Lets the session release its connection, as for a failed query.
            raise
    await _async_run(self, query, *args)


async def _guarded_async_iter(self: Any) -> AsyncIterator[neo4j.Record]:
    """Checks each record before the async driver returns it."""
    rules = _rules_of(self)
    async for record in _async_iter(self):
        if rules is not None:
            check_record(record, rules)
        yield record


async def _guarded_async_buffer(self: Any, n: int | None = None) -> None:
    """Checks the buffered records that the async `fetch()`, `peek()` and `single()` return."""
    await _async_buffer(self, n)
    rules = _rules_of(self)
    if rules is not None:
        for record in islice(self._record_buffer, n):
            check_record(record, rules)


neo4j.Result._run = _guarded_run  # type: ignore[method-assign]
neo4j.Result.__iter__ = _guarded_iter  # type: ignore[method-assign]
neo4j.Result._buffer = _guarded_buffer  # type: ignore[method-assign]
neo4j.AsyncResult._run = _guarded_async_run  # type: ignore[method-assign]
neo4j.AsyncResult.__aiter__ = _guarded_async_iter  # type: ignore[method-assign]
neo4j.AsyncResult._buffer = _guarded_async_buffer  # type: ignore[method-assign]


def guard(
    driver: Driver,
    mode: ModeName = "read",
    *,
    allow: Iterable[str] | None = None,
    disallow: Iterable[str] | None = None,
) -> Driver:
    """Guards a Neo4j driver so it only runs allowed queries and only returns allowed results.

    Every query the driver sends is checked first, through `execute_query`, sessions and
    transactions. A blocked query raises `GuardError` and is never sent. A blocked result
    raises `GuardError` while it is read. In a transaction, as with `execute_query`, this
    rolls the transaction back. An auto-commit query from `session.run` has already run.

    Entries in `allow` and `disallow` can be a clause ("DELETE") or a procedure or
    function ("apoc.load.json", "apoc.meta.*"). `disallow` also accepts a label or
    relationship type ("Employee") and a property ("Person.ssn").

    Args:
        driver: The Neo4j driver to guard, sync or async.
        mode: "read" (the default) or "write", which also allows creating and updating data.
            "readonly" and "readwrite" are aliases for "read" and "write".
        allow: Entries to allow on top of the mode's defaults.
        disallow: Entries to block. They take precedence over `allow`.

    Returns:
        The same driver, now guarded.

    Raises:
        ValueError: If `mode` or an entry is invalid, or the driver is already guarded.
        TypeError: If `allow` or `disallow` is a single string instead of a list.
    """
    rules = Rules.build(mode, allow or (), disallow or ())
    if driver._pool in _GUARDED:
        raise ValueError("the driver is already guarded; create a separate driver for each mode")
    _GUARDED[driver._pool] = rules
    return driver
