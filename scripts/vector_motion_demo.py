#!/usr/bin/env python3
"""Vector motion engine demo gallery — one subject, many styles.

Renders the same creative brief through several style archetypes plus a loop
background, writing animated ``.svg`` files (instant, browser-viewable) and —
with ``--video`` — real ``.mp4`` renders through the product path
(lumenframe html layer → HyperFrames/Chromium). Deterministic: same seeds,
same bytes.

Usage (from the repo root)::

    python3 scripts/vector_motion_demo.py out_dir            # SVGs only
    python3 scripts/vector_motion_demo.py out_dir --video    # + mp4 renders
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.vector import build_scene, scene_to_html_layer, scene_to_svg

BRIEFS: dict[str, dict] = {
    # The same wordmark reveal, restyled by naming one archetype.
    "logo_playful": {
        "subject": {"kind": "logo_text", "text": "Lumeri", "mark": "ring"},
        "intent": "reveal", "style": "playful",
        "feeling": ["creative", "energetic"], "duration": 5.0, "seed": 11,
    },
    "logo_minimal": {
        "subject": {"kind": "logo_text", "text": "Lumeri", "mark": "ring"},
        "intent": "reveal", "style": "minimal", "duration": 5.0, "seed": 11,
    },
    "logo_luxury": {
        "subject": {"kind": "logo_text", "text": "Lumeri", "mark": "ring"},
        "intent": "reveal", "style": "luxury", "duration": 6.0, "seed": 11,
    },
    "logo_lumeri": {
        "subject": {"kind": "logo_text", "text": "Lumeri", "mark": "ring"},
        "intent": "reveal", "style": "lumeri",
        "feeling": ["creative", "intelligent"], "duration": 5.0, "seed": 11,
    },
    # An organic looping background (Tech-AI register).
    "loop_tech_bg": {
        "subject": {"kind": "abstract"},
        "intent": "loop", "style": "tech",
        "feeling": ["organic", "futuristic"], "duration": 8.0, "seed": 4,
    },
    # A mark morph transition sting.
    "mark_morph": {
        "subject": {"kind": "mark", "preset": "hex", "morph_to": "blob"},
        "intent": "reveal", "style": "tech", "duration": 4.0, "seed": 2,
        "params": {"organicness": 0.9},
    },
}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    want_video = "--video" in sys.argv[2:]

    for name, brief in BRIEFS.items():
        result = build_scene(brief)
        svg_path = out_dir / f"{name}.svg"
        svg_path.write_text(scene_to_svg(result["scene"]), encoding="utf-8")
        (out_dir / f"{name}.plan.json").write_text(
            json.dumps(result["plan"], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{name}: {svg_path.name} "
              f"({len(result['plan']['phases'])} phases, "
              f"{sum(1 for _ in result['scene']['nodes'])} top nodes)")

        if want_video:
            from lumenframe.render_range import export_range

            layer = scene_to_html_layer(result["scene"], name=name, brief=brief)
            doc = empty_doc(title=name, width=1920, height=1080, fps=30)
            doc = apply_layer_patch(doc, {"version": 1, "ops": [
                {"op": "add_layer", "type": "html", "layer": layer},
            ]})
            mp4 = out_dir / f"{name}.mp4"
            export_range(doc, 0.0, float(result["scene"]["duration"]), str(mp4))
            print(f"  → {mp4.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
