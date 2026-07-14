#!/usr/bin/env bash
#
# Canonical check gate — the SINGLE SOURCE OF TRUTH for CI.
# Run locally before pushing:      scripts/ci.sh
# GitHub Actions runs the same:    bash scripts/ci.sh <stage>  (one step each)
#
# Usage: scripts/ci.sh [stage]
#   stage = lint | format | type | security | test | all   (default: all)
#
# `uv run` keeps the environment in sync automatically, so this works from a
# clean checkout without a manual `uv sync` first.
set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || dirname "$(dirname "$0")")"

step() { printf '\n\033[1m▶ %s\033[0m\n' "$1"; }

check_lint()     { step "ruff check";          uv run ruff check .; }
check_format()   { step "ruff format --check"; uv run ruff format --check .; }
check_type()     { step "mypy (strict)";       uv run mypy; }
check_security() { step "bandit";              uv run bandit -q -r src; }
check_test()     { step "pytest + coverage";   uv run pytest; }

case "${1:-all}" in
  lint)     check_lint ;;
  format)   check_format ;;
  type)     check_type ;;
  security) check_security ;;
  test)     check_test ;;
  all)
    check_lint
    check_format
    check_type
    check_security
    check_test
    printf '\n\033[1;32m✓ all checks passed\033[0m\n'
    ;;
  *)
    echo "unknown stage: ${1}" >&2
    echo "valid stages: lint | format | type | security | test | all" >&2
    exit 2
    ;;
esac
