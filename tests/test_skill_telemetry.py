from __future__ import annotations

from datetime import datetime, timezone

from gemia.ai.skill_telemetry import (
    RouteEvent,
    format_skill_stats,
    record_route_event,
    skill_stats,
    update_final_plan_steps,
)


def test_skill_telemetry_insert_update_and_stats(tmp_path) -> None:
    db = tmp_path / "skill.sqlite3"
    event_id = record_route_event(
        RouteEvent(
            timestamp=datetime.now(timezone.utc),
            raw_request="加转场",
            effective_request="加转场",
            clarifications={},
            hit_skills=["transition", "color-grade"],
            route_source="keyword",
            confidence=1.0,
        ),
        db_path=db,
    )
    update_final_plan_steps(
        event_id,
        ["gemia.video.transitions.transition_dissolve"],
        db_path=db,
    )
    record_route_event(
        RouteEvent(
            timestamp=datetime.now(timezone.utc),
            raw_request="???",
            effective_request="???",
            clarifications={},
            hit_skills=["timeline-ops", "color-grade", "transition"],
            route_source="fallback",
            confidence=0.0,
        ),
        db_path=db,
    )

    stats = skill_stats(days=7, db_path=db, all_skill_ids=["transition", "color-grade", "timeline-ops"])
    text = format_skill_stats(stats)

    assert stats["source_distribution"]["keyword"]["count"] == 1
    assert stats["source_distribution"]["fallback"]["count"] == 1
    assert "???" in stats["fallback_requests"]
    assert "transition" in text
