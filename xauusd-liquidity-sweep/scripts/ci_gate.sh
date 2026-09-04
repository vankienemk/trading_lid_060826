#!/usr/bin/env bash
# =============================================================================
# CI/CD gate (guide section 30.4) — operated by Agent 0 before every merge.
#
#   lint        ruff check (src, tests, scripts)
#   type check  mypy src   (implementation code — tests are enforced by pytest
#                           + ruff; pandas-typed test helpers are out of scope)
#   unit tests  pytest
#   lookahead   pytest -m no_lookahead  (mandatory for Agent 2 / Agent 4 changes)
#
# Usage:
#   scripts/ci_gate.sh [--with-no-lookahead]
#
# Merges are REJECTED if any step fails. Agent 0 has no authority to override
# the gate. Use the venv python/tools when present, else system tools.
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_LOOKAHEAD=0
for arg in "$@"; do
  case "$arg" in
    --with-no-lookahead) WITH_LOOKAHEAD=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# --- tool resolution ---------------------------------------------------------
PY="${PYTHON:-python3}"
if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; fi

resolve_tool() {
  local name="$1"
  if [ -x "$ROOT/.venv/bin/$name" ]; then echo "$ROOT/.venv/bin/$name";
  elif command -v "$name" >/dev/null 2>&1; then echo "$name";
  else echo ""; fi
}

RUFF="$(resolve_tool ruff)"
MYPY="$(resolve_tool mypy)"

run_step() {
  local label="$1"; shift
  echo "==> $label"
  if ! "$@"; then
    echo "FAILED: $label"
    exit 1
  fi
}

# --- steps -------------------------------------------------------------------
if [ -n "$RUFF" ]; then
  run_step "lint: ruff check" "$RUFF" check src tests scripts
else
  echo "WARN: ruff not found — installing via pip is required for the gate" >&2
  exit 1
fi

if [ -n "$MYPY" ]; then
  run_step "type check: mypy" "$MYPY" src
else
  echo "WARN: mypy not found — installing via pip is required for the gate" >&2
  exit 1
fi

run_step "unit tests: pytest" "$PY" -m pytest -q

if [ "$WITH_LOOKAHEAD" -eq 1 ]; then
  run_step "no-lookahead: pytest -m no_lookahead" "$PY" -m pytest -q -m no_lookahead
fi

echo "CI gate: ALL STEPS PASSED"