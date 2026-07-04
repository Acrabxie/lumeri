"""Time-dimension feature demo — engine-rendered, end-to-end proof.

Four clearly-labelled segments, each ~4-6s, that exercise lumenframe's TIME
features with *synthetic* visuals only (gradients / shapes / text — NO external
media, NO AI generation):

  1. SEEK / range    — render a sub-range ``[t_in, t_out]`` of a longer comp
                       via :func:`lumenframe.render_range.render_range`.
                       caption: "view a time range".
  2. RETIME segment  — a clip whose middle sub-range plays 2x, via the
                       ``retime_segment`` op.   caption: "2x a segment".
  3. LANES           — layers on different lanes stack as parallel tracks
                       (``lane`` is read by compile's ``_lane_ordered_children``).
                       caption: "lanes / parallel tracks".
  4. MERGE           — two comps merged (append + overlay) via the
                       ``merge_compositions`` op.   caption: "merge timelines".

Mirrors the engine-render pattern of ``overnight-artifacts/showreel/comp_show.py``:
build a lumenframe doc per segment, ``compile_to_layer_stack`` it, render every
frame, then ``ffmpeg`` to a single mp4. The heavy full render is in ``__main__``;
the fast smoke test (``tests/test_time_demo_smoke.py``) only composes + renders a
frame or two per segment.

Run the real render::

    /Volumes/Extreme\\ SSD/gemia/.venv/bin/python examples/time_demo.py

Output: ``overnight-artifacts/time-demo/lumeri_time_features.mp4``.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# Allow ``python examples/time_demo.py`` from the repo root: running a script puts
# its OWN dir (examples/) on sys.path[0], not the repo root, so the lumenframe /
# gemia packages would be invisible. Add the repo root (this file's parent's
# parent) to the path before importing them.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lumenframe import apply_layer_patch, empty_doc, model
from lumenframe.compile import compile_to_layer_stack
from lumenframe.render_range import render_range
from lumenframe.resolve import default_resolver

# ── canvas / output ──────────────────────────────────────────────────────
W, H, FPS = 1280, 720, 30
OUT_DIR = Path("/Volumes/Extreme SSD/GemiaTemp/overnight-artifacts/time-demo")
OUT_MP4 = OUT_DIR / "lumeri_time_features.mp4"

# Each segment's intended on-screen duration (seconds). render_range is special:
# its source comp is longer, but only the sub-range below is shown / encoded.
SEG_SECS = 5.0                       # most segments
RANGE_SRC_SECS = 12.0                # full length of the seek source comp
RANGE_IN, RANGE_OUT = 3.5, 8.5       # the [t_in, t_out) slice we actually show
RANGE_SHOWN_SECS = RANGE_OUT - RANGE_IN  # = 5.0


# ── tiny doc helpers (mirroring comp_show.py's style) ─────────────────────
def _doc(secs: float):
    return empty_doc(width=W, height=H, fps=secs and FPS or FPS)


def ap(d, *ops):
    return apply_layer_patch(d, {"version": 1, "ops": list(ops)})


def _set_lane(d, layer_id: str, lane: int):
    """Set a layer's ``lane`` directly (the add_shape sugar does not take lane)."""
    layer = model.find_layer(d, layer_id)
    if layer is not None:
        layer["lane"] = int(lane)
    return d


def caption(d, secs: float, text: str, *, y: float = 285.0, size: int = 44,
            color: str = "#cdebff"):
    """Add a centred, fading caption layer spanning ``secs`` seconds."""
    d = ap(d, {"op": "add_layer", "type": "text", "id": "caption", "text": text,
               "duration": secs})
    d = ap(d, {"op": "set_text", "layer_id": "caption", "text": text,
               "font_size": size, "color": color, "align": "center",
               "shadow": {"dx": 2, "dy": 2, "color": "#000000", "blur": 6}})
    d = ap(d, {"op": "set_transform", "layer_id": "caption", "y": y})
    return _fade(d, "caption", secs, 0.4, 0.4)


