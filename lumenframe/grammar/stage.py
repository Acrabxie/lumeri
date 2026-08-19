"""The stage report: what the frame looks like, said in a way you can act on.

A rendered frame tells a model *that* the title sits on the subject's face. It
does not tell it which layer to move, or by how much. This module produces the
other half — every layer's real position, the region it occupies, what it covers
and what covers it — keyed by layer id, so an observation turns straight into an
edit.

Ordering follows the compositor (index 0 composites first, at the back), so ``z``
reads directly as "who is on top".

Occlusion and region-span both read the **same** coverage mask. Letting them
diverge would let the report say a layer paints into a cell while also saying
nothing of it overlaps what is in that cell.
"""
from __future__ import annotations

from typing import Any

from lumenframe.grammar import blindspots, extent, vocabulary

__all__ = ["DETAIL_LEVELS", "render_text", "stage_report"]

#: Report verbosity. ``brief`` drops per-layer geometry down to position and
#: region; ``full`` adds the resolved vocabulary. Present because a report that
#: costs more context than a frame would defeats its own purpose.
DETAIL_LEVELS: tuple[str, ...] = ("brief", "normal", "full")


def stage_report(
    doc: dict[str, Any],
    seconds: float,
    *,
    detail: str = "normal",
    resolver: Any = None,
) -> dict[str, Any]:
    """Full stage report for ``doc`` at ``seconds``.

    Args:
        doc: A lumenframe document.
        seconds: The moment to describe, quantised to a frame.
        detail: One of :data:`DETAIL_LEVELS`.
        resolver: Optional content resolver forwarded to the measurement render.

    Returns:
        A JSON-serialisable report. ``warnings`` carries what the alpha
        measurement structurally cannot see (see
        :mod:`lumenframe.grammar.blindspots`); an empty list means the checks ran
        and found nothing, never that they were skipped.
    """
    if detail not in DETAIL_LEVELS:
        raise ValueError(f"unknown detail {detail!r}; expected one of {list(DETAIL_LEVELS)}")

    canvas = doc.get("canvas") or {}
    width = int(canvas.get("width") or 1920)
    height = int(canvas.get("height") or 1080)

    measurements, degradations = extent.measure_layers(doc, seconds, resolver=resolver)

    layers: list[dict[str, Any]] = []
    for m in measurements:
        entry: dict[str, Any] = {
            "id": m["id"],
            "name": m["name"],
            "type": m["type"],
            "z": m["z"],
            "extent_source": m["extent_source"],
            "opacity": m["opacity"],
        }
        bbox = m["bbox"]
        if bbox is None:
            entry["present"] = False
            entry["bbox_px"] = None
            layers.append(entry)
            continue

        x, y, w, h = bbox
        cx, cy = x + w / 2.0, y + h / 2.0
        entry.update({
            "present": True,
            "bbox_px": {"x": x, "y": y, "w": w, "h": h},
            "center_px": {"x": round(cx, 1), "y": round(cy, 1)},
            "region": vocabulary.region_for_point(cx, cy, width, height),
            "regions_covered": vocabulary.regions_covered(m["coverage"]),
            "canvas_ratio": extent.canvas_ratio(m["coverage"]),
            "safe_area": vocabulary.safe_area_status(bbox, width, height),
            "overlaps": [],
        })
        layers.append(entry)

    _fill_overlaps(layers, measurements)

    report: dict[str, Any] = {
        "time": round(float(seconds), 6),
        "frame": extent.frame_for_time(doc, seconds),
        "canvas": {"width": width, "height": height, "fps": float(canvas.get("fps") or 30.0)},
        "detail": detail,
        "occlusion_method": f"alpha_raster@1/{extent.COVERAGE_DOWNSAMPLE}",
        "layers": layers,
        "degradations": degradations,
        "warnings": blindspots.collect(doc, measurements),
    }
    if detail == "full":
        report["grid"] = vocabulary.describe_vocabulary(width, height)
    return report


