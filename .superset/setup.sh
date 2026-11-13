#!/usr/bin/env bash
set -euo pipefail

echo "Workspace: ${SUPERSET_WORKSPACE_NAME:-unknown}"

# Share secrets from the main checkout. Never commit .env.
if [ -f "$SUPERSET_ROOT_PATH/.env" ]; then
  cp "$SUPERSET_ROOT_PATH/.env" .env
else
  echo "WARNING: no .env in $SUPERSET_ROOT_PATH"
fi

# Python environment for this worktree
uv sync

# Fast self-check so a broken workspace is obvious
uv run engenia --help >/dev/null && echo "engenia CLI ok"
uv run pytest -q tests/smoke || echo "WARNING: smoke tests failing"

echo "Workspace ready"
