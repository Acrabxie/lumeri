#!/bin/bash
set -euo pipefail

GEMIA_DIR="/Users/xiehaibo/Code/gemia"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PYTHONPATH="$GEMIA_DIR${PYTHONPATH:+:$PYTHONPATH}"
export HTTPS_PROXY=""
export HTTP_PROXY=""
export ALL_PROXY=""
export https_proxy=""
export http_proxy=""
export all_proxy=""
export NO_PROXY="*"
export no_proxy="*"

cd "$GEMIA_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ "${GEMIA_CONTROLLER_USE_UV:-0}" != "1" ]; then
  exec "$PYTHON_BIN" -m gemia.automation.loop_controller "$@"
fi

UV_BIN="${UV_BIN:-/opt/homebrew/bin/uv}"
exec "$UV_BIN" run --no-project --with google-genai python -m gemia.automation.loop_controller "$@"
