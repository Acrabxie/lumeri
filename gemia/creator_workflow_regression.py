"""Advisory creator-workflow regression for public and private projects.

The runner deliberately reuses the product's existing ProjectStore, preview,
inspection, and export entrypoints.  It does not add a director state machine:
preview-first and explicit full export are regression recommendations only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gemia.compat import ffmpeg_path
from gemia.project_export import export_project
from gemia.project_inspect import inspect_project
from gemia.project_model import empty_project, normalize_project
from gemia.project_render import ffprobe_media, render_project_preview
from gemia.project_store import ProjectStore


RECEIPT_SCHEMA = "lumeri.creator-workflow-regression.receipt.v1"
INPUT_MANIFEST_SCHEMA = "lumeri.creator-workflow-regression.input.v1"
FIXTURE_SCHEMA = "lumeri.creator-workflow-regression.fixture.v1"
DEFAULT_PUBLIC_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "creator_workflow"
    / "public_short.json"
)


class CreatorWorkflowRegressionError(RuntimeError):
    """High-level regression failure safe to present at a command boundary."""


@dataclass(frozen=True)
class _RegressionInput:
    project: dict[str, Any]
    input_kind: str
    input_binding_sha256: str
    review_start_sec: float | None = None
    review_duration_sec: float | None = None
    review_time_sec: float | None = None
    expected_duration_sec: float | None = None
    lumenframe_document: dict[str, Any] | None = None


def run_creator_workflow_regression(
    *,
    output_root: str | Path,
    receipt_path: str | Path | None = None,
    fixture_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    project_state_path: str | Path | None = None,
    project_root: str | Path | None = None,
    project_id: str | None = None,
    lumenframe_document_path: str | Path | None = None,
    review_start_sec: float | None = None,
    review_duration_sec: float | None = None,
    full_export: bool = False,
    export_quality: str = "draft",
) -> dict[str, Any]:
    """Run preview-first regression and write a path-redacted bound receipt.

    Exactly one input mode is selected: the repository's generated public
    fixture (default), an external manifest, an external canonical state JSON,
    an existing ProjectStore root + project id, or an external LumenFrame
    document. External projects are cloned before any marker patch is applied.
    """
    output = Path(output_root).expanduser().resolve()
    receipt = (
        Path(receipt_path).expanduser().resolve()
        if receipt_path is not None
        else output / "creator-workflow-regression-receipt.json"
    )
    _guard_output_outside_repository(output)
    _guard_output_outside_repository(receipt)
    output.mkdir(parents=True, exist_ok=True)

    run_root = output / "creator-workflow-runs" / f"run-{uuid.uuid4().hex[:12]}"
    run_root.mkdir(parents=True, exist_ok=False)
    source = _resolve_input(
        run_root=run_root,
        fixture_path=fixture_path,
        manifest_path=manifest_path,
        project_state_path=project_state_path,
        project_root=project_root,
        project_id=project_id,
        lumenframe_document_path=lumenframe_document_path,
    )
    full_project = normalize_project(source.project)
    full_duration = _timeline_duration(full_project)
    if source.expected_duration_sec is not None:
        if abs(full_duration - source.expected_duration_sec) > 0.75:
            raise CreatorWorkflowRegressionError(
                "The supplied project duration does not match its regression manifest."
            )

    requested_start = review_start_sec
    if requested_start is None:
        requested_start = source.review_start_sec
    requested_duration = review_duration_sec
    if requested_duration is None:
        requested_duration = source.review_duration_sec
    window_start, window_duration = _review_window(
        full_project,
        start_sec=requested_start,
        duration_sec=requested_duration,
    )
    review_project = _slice_project(full_project, window_start, window_duration)

    full_store = ProjectStore(run_root / "full-store")
    full_store.create("creator_full", seed=full_project)
    review_store = ProjectStore(run_root / "review-store")
    review_store.create("creator_review", seed=review_project)

    before = _safe_inspection(inspect_project(review_store, "creator_review"))
    first_started = time.monotonic()
    first_preview = render_project_preview(
        review_store,
        "creator_review",
        output_root=output,
        label="creator-review-before",
    )
    first_elapsed = time.monotonic() - first_started

    marker_time = _review_marker_time(
        window_start=window_start,
        window_duration=window_duration,
        requested_time=source.review_time_sec,
        fps=float((review_project.get("timeline") or {}).get("fps") or 30.0),
    )
    patch_result = review_store.apply_patches(
        "creator_review",
        [
            {
                "version": 1,
                "ops": [
                    {
                        "op": "add_marker",
                        "marker_id": "creator_regression_review",
                        "time": marker_time,
                        "label": "review key action",
                    }
                ],
            }
        ],
        session_id="creator-regression",
        script_hash="advisory-review-marker",
    )

    second_started = time.monotonic()
    second_preview = render_project_preview(
        review_store,
        "creator_review",
        output_root=output,
        label="creator-review-after",
    )
    second_elapsed = time.monotonic() - second_started
    # The patch result is authoritative for this single regression mutation.
    # Avoid scanning directory metadata here: removable filesystems may create
    # AppleDouble sidecars alongside JSON history files, which are not product
    # project data and must not turn a successful review into a user-visible
    # infrastructure failure.
    after = _safe_inspection(inspect_project(review_store, "creator_review"))
    after["recent_patch_count"] = 1

    frame_path = run_root / "review-frame.png"
    _extract_review_frame(
        Path(str(second_preview.get("preview_path") or "")),
        frame_path,
        marker_time,
    )

    native_lumenframe_review: dict[str, Any] | None = None
    if source.lumenframe_document is not None:
        native_review_path = run_root / "lumenframe-native-review.mp4"
        native_started = time.monotonic()
        native_result = _render_lumenframe_window(
            source.lumenframe_document,
            native_review_path,
            start_sec=window_start,
            duration_sec=window_duration,
        )
        native_lumenframe_review = {
            "status": "completed",
            "engine": "lumenframe-native-range",
            "elapsed_sec": round(time.monotonic() - native_started, 6),
            **native_result,
        }

    export_summary: dict[str, Any]
    if full_export:
        export_started = time.monotonic()
        if source.lumenframe_document is not None:
            native_export_path = run_root / "lumenframe-native-full.mp4"
            native_export = _render_lumenframe_window(
                source.lumenframe_document,
                native_export_path,
                start_sec=0.0,
                duration_sec=full_duration,
            )
            export_summary = {
                "status": "completed",
                "engine": "lumenframe-native-full",
                "quality": "document-native",
                "elapsed_sec": round(time.monotonic() - export_started, 6),
                **native_export,
            }
        else:
            export_result = export_project(
                full_store,
                "creator_full",
                output_root=output,
                quality=export_quality,
                label="creator-regression-full",
            )
            export_summary = {
                "status": "completed",
                "engine": "project-export",
                "quality": export_quality,
                "elapsed_sec": round(time.monotonic() - export_started, 6),
                "duration_sec": round(float(export_result.get("duration") or 0.0), 6),
                "resolution": _safe_resolution(export_result.get("resolution")),
                "sha256": _sha256_file(Path(str(export_result.get("export_path") or ""))),
            }
    else:
        export_summary = {
            "status": "skipped",
            "reason": "optional full-length regression measurement was not requested",
        }

    receipt_payload: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "passed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "kind": source.input_kind,
            "binding_sha256": source.input_binding_sha256,
            "duration_sec": round(full_duration, 6),
            "asset_count": len(full_project.get("assets") or []),
            "clip_count": len((full_project.get("timeline") or {}).get("clips") or []),
        },
        "workflow_policy": {
            "enforcement": "advisory",
            "recommended_sequence": [
                "understand",
                "plan",
                "edit",
                "inspect",
                "revise",
                "export",
            ],
            "regression_preview_before_optional_full_measurement": True,
            "full_export_requires_explicit_runner_flag": True,
            "product_export_behavior_unchanged": True,
            "user_preview_required": False,
            "recoverable_failures_are_internal": True,
        },
        "review_window": {
            "start_sec": round(window_start, 6),
            "duration_sec": round(window_duration, 6),
            "marker_time_sec": round(marker_time, 6),
        },
        "stages": {
            "inspect_before": before,
            "preview_before": _safe_preview_metrics(first_preview, first_elapsed),
            "review_patch": {
                "kind": "review_marker",
                "patch_seq": int(patch_result.get("patch_seq_end") or 0),
            },
            "preview_after": _safe_preview_metrics(second_preview, second_elapsed),
            "inspect_after": after,
            "review_frame": {
                "sha256": _sha256_file(frame_path),
                "bytes": int(frame_path.stat().st_size),
            },
            "lumenframe_native_review": native_lumenframe_review or {
                "status": "not_applicable"
            },
            "full_export": export_summary,
        },
        "privacy": {
            "receipt_contains_paths": False,
            "source_material_copied_into_repository": False,
            "outputs_outside_repository": True,
        },
    }
    receipt_payload["receipt_binding_sha256"] = _json_sha256(receipt_payload)
    _write_json(receipt, receipt_payload)
    return receipt_payload


def verify_creator_workflow_receipt(receipt: dict[str, Any]) -> bool:
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        return False
    expected = str(receipt.get("receipt_binding_sha256") or "")
    payload = copy.deepcopy(receipt)
    payload.pop("receipt_binding_sha256", None)
    return len(expected) == 64 and _json_sha256(payload) == expected


def _resolve_input(
    *,
    run_root: Path,
    fixture_path: str | Path | None,
    manifest_path: str | Path | None,
    project_state_path: str | Path | None,
    project_root: str | Path | None,
    project_id: str | None,
    lumenframe_document_path: str | Path | None,
) -> _RegressionInput:
    explicit_modes = sum(
        int(value is not None)
        for value in (
            manifest_path,
            project_state_path,
            project_root,
            lumenframe_document_path,
        )
    )
    if explicit_modes > 1:
        raise CreatorWorkflowRegressionError("Choose exactly one regression input mode.")
    if fixture_path is not None and explicit_modes:
        raise CreatorWorkflowRegressionError("Choose exactly one regression input mode.")
    if project_id and project_root is None:
        raise CreatorWorkflowRegressionError("A project id requires a ProjectStore root.")
    if project_root is not None and not project_id:
        raise CreatorWorkflowRegressionError("A ProjectStore root requires a project id.")

    if manifest_path is not None:
        return _input_from_manifest(Path(manifest_path).expanduser().resolve())
    if project_state_path is not None:
        state_path = Path(project_state_path).expanduser().resolve()
        project = _read_json_object(state_path)
        project = normalize_project(project)
        return _RegressionInput(
            project=project,
            input_kind="external-project-state",
            input_binding_sha256=_project_state_binding_sha256(state_path, project),
        )
    if project_root is not None:
        root = Path(project_root).expanduser().resolve()
        state_path = root / str(project_id) / "state.json"
        if not state_path.is_file():
            raise CreatorWorkflowRegressionError("The external ProjectStore project is unavailable.")
        store = ProjectStore(root)
        project = store.load(str(project_id))
        return _RegressionInput(
            project=project,
            input_kind="external-project-store",
            input_binding_sha256=_project_state_binding_sha256(state_path, project),
        )
    if lumenframe_document_path is not None:
        document_path = Path(lumenframe_document_path).expanduser().resolve()
        project, asset_digests, document = _project_from_lumenframe_document(document_path)
        return _RegressionInput(
            project=project,
            input_kind="external-lumenframe-document",
            input_binding_sha256=_combined_sha256(
                _sha256_file(document_path),
                *asset_digests,
            ),
            expected_duration_sec=_timeline_duration(project),
            lumenframe_document=document,
        )

    spec_path = Path(fixture_path).expanduser().resolve() if fixture_path else DEFAULT_PUBLIC_FIXTURE
    spec = _read_json_object(spec_path)
    project = _generate_public_fixture(spec, run_root / "fixture-media")
    return _RegressionInput(
        project=project,
        input_kind="public-generated-fixture",
        input_binding_sha256=_sha256_file(spec_path),
        review_time_sec=float(spec.get("review_time_sec") or 0.0),
        lumenframe_document=_lumenframe_document_from_project(project),
    )


def _input_from_manifest(manifest_path: Path) -> _RegressionInput:
    manifest = _read_json_object(manifest_path)
    if manifest.get("schema") != INPUT_MANIFEST_SCHEMA:
        raise CreatorWorkflowRegressionError("The external regression manifest schema is unsupported.")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise CreatorWorkflowRegressionError("The external regression manifest has no source.")
    kind = str(source.get("kind") or "")
    base = manifest_path.parent
    payload_digest: str
    if kind == "media":
        media_path = _resolve_manifest_path(base, source.get("path"))
        payload_digest = _sha256_file(media_path)
        project = _project_from_media(media_path)
        input_kind = "external-media-manifest"
    elif kind == "project_state":
        state_path = _resolve_manifest_path(base, source.get("path"))
        project = normalize_project(_read_json_object(state_path))
        payload_digest = _project_state_binding_sha256(state_path, project)
        input_kind = "external-project-manifest"
    elif kind == "project_store":
        root = _resolve_manifest_path(base, source.get("root"))
        project_id = str(source.get("project_id") or "")
        state_path = root / project_id / "state.json"
        if not state_path.is_file():
            raise CreatorWorkflowRegressionError("The manifest ProjectStore project is unavailable.")
        project = ProjectStore(root).load(project_id)
        payload_digest = _project_state_binding_sha256(state_path, project)
        input_kind = "external-project-store-manifest"
    elif kind == "lumenframe_document":
        document_path = _resolve_manifest_path(base, source.get("path"))
        project, asset_digests, document = _project_from_lumenframe_document(document_path)
        payload_digest = _combined_sha256(_sha256_file(document_path), *asset_digests)
        input_kind = "external-lumenframe-manifest"
    else:
        raise CreatorWorkflowRegressionError("The external regression source kind is unsupported.")

    review = manifest.get("review_window") if isinstance(manifest.get("review_window"), dict) else {}
    binding = _combined_sha256(_sha256_file(manifest_path), payload_digest)
    return _RegressionInput(
        project=project,
        input_kind=input_kind,
        input_binding_sha256=binding,
        review_start_sec=_optional_finite(review.get("start_sec")),
        review_duration_sec=_optional_finite(review.get("duration_sec")),
        review_time_sec=_optional_finite(manifest.get("review_time_sec")),
        expected_duration_sec=_optional_finite(manifest.get("expected_duration_sec")),
        lumenframe_document=document if kind == "lumenframe_document" else None,
    )


def _generate_public_fixture(spec: dict[str, Any], media_dir: Path) -> dict[str, Any]:
    if spec.get("schema") != FIXTURE_SCHEMA:
        raise CreatorWorkflowRegressionError("The public fixture schema is unsupported.")
    canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    width = max(16, int(canvas.get("width") or 320))
    height = max(16, int(canvas.get("height") or 180))
    fps = max(1.0, float(canvas.get("fps") or 12.0))
    segments = spec.get("segments") if isinstance(spec.get("segments"), list) else []
    if not segments:
        raise CreatorWorkflowRegressionError("The public fixture has no segments.")
    media_dir.mkdir(parents=True, exist_ok=True)

    project = empty_project(title="Public Creator Workflow Fixture")
    project["project_id"] = "public_short_v1"
    project["timeline"]["width"] = width
    project["timeline"]["height"] = height
    project["timeline"]["fps"] = fps
    project["render_settings"].update({"width": width, "height": height, "fps": fps})
    cursor = 0.0
    assets: list[dict[str, Any]] = []
    clips: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        media_kind = str(segment.get("media_kind") or "")
        duration = max(0.1, float(segment.get("duration_sec") or 1.0))
        safe_id = "".join(ch if ch.isalnum() else "_" for ch in str(segment.get("id") or index))
        asset_id = f"fixture_asset_{safe_id}"
        if media_kind == "video":
            source = media_dir / f"{index:02d}-{safe_id}.mp4"
            _generate_fixture_video(source, width=width, height=height, fps=fps, duration=duration)
            mime_type = "video/mp4"
        elif media_kind == "image":
            source = media_dir / f"{index:02d}-{safe_id}.png"
            _generate_fixture_image(
                source,
                width=width,
                height=height,
                background=str(segment.get("background") or "#080b10"),
                accent=str(segment.get("accent") or "#5fc6de"),
            )
            mime_type = "image/png"
        else:
            raise CreatorWorkflowRegressionError("The public fixture media kind is unsupported.")
        assets.append(
            {
                "id": asset_id,
                "asset_id": asset_id,
                "name": f"fixture-{safe_id}",
                "media_kind": media_kind,
                "mime_type": mime_type,
                "source_path": str(source),
                "duration": duration,
                "metadata": {"fixture": True},
            }
        )
        clips.append(
            {
                "id": f"fixture_clip_{safe_id}",
                "asset_id": asset_id,
                "track_id": "V1",
                "media_kind": media_kind,
                "name": f"fixture-{safe_id}",
                "start": cursor,
                "duration": duration,
                "source_in": 0.0,
                "source_out": duration,
                "enabled": True,
                "effects": {},
            }
        )
        cursor += duration
    project["assets"] = assets
    project["timeline"]["clips"] = clips
    project["timeline"]["duration"] = round(cursor, 6)
    project["metadata"] = {"generator": "lumeri-public-regression-fixture"}
    return normalize_project(project)


def _lumenframe_document_from_project(project: dict[str, Any]) -> dict[str, Any]:
    """Build an equivalent runtime-only LumenFrame document for the fixture."""
    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    assets = {
        str(asset.get("id") or asset.get("asset_id") or ""): asset
        for asset in project.get("assets") or []
        if isinstance(asset, dict)
    }
    children: list[dict[str, Any]] = []
    lumen_assets: list[dict[str, Any]] = []
    for asset_id, asset in assets.items():
        source_path = str(asset.get("source_path") or "")
        if asset_id and source_path:
            lumen_assets.append({"id": asset_id, "path": source_path})
    for index, clip in enumerate(timeline.get("clips") or []):
        if not isinstance(clip, dict) or not bool(clip.get("enabled", True)):
            continue
        media_kind = str(clip.get("media_kind") or "")
        asset_id = str(clip.get("asset_id") or "")
        if media_kind not in {"video", "image"} or asset_id not in assets:
            continue
        source_in = max(0.0, float(clip.get("source_in") or 0.0))
        duration = max(0.1, float(clip.get("duration") or 0.1))
        children.append(
            {
                "id": str(clip.get("id") or f"fixture_layer_{index}"),
                "type": media_kind,
                "name": str(clip.get("name") or asset_id),
                "start": max(0.0, float(clip.get("start") or 0.0)),
                "duration": duration,
                "source_in": source_in,
                "source_out": source_in + duration,
                "speed": 1.0,
                "lane": 0,
                "visible": True,
                "asset_id": asset_id,
                "effects": [],
                "keyframes": {},
                "props": {},
            }
        )
    duration = _timeline_duration(project)
    return {
        "version": 1,
        "id": "public_creator_workflow_runtime_fixture",
        "title": "Public Creator Workflow Runtime Fixture",
        "canvas": {
            "width": max(16, int(timeline.get("width") or 320)),
            "height": max(16, int(timeline.get("height") or 180)),
            "fps": max(1.0, float(timeline.get("fps") or 12.0)),
            "background": "#000000",
        },
        "assets": lumen_assets,
        "root": {
            "id": "root",
            "type": "composition",
            "name": "Root",
            "start": 0.0,
            "duration": duration,
            "source_in": 0.0,
            "source_out": duration,
            "speed": 1.0,
            "lane": 0,
            "visible": True,
            "children": children,
        },
        "selection": [],
    }


def _generate_fixture_video(
    output: Path, *, width: int, height: int, fps: float, duration: float
) -> None:
    _run_fixture_ffmpeg(
        [
            ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=duration={duration:.6f}:size={width}x{height}:rate={fps:.6f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        output,
    )


def _generate_fixture_image(
    output: Path, *, width: int, height: int, background: str, accent: str
) -> None:
    bg = background.replace("#", "0x")
    fg = accent.replace("#", "0x")
    _run_fixture_ffmpeg(
        [
            ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={bg}:s={width}x{height}:d=1",
            "-vf",
            (
                f"drawbox=x={width * 0.12:.2f}:y={height * 0.24:.2f}:"
                f"w={width * 0.46:.2f}:h={height * 0.13:.2f}:color={fg}:t=fill,"
                f"drawbox=x={width * 0.68:.2f}:y={height * 0.52:.2f}:"
                f"w={height * 0.18:.2f}:h={height * 0.18:.2f}:color={fg}:t=fill"
            ),
            "-frames:v",
            "1",
            str(output),
        ],
        output,
    )


def _run_fixture_ffmpeg(cmd: list[str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        raise CreatorWorkflowRegressionError("The public regression fixture could not be generated.")


def _project_from_media(media_path: Path) -> dict[str, Any]:
    if not media_path.is_file():
        raise CreatorWorkflowRegressionError("The external media source is unavailable.")
    probe = ffprobe_media(media_path)
    duration = _probe_duration(probe)
    resolution = _probe_resolution(probe)
    fps = _probe_fps(probe)
    if duration <= 0:
        raise CreatorWorkflowRegressionError("The external media duration could not be verified.")
    project = empty_project(title="External Creator Workflow Input")
    project["project_id"] = "external_media_input"
    project["timeline"].update(
        {
            "width": resolution["width"],
            "height": resolution["height"],
            "fps": fps,
            "duration": duration,
        }
    )
    project["render_settings"].update(
        {"width": resolution["width"], "height": resolution["height"], "fps": fps}
    )
    project["assets"] = [
        {
            "id": "external_media",
            "asset_id": "external_media",
            "name": "external-media",
            "media_kind": "video",
            "mime_type": "video/mp4",
            "source_path": str(media_path),
            "duration": duration,
            "metadata": {},
        }
    ]
    project["timeline"]["clips"] = [
        {
            "id": "external_media_clip",
            "asset_id": "external_media",
            "track_id": "V1",
            "media_kind": "video",
            "name": "external-media",
            "start": 0.0,
            "duration": duration,
            "source_in": 0.0,
            "source_out": duration,
            "enabled": True,
            "effects": {},
        }
    ]
    return normalize_project(project)


def _project_from_lumenframe_document(
    document_path: Path,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Adapt a materialized LumenFrame document to a transient ProjectStore.

    Only existing video/image asset layers are projected.  The source document
    and media stay in place; the regression store receives references and is
    created under the caller's external output root.
    """
    document = copy.deepcopy(_read_json_object(document_path))
    canvas = document.get("canvas") if isinstance(document.get("canvas"), dict) else {}
    root = document.get("root") if isinstance(document.get("root"), dict) else {}
    raw_assets = document.get("assets") if isinstance(document.get("assets"), list) else []
    assets_by_id: dict[str, dict[str, Any]] = {}
    asset_digests: list[str] = []
    for raw in raw_assets:
        if not isinstance(raw, dict):
            continue
        asset_id = str(raw.get("id") or "")
        raw_path = str(raw.get("path") or "")
        if not asset_id or not raw_path:
            continue
        path = Path(raw_path).expanduser()
        path = path.resolve() if path.is_absolute() else (document_path.parent / path).resolve()
        if not path.is_file():
            raise CreatorWorkflowRegressionError(
                "A LumenFrame regression asset is unavailable."
            )
        assets_by_id[asset_id] = {"path": path, "raw": raw}
        # Native compilation receives the same deep-copied document, with all
        # asset references made independent of the runner's current directory.
        raw["path"] = str(path)
        asset_digests.append(_sha256_file(path))

    width = max(16, int(canvas.get("width") or 1920))
    height = max(16, int(canvas.get("height") or 1080))
    fps = max(1.0, float(canvas.get("fps") or 30.0))
    project = empty_project(title="External LumenFrame Regression Input")
    project["project_id"] = "external_lumenframe_input"
    project["timeline"].update({"width": width, "height": height, "fps": fps})
    project["render_settings"].update({"width": width, "height": height, "fps": fps})

    project_assets: list[dict[str, Any]] = []
    clips: list[dict[str, Any]] = []
    used_assets: set[str] = set()
    for layer in root.get("children") or []:
        if not isinstance(layer, dict) or not bool(layer.get("visible", True)):
            continue
        layer_type = str(layer.get("type") or "")
        if layer_type not in {"video", "image"}:
            continue
        asset_id = str(layer.get("asset_id") or "")
        asset_info = assets_by_id.get(asset_id)
        if asset_info is None:
            continue
        duration = max(0.1, float(layer.get("duration") or 0.1))
        source_in = max(0.0, float(layer.get("source_in") or 0.0))
        clips.append(
            {
                "id": str(layer.get("id") or f"layer_{len(clips)}"),
                "asset_id": asset_id,
                "track_id": "V1",
                "media_kind": layer_type,
                "name": str(layer.get("name") or asset_id),
                "start": max(0.0, float(layer.get("start") or 0.0)),
                "duration": duration,
                "source_in": source_in,
                "source_out": source_in + duration,
                "enabled": True,
                "effects": {},
            }
        )
        if asset_id not in used_assets:
            path = asset_info["path"]
            project_assets.append(
                {
                    "id": asset_id,
                    "asset_id": asset_id,
                    "name": str(layer.get("name") or asset_id),
                    "media_kind": layer_type,
                    "mime_type": "video/mp4" if layer_type == "video" else "image/png",
                    "source_path": str(path),
                    "duration": duration,
                    "metadata": {"lumenframe_reference": True},
                }
            )
            used_assets.add(asset_id)
    if not clips:
        raise CreatorWorkflowRegressionError(
            "The LumenFrame document has no materialized video or image layers."
        )
    project["assets"] = project_assets
    project["timeline"]["clips"] = clips
    project["timeline"]["duration"] = max(
        float(root.get("duration") or 0.0),
        max(float(clip["start"]) + float(clip["duration"]) for clip in clips),
    )
    project["metadata"] = {"generator": "lumenframe-regression-adapter"}
    return normalize_project(project), sorted(asset_digests), document


