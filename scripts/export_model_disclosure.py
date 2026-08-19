#!/usr/bin/env python3
"""Render gemia/model_strength.py to the model list the public policy page shows.

Waffo's AIGC requirements ask a merchant to name the models it actually uses,
specifically and truthfully — "AI technology" or "30+ models" is called out as
unacceptable. A hand-written list on a web page satisfies that on the day it is
written and quietly becomes a false statement the next time the model roster
moves, which is worse than saying nothing.

So the disclosure is generated from ``MEDIA_MODEL_PRIORITY`` — the same table
the runtime picks from — and ``tests/test_public_policy.py`` reds when the
committed JSON drifts from it.

Writes static/v3/models.json. Run after ANY change to MEDIA_MODEL_PRIORITY.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gemia.model_strength import MEDIA_MODEL_PRIORITY  # noqa: E402

# What each surface is called on the page. Routing backends (vertex, openrouter,
# gemini) are an internal deployment detail — the obligation is to name the
# model, not to publish the plumbing.
_SURFACE_LABELS = {
    "image": "Image generation",
    "video": "Video generation",
    "audio": "Music and sound generation",
}


def _bare(model: str) -> str:
    """Drop the provider prefix so one model is listed once, not per backend."""
    return model.split("/", 1)[1] if "/" in model else model


def build() -> dict:
    surfaces = []
    for slot, label in _SURFACE_LABELS.items():
        seen: list[str] = []
        for backend_models in MEDIA_MODEL_PRIORITY.get(slot, {}).values():
            for model in backend_models:
                name = _bare(model)
                if name not in seen:
                    seen.append(name)
        surfaces.append({"slot": slot, "label": label, "models": seen})
    return {
        "source": "gemia/model_strength.py",
        "note": (
            "Models are selected strongest-first from this list at request time; "
            "a weaker entry is used only when a stronger one is unavailable. "
            "One model can appear under more than one identifier because "
            "providers name the same model differently; every identifier listed "
            "is one this platform actually calls. Screening applies identically "
            "whichever model answers."
        ),
        "surfaces": surfaces,
    }


def main() -> int:
    payload = json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    out = REPO / "static" / "v3" / "models.json"
    out.write_text(payload, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