def badge(d, secs: float, text: str, num: str):
    """Small top-left segment label (e.g. '1  SEEK / range')."""
    d = ap(d, {"op": "add_layer", "type": "text", "id": "badge",
               "text": f"{num}   {text}", "duration": secs})
    d = ap(d, {"op": "set_text", "layer_id": "badge", "text": f"{num}   {text}",
               "font_size": 30, "color": "#8fd0ff", "align": "left",
               "shadow": {"dx": 1, "dy": 1, "color": "#000000", "blur": 4}})
    d = ap(d, {"op": "set_transform", "layer_id": "badge", "x": -430.0, "y": -300.0})
    return _fade(d, "badge", secs, 0.3, 0.3)


def _fade(d, layer_id: str, secs: float, fin: float = 0.4, fout: float = 0.4):
    pts = [(0.0, 0.0), (fin, 1.0), (max(secs - fout, fin + 0.01), 1.0), (secs, 0.0)]
    for t, v in pts:
        d = ap(d, {"op": "set_keyframe", "layer_id": layer_id, "property": "opacity",
                   "t": float(t), "value": float(v), "interp": "linear"})
    return d


# ═════════════════════════════════════════════════════════════════════════
# (1) SEEK / range — a longer comp; we view only [RANGE_IN, RANGE_OUT)
# ═════════════════════════════════════════════════════════════════════════
def build_seek_doc():
    """A 12s comp: a marker sweeps left->right and a pulsing ring travels.

    Only the sub-range [RANGE_IN, RANGE_OUT) is rendered for the demo, proving
    render_range slices a longer timeline. The doc's total_frames is the FULL
    12s; the *shown* window is RANGE_SHOWN_SECS.
    """
    s = RANGE_SRC_SECS
    d = empty_doc(width=W, height=H, fps=FPS)
    d = ap(d, {"op": "add_gradient", "id": "bg", "mode": "linear",
               "stops": [[0, "#0c1c3a"], [1, "#03060e"]], "angle": 90, "duration": s})
    # A long horizontal "timeline" bar near the bottom + a playhead marker that
    # sweeps the whole 12s, so the rendered slice visibly starts mid-motion.
    d = ap(d, {"op": "add_shape", "id": "track", "kind": "rect", "fill": "#1d3360",
               "rect": [0.06, 0.80, 0.94, 0.84], "radius": 8, "duration": s})
    d = ap(d, {"op": "add_shape", "id": "play", "kind": "rect", "fill": "#ffd166",
               "rect": [0.0, 0.78, 0.02, 0.86], "radius": 4, "duration": s})
    for t, frac in [(0.0, 0.06), (s, 0.94)]:
        d = ap(d, {"op": "set_keyframe", "layer_id": "play", "property": "transform.x",
                   "t": float(t), "value": (frac - 0.5) * W, "interp": "linear"})
    # A pulsing travelling ring so the slice clearly shows continuous motion.
    d = ap(d, {"op": "add_shape", "id": "orb", "kind": "ellipse", "fill": "#36d6ff",
               "rect": [0.44, 0.32, 0.56, 0.56], "duration": s})
    for t, frac in [(0.0, 0.1), (s, 0.9)]:
        d = ap(d, {"op": "set_keyframe", "layer_id": "orb", "property": "transform.x",
                   "t": float(t), "value": (frac - 0.5) * W, "interp": "linear"})
    for t, sc in [(0.0, 0.8), (3.0, 1.25), (6.0, 0.8), (9.0, 1.25), (s, 0.8)]:
        d = ap(d, {"op": "set_keyframe", "layer_id": "orb", "property": "transform.scale",
                   "t": float(t), "value": sc, "interp": "linear"})
    d = badge(d, s, "SEEK / range", "1")
    d = caption(d, s, "view a time range")
    # A small note showing which slice we are viewing.
    d = ap(d, {"op": "add_layer", "type": "text", "id": "note",
               "text": f"[t_in={RANGE_IN:g}s  ->  t_out={RANGE_OUT:g}s]  of a 12s comp",
               "duration": s})
    d = ap(d, {"op": "set_text", "layer_id": "note",
               "text": f"[t_in={RANGE_IN:g}s  ->  t_out={RANGE_OUT:g}s]  of a 12s comp",
               "font_size": 26, "color": "#7fa8d8", "align": "center"})
    d = ap(d, {"op": "set_transform", "layer_id": "note", "y": 340.0})
    return d