def _review_window(
    project: dict[str, Any], *, start_sec: float | None, duration_sec: float | None
) -> tuple[float, float]:
    total = _timeline_duration(project)
    if total <= 0:
        raise CreatorWorkflowRegressionError("The project has no reviewable duration.")
    start = max(0.0, float(start_sec or 0.0))
    if start >= total:
        start = max(0.0, total - min(total, 8.0))
    duration = float(duration_sec) if duration_sec is not None else min(8.0, total - start)
    duration = max(0.1, min(duration, total - start))
    candidate = _slice_project(project, start, duration)
    if _has_renderable_video(candidate):
        return round(start, 6), round(duration, 6)

    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    for clip in timeline.get("clips") or []:
        if not isinstance(clip, dict) or not bool(clip.get("enabled", True)):
            continue
        if str(clip.get("media_kind") or "video") not in {"video", "image"}:
            continue
        clip_start = max(0.0, float(clip.get("start") or 0.0))
        clip_duration = max(0.1, float(clip.get("duration") or 0.1))
        return round(clip_start, 6), round(min(8.0, clip_duration), 6)
    raise CreatorWorkflowRegressionError("The project has no reviewable video clips.")


def _slice_project(project: dict[str, Any], start: float, duration: float) -> dict[str, Any]:
    sliced = copy.deepcopy(project)
    timeline = sliced.get("timeline") if isinstance(sliced.get("timeline"), dict) else {}
    end = start + duration
    clips: list[dict[str, Any]] = []
    for raw in timeline.get("clips") or []:
        if not isinstance(raw, dict):
            continue
        clip_start = float(raw.get("start") or 0.0)
        clip_duration = max(0.0, float(raw.get("duration") or 0.0))
        clip_end = clip_start + clip_duration
        overlap_start = max(start, clip_start)
        overlap_end = min(end, clip_end)
        if overlap_end <= overlap_start + 1e-6:
            continue
        clip = copy.deepcopy(raw)
        offset = overlap_start - clip_start
        clipped_duration = overlap_end - overlap_start
        source_in = float(clip.get("source_in") or 0.0) + offset
        clip["start"] = round(overlap_start - start, 6)
        clip["duration"] = round(clipped_duration, 6)
        clip["source_in"] = round(source_in, 6)
        clip["source_out"] = round(source_in + clipped_duration, 6)
        clips.append(clip)
    markers: list[dict[str, Any]] = []
    for marker in timeline.get("markers") or []:
        if not isinstance(marker, dict):
            continue
        at = float(marker.get("time") or 0.0)
        if start <= at <= end:
            shifted = copy.deepcopy(marker)
            shifted["time"] = round(at - start, 6)
            markers.append(shifted)
    timeline["clips"] = clips
    timeline["markers"] = markers
    timeline["duration"] = round(duration, 6)
    sliced["timeline"] = timeline
    return normalize_project(sliced)


