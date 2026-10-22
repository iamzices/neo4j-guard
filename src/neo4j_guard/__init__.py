"""A guardrail for LLM-generated Cypher, built for Neo4j."""

from ._checker import GuardError
from ._guard import check, guard

__all__ = ["GuardError", "check", "guard"]
