# neo4j-guard

[![PyPI version](https://img.shields.io/pypi/v/neo4j-guard)](https://pypi.org/project/neo4j-guard/)
[![PyPI downloads](https://img.shields.io/pepy/dt/neo4j-guard)](https://pepy.tech/projects/neo4j-guard)
[![CI](https://github.com/iamzices/neo4j-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/iamzices/neo4j-guard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/iamzices/neo4j-guard)](LICENSE)

neo4j-guard is a Python library that blocks dangerous Cypher queries before execution. It wraps the official `neo4j` driver and parses every query with [Neo4j](https://neo4j.com/)'s own Cypher 25 grammar.

## Features

- Configurable read and write modes
- Dangerous queries blocked by default
- Error messages an agent can act on

## Requirements

- Python 3.10+
- `neo4j` 5.8+

## Installation

```sh
pip install neo4j-guard
```

## Use with Neo4j driver

```python
from neo4j import GraphDatabase
from neo4j_guard import GuardError, guard

driver = guard(GraphDatabase.driver(URL, auth=AUTH))
try:
    driver.execute_query("CREATE (:Person {name: 'Ada'})")
except GuardError as error:
    print(error)  # CREATE is not allowed in read mode
```

## Use with LangChain

```python
from langchain_neo4j import Neo4jGraph
from neo4j_guard import guard

graph = Neo4jGraph(url=URL, username=USERNAME, password=PASSWORD)
guard(graph._driver)
```

## Use with Neo4j MCP server

```python
import asyncio
from mcp_neo4j_cypher.server import create_mcp_server
from neo4j import AsyncGraphDatabase
from neo4j_guard import guard


async def main() -> None:
    driver = guard(
        AsyncGraphDatabase.driver(URL, auth=AUTH),
        allow=["apoc.meta.schema"],
    )
    await create_mcp_server(driver, read_only=True).run_stdio_async()


asyncio.run(main())
```

## Configuration

neo4j-guard is configurable. By default, a guarded driver can only run a small set of read-only Cypher.

```python
guard(
    driver,
    mode="write",  # "read" (default) or "write"
    allow=["DELETE", "apoc.load.json"],
    disallow=["apoc.meta.*", "Employee", "Person.ssn"],
)
```