#!/usr/bin/env bash
# Lumeri test-box bootstrap for a fresh GCP VM.
#
# Recommended image: Ubuntu 24.04 LTS (ships Python 3.12; lumeri needs >=3.12).
# Usage on the VM:
#   git clone https://github.com/Acrabxie/lumeri.git
#   cd lumeri && git checkout overnight/base-20260627
#   bash setup_gcp.sh
#
# What lumeri ACTUALLY needs to run+test: Python 3.12, ffmpeg, and the pip deps.
# NODE / codex / claude-code are NOT required to run lumeri (Node is only for the
# Tauri desktop app or the lumeri-cli TUI; the web UI under static/v3 is static).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> [1/4] system deps (python3.12, ffmpeg, OpenCV/soundfile libs, git)"
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  ffmpeg git build-essential \
  libgl1 libglib2.0-0 libsndfile1

PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
echo "    python3 = $PYV"
case "$PYV" in
  3.12|3.13|3.14) : ;;
  *) echo "!! lumeri needs Python >=3.12 (found $PYV). Use an Ubuntu 24.04 image, or install python3.12 / use 'uv'."; exit 2 ;;
esac

echo "==> [2/4] venv + lumeri python deps (numpy / Pillow / opencv / librosa / scipy / otio ...)"
python3 -m venv .venv
./.venv/bin/pip install -U pip wheel
./.venv/bin/pip install -e .

echo "==> [3/4] smoke check"
ffmpeg -version | head -1
./.venv/bin/python -c "import numpy, cv2, PIL, librosa, scipy, opentimelineio; print('deps OK: numpy', numpy.__version__, '| cv2', cv2.__version__)"

echo "==> [4/4] done."
cat <<'EOF'

──────────────────────────────────────────────────────────────────────────
Run it:
  ./.venv/bin/python server.py --port 7788
    First start runs the ONBOARDING wizard:
      - SSH tty   -> interactive (pick provider: vertex/gemini/openrouter/gpt/claude,
                     enter key, optional search-engine key)
      - headless  -> prints the exact ~/.gemia/config.json template, then exits
    Re-run anytime:  ./.venv/bin/python -m gemia setup

View the web UI from your laptop (server stays headless on GCP):
  ssh -L 7788:localhost:7788 <vm>     # then open http://localhost:7788

──────────────────────────────────────────────────────────────────────────
OPTIONAL (only if you specifically want them):
  # Vertex as the brain/media (Veo/Lyria/Nano Banana). Easiest on GCP is a
  # gemini_api_key or openrouter key (no gcloud). For Vertex itself:
  #   gcloud auth application-default login        # lumeri reads this ADC file
  # (pure VM service-account/metadata auth is not yet supported by lumeri.)

  # Node 20 — ONLY for the Tauri desktop app or the lumeri-cli TUI:
  #   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  #   sudo apt-get install -y nodejs

  # BYOK search: during onboarding pick a provider + paste its key
  #   (tavily / serper / brave / exa / bing / google_cse). Skipping -> DuckDuckGo.
──────────────────────────────────────────────────────────────────────────
EOF
