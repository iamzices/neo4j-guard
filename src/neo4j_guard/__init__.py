"""A guardrail for LLM-generated Cypher, built for Neo4j."""

from ._checker import GuardError
from ._guard import guard

__all__ = ["GuardError", "guard"]
