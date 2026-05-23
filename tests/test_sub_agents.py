from __future__ import annotations

from gemia.ai.sub_agents import AGENT_ALIASES, BUILTIN_AGENTS, DEFAULT_PLANNER, SubAgentRegistry


def test_default_planner_is_gemini_31_pro() -> None:
    assert DEFAULT_PLANNER == "Gemini31Pro"
    assert BUILTIN_AGENTS[DEFAULT_PLANNER].model == "google/gemini-3.1-pro-preview"


def test_legacy_gemini31_alias_points_to_pro() -> None:
    assert AGENT_ALIASES["Gemini3.1pro"] == "Gemini31Pro"
    assert AGENT_ALIASES["LumeriPlanner"] == "Gemini31Pro"


def test_registry_planner_uses_gemini_31_pro(monkeypatch) -> None:
    monkeypatch.setenv("GEMIA_AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    registry = SubAgentRegistry()
    planner = registry.planner()
    alias = registry.get("Gemini3.1pro")

    assert planner.model == "google/gemini-3.1-pro-preview"
    assert planner.openrouter_model == "google/gemini-3.1-pro-preview"
    assert alias.model == "google/gemini-3.1-pro-preview"
