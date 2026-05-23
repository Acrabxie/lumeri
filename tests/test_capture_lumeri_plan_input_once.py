from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "capture_lumeri_plan_input_once.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("capture_lumeri_plan_input_once", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_record_matches_current_skill_payload_shape(tmp_path) -> None:
    script = _load_script()

    record = script.build_capture_record(
        request="加转场并做冷色调色",
        input_path="/tmp/source.mp4",
        output_path="/tmp/out.mp4",
        answers=None,
        project_state={
            "clips": [
                {
                    "id": "clip1",
                    "mediaKind": "video",
                    "serverPath": "/tmp/source.mp4",
                    "thumbnailStrip": ["x"] * 20,
                    "waveformPeaks": [0.1] * 20,
                    "summary": {
                        "duration": 8,
                        "mood": "clean",
                        "key_frame": "00:02",
                        "suggested_use": "main",
                    },
                }
            ]
        },
        model="google/gemini-3.1-pro-preview",
    )
    txt_path = script.write_capture(record, out_dir=tmp_path, also_json=True)

    assert txt_path.exists()
    assert (tmp_path / "latest-capture.txt").exists()
    assert record["dry_run"] is True
    assert record["request_meta"]["selected_skills"] == ["color-grade", "transition"]
    assert record["request_body"]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert record["request_body"]["messages"][1]["role"] == "system"
    assert record["request_body"]["messages"][2]["role"] == "user"
    assert "{duration|" not in record["request_body"]["messages"][1]["content"]
    assert "```json" in record["request_body"]["messages"][1]["content"]
    assert "clarification_policy" in record["request_body"]["messages"][2]["content"]
    assert "default_first" in record["request_body"]["messages"][2]["content"]
    assert "thumbnailStrip" not in record["request_body"]["messages"][2]["content"]
    assert "waveformPeaks" not in record["request_body"]["messages"][2]["content"]
