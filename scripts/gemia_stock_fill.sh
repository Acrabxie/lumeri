#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/_run_gemia_controller.sh" stock-fill-once "$@"
