"""Money-leak audit tests for google_genai_client.

No network. Monkeypatches GoogleGenAIClient._post_json so the audit
wrapper (submitted/completed/failed) runs for real against a temp JSONL
file pointed at by GEMIA_V3_API_AUDIT.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from gemia.ai import google_genai_client as gc
from gemia.ai.google_genai_client import (
    GoogleGenAIClient,
    VertexAPIError,
    find_suspected_leaks,
    read_api_calls,
)
from gemia.budget_guard import tool_cost_usd


_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


def _ok_response() -> dict:
    return {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": "image/png", "data": _TINY_PNG_B64}}
                    ]
                },
            }
        ],
        "usageMetadata": {"totalTokenCount": 100},
    }


def _client() -> GoogleGenAIClient:
    client = GoogleGenAIClient.__new__(GoogleGenAIClient)
    client.project = "test-project"
    client.location = "global"
    client.api_version = "v1beta1"
    client.base_url = "https://example.invalid/v1beta1/projects/test/locations/global/publishers/google/models"
    client.proxy = None
    client.timeout_sec = 60.0
    return client


def test_audit_submitted_then_completed_on_success(monkeypatch, tmp_path: Path) -> None:
    audit = tmp_path / "api-calls.jsonl"
    monkeypatch.setenv("GEMIA_V3_API_AUDIT", str(audit))

    async def fake_post(self, path, body):
        return _ok_response()

    monkeypatch.setattr(GoogleGenAIClient, "_post_json", fake_post)

    result = asyncio.run(
        _client().generate_image(
            prompt="a red apple",
            estimated_cost_usd=0.101,
            asset_id="img_001",
        )
    )
    assert result["request_id"]
    assert result["image_bytes"]  # decoded

    records = read_api_calls(audit)
    assert len(records) == 2
    sub, comp = records
    assert sub["status"] == "submitted"
    assert sub["verb"] == "generate_image"
    assert sub["provider"] == "vertex"
    assert sub["estimated_cost_usd"] == pytest.approx(0.101)
    assert comp["status"] == "completed"
    assert comp["actual_asset_id"] == "img_001"
    assert comp["size_bytes"] > 0
    # same request_id links the pair
    assert sub["request_id"] == comp["request_id"] == result["request_id"]
    # no leak
    assert find_suspected_leaks(audit) == []


def test_audit_submitted_then_failed_on_transport_error(monkeypatch, tmp_path: Path) -> None:
    audit = tmp_path / "api-calls.jsonl"
    monkeypatch.setenv("GEMIA_V3_API_AUDIT", str(audit))

    async def boom(self, path, body):
        raise VertexAPIError("Vertex HTTP 0 SSL EOF", status=None, body_tail="")

    monkeypatch.setattr(GoogleGenAIClient, "_post_json", boom)

    with pytest.raises(VertexAPIError, match="SSL EOF"):
        asyncio.run(
            _client().generate_image(prompt="x", estimated_cost_usd=0.101, asset_id="img_001")
        )

    records = read_api_calls(audit)
    assert [r["status"] for r in records] == ["submitted", "failed"]
    assert "SSL EOF" in records[1]["error"]

    # The failed call IS a suspected leak: submitted + failed, no completed.
    leaks = find_suspected_leaks(audit)
    assert len(leaks) == 1
    assert leaks[0]["request_id"] == records[0]["request_id"]


def test_find_suspected_leaks_flags_submitted_only(tmp_path: Path) -> None:
    """A process that died after submit (no completed/failed) is a suspect."""
    audit = tmp_path / "api-calls.jsonl"
    audit.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"ts": "t1", "request_id": "good", "status": "submitted"},
                {"ts": "t2", "request_id": "good", "status": "completed", "actual_asset_id": "img_001"},
                {"ts": "t3", "request_id": "orphan", "status": "submitted"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    leaks = find_suspected_leaks(audit)
    assert len(leaks) == 1
    assert leaks[0]["request_id"] == "orphan"


def test_read_api_calls_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_api_calls(tmp_path / "nope.jsonl") == []


def test_read_api_calls_skips_corrupt_lines(tmp_path: Path) -> None:
    audit = tmp_path / "api-calls.jsonl"
    audit.write_text(
        '{"request_id": "a", "status": "submitted"}\n'
        "not json at all\n"
        '{"request_id": "a", "status": "completed"}\n',
        encoding="utf-8",
    )
    records = read_api_calls(audit)
    assert len(records) == 2
    assert find_suspected_leaks(audit) == []


def test_budget_tool_cost_usd_matches_table() -> None:
    assert tool_cost_usd("generate_image") == pytest.approx(0.101)
    assert tool_cost_usd("edit_video") == 0.0
    assert tool_cost_usd("nonexistent_verb") == 0.0


def test_budget_guard_failed_call_does_not_charge_usd() -> None:
    """Mirror the agent_loop change: a failed dispatch commits usd=0."""
    from gemia.budget_guard import BudgetGuard

    guard = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    # success path (agent loop commits estimated usd)
    guard.commit("generate_image", actual_seconds=2.0)
    assert guard.spent_usd == pytest.approx(0.101)
    # failure path (agent loop commits usd=0)
    guard.commit("generate_image", actual_usd=0.0, actual_seconds=2.0)
    assert guard.spent_usd == pytest.approx(0.101)  # unchanged by the failure