def _fill_overlaps(
    layers: list[dict[str, Any]], measurements: list[dict[str, Any]]
) -> None:
    """Attach real-coverage overlap records to every present layer."""
    by_id = {m["id"]: m for m in measurements}
    for entry in layers:
        if not entry.get("present"):
            continue
        mine = by_id[entry["id"]]
        for other in measurements:
            if other["id"] == entry["id"] or other["bbox"] is None:
                continue
            ratio = extent.coverage_ratio(mine["coverage"], other["coverage"])
            if ratio <= 0.0:
                continue
            entry["overlaps"].append({
                "with": other["id"],
                "with_name": other["name"],
                "ratio_of_self": ratio,
                "ratio_of_other": extent.coverage_ratio(other["coverage"], mine["coverage"]),
                # Compositing order decides who wins the shared pixels.
                "relation": "covered_by" if other["z"] > mine["z"] else "covers",
            })


# ── text rendering ──────────────────────────────────────────────────────

_ABSENT_REASON = {
    "empty_at_time": "nothing visible at this moment",
    "unavailable": "could not be measured",
}


def _fmt_overlap(o: dict[str, Any]) -> str:
    verb = "covers" if o["relation"] == "covers" else "is covered by"
    return (
        f"{verb} '{o['with_name']}' — {o['ratio_of_self'] * 100:.1f}% of itself "
        f"intersects, which is {o['ratio_of_other'] * 100:.1f}% of that layer"
    )


def render_text(report: dict[str, Any]) -> str:
    """Render the report as the text a model actually reads."""
    c = report["canvas"]
    lines = [
        f"Canvas {c['width']}x{c['height']} @{c['fps']:g}fps | "
        f"t={report['time']:g}s (frame {report['frame']})",
        f"Grid: rule of thirds | Occlusion: {report['occlusion_method']} "
        f"(real alpha coverage, not a bounding-box estimate)",
        "",
        "Layers (low z at the back, high z on top):",
    ]

    brief = report["detail"] == "brief"
    for layer in report["layers"]:
        if not layer.get("present"):
            reason = _ABSENT_REASON.get(layer["extent_source"], layer["extent_source"])
            lines.append(f"  [z{layer['z']}] '{layer['name']}' ({layer['type']}) — {reason}")
            continue

        b, ctr = layer["bbox_px"], layer["center_px"]
        lines.append(f"  [z{layer['z']}] '{layer['name']}' ({layer['type']})")
        lines.append(
            f"        at x={b['x']} y={b['y']} size {b['w']}x{b['h']} | "
            f"centre ({ctr['x']:g}, {ctr['y']:g}) | {layer['canvas_ratio'] * 100:.1f}% of frame"
        )
        if not brief:
            covered = layer["regions_covered"]
            span = f", painting into {'/'.join(covered)}" if len(covered) > 1 else ""
            lines.append(f"        centre falls in {layer['region']}{span}")

        sa = layer["safe_area"]
        if sa["status"] != "inside" or sa["violated_zones"]:
            zones = f", outside {'/'.join(sa['violated_zones'])} safe area" if sa["violated_zones"] else ""
            lines.append(f"        ! edge: {sa['status']}{zones}")

        for o in layer["overlaps"]:
            lines.append(f"        {_fmt_overlap(o)}")

    if report["degradations"]:
        lines.append("")
        lines.append("Degradations (never silent):")
        for d in report["degradations"]:
            lines.append(f"  - {d['kind']}: {d['detail']}")

    if report["warnings"]:
        lines.append("")
        lines.append("! Measurement blind spots (invisible to alpha; found in the document):")
        for w in report["warnings"]:
            if w["kind"] == "discarded_intent":
                lines.append(
                    f"  - '{w['layer_name']}' sets {w['field']}={w['set_to']}, "
                    f"but the effective value is {w['effective']} — {w['detail']}"
                )
            else:
                edges = "/".join(w["suspected_clipped_edges"])
                lines.append(
                    f"  - '{w['layer_name']}' touches the {edges} canvas edge — {w['consequence']}"
                )

    return "\n".join(lines)
