#!/usr/bin/env bash
# Regenerates src/neo4j_guard/_parser from Neo4j's published Cypher 25 grammar.
# Requires Java and curl. Usage: scripts/generate_parser.sh [tag]
set -euo pipefail

TAG="${1:-2026.09}"
ANTLR_VERSION="4.13.2" # Must match antlr4-python3-runtime in pyproject.toml.
GRAMMAR="https://raw.githubusercontent.com/neo4j/neo4j/$TAG/community/cypher/front-end/parser/v25/parser/src/main/antlr4/org/neo4j/cypher/internal/parser/v25"
OUT="$(cd "$(dirname "$0")/.." && pwd)/src/neo4j_guard/_parser"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

curl -sSfLO "https://www.antlr.org/download/antlr-$ANTLR_VERSION-complete.jar"
# The grammar has two Java-only snippets. The sed commands translate them to Python.
curl -sSfL "$GRAMMAR/Cypher25Lexer.g4" \
  | sed 's/{ if (!_modeStack.isEmpty()) popMode(); }/{if self._modeStack: self.popMode()}/' > Cypher25Lexer.g4
curl -sSfL "$GRAMMAR/Cypher25Parser.g4" | sed 's/\[String paramType\]/[paramType]/' > Cypher25Parser.g4
if ! grep -q 'self._modeStack' Cypher25Lexer.g4 || grep -q '\[String ' Cypher25Parser.g4; then
  echo "The grammar changed and the edits above no longer apply." >&2
  exit 1
fi

java -jar "antlr-$ANTLR_VERSION-complete.jar" -Dlanguage=Python3 -no-listener Cypher25Lexer.g4 Cypher25Parser.g4
cp Cypher25Lexer.py Cypher25Parser.py "$OUT/"
echo "Generated $OUT from Neo4j $TAG"
