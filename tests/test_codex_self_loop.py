from __future__ import annotations

import json
from pathlib import Path

from gemia.automation.codex_self_loop import run_self_loop, safe_slug


def test_safe_slug_keeps_chinese_goal_words() -> None:
    assert safe_slug("让 Lumeri 像 Codex / Claude Code") == "让-lumeri-像-codex-claude-code"


def test_self_loop_writes_followup_ready_artifacts(tmp_path: Path) -> None:
    cwd = Path(__file__).resolve().parents[1]

    manifests = run_self_loop(cwd, tmp_path, "补一项最小可验证能力", 1)

    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest["status"] == "succeeded"
    iteration_dir = Path(str(manifest["iteration_dir"]))
    assert (iteration_dir / "codex-prompt.md").exists()
    assert (iteration_dir / "context.json").exists()
    assert (iteration_dir / "codex-result.md").exists()
    assert (iteration_dir / "manifest.json").exists()
    assert (iteration_dir / "workspace-diagnostics.json").exists()

    saved_manifest = json.loads((iteration_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["result_report"]["bytes"] > 100
    assert saved_manifest["project_snapshot"]["files"]["scripts/codex_lumeri_self_loop.py"] is True
    assert saved_manifest["workspace_diagnostics"]["gap_count"] >= 0

    diagnostics = json.loads((iteration_dir / "workspace-diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["status"] in {"ready", "needs_recovery"}
    assert isinstance(diagnostics["next_focus"], str)


def test_workspace_diagnostics_explain_missing_runtime_files(tmp_path: Path) -> None:
    cwd = Path(__file__).resolve().parents[1]

    manifest = run_self_loop(cwd, tmp_path, "diagnose missing runtime files", 1)[0]
    diagnostics_path = Path(str(manifest["workspace_diagnostics"]["path"]))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))

    if (cwd / "gemia" / "runtime_vnext.py").exists():
        assert "gemia/runtime_vnext.py" not in [gap["path"] for gap in diagnostics["gaps"]]
    else:
        runtime_gap = next(gap for gap in diagnostics["gaps"] if gap["path"] == "gemia/runtime_vnext.py")
        assert runtime_gap["capability"] == "Runtime Kernel natural-language execution path"
        assert "Runtime Kernel" in runtime_gap["restore_hint"]


def test_context_redacts_secret_like_shared_lines(tmp_path: Path, monkeypatch) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ROLES.md").write_text("token=abc123\nnormal line\n", encoding="utf-8")
    (shared / "QUEUE.md").write_text("", encoding="utf-8")
    (shared / "MEMORY.md").write_text("", encoding="utf-8")
    daily = shared / "daily"
    daily.mkdir()

    monkeypatch.setattr("gemia.automation.codex_self_loop.SHARED_ROOT", shared)
    manifests = run_self_loop(Path(__file__).resolve().parents[1], tmp_path / "out", "redaction", 1)
    context_path = Path(str(manifests[0]["iteration_dir"])) / "context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))

    assert "abc123" not in context["shared"]["roles"]
    assert "[redacted secret-like line]" in context["shared"]["roles"]
