# neo4j-guard

A guardrail for LLM-generated Cypher, built for Neo4j.

By default, only read queries are allowed. Writes, `LOAD CSV` and APOC procedures are blocked unless explicitly allowed.

## Requirements

- Python 3.10+
- `neo4j` 5.8+

## Installation

```sh
pip install neo4j-guard
```

## Usage

Pass an existing driver to `guard()` and give the guarded driver to the agent:

```python
from neo4j_guard import GuardError, guard

db = guard(driver, mode="read")

try:
    result = db.execute_query("MATCH (p:Person) DETACH DELETE p")
except GuardError as error:
    print(error)  # DELETE is not allowed in read mode
```

With LangChain, guard the driver of `Neo4jGraph`:

```python
from langchain_neo4j import Neo4jGraph

graph = Neo4jGraph(url, username, password)
guard(graph._driver)
```

With the Neo4j MCP server, guard the driver passed to `create_mcp_server()`:

```python
from mcp_neo4j_cypher.server import create_mcp_server
from neo4j import AsyncGraphDatabase

mcp = create_mcp_server(guard(AsyncGraphDatabase.driver(url, auth=auth), allow=["apoc.meta.*"]))
```

## Configuration

### Modes

`mode` sets the defaults. `"read"` (or `"readonly"`) is the default mode, and `"write"` (or `"readwrite"`) also allows creating and updating data.

```python
db = guard(driver, mode="write")
```

|                                                                               | Read | Write |
| ----------------------------------------------------------------------------- | ---- | ----- |
| Read clauses (`MATCH`, `RETURN`, `WITH`, ...)                                 | ✓    | ✓     |
| Write clauses (`CREATE`, `MERGE`, `SET`, ...)                                 |      | ✓     |
| Delete clauses (`DELETE`, `DETACH DELETE`)                                    |      |       |
| Other clauses (`LOAD CSV`, `USE`)                                             |      |       |
| Built-in functions (`toUpper`, `count`, ...)                                  | ✓    | ✓     |
| Schema and search procedures (`db.labels`, `db.index.vector.queryNodes`, ...) | ✓    | ✓     |
| Other procedures and functions (`apoc.*`, `dbms.*`, ...)                      |      |       |
| Admin commands (`SHOW USERS`, `CREATE INDEX`, ...)                            |      |       |

### Allow and disallow

`allow` and `disallow` adjust the defaults.

```python
db = guard(driver, allow=["apoc.meta.*"], disallow=["Employee", "Person.ssn"])
```

|                            | Example                             | allow | disallow |
| -------------------------- | ----------------------------------- | ----- | -------- |
| Clause                     | `"DELETE"`                          | ✓     | ✓        |
| Procedure or function      | `"apoc.load.json"`, `"apoc.meta.*"` | ✓     | ✓        |
| Label or relationship type | `"Employee"`                        |       | ✓        |
| Property                   | `"Person.ssn"`                      |       | ✓        |

Labels and properties are checked in the query and in returned nodes, relationships and paths. A query can still reach them without naming them, for example through `labels(n)`, so this does not replace Neo4j's access control.
