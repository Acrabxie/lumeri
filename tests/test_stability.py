from __future__ import annotations

from gemia.plan_contract import PlanContractError
from gemia.stability import error_envelope, error_event


def test_plan_contract_error_gets_human_firewall_message() -> None:
    envelope = error_envelope(
        PlanContractError("规划调用了本轮没有激活的能力。", detail="inactive primitive: gemia.video.blender_link.render_3d_scene"),
        context="test.plan_contract",
    )

    assert envelope["error_code"] == "E_PLAN_CONTRACT"
    assert "执行契约" in envelope["user_message"]
    assert "未激活能力" in envelope["user_message"]
    assert "重新规划" in envelope["next_action"]
    assert envelope["debug_id"].startswith("dbg_")
    assert "inactive primitive" in envelope["technical_detail"]


def test_plan_contract_error_event_uses_gemini_voice() -> None:
    event = error_event(
        PlanContractError("规划里还有未解析的模板占位符。", detail="placeholder={duration}"),
        context="test.plan_contract",
    )

    assert event["phase"] == "error"
    assert event["voice"] == "gemini"
    assert event["status"] == "failed"
    assert event["error_code"] == "E_PLAN_CONTRACT"
    assert "执行契约" in event["body"]