# ═════════════════════════════════════════════════════════════════════════
# (2) RETIME segment — middle sub-range plays 2x
# ═════════════════════════════════════════════════════════════════════════
def build_retime_doc():
    """A bar sweeps across; the MIDDLE [1.5, 3.5]s plays at 2x via retime_segment.

    retime_segment splits the clip at the edges and set_speed=2 on the middle
    piece, so total duration shrinks: the 2s middle becomes 1s. The intended
    on-screen length is therefore start(1.5) + half(1.0) + tail(1.5) = 4.0s.
    """
    pre, mid, post = 1.5, 2.0, 1.5     # original (output) timing before retime
    full = pre + mid + post            # = 5.0s authored (sweep, before retime)
    shown = pre + (mid / 2.0) + post   # = 4.0s after the 2x retime (middle halved)
    d = empty_doc(width=W, height=H, fps=FPS)
    # bg / caption span the RETIMED total so the comp duration == ``shown``.
    d = ap(d, {"op": "add_gradient", "id": "bg", "mode": "linear",
               "stops": [[0, "#2a1140"], [1, "#070310"]], "angle": 90, "duration": shown})
    # vertical sweep bar — its motion speed visibly doubles in the middle.
    d = ap(d, {"op": "add_shape", "id": "sweep", "kind": "rect", "fill": "#00e5ff",
               "rect": [0.0, 0.18, 0.05, 0.70], "radius": 6, "duration": full})
    for t, frac in [(0.0, 0.06), (full, 0.94)]:
        d = ap(d, {"op": "set_keyframe", "layer_id": "sweep", "property": "transform.x",
                   "t": float(t), "value": (frac - 0.5) * W, "interp": "linear"})
    # 2x ONLY the middle [pre, pre+mid]. retime_segment splits + set_speed=2 on
    # the middle piece (2s -> 1s) but leaves the tail at its original start, so a
    # gap opens. Ripple the tail back by the time saved (mid/2) to keep the sweep
    # continuous and make the comp duration exactly ``shown``.
    d = ap(d, {"op": "retime_segment", "layer_id": "sweep",
               "t0": pre, "t1": pre + mid, "speed": 2.0})
    saved = mid / 2.0
    for child in d["root"]["children"]:
        if abs(model._as_float(child.get("start")) - (pre + mid)) < 1e-6:
            d = ap(d, {"op": "set_time", "layer_id": str(child["id"]),
                       "start": (pre + mid) - saved})
            break
    d = badge(d, shown, "RETIME segment", "2")
    d = caption(d, shown, "2x a segment")
    # a marker band over the sped-up window so the viewer sees WHERE the 2x is.
    hot_start = pre
    hot_len = mid / 2.0
    d = ap(d, {"op": "add_shape", "id": "hot", "kind": "rect", "fill": "#ff5d73",
               "rect": [0.30, 0.75, 0.70, 0.79], "radius": 4,
               "start": hot_start, "duration": hot_len})
    d = ap(d, {"op": "add_layer", "type": "text", "id": "hotlbl", "text": "2x  >>",
               "start": hot_start, "duration": hot_len})
    d = ap(d, {"op": "set_text", "layer_id": "hotlbl", "text": "2x  >>",
               "font_size": 30, "color": "#ff9db0", "align": "center"})
    d = ap(d, {"op": "set_transform", "layer_id": "hotlbl", "y": 330.0})
    return d


