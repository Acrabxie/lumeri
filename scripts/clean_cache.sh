#!/usr/bin/env bash
# scripts/clean_cache.sh — safely prune Gemia repo-internal caches.
#
# Targets repo-internal disposable directories (temp, build, dist,
# __pycache__, .pytest_cache, midscene_run) and ages out old logs/outputs.
# Does NOT touch ~/.gemia/accounts/ (user data) or .venv/.git.
#
# Run manually when disk gets tight; safe to re-run.

set -euo pipefail

# Resolve repo root from the script location so it runs from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT"

echo "Cleaning repo-internal caches under: $ROOT"

# Disposable: regenerated on demand.
rm -rf temp/* build dist .pytest_cache midscene_run 2>/dev/null || true

# Logs: keep last 7 days only.
if [ -d logs ]; then
  find logs -type f -mtime +7 -delete
fi

# Render artefacts: keep last 14 days.
for dir in outputs frames styled; do
  if [ -d "$dir" ]; then
    find "$dir" -type f -mtime +14 -delete
  fi
done

# Plan/task JSON history: keep last 30 days.
for dir in plans tasks; do
  if [ -d "$dir" ]; then
    find "$dir" -type f -mtime +30 -delete
  fi
done

# Bytecode caches everywhere under the repo.
find . -name __pycache__ -type d -prune -exec rm -rf {} +

echo "Done."