def _has_renderable_video(project: dict[str, Any]) -> bool:
    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    video_tracks = {
        str(track.get("id") or "")
        for track in timeline.get("tracks") or []
        if isinstance(track, dict) and str(track.get("kind") or "") == "video"
    }
    return any(
        isinstance(clip, dict)
        and bool(clip.get("enabled", True))
        and str(clip.get("track_id") or "") in video_tracks
        and str(clip.get("media_kind") or "video") in {"video", "image"}
        for clip in timeline.get("clips") or []
    )


def _render_lumenframe_window(
    document: dict[str, Any],
    output: Path,
    *,
    start_sec: float,
    duration_sec: float,
) -> dict[str, Any]:
    """Render a real LumenFrame range through compile + default resolver."""
    try:
        from lumenframe import timebase
        from lumenframe.compile import compile_to_layer_stack
        from lumenframe.resolve import default_resolver

        stack = compile_to_layer_stack(
            document,
            resolver=default_resolver,
            strict=False,
        )
        start_frame = min(
            max(0, int(timebase.to_frame(float(start_sec), float(stack.fps)))),
            int(stack.total_frames),
        )
        stop_frame = min(
            max(
                start_frame,
                int(
                    timebase.to_frame(
                        float(start_sec) + float(duration_sec),
                        float(stack.fps),
                    )
                ),
            ),
            int(stack.total_frames),
        )
        if stop_frame <= start_frame:
            raise CreatorWorkflowRegressionError(
                "The LumenFrame review window contains no frames."
            )
        stack.render_to_video(
            output,
            start_frame=start_frame,
            end_frame=stop_frame,
            step=1,
        )
        probe = ffprobe_media(output)
    except CreatorWorkflowRegressionError:
        raise
    except Exception as exc:
        raise CreatorWorkflowRegressionError(
            "The LumenFrame regression render could not be completed."
        ) from exc
    return {
        "frame_start": start_frame,
        "frame_stop": stop_frame,
        "frame_count": stop_frame - start_frame,
        "duration_sec": round(_probe_duration(probe), 6),
        "resolution": _probe_resolution(probe),
        "sha256": _sha256_file(output),
    }