# ═════════════════════════════════════════════════════════════════════════
# (3) LANES / parallel tracks — layers on lanes 1/2/3 stack as tracks
# ═════════════════════════════════════════════════════════════════════════
def build_lanes_doc():
    """Three coloured track-bars on lanes 1/2/3 plus a top-lane playhead.

    ``lane`` is read by compile's ``_lane_ordered_children`` (stable sort): a
    higher lane composites ABOVE a lower one. We stagger their starts so they
    visibly stack in as separate parallel tracks.
    """
    s = SEG_SECS
    d = empty_doc(width=W, height=H, fps=FPS)
    d = ap(d, {"op": "add_gradient", "id": "bg", "mode": "linear",
               "stops": [[0, "#08131a"], [1, "#02060a"]], "angle": 90, "duration": s})
    tracks = [
        ("trk1", "#ff5d73", 0.20, 1, 0.2),   # (id, colour, y-top, lane, start)
        ("trk2", "#5dd4ff", 0.40, 2, 0.7),
        ("trk3", "#9dff5d", 0.60, 3, 1.2),
    ]
    for tid, col, ytop, lane, start in tracks:
        d = ap(d, {"op": "add_shape", "id": tid, "kind": "rect", "fill": col,
                   "rect": [0.10, ytop, 0.90, ytop + 0.12], "radius": 10,
                   "start": start, "duration": s - start})
        d = _set_lane(d, tid, lane)
        # slide-in + label per track
        for t, x in [(start, -W * 0.6), (start + 0.5, 0.0)]:
            d = ap(d, {"op": "set_keyframe", "layer_id": tid, "property": "transform.x",
                       "t": float(t), "value": float(x), "interp": "linear"})
        lbl = f"{tid}lbl"
        d = ap(d, {"op": "add_layer", "type": "text", "id": lbl,
                   "text": f"track {lane}", "start": start, "duration": s - start})
        d = ap(d, {"op": "set_text", "layer_id": lbl, "text": f"track {lane}",
                   "font_size": 26, "color": "#04121a", "align": "center"})
        d = ap(d, {"op": "set_transform", "layer_id": lbl, "x": -480.0,
                   "y": (ytop + 0.06 - 0.5) * H})
        d = _set_lane(d, lbl, lane)
    # a thin playhead on the TOP lane so it draws over all tracks.
    d = ap(d, {"op": "add_shape", "id": "head", "kind": "rect", "fill": "#ffd166",
               "rect": [0.0, 0.12, 0.012, 0.80], "duration": s})
    d = _set_lane(d, "head", 9)
    for t, frac in [(0.0, 0.10), (s, 0.90)]:
        d = ap(d, {"op": "set_keyframe", "layer_id": "head", "property": "transform.x",
                   "t": float(t), "value": (frac - 0.5) * W, "interp": "linear"})
    d = badge(d, s, "LANES", "3")
    d = caption(d, s, "lanes / parallel tracks", y=300.0)
    return d


# ═════════════════════════════════════════════════════════════════════════
# (4) MERGE timelines — two comps merged append + overlay
# ═════════════════════════════════════════════════════════════════════════
def build_merge_doc():
    """Build comp A (3s) + comp B (3s) + overlay comp C, merge into one timeline.

    A is appended, B is appended after A (so A->B play in sequence), and C is
    overlaid (composites over the whole merged span). The source comps live at
    root and the (initially empty) ``main`` comp is the merge target, so each
    append baseline only counts already-merged content -> A:0-3, B:3-6, C:0-6.
    """
    a_secs, b_secs = 3.0, 3.0
    merged_secs = a_secs + b_secs       # 6.0s
    d = empty_doc(width=W, height=H, fps=FPS)
    # empty merge target FIRST (stays as the sole root child after merges).
    d = ap(d, {"op": "add_layer", "type": "composition", "id": "main", "duration": 0.0})

    # comp A — warm panel + label
    d = ap(d, {"op": "add_layer", "type": "composition", "id": "compA", "duration": a_secs})
    d = ap(d, {"op": "add_gradient", "id": "a_bg", "mode": "linear",
               "stops": [[0, "#7a1f3d"], [1, "#3a0f20"]], "angle": 45,
               "duration": a_secs, "parent_id": "compA"})
    d = ap(d, {"op": "add_layer", "type": "text", "id": "a_lbl", "text": "comp A",
               "duration": a_secs, "parent_id": "compA"})
    d = ap(d, {"op": "set_text", "layer_id": "a_lbl", "text": "comp A",
               "font_size": 90, "color": "#ffd9e3", "align": "center"})

    # comp B — cool panel + label
    d = ap(d, {"op": "add_layer", "type": "composition", "id": "compB", "duration": b_secs})
    d = ap(d, {"op": "add_gradient", "id": "b_bg", "mode": "linear",
               "stops": [[0, "#1f4f7a"], [1, "#0f2840"]], "angle": 45,
               "duration": b_secs, "parent_id": "compB"})
    d = ap(d, {"op": "add_layer", "type": "text", "id": "b_lbl", "text": "comp B",
               "duration": b_secs, "parent_id": "compB"})
    d = ap(d, {"op": "set_text", "layer_id": "b_lbl", "text": "comp B",
               "font_size": 90, "color": "#d6ecff", "align": "center"})

    # comp C — overlay badge that rides over the whole merged span
    d = ap(d, {"op": "add_layer", "type": "composition", "id": "compC", "duration": merged_secs})
    d = ap(d, {"op": "add_shape", "id": "c_badge", "kind": "ellipse", "fill": "#ffd166",
               "rect": [0.40, 0.06, 0.60, 0.26], "duration": merged_secs, "parent_id": "compC"})
    d = ap(d, {"op": "add_layer", "type": "text", "id": "c_lbl", "text": "overlay C",
               "duration": merged_secs, "parent_id": "compC"})
    d = ap(d, {"op": "set_text", "layer_id": "c_lbl", "text": "overlay C",
               "font_size": 30, "color": "#3a2a00", "align": "center"})
    d = ap(d, {"op": "set_transform", "layer_id": "c_lbl", "y": (0.16 - 0.5) * H})

    # MERGE: append A, append B (after A), overlay C — all into ``main``.
    d = ap(d, {"op": "merge_compositions", "into_id": "main",
               "source_ids": ["compA"], "mode": "append"})
    d = ap(d, {"op": "merge_compositions", "into_id": "main",
               "source_ids": ["compB"], "mode": "append"})
    d = ap(d, {"op": "merge_compositions", "into_id": "main",
               "source_ids": ["compC"], "mode": "overlay"})
    # size ``main`` to its merged content so the root sees the full length.
    main = model.find_layer(d, "main")
    ext = model._composition_extent(main)
    d = ap(d, {"op": "set_time", "layer_id": "main", "duration": ext})

    # caption + badge (top-level over the merged comp).
    d = badge(d, merged_secs, "MERGE timelines", "4")
    d = caption(d, merged_secs, "merge timelines", y=300.0)
    return d


