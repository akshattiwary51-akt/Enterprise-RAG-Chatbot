#!/usr/bin/env bash
# Runs both backend suites (they cannot share one pytest process: two different `conftest` modules).
# Usage: scripts/run_tests.sh [extra pytest args]
set -u
cd "$(dirname "$0")/../backend"
PY="${PYTHON:-.venv/bin/python}"
status=0

echo "=== main suite (tests/) ==="
"$PY" -m pytest tests -q -p no:cacheprovider "$@" || status=1

echo
echo "=== legacy suite (tests/legacy/) ==="
"$PY" -m pytest tests/legacy -q -p no:cacheprovider -o addopts="" "$@" || status=1

exit $status
