"""Phase 2 comp_ref bridge gates (docs/timeline-canonical-plan.md §3 + §6).

Real end-to-end: a lumenframe solid-color composition enters the clip timeline
via ``lumen_comp_to_timeline`` (content-addressed cache render + one atomic
patch), export pass 0 re-renders stale windows when ``lumenframe.json``'s
sha256 changes, a shrunken comp fails the export with a typed error instead of
silently changing clip duration, single-step undo removes clip+asset, and
re-invocation with an unchanged doc reuses the cache. Plus the Phase 2 patch
cleanup: move/delete positionally re-validate ``transition_after``.
"""
from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from lumenframe import apply_layer_patch, empty_doc

from gemia.project_export import ProjectExportError, export_project
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools import layer as layer_module
from gemia.tools._context import AssetRegistry, ToolContext


# ── helpers ──────────────────────────────────────────────────────────────────


def _ctx(tmp_path: Path) -> ToolContext:
    """Project-backed ToolContext with a unique session/project per test.

    Unique names matter: layer._LUMENFRAME_PATH_CACHE is keyed by
    (session_id, project_id) and would otherwise leak paths across tests.
    """
    name = f"bridge_{uuid.uuid4().hex[:8]}"
    handle = ProjectHandle.open(tmp_path / "projects", name, session_id=name)
    return ToolContext(
        session_id=name,
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
        project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def _solid_doc(color: str, duration: float) -> dict[str, Any]:
    """192x108 (16:9 — scale+pad to the 1080p canvas adds no bars) @ 30 fps."""
    doc = empty_doc(width=192, height=108, fps=30)
    return apply_layer_patch(doc, {"version": 1, "ops": [{
        "op": "add_layer", "id": "bg", "type": "solid", "color": color,
        "start": 0.0, "duration": duration,
    }]})


def _export(handle: ProjectHandle, tmp_path: Path, label: str) -> dict[str, Any]:
    return export_project(
        handle.store, handle.project_id,
        output_root=tmp_path / "out", quality="draft", label=label,
    )


def _frame_rgb_mean(path: str | Path, t: float) -> tuple[float, float, float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{t:.6f}", "-i", str(path),
         "-frames:v", "1", "-vf", "scale=16:16",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    data = proc.stdout
    assert data, f"no frame decoded at t={t}"
    n = len(data) // 3
    return (sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n)


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


_RED = (255.0, 0.0, 0.0)
_BLUE = (0.0, 0.0, 255.0)
_COLOR_TOL = 40.0  # draft-quality encode + 16x16 downscale smear


def _project_asset(handle: ProjectHandle, asset_id: str) -> dict[str, Any] | None:
    for asset in handle.load().get("assets") or []:
        if str(asset.get("id")) == asset_id:
            return asset
    return None


def _clips(handle: ProjectHandle) -> list[dict[str, Any]]:
    return list(handle.load().get("timeline", {}).get("clips") or [])


# ── gate 1: insert renders the cache file and export consumes it ─────────────


def test_insert_renders_cache_and_export_consumes_it(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layer_module._save_lumendoc(ctx, _solid_doc("#FF0000", 2.0))

    result = _call("lumen_comp_to_timeline", {"t_in": 0.5, "t_out": 1.5}, ctx)
    assert result["applied"] is True
    assert result["cache_hit"] is False
    assert result["duration"] == pytest.approx(1.0, abs=1e-6)

    cache = Path(result["path"])
    assert cache.exists() and cache.stat().st_size > 0
    assert cache.name.startswith("comp_")
    renders_dir = ctx.project.store.renders_dir(ctx.project.project_id)
    assert cache.parent == renders_dir

    # The asset is a NORMAL video asset with comp_ref provenance in metadata.
    asset = _project_asset(ctx.project, result["asset_id"])
    assert asset is not None and asset.get("media_kind") == "video"
    ref = (asset.get("metadata") or {}).get("comp_ref")
    assert isinstance(ref, dict)
    assert ref["doc_hash"].startswith("sha256:") and len(ref["doc_hash"]) == 7 + 64
    assert ref["doc_hash_source"] == "file"
    assert ref["t_in"] == pytest.approx(0.5) and ref["t_out"] == pytest.approx(1.5)

    # Clip invariants: duration == source range, placed on V1 at 0.
    (clip,) = _clips(ctx.project)
    assert clip["asset_id"] == result["asset_id"]
    assert clip["duration"] == pytest.approx(1.0, abs=1e-6)
    assert clip["source_in"] == pytest.approx(0.0)
    assert clip["source_out"] == pytest.approx(1.0, abs=1e-6)

    manifest = _export(ctx.project, tmp_path, "bridge-insert")
    assert manifest["comp_refreshed"] == []
    assert _dist(_frame_rgb_mean(manifest["export_path"], 0.5), _RED) < _COLOR_TOL


# ── gate 2: live reference — doc edit triggers pass-0 refresh ─────────────────


def test_export_pass0_refreshes_stale_comp(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layer_module._save_lumendoc(ctx, _solid_doc("#FF0000", 2.0))
    result = _call("lumen_comp_to_timeline", {"t_in": 0.5, "t_out": 1.5}, ctx)
    old_path = result["path"]
    asset_id = result["asset_id"]

    # Edit the composition (red -> blue): the clip is a LIVE reference.
    layer_module._save_lumendoc(ctx, _solid_doc("#0000FF", 2.0))

    manifest = _export(ctx.project, tmp_path, "bridge-refresh")
    (record,) = manifest["comp_refreshed"]
    assert record["status"] == "refreshed"
    assert record["asset_id"] == asset_id
    assert record["old_hash"] != record["new_hash"]

    asset = _project_asset(ctx.project, asset_id)
    assert asset["source_path"] == record["source_path"] != old_path
    assert Path(asset["source_path"]).exists()
    assert (asset["metadata"]["comp_ref"]["doc_hash"]) == record["new_hash"]
    # Old cache file stays in place (append-only cache; undo can re-point to it).
    assert Path(old_path).exists()

    assert _dist(_frame_rgb_mean(manifest["export_path"], 0.5), _BLUE) < _COLOR_TOL

    # Second export with an unchanged doc: nothing left to refresh.
    manifest2 = _export(ctx.project, tmp_path, "bridge-fresh")
    assert manifest2["comp_refreshed"] == []


# ── gate 3: single-step undo removes clip AND asset ──────────────────────────


def test_single_step_undo_removes_clip_and_asset(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layer_module._save_lumendoc(ctx, _solid_doc("#FF0000", 2.0))
    result = _call("lumen_comp_to_timeline", {"t_in": 0.0, "t_out": 1.0}, ctx)
    assert len(_clips(ctx.project)) == 1
    assert _project_asset(ctx.project, result["asset_id"]) is not None

    ctx.project.undo(1)

    assert _clips(ctx.project) == []
    assert _project_asset(ctx.project, result["asset_id"]) is None
    # The rendered cache file survives undo — only the reference is undone.
    assert Path(result["path"]).exists()


# ── gate 4: shrunken comp fails the export with a typed error ────────────────


def test_shrunken_comp_fails_export_typed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layer_module._save_lumendoc(ctx, _solid_doc("#FF0000", 2.0))
    _call("lumen_comp_to_timeline", {"t_in": 0.5, "t_out": 1.5}, ctx)

    # The comp no longer covers t_out=1.5 — export must fail, NEVER silently
    # change clip.duration (overlap/duration invariants + pass-3 audio depend on it).
    layer_module._save_lumendoc(ctx, _solid_doc("#FF0000", 0.6))

    with pytest.raises(ProjectExportError) as excinfo:
        _export(ctx.project, tmp_path, "bridge-shrunk")
    assert excinfo.value.code == "comp_shrunk"


# ── gate 5: idempotent re-invocation reuses the cache ────────────────────────


def test_idempotent_reinvocation_reuses_cache(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layer_module._save_lumendoc(ctx, _solid_doc("#FF0000", 2.0))

    first = _call("lumen_comp_to_timeline", {"t_in": 0.5, "t_out": 1.5}, ctx)
    second = _call("lumen_comp_to_timeline", {"t_in": 0.5, "t_out": 1.5}, ctx)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["path"] == first["path"]

    renders_dir = ctx.project.store.renders_dir(ctx.project.project_id)
    assert len(list(renders_dir.glob("comp_*.mp4"))) == 1
    # The insertion itself still happens on every call.
    assert len(_clips(ctx.project)) == 2


# ── gate 6: window clamping + argument/window errors ─────────────────────────


def test_window_clamps_and_errors(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layer_module._save_lumendoc(ctx, _solid_doc("#FF0000", 2.0))

    # Window entirely past the comp end -> empty after clamping.
    result = _call("lumen_comp_to_timeline", {"t_in": 5.0, "t_out": 6.0}, ctx)
    assert result["applied"] is False
    assert result["error_code"] == "E_EMPTY_RANGE"

    # t_out beyond the end is clamped; the clip uses the clamped window.
    result = _call("lumen_comp_to_timeline", {"t_in": 1.0, "t_out": 9.0}, ctx)
    assert result["applied"] is True
    assert result["t_out"] == pytest.approx(2.0, abs=1e-6)
    assert result["duration"] == pytest.approx(1.0, abs=1e-6)

    no_project = ToolContext(
        session_id=f"noproj_{uuid.uuid4().hex[:8]}",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )
    result = _call("lumen_comp_to_timeline", {"t_in": 0.0, "t_out": 1.0}, no_project)
    assert result["applied"] is False
    assert result["error_code"] == "E_NO_PROJECT"


# ── gate 7 (Phase 2 cleanup): move/delete re-validate transition_after ────────


def _seed_adjacent(handle: ProjectHandle, ids: list[str]) -> None:
    """Butt-joined 2 s video clips on V1 (fake sources — patch layer only)."""
    ops: list[dict[str, Any]] = []
    for index, clip_id in enumerate(ids):
        ops.append({"op": "upsert_asset", "asset": {
            "id": f"asset_{clip_id}", "asset_id": f"asset_{clip_id}",
            "name": f"{clip_id}.mp4", "media_kind": "video",
            "source_path": f"/nonexistent/{clip_id}.mp4", "duration": 2.0}})
        ops.append({"op": "insert_clip", "track_id": "V1",
                    "at": {"time": 2.0 * index},
                    "data": {"clip": {
                        "id": clip_id, "asset_id": f"asset_{clip_id}",
                        "media_kind": "video", "duration": 2.0,
                        "source_in": 0.0, "source_out": 2.0}}})
    handle.apply_ops(ops, label="seed-adjacent")


def _transition_after(handle: ProjectHandle, clip_id: str) -> Any:
    for clip in _clips(handle):
        if str(clip.get("id")) == clip_id:
            return clip.get("transition_after")
    raise AssertionError(f"clip {clip_id} not found")


def test_move_clears_stale_transition(tmp_path: Path) -> None:
    handle = ProjectHandle.open(tmp_path / "projects", "mv", session_id="mv")
    _seed_adjacent(handle, ["cA", "cB"])
    handle.apply_ops([{"op": "add_transition", "clip_id": "cA",
                       "kind": "dissolve", "duration_sec": 0.5}], label="t")
    assert isinstance(_transition_after(handle, "cA"), dict)

    # Moving B away breaks the adjacency -> A's transition is stale -> cleared.
    handle.apply_ops([{"op": "move_clip", "clip_id": "cB", "start": 6.0}], label="mv")
    assert _transition_after(handle, "cA") is None


def test_delete_clears_stale_transition(tmp_path: Path) -> None:
    handle = ProjectHandle.open(tmp_path / "projects", "del", session_id="del")
    _seed_adjacent(handle, ["cA", "cB"])
    handle.apply_ops([{"op": "add_transition", "clip_id": "cA",
                       "kind": "fade", "duration_sec": 0.5}], label="t")

    handle.apply_ops([{"op": "delete_clip", "clip_id": "cB"}], label="del")
    assert _transition_after(handle, "cA") is None


def test_ripple_delete_keeps_positionally_valid_transition(tmp_path: Path) -> None:
    """Positional semantics: after ripple-delete of B, C slides into B's slot,
    A is butt-joined to C again, and A's transition stays — same adjacency the
    export's runtime re-checks would accept."""
    handle = ProjectHandle.open(tmp_path / "projects", "rip", session_id="rip")
    _seed_adjacent(handle, ["cA", "cB", "cC"])
    handle.apply_ops([{"op": "add_transition", "clip_id": "cA",
                       "kind": "dissolve", "duration_sec": 0.5}], label="t")

    handle.apply_ops([{"op": "delete_clip", "clip_id": "cB", "ripple": True}], label="del")
    assert isinstance(_transition_after(handle, "cA"), dict)