# ── segment registry ──────────────────────────────────────────────────────
# Each entry: (name, builder, kind, shown_seconds, [range bounds])
#   kind == "range": render only [t_in, t_out) of the (longer) compiled comp.
#   kind == "full":  render the whole compiled comp.
SEGMENTS = [
    ("seek",   build_seek_doc,   "range", RANGE_SHOWN_SECS, (RANGE_IN, RANGE_OUT)),
    ("retime", build_retime_doc, "full",  1.5 + 1.0 + 1.5,  None),
    ("lanes",  build_lanes_doc,  "full",  SEG_SECS,         None),
    ("merge",  build_merge_doc,  "full",  6.0,              None),
]


def _frames_for_segment(entry, *, resolver=default_resolver):
    """Return the list of RGBA frames for one segment (full or sliced range)."""
    name, builder, kind, _shown, bounds = entry
    doc = builder()
    if kind == "range":
        t_in, t_out = bounds
        return render_range(doc, t_in, t_out, resolver=resolver)
    stack = compile_to_layer_stack(doc, resolver=resolver)
    return stack.render_frames()


def render(out_path: Path = OUT_MP4) -> Path:
    """Render every segment, concatenate, and encode a single H.264 mp4."""
    import cv2  # local import: heavy
    from gemia.video.layers import _flatten_rgba_for_video, to_uint8

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = out_path.with_name(out_path.stem + ".raw.mp4")

    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    total = 0
    t0 = time.time()
    for entry in SEGMENTS:
        name = entry[0]
        seg_t = time.time()
        frames = _frames_for_segment(entry)
        for frame in frames:
            writer.write(to_uint8(_flatten_rgba_for_video(frame, background_color=(0, 0, 0))))
        total += len(frames)
        print(f"  segment {name:7s} {len(frames):4d}f  {time.time() - seg_t:5.1f}s")
    writer.release()

    # Transcode to browser-friendly H.264 / yuv420p.
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-crf", "20", "-movflags", "+faststart", str(out_path)],
        check=True, capture_output=True,
    )
    raw.unlink(missing_ok=True)
    print(f"DONE {out_path}  |  {total} frames ({total / FPS:.1f}s)  "
          f"|  {time.time() - t0:.0f}s")
    return out_path


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_MP4
    render(dest)
