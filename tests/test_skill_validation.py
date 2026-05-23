from __future__ import annotations

from gemia.ai.skills._validate import validate_skills


def test_skill_self_validation_passes() -> None:
    report = validate_skills()

    assert report.ok, report.errors
    assert report.stats["skill_count"] == 23
    assert report.stats["index_tokens_est"] < 2500