def _review_marker_time(
    *, window_start: float, window_duration: float, requested_time: float | None, fps: float
) -> float:
    if requested_time is None:
        local = window_duration / 2.0
    else:
        local = float(requested_time) - window_start
    epsilon = max(0.001, 0.5 / max(fps, 1.0))
    return round(max(0.0, min(local, max(0.0, window_duration - epsilon))), 6)


def _safe_inspection(summary: dict[str, Any]) -> dict[str, Any]:
    timeline = summary.get("timeline") if isinstance(summary.get("timeline"), dict) else {}
    return {
        "patch_seq": int(summary.get("patch_seq") or 0),
        "duration_sec": round(float(timeline.get("duration") or 0.0), 6),
        "fps": round(float(timeline.get("fps") or 0.0), 6),
        "resolution": {
            "width": int(timeline.get("width") or 0),
            "height": int(timeline.get("height") or 0),
        },
        "clip_count": int(timeline.get("clip_count") or 0),
        "asset_count": int(summary.get("asset_count") or 0),
        "recent_patch_count": len(summary.get("recent_patches") or []),
    }


def _safe_preview_metrics(manifest: dict[str, Any], elapsed: float) -> dict[str, Any]:
    preview_path = Path(str(manifest.get("preview_path") or ""))
    cache = manifest.get("segment_cache") if isinstance(manifest.get("segment_cache"), dict) else {}
    return {
        "status": "completed",
        "elapsed_sec": round(elapsed, 6),
        "duration_sec": round(float(manifest.get("duration") or 0.0), 6),
        "resolution": _safe_resolution(manifest.get("resolution")),
        "sha256": _sha256_file(preview_path),
        "cache": {
            "segments_total": int(cache.get("segments_total") or 0),
            "hits": int(cache.get("hits") or 0),
            "misses": int(cache.get("misses") or 0),
            "rebuilds": int(cache.get("rebuilds") or 0),
            "bypassed": int(cache.get("bypassed") or 0),
            "hit_ratio": round(float(cache.get("hit_ratio") or 0.0), 6),
        },
    }


