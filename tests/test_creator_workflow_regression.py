"""Two-layer creator workflow regression: public fixture + external private input."""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from gemia.creator_workflow_regression import (
    CreatorWorkflowRegressionError,
    DEFAULT_PUBLIC_FIXTURE,
    INPUT_MANIFEST_SCHEMA,
    run_creator_workflow_regression,
    verify_creator_workflow_receipt,
)
from gemia.project_model import empty_project


def _video(
    path: Path,
    *,
    duration: float = 1.0,
    size: str = "96x54",
    fps: int = 10,
    pattern: str = "testsrc2",
) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"{pattern}=duration={duration}:size={size}:rate={fps}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def _write_project_state(path: Path, media: Path) -> None:
    project = empty_project(title="Project state regression input")
    project["project_id"] = "project_state_fixture"
    project["timeline"].update(
        {"width": 96, "height": 54, "fps": 10.0, "duration": 1.0}
    )
    project["render_settings"].update({"width": 96, "height": 54, "fps": 10.0})
    project["assets"] = [
        {
            "id": "state_asset",
            "asset_id": "state_asset",
            "name": "state-media",
            "media_kind": "video",
            "mime_type": "video/mp4",
            "source_path": str(media),
            "duration": 1.0,
            "metadata": {},
        }
    ]
    project["timeline"]["clips"] = [
        {
            "id": "state_clip",
            "asset_id": "state_asset",
            "track_id": "V1",
            "media_kind": "video",
            "name": "state-media",
            "start": 0.0,
            "duration": 1.0,
            "source_in": 0.0,
            "source_out": 1.0,
            "enabled": True,
            "effects": {},
        }
    ]
    path.write_text(json.dumps(project), encoding="utf-8")


def test_public_fixture_is_path_free_and_runtime_generated() -> None:
    payload = json.loads(DEFAULT_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    encoded = json.dumps(payload, ensure_ascii=False).lower()
    assert payload["schema"] == "lumeri.creator-workflow-regression.fixture.v1"
    assert "/users/" not in encoded
    assert "/volumes/" not in encoded
    assert "@" not in encoded
    assert "token" not in encoded
    assert "secret" not in encoded
    assert list(DEFAULT_PUBLIC_FIXTURE.parent.iterdir()) == [DEFAULT_PUBLIC_FIXTURE]


def test_public_runner_produces_advisory_bound_receipt_and_cache_metrics(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt = run_creator_workflow_regression(
        output_root=tmp_path / "artifacts",
        receipt_path=receipt_path,
    )

    assert receipt["status"] == "passed"
    assert receipt["input"]["kind"] == "public-generated-fixture"
    assert receipt["workflow_policy"]["enforcement"] == "advisory"
    assert (
        receipt["workflow_policy"]["regression_preview_before_optional_full_measurement"]
        is True
    )
    assert receipt["workflow_policy"]["product_export_behavior_unchanged"] is True
    assert receipt["workflow_policy"]["user_preview_required"] is False
    assert receipt["stages"]["preview_before"]["cache"]["segments_total"] == 2
    assert receipt["stages"]["preview_after"]["cache"]["hits"] == 2
    assert receipt["stages"]["preview_after"]["cache"]["hit_ratio"] == 1.0
    assert receipt["stages"]["full_export"]["status"] == "skipped"
    native = receipt["stages"]["lumenframe_native_review"]
    assert native["status"] == "completed"
    assert native["engine"] == "lumenframe-native-range"
    assert native["frame_count"] == 36
    assert native["resolution"] == {"width": 320, "height": 180}
    assert receipt["stages"]["inspect_after"]["recent_patch_count"] == 1
    assert verify_creator_workflow_receipt(receipt)
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt

    tampered = copy.deepcopy(receipt)
    tampered["input"]["duration_sec"] = 99.0
    assert not verify_creator_workflow_receipt(tampered)


def test_external_media_manifest_receipt_redacts_source_paths(tmp_path: Path) -> None:
    private_dir = tmp_path / "private-customer-material"
    private_dir.mkdir()
    media = private_dir / "secret-project-name.mp4"
    _video(media)
    manifest = private_dir / "review-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": INPUT_MANIFEST_SCHEMA,
                "source": {"kind": "media", "path": media.name},
                "expected_duration_sec": 1.0,
                "review_window": {"start_sec": 0.2, "duration_sec": 0.5},
                "review_time_sec": 0.4,
            }
        ),
        encoding="utf-8",
    )

    receipt = run_creator_workflow_regression(
        output_root=tmp_path / "external-output",
        manifest_path=manifest,
    )
    encoded = json.dumps(receipt, ensure_ascii=False)

    assert receipt["input"]["kind"] == "external-media-manifest"
    assert receipt["review_window"]["duration_sec"] == pytest.approx(0.5)
    assert str(tmp_path) not in encoded
    assert media.name not in encoded
    assert manifest.name not in encoded
    assert receipt["privacy"]["receipt_contains_paths"] is False
    assert verify_creator_workflow_receipt(receipt)


