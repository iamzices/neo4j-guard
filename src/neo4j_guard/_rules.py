"""The defaults and the allow and disallow rules for clauses, procedures, functions, labels and properties."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import cached_property
from typing import Literal

# The two guard modes.
Mode = Literal["read", "write"]

# The mode names `guard()` accepts, including the aliases "readonly" and "readwrite".
ModeName = Literal["read", "write", "readonly", "readwrite"]

# The mode each accepted name resolves to.
_MODES: dict[str, Mode] = {"read": "read", "write": "write", "readonly": "read", "readwrite": "write"}

# Every clause name that can appear in allow and disallow.
CLAUSES = frozenset({
    "CALL", "CREATE", "DELETE", "FILTER", "FINISH", "FOR", "FOREACH", "INSERT", "LET", "LOAD CSV",
    "MATCH", "MERGE", "ORDER BY", "REMOVE", "RETURN", "SET", "UNWIND", "USE", "WITH",
})  # fmt: skip

# Other names of clauses, for the clause they are part of.
_CLAUSE_ALIASES = {"DETACH DELETE": "DELETE", "OPTIONAL MATCH": "MATCH"}

# Clauses that read mode allows by default.
READ_CLAUSES = frozenset({"CALL", "FILTER", "FINISH", "FOR", "LET", "MATCH", "ORDER BY", "RETURN", "UNWIND", "WITH"})

# Clauses that write mode allows by default.
WRITE_CLAUSES = READ_CLAUSES | {"CREATE", "FOREACH", "INSERT", "MERGE", "REMOVE", "SET"}

# Procedures allowed by default, as lowercase fnmatch patterns. They only read the current graph.
_PROCEDURES = (
    "db.index.fulltext.query*", "db.index.vector.query*", "db.labels", "db.propertykeys",
    "db.relationshiptypes", "db.schema.*",
)  # fmt: skip

# Functions allowed by default, as lowercase fnmatch patterns. They only compute values.
_FUNCTIONS = (
    # Aggregating.
    "avg", "collect", "count", "max", "min", "percentilecont", "percentiledisc", "stdev", "stdevp", "sum",
    # Lists.
    "coll.*", "keys", "labels", "nodes", "range", "relationships", "reverse", "tail",
    "tobooleanlist", "tofloatlist", "tointegerlist", "tostringlist",
    # Numeric, logarithmic and trigonometric.
    "abs", "acos", "asin", "atan", "atan2", "ceil", "cos", "cot", "degrees", "e", "exp", "floor",
    "haversin", "isnan", "log", "log10", "pi", "radians", "rand", "round", "sign", "sin", "sqrt", "tan",
    # Predicates and scalars.
    "char_length", "character_length", "coalesce", "elementid", "endnode", "exists", "head", "id",
    "isempty", "last", "length", "nullif", "properties", "randomuuid", "size", "startnode", "timestamp",
    "toboolean", "tobooleanornull", "tofloat", "tofloatornull", "tointeger", "tointegerornull",
    "type", "valuetype",
    # Strings.
    "btrim", "left", "lower", "ltrim", "replace", "right", "rtrim", "split", "substring",
    "tolower", "tostring", "tostringornull", "toupper", "upper",
    # Spatial, temporal, vector and other.
    "date", "date.*", "datetime", "datetime.*", "db.namefromelementid", "duration", "duration.*", "file",
    "linenumber", "localdatetime", "localdatetime.*", "localtime", "localtime.*", "point", "point.*",
    "time", "time.*", "vector.similarity.*", "vector_dimension_count",
)  # fmt: skip


def _clause(entry: str) -> str:
    """Returns the entry as a clause name, uppercase with single spaces."""
    name = " ".join(entry.upper().split())
    return _CLAUSE_ALIASES.get(name, name)


def _is_routine(name: str) -> bool:
    """Returns whether a name is a built-in or APOC procedure or function, rather than a property."""
    return name.lower() in _PROCEDURES + _FUNCTIONS or name.lower().startswith("apoc.")


def _matches(name: str, patterns: Iterable[str]) -> bool:
    """Returns whether the name matches any pattern, ignoring case."""
    return any(fnmatchcase(name.lower(), pattern) for pattern in patterns)


@dataclass(frozen=True)
class Rules:
    """The allowed and blocked entries of one guarded driver.

    Create them with `Rules.build()`.

    Attributes:
        mode: The mode the rules were built for.
        allowed_clauses: Allowed clauses.
        allowed_procedures: Patterns for allowed procedures.
        allowed_functions: Patterns for allowed functions.
        blocked_names: Patterns for blocked procedures and functions.
        blocked_labels: Blocked labels and relationship types.
        blocked_properties: Blocked label and property key pairs.
    """

    mode: Mode
    allowed_clauses: frozenset[str]
    allowed_procedures: tuple[str, ...]
    allowed_functions: tuple[str, ...]
    blocked_names: tuple[str, ...]
    blocked_labels: frozenset[str]
    blocked_properties: frozenset[tuple[str, str]]

    @classmethod
    def build(cls, mode: ModeName, allow: Iterable[str], disallow: Iterable[str]) -> Rules:
        """Builds the rules from the defaults for a mode.

        Args:
            mode: "read" or "write", or their aliases "readonly" and "readwrite".
            allow: Entries to allow on top of the defaults.
            disallow: Entries to block. They take precedence over `allow`.

        Returns:
            The resolved rules.

        Raises:
            ValueError: If `mode` or an entry is invalid.
            TypeError: If `allow` or `disallow` is a single string instead of a list.
        """
        if mode not in _MODES:
            raise ValueError(f"mode must be 'read', 'write', 'readonly' or 'readwrite', not {mode!r}")
        if isinstance(allow, str) or isinstance(disallow, str):
            raise TypeError("allow and disallow must be lists of strings, not strings")
        allow, disallow = list(allow), list(disallow)
        for entry in (*allow, *disallow):
            if not isinstance(entry, str) or not entry.strip():
                raise ValueError(f"allow and disallow entries must be non-empty strings, not {entry!r}")

        resolved = _MODES[mode]
        defaults = READ_CLAUSES if resolved == "read" else WRITE_CLAUSES
        allow_clauses = {_clause(e) for e in allow if _clause(e) in CLAUSES}
        disallow_clauses = {_clause(e) for e in disallow if _clause(e) in CLAUSES}
        allow_names = tuple(e.lower() for e in allow if _clause(e) not in CLAUSES)
        # A name that is not a clause is a procedure or function. A plain name is also a
        # label or relationship type, and Label.key is also a property unless it names a
        # procedure or function. Labels and keys are case-sensitive in Neo4j, so they keep their case.
        disallow_names = [e for e in disallow if _clause(e) not in CLAUSES]
        exact = [e for e in disallow_names if "*" not in e]
        properties = [e for e in exact if e.count(".") == 1 and not _is_routine(e)]
        return cls(
            mode=resolved,
            allowed_clauses=(defaults | allow_clauses) - disallow_clauses,
            allowed_procedures=_PROCEDURES + allow_names,
            allowed_functions=_FUNCTIONS + allow_names,
            blocked_names=tuple(e.lower() for e in disallow_names),
            blocked_labels=frozenset(e for e in exact if "." not in e),
            blocked_properties=frozenset((label, key) for label, _, key in (e.partition(".") for e in properties)),
        )

    @cached_property
    def blocked_keys(self) -> frozenset[str]:
        """Property keys that are blocked on at least one label."""
        return frozenset(key for _, key in self.blocked_properties)

    def allows_procedure(self, name: str) -> bool:
        """Returns whether a procedure is allowed.

        Args:
            name: The procedure's full name, like "db.labels".

        Returns:
            True if the procedure is allowed and not blocked.
        """
        return _matches(name, self.allowed_procedures) and not _matches(name, self.blocked_names)

    def allows_function(self, name: str) -> bool:
        """Returns whether a function is allowed.

        `properties()` is blocked while any property is blocked, because it returns them all.

        Args:
            name: The function's full name, like "toUpper".

        Returns:
            True if the function is allowed and not blocked.
        """
        if self.blocked_keys and name.lower() == "properties":
            return False
        return _matches(name, self.allowed_functions) and not _matches(name, self.blocked_names)
