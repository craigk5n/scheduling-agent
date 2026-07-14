#!/usr/bin/env bash
#
# One-time setup: point git at the tracked .githooks/ directory so the
# version-controlled pre-push hook runs. Idempotent — safe to re-run.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh 2>/dev/null || true
echo "Installed: core.hooksPath=.githooks — pre-push now runs scripts/ci.sh."