def test_lumenframe_document_runs_real_native_review_range(tmp_path: Path) -> None:
    media = tmp_path / "native-input.mp4"
    _video(media, duration=1.0, size="64x64", fps=10)
    document = tmp_path / "native.lumen.json"
    document.write_text(
        json.dumps(
            {
                "version": 1,
                "id": "private-doc-id",
                "title": "Private title must not enter receipt",
                "canvas": {"width": 64, "height": 64, "fps": 10.0, "background": "#000000"},
                "assets": [{"id": "clip_asset", "path": media.name}],
                "root": {
                    "id": "root",
                    "type": "composition",
                    "name": "Root",
                    "start": 0.0,
                    "duration": 1.0,
                    "source_in": 0.0,
                    "source_out": 0.0,
                    "speed": 1.0,
                    "lane": 0,
                    "visible": True,
                    "children": [
                        {
                            "id": "video_layer",
                            "type": "video",
                            "name": "Video",
                            "start": 0.0,
                            "duration": 1.0,
                            "source_in": 0.0,
                            "source_out": 1.0,
                            "speed": 1.0,
                            "lane": 0,
                            "visible": True,
                            "asset_id": "clip_asset",
                            "effects": [],
                            "keyframes": {},
                            "props": {},
                        }
                    ],
                },
                "selection": [],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_creator_workflow_regression(
        output_root=tmp_path / "native-output",
        lumenframe_document_path=document,
        review_start_sec=0.2,
        review_duration_sec=0.4,
    )
    native = receipt["stages"]["lumenframe_native_review"]
    encoded = json.dumps(receipt, ensure_ascii=False)

    assert receipt["input"]["kind"] == "external-lumenframe-document"
    assert native["status"] == "completed"
    assert native["engine"] == "lumenframe-native-range"
    assert native["frame_count"] == 4
    assert native["resolution"] == {"width": 64, "height": 64}
    assert len(native["sha256"]) == 64
    assert str(tmp_path) not in encoded
    assert "private-doc-id" not in encoded
    assert "Private title" not in encoded


def test_project_state_full_export_uses_existing_project_export_entrypoint(
    tmp_path: Path,
) -> None:
    media = tmp_path / "project-state.mp4"
    _video(media)
    state = tmp_path / "state.json"
    _write_project_state(state, media)
    receipt = run_creator_workflow_regression(
        output_root=tmp_path / "full-output",
        project_state_path=state,
        full_export=True,
        export_quality="draft",
    )
    export = receipt["stages"]["full_export"]
    assert receipt["stages"]["lumenframe_native_review"]["status"] == "not_applicable"
    assert export["status"] == "completed"
    assert export["engine"] == "project-export"
    assert export["duration_sec"] == pytest.approx(1.0, abs=0.25)
    assert len(export["sha256"]) == 64


def test_project_state_binding_changes_when_referenced_media_is_replaced(
    tmp_path: Path,
) -> None:
    media = tmp_path / "replace-in-place.mp4"
    _video(media, pattern="testsrc2")
    state = tmp_path / "state.json"
    _write_project_state(state, media)
    state_bytes = state.read_bytes()

    first = run_creator_workflow_regression(
        output_root=tmp_path / "binding-before",
        project_state_path=state,
    )
    _video(media, pattern="smptebars")
    assert state.read_bytes() == state_bytes
    second = run_creator_workflow_regression(
        output_root=tmp_path / "binding-after",
        project_state_path=state,
    )

    assert first["input"]["binding_sha256"] != second["input"]["binding_sha256"]
    assert str(media) not in json.dumps(first, ensure_ascii=False)
    assert str(media) not in json.dumps(second, ensure_ascii=False)


def test_runner_rejects_artifacts_inside_source_repository() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with pytest.raises(CreatorWorkflowRegressionError, match="outside the source repository"):
        run_creator_workflow_regression(output_root=repo_root / ".private-regression-output")
    with pytest.raises(CreatorWorkflowRegressionError, match="outside the source repository"):
        run_creator_workflow_regression(
            output_root=Path("/tmp") / "lumeri-private-regression-output",
            receipt_path=repo_root / ".private-regression-receipt.json",
        )