def _safe_resolution(value: Any) -> dict[str, int]:
    value = value if isinstance(value, dict) else {}
    return {"width": int(value.get("width") or 0), "height": int(value.get("height") or 0)}


def _extract_review_frame(video_path: Path, output: Path, at_sec: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{at_sec:.6f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        raise CreatorWorkflowRegressionError("The review frame could not be verified.")


def _timeline_duration(project: dict[str, Any]) -> float:
    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    try:
        return max(0.0, float(timeline.get("duration") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _probe_duration(probe: dict[str, Any]) -> float:
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    try:
        return max(0.0, float(fmt.get("duration") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _probe_resolution(probe: dict[str, Any]) -> dict[str, int]:
    for stream in probe.get("streams") or []:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if width > 0 and height > 0:
                return {"width": width, "height": height}
    raise CreatorWorkflowRegressionError("The external media resolution could not be verified.")


def _probe_fps(probe: dict[str, Any]) -> float:
    for stream in probe.get("streams") or []:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "")
        try:
            if "/" in raw:
                numerator, denominator = raw.split("/", 1)
                value = float(numerator) / float(denominator)
            else:
                value = float(raw)
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return 30.0


def _resolve_manifest_path(base: Path, value: Any) -> Path:
    raw = str(value or "")
    if not raw:
        raise CreatorWorkflowRegressionError("The external regression source path is missing.")
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CreatorWorkflowRegressionError("The regression input could not be read.") from exc
    if not isinstance(value, dict):
        raise CreatorWorkflowRegressionError("The regression input must be a JSON object.")
    return value


def _guard_output_outside_repository(path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    try:
        path.resolve().relative_to(repo)
    except ValueError:
        return
    raise CreatorWorkflowRegressionError(
        "Regression outputs must stay outside the source repository."
    )


def _optional_finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _project_state_binding_sha256(state_path: Path, project: dict[str, Any]) -> str:
    """Bind canonical state bytes plus every readable referenced asset.

    Asset digests are sorted so project asset ordering does not make the
    binding nondeterministic. Source paths are never included in the binding or
    in errors returned by this command boundary.
    """
    digests: list[str] = []
    base = state_path.parent
    for asset in project.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        raw_path = str(asset.get("source_path") or "")
        if not raw_path:
            continue
        try:
            path = Path(raw_path).expanduser()
            path = path.resolve() if path.is_absolute() else (base / path).resolve()
            if not path.is_file():
                continue
            digests.append(_sha256_file(path))
        except OSError as exc:
            raise CreatorWorkflowRegressionError(
                "A project asset could not be verified."
            ) from exc
    return _combined_sha256(_sha256_file(state_path), *sorted(digests))


def _combined_sha256(*digests: str) -> str:
    payload = "\n".join(str(value) for value in digests).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CreatorWorkflowRegressionError("A regression artifact could not be verified.") from exc
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


__all__ = [
    "CreatorWorkflowRegressionError",
    "DEFAULT_PUBLIC_FIXTURE",
    "FIXTURE_SCHEMA",
    "INPUT_MANIFEST_SCHEMA",
    "RECEIPT_SCHEMA",
    "run_creator_workflow_regression",
    "verify_creator_workflow_receipt",
]
