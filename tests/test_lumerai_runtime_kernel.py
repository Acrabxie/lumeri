from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import lumerai as lm
from gemia.ai.ai_client import AIClient
from gemia.runtime_vnext import RuntimeService
from gemia.orchestrator import GemiaOrchestrator
from gemia.project_model import empty_project, normalize_project
from lumerai.patches import apply_timeline_patches
from lumerai.sandbox import execute_script, validate_script


def _project_with_clip(path: str) -> dict:
    project = empty_project(title="Runtime Kernel Test")
    asset = {
        "id": "asset_demo",
        "asset_id": "asset_demo",
        "name": "demo.mp4",
        "media_kind": "video",
        "mime_type": "video/mp4",
        "source_path": path,
        "duration": 2.0,
        "metadata": {"duration": 2.0},
        "created_at": "2026-05-16T00:00:00Z",
    }
    clip = {
        "id": "clip_demo",
        "asset_id": "asset_demo",
        "track_id": "V1",
        "name": "demo.mp4",
        "media_kind": "video",
        "start": 0.0,
        "duration": 2.0,
        "source_in": 0.0,
        "source_out": 2.0,
        "enabled": True,
        "effects": {"rotation": 0, "mirrored": False, "muted": False, "audioDetached": False, "speed": 1},
    }
    project["assets"] = [asset]
    project["timeline"]["clips"] = [clip]
    project["timeline"]["duration"] = 2.0
    return project


def test_sandbox_dry_run_validates_without_execution(tmp_path: Path) -> None:
    script = "import lumerai as lm\nstate = lm.timeline_state()\n"
    result = execute_script(
        script,
        project_state=empty_project(),
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        session_id="sess_test",
        dry_run=True,
    )
    assert result.ok
    assert result.patches == []


@pytest.mark.parametrize(
    "script,expected",
    [
        ("import os\n", "Blocked import"),
        ("import lumerai as lm\nx = getattr(lm, 'timeline_state')\n", "Blocked call"),
        ("import lumerai as lm\nopen('x')\n", "Blocked call"),
        ("import lumerai as lm\n__builtins__\n", "Blocked name"),
    ],
)
def test_sandbox_rejects_dangerous_scripts(script: str, expected: str) -> None:
    with pytest.raises(Exception) as exc:
        validate_script(script)
    assert expected in str(exc.value)


def test_sandbox_returns_structured_syntax_error(tmp_path: Path) -> None:
    result = execute_script(
        "import lumerai as lm\nif True print('bad')\n",
        project_state=empty_project(),
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        session_id="sess_test",
    )
    assert not result.ok
    assert result.returncode == 2
    assert "Syntax error" in (result.error or "")


def test_sandbox_timeout_is_reported(tmp_path: Path) -> None:
    result = execute_script(
        "import lumerai as lm\nwhile True:\n    pass\n",
        project_state=empty_project(),
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        session_id="sess_test",
        timeout_sec=1,
    )
    assert not result.ok
    assert result.returncode in {124, -9, -24}


def test_runtime_api_emits_insert_patch_with_provenance(sample_video_path: str, tmp_path: Path) -> None:
    patch_stream = io.StringIO()
    project = _project_with_clip(sample_video_path)
    lm.configure_runtime(
        project_state=project,
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        session_id="sess_runtime",
        ai_model="test-model",
        script_hash="hash123",
        patch_output=patch_stream,
    )
    clip = lm.clip_load("demo.mp4")
    trimmed = lm.clip_trim(clip, start=0.2, end=1.2)
    graded = lm.clip_color_grade(trimmed, preset="warm", strength=0.5)
    assert Path(graded["path"]).exists()
    patch = lm.timeline_insert(graded, at=1.0)

    emitted = json.loads(patch_stream.getvalue().strip())
    assert emitted == patch
    op = emitted["ops"][0]
    assert op["op"] == "insert_clip"
    assert op["data"]["clip"]["duration"] > 0
    assert op["provenance"]["session_id"] == "sess_runtime"
    assert op["provenance"]["script_hash"] == "hash123"


def test_hyperframes_render_writes_project_and_preserves_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import gemia.hyperframes_adapter as adapter

    commands: list[list[str]] = []

    def fake_run(cmd, *, cwd, capture_output, text, timeout):
        commands.append(list(cmd))
        if cmd[:3] == ["hyperframes", "lint", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true,"findings":[]}', stderr="")
        if cmd[:3] == ["hyperframes", "snapshot", "--frames"]:
            snapshots = Path(cwd) / "snapshots"
            snapshots.mkdir(exist_ok=True)
            (snapshots / "frame-00-at-0pct.png").write_bytes(b"png")
            (snapshots / "frame-01-at-100pct.png").write_bytes(b"png")
            return subprocess.CompletedProcess(cmd, 0, stdout="snapshots saved", stderr="")
        if cmd[:2] == ["hyperframes", "render"]:
            output = Path(cmd[cmd.index("--output") + 1])
            output.write_bytes(b"mp4")
            return subprocess.CompletedProcess(cmd, 0, stdout="rendered", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    real_which = shutil.which
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    monkeypatch.setattr(adapter.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name == "hyperframes" else real_which(name))
    monkeypatch.setattr(
        adapter,
        "ffprobe_media",
        lambda path: {
            "format": {"duration": "0.500000"},
            "streams": [{"codec_type": "video", "width": 320, "height": 180}],
        },
    )

    patch_stream = io.StringIO()
    workspace = tmp_path / "workspaces" / "sess_hf"
    lm.configure_runtime(
        project_state=empty_project(),
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        workspace_dir=workspace,
        session_id="sess_hf",
        ai_model="test-model",
        script_hash="hash_hf",
        script_path="/tmp/script.py",
        patch_output=patch_stream,
    )

    clip = lm.hyperframes_render(
        '<section class="title">LUMERI</section>',
        css=".title{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:42px}",
        duration=0.5,
        width=320,
        height=180,
        fps=30,
        name="Title Card!",
    )
    metadata = clip["metadata"]["hyperframes"]
    project_dir = Path(metadata["project_dir"])
    assert project_dir == workspace / "hyperframes" / project_dir.name
    assert project_dir.name.startswith("title-card-")
    assert (project_dir / "index.html").exists()
    assert (project_dir / "lint.json").exists()
    assert (project_dir / "snapshots").is_dir()
    assert (project_dir / "render.mp4").exists()
    manifest_path = Path(metadata["manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["hyperframes"]["source_html_path"] == str(project_dir / "index.html")
    assert manifest["hyperframes"]["duration"] == 0.5
    assert manifest["hyperframes"]["width"] == 320
    assert manifest["hyperframes"]["height"] == 180
    assert manifest["hyperframes"]["fps"] == 30.0
    assert len(metadata["snapshot_paths"]) == 2
    assert commands[0] == ["hyperframes", "lint", "--json", str(project_dir)]
    assert commands[1] == ["hyperframes", "snapshot", "--frames", "5", str(project_dir)]
    assert commands[2][:8] == ["hyperframes", "render", "--strict", "--workers", "1", "--quality", "draft", "--fps"]
    assert commands[2][commands[2].index("--output") + 1] == str(project_dir / "render.mp4")

    patch = lm.timeline_insert(clip, at=0.0)
    updated = apply_timeline_patches(empty_project(), [patch])
    asset = updated["assets"][0]
    assert asset["metadata"]["generated_by"] == "hyperframes"
    assert asset["metadata"]["hyperframes"]["render_path"] == str(project_dir / "render.mp4")
    assert asset["metadata"]["hyperframes"]["script_hash"] == "hash_hf"


def test_hyperframes_render_falls_back_to_local_ffmpeg_when_cli_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import gemia.hyperframes_adapter as adapter

    commands: list[list[str]] = []
    real_which = shutil.which

    def fake_which(name: str) -> str | None:
        if name == "hyperframes":
            return None
        if name == "ffmpeg":
            return "/usr/local/bin/ffmpeg"
        return real_which(name)

    def fake_run(cmd, *, cwd, capture_output, text, timeout):
        commands.append(list(cmd))
        output = Path(cmd[-1])
        output.write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="rendered", stderr="")

    monkeypatch.setattr(adapter.shutil, "which", fake_which)
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    monkeypatch.setattr(
        adapter,
        "ffprobe_media",
        lambda path: {
            "format": {"duration": "0.500000"},
            "streams": [{"codec_type": "video", "width": 320, "height": 180}],
        },
    )

    lm.configure_runtime(
        project_state=empty_project(),
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        workspace_dir=tmp_path / "workspaces" / "sess_hf_fallback",
        session_id="sess_hf_fallback",
        ai_model="test-model",
        script_hash="hash_fallback",
        script_path="/tmp/script.py",
    )

    clip = lm.hyperframes_render(
        '<section class="stage"><div class="cursor"></div></section>',
        duration=0.5,
        width=320,
        height=180,
        fps=30,
        name="fallback-card",
    )

    metadata = clip["metadata"]["hyperframes"]
    project_dir = Path(metadata["project_dir"])
    assert clip["metadata"]["generated_by"] == "hyperframes_fallback"
    assert metadata["fallback_renderer"] == "ffmpeg"
    assert (project_dir / "index.html").exists()
    assert (project_dir / "manifest.json").exists()
    assert (project_dir / "render.mp4").read_bytes() == b"mp4"
    assert commands and commands[0][0] == "/usr/local/bin/ffmpeg"
    assert not any(cmd[:2] == ["hyperframes", "render"] for cmd in commands)


def test_hyperframes_render_rejects_remote_assets(tmp_path: Path) -> None:
    lm.configure_runtime(
        project_state=empty_project(),
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        workspace_dir=tmp_path / "workspaces" / "sess_hf_block",
        session_id="sess_hf_block",
    )
    with pytest.raises(Exception) as exc:
        lm.hyperframes_render('<img src="https://example.com/card.png">', duration=0.5, width=320, height=180)
    assert "URL" in str(exc.value) or "asset" in str(exc.value)


def _real_hyperframes_available() -> bool:
    return bool(shutil.which("hyperframes") and shutil.which("ffprobe") and shutil.which("ffmpeg"))


@pytest.mark.skipif(not _real_hyperframes_available(), reason="real HyperFrames/FFmpeg tools are not installed")
def test_sandbox_script_can_call_hyperframes_render(tmp_path: Path) -> None:
    script = """
import lumerai as lm

clip = lm.hyperframes_render(
    '<section class="title">LUMERI</section>',
    css=".title{width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#101218;color:#f8fafc;font-size:42px;font-weight:800}",
    duration=0.5,
    width=320,
    height=180,
    fps=30,
    name="sandbox-title",
)
lm.timeline_insert(clip, at=0.0)
"""
    result = execute_script(
        script,
        project_state=empty_project(),
        output_dir=tmp_path / "outputs",
        project_root=tmp_path,
        workspace_dir=tmp_path / "workspaces" / "sess_hf_sandbox",
        session_id="sess_hf_sandbox",
        ai_model="sandbox-test",
        timeout_sec=120,
    )
    assert result.ok, result.stderr
    assert result.patches
    updated = apply_timeline_patches(empty_project(), result.patches)
    asset = updated["assets"][0]
    assert Path(asset["source_path"]).exists()
    assert asset["metadata"]["generated_by"] == "hyperframes"
    assert Path(asset["metadata"]["hyperframes"]["manifest_path"]).exists()


def test_apply_timeline_patches_inserts_and_replaces(sample_video_path: str, tmp_path: Path) -> None:
    project = _project_with_clip(sample_video_path)
    patch = {
        "version": 1,
        "ops": [
            {
                "op": "replace_clip",
                "target": "timeline",
                "clip_id": "clip_demo",
                "provenance": {"session_id": "sess_patch", "script_hash": "abc"},
                "data": {
                    "asset": {
                        "id": "asset_new",
                        "asset_id": "asset_new",
                        "name": "new.mp4",
                        "media_kind": "video",
                        "mime_type": "video/mp4",
                        "source_path": str(tmp_path / "new.mp4"),
                        "duration": 1.0,
                    },
                    "clip": {
                        "id": "clip_generated",
                        "asset_id": "asset_new",
                        "track_id": "V1",
                        "name": "new.mp4",
                        "media_kind": "video",
                        "start": 0.0,
                        "duration": 1.0,
                        "source_in": 0.0,
                        "source_out": 1.0,
                        "enabled": True,
                        "effects": {},
                    },
                },
            }
        ],
    }
    updated = apply_timeline_patches(project, [patch])
    clip = updated["timeline"]["clips"][0]
    assert clip["id"] == "clip_demo"
    assert clip["asset_id"] == "asset_new"
    assert clip["provenance"]["session_id"] == "sess_patch"


def test_golden_scripts_execute_through_sandbox(sample_video_path: str, tmp_path: Path) -> None:
    project = _project_with_clip(sample_video_path)
    for script_file in [
        Path("tests/scripts/lumerai_trim_insert.py"),
        Path("tests/scripts/lumerai_trim_grade_insert.py"),
        Path("tests/scripts/lumerai_replace_existing.py"),
    ]:
        result = execute_script(
            script_file.read_text(),
            project_state=project,
            output_dir=tmp_path / "outputs",
            project_root=Path.cwd(),
            session_id=f"sess_{script_file.stem}",
            ai_model="golden",
            timeout_sec=20,
        )
        assert result.ok, result.stderr
        assert result.patches
        assert result.patches[0]["ops"][0]["provenance"]["session_id"].startswith("sess_")


def test_orchestrator_plan_from_script_requires_feature_flag(tmp_path: Path) -> None:
    orch = GemiaOrchestrator(root_dir=tmp_path / "repo")
    with pytest.raises(RuntimeError, match="LUMERAI_SCRIPT_MODE=1"):
        orch.plan_from_script("import lumerai as lm\n", project_state=empty_project(), session_id="sess")


def test_orchestrator_plan_from_script_applies_patch(
    monkeypatch: pytest.MonkeyPatch,
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LUMERAI_SCRIPT_MODE", "1")
    orch = GemiaOrchestrator(root_dir=tmp_path / "repo")
    project = _project_with_clip(sample_video_path)
    script = Path("tests/scripts/lumerai_trim_grade_insert.py").read_text()
    task = orch.plan_from_script(script, project_state=project, session_id="sess_orch", ai_model="test")

    assert task["status"] == "succeeded"
    assert task["engine"] == "lumerai_script"
    updated = normalize_project(task["project_state"])
    assert len(updated["timeline"]["clips"]) == 2
    inserted = updated["timeline"]["clips"][-1]
    assert inserted["provenance"]["session_id"] == "sess_orch"
    assert Path(updated["assets"][-1]["source_path"]).exists()


def test_ai_client_generate_script_extracts_and_validates_python() -> None:
    class FakeAdapter:
        async def generate_text(self, system_prompt, user_payload, tag):
            assert "lm.timeline_insert" in system_prompt
            assert "lm.hyperframes_render" in system_prompt
            assert "choose an implementation strategy" in system_prompt
            assert "For editing an existing timeline clip" not in system_prompt
            assert "For adding a new edited clip" not in system_prompt
            assert user_payload["runtime_contract"]["no_json_plan"] is True
            assert user_payload["runtime_contract"]["strategy"] == "model_selects_method"
            return """```python
import lumerai as lm

clip = lm.clip_load("demo.mp4")
trimmed = lm.clip_trim(clip, start=0.2, end=1.2)
lm.timeline_insert(trimmed, at=0.0)
```"""

    client = AIClient(adapter=FakeAdapter())  # type: ignore[arg-type]
    script = asyncio.run(client.generate_script("截取第 0.2 到 1.2 秒", project_state=empty_project()))
    assert script.startswith("import lumerai as lm")
    assert "timeline_insert" in script


def test_ai_client_generate_script_keeps_backticks_inside_python_string() -> None:
    class FakeAdapter:
        async def generate_text(self, system_prompt, user_payload, tag):
            return '''```python
import lumerai as lm

clip = lm.hyperframes_render("<section><pre>```</pre></section>", duration=1.0)
lm.timeline_insert(clip, at=0.0)
```'''

    client = AIClient(adapter=FakeAdapter())  # type: ignore[arg-type]
    script = asyncio.run(client.generate_script("做一个包含代码符号的卡片", project_state=empty_project()))
    assert "<pre>```</pre>" in script
    assert "timeline_insert" in script


def test_ai_client_generate_script_keeps_fence_inside_triple_quoted_string() -> None:
    class FakeAdapter:
        async def generate_text(self, system_prompt, user_payload, tag):
            return '''```python
import lumerai as lm

stage_html = """
<section>
```
</section>
"""
clip = lm.hyperframes_render(stage_html, duration=1.0)
lm.timeline_insert(clip, at=0.0)
```'''

    client = AIClient(adapter=FakeAdapter())  # type: ignore[arg-type]
    script = asyncio.run(client.generate_script("做一个包含代码围栏符号的卡片", project_state=empty_project()))
    assert "stage_html" in script
    assert "timeline_insert" in script
    validate_script(script)


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(args: list[str], *, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("LUMERAI_SCRIPT_MODE", None)
    if env_overrides:
        env.update(env_overrides)
    env.setdefault("PYTHONPATH", str(_REPO_ROOT))
    return subprocess.run(
        [sys.executable, "-m", "gemia", "lumerai-script", *args],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_lumerai_script_requires_feature_flag(tmp_path: Path) -> None:
    script = tmp_path / "noop.py"
    script.write_text("import lumerai as lm\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    result = _run_cli(
        [
            "--script", str(script),
            "--project-state", str(state),
            "--session-id", "sess_dev",
            "--root", str(tmp_path / "repo"),
        ],
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "feature_flag_disabled"


def test_cli_lumerai_script_succeeds_and_writes_task(sample_video_path: str, tmp_path: Path) -> None:
    project = _project_with_clip(sample_video_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(project), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    result = _run_cli(
        [
            "--script", str(script_path),
            "--project-state", str(state_path),
            "--session-id", "sess_cli_ok",
            "--ai-model", "cli-test",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["task_id"].startswith("task_")
    assert payload["patch_count"] == 1
    assert payload["timeline_clip_count"] == 2
    assert payload["script_hash"]
    task_path = Path(payload["task_path"])
    assert task_path.exists()
    task_doc = json.loads(task_path.read_text(encoding="utf-8"))
    assert task_doc["engine"] == "lumerai_script"
    assert len(task_doc["project_state"]["timeline"]["clips"]) == 2


def test_cli_lumerai_script_sandbox_failure_returns_structured_error(tmp_path: Path) -> None:
    bad_script = tmp_path / "bad.py"
    bad_script.write_text("import os\nos.system('echo nope')\n", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    result = _run_cli(
        [
            "--script", str(bad_script),
            "--project-state", str(state_path),
            "--session-id", "sess_cli_fail",
            "--root", str(tmp_path / "repo"),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "script_execution_failed"
    assert "Blocked import" in payload["error"]["message"]
    assert payload["script_hash"]
    assert "Traceback" not in result.stdout


def test_cli_lumerai_script_runtime_error_does_not_print_traceback(tmp_path: Path) -> None:
    bad_script = tmp_path / "missing_clip.py"
    bad_script.write_text("import lumerai as lm\nlm.clip_load('missing.mp4')\n", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    result = _run_cli(
        [
            "--script", str(bad_script),
            "--project-state", str(state_path),
            "--session-id", "sess_cli_runtime_fail",
            "--root", str(tmp_path / "repo"),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "script_execution_failed"
    assert "FileNotFoundError:" in payload["error"]["message"]
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_lumerai_script_no_patch_is_structured_failure(tmp_path: Path) -> None:
    no_patch = tmp_path / "no_patch.py"
    no_patch.write_text("import lumerai as lm\nlm.timeline_state()\n", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    result = _run_cli(
        [
            "--script", str(no_patch),
            "--project-state", str(state_path),
            "--session-id", "sess_cli_no_patch",
            "--root", str(tmp_path / "repo"),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "script_emitted_no_patches"
    assert "TimelinePatch" in payload["error"]["message"]
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_project_store_creates_and_loads(tmp_path: Path) -> None:
    from gemia.project_store import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    state = store.create("proj_x")
    assert state["timeline"]["clips"] == []
    assert (tmp_path / "projects" / "proj_x" / "state.json").exists()
    assert (tmp_path / "projects" / "proj_x" / "meta.json").exists()
    assert (tmp_path / "projects" / "proj_x" / "patches").is_dir()
    loaded = store.load("proj_x")
    assert loaded["timeline"]["fps"] == state["timeline"]["fps"]


def test_project_store_apply_patches_persists_history(
    sample_video_path: str, tmp_path: Path
) -> None:
    from gemia.project_store import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    seed = _project_with_clip(sample_video_path)
    store.create("proj_h", seed=seed)
    patch = {
        "version": 1,
        "ops": [
            {
                "op": "insert_clip",
                "target": "timeline",
                "provenance": {"session_id": "sess_h", "script_hash": "hh"},
                "data": {
                    "asset": {
                        "id": "asset_new",
                        "asset_id": "asset_new",
                        "name": "new.mp4",
                        "media_kind": "video",
                        "mime_type": "video/mp4",
                        "source_path": str(tmp_path / "new.mp4"),
                        "duration": 1.0,
                    },
                    "clip": {
                        "id": "clip_new",
                        "asset_id": "asset_new",
                        "track_id": "V1",
                        "name": "new.mp4",
                        "media_kind": "video",
                        "start": 2.0,
                        "duration": 1.0,
                        "source_in": 0.0,
                        "source_out": 1.0,
                        "enabled": True,
                        "effects": {},
                    },
                },
            }
        ],
    }
    result = store.apply_patches(
        "proj_h", [patch], session_id="sess_h", script_hash="hh"
    )
    assert result["patch_seq_start"] == 1
    assert result["patch_seq_end"] == 1
    assert (tmp_path / "projects" / "proj_h" / "patches" / "0001.json").exists()
    history = store.history("proj_h")
    assert len(history) == 1
    assert history[0]["seq"] == 1
    assert history[0]["session_id"] == "sess_h"
    assert history[0]["patch"] == patch
    reloaded = store.load("proj_h")
    assert len(reloaded["timeline"]["clips"]) == 2


def test_project_store_rejects_unsafe_project_id(tmp_path: Path) -> None:
    from gemia.project_store import ProjectStore, ProjectStoreError

    store = ProjectStore(tmp_path / "projects")
    for bad in ["../evil", "", "foo/bar", ".", "a" * 100, "with space"]:
        with pytest.raises(ProjectStoreError):
            store.project_dir(bad)


def test_cli_lumerai_script_with_project_id_autocreates(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    result = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_dev",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_dev_1",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["project_id"] == "proj_dev"
    assert payload["created"] is True
    assert payload["patch_seq_start"] == 1
    assert payload["patch_seq_end"] == 1
    assert payload["timeline_clip_count"] == 2
    state_path = Path(payload["project_state_path"])
    assert state_path.exists()
    assert (repo_root / "projects" / "proj_dev" / "patches" / "0001.json").exists()


def test_cli_lumerai_script_with_project_id_accumulates(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"

    first = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_acc",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_acc_1",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert first.returncode == 0, first.stderr
    payload1 = json.loads(first.stdout)
    assert payload1["created"] is True
    assert payload1["timeline_clip_count"] == 2

    second = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_acc",
            "--session-id", "sess_acc_2",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert second.returncode == 0, second.stderr
    payload2 = json.loads(second.stdout)
    assert payload2["created"] is False
    assert payload2["timeline_clip_count"] == 3
    assert payload2["patch_seq_start"] == 2
    assert payload2["patch_seq_end"] == 2
    patches_dir = repo_root / "projects" / "proj_acc" / "patches"
    assert (patches_dir / "0001.json").exists()
    assert (patches_dir / "0002.json").exists()


def test_cli_lumerai_script_rejects_both_project_flags(tmp_path: Path) -> None:
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    result = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_bad",
            "--project-state", str(state_path),
            "--session-id", "sess_bad",
            "--root", str(tmp_path / "repo"),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "conflicting_project_inputs"


def test_project_store_undo_rewinds_and_renumbers(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"

    first = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_undo",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_u1",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert first.returncode == 0, first.stderr
    second = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_undo",
            "--session-id", "sess_u2",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert second.returncode == 0, second.stderr
    payload2 = json.loads(second.stdout)
    assert payload2["timeline_clip_count"] == 3

    undo = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-undo",
            "--project-id", "proj_undo",
            "--to-seq", "1",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "LUMERAI_SCRIPT_MODE": "1", "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert undo.returncode == 0, undo.stderr
    undo_payload = json.loads(undo.stdout)
    assert undo_payload["status"] == "succeeded"
    assert undo_payload["from_seq"] == 2
    assert undo_payload["to_seq"] == 1
    assert undo_payload["discarded_count"] == 1

    from gemia.project_store import ProjectStore

    store = ProjectStore(repo_root / "projects")
    state = store.load("proj_undo")
    assert len(state["timeline"]["clips"]) == 2
    assert not (repo_root / "projects" / "proj_undo" / "patches" / "0002.json").exists()
    discarded = list((repo_root / "projects" / "proj_undo" / "patches_discarded").glob("0002.*.json"))
    assert len(discarded) == 1

    third = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_undo",
            "--session-id", "sess_u3",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert third.returncode == 0, third.stderr
    payload3 = json.loads(third.stdout)
    assert payload3["patch_seq_start"] == 2
    assert payload3["timeline_clip_count"] == 3
    assert (repo_root / "projects" / "proj_undo" / "patches" / "0002.json").exists()


def test_project_store_undo_to_zero_restores_seed(
    sample_video_path: str, tmp_path: Path
) -> None:
    from gemia.project_store import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    seed = _project_with_clip(sample_video_path)
    store.create("proj_zero", seed=seed)
    patch = {
        "version": 1,
        "ops": [
            {
                "op": "insert_clip",
                "target": "timeline",
                "data": {
                    "asset": {
                        "id": "asset_z",
                        "asset_id": "asset_z",
                        "name": "z.mp4",
                        "media_kind": "video",
                        "mime_type": "video/mp4",
                        "source_path": str(tmp_path / "z.mp4"),
                        "duration": 1.0,
                    },
                    "clip": {
                        "id": "clip_z",
                        "asset_id": "asset_z",
                        "track_id": "V1",
                        "name": "z.mp4",
                        "media_kind": "video",
                        "start": 2.0,
                        "duration": 1.0,
                        "source_in": 0.0,
                        "source_out": 1.0,
                        "enabled": True,
                        "effects": {},
                    },
                },
            }
        ],
    }
    store.apply_patches("proj_zero", [patch], session_id="sess_z", script_hash="hz")
    assert len(store.load("proj_zero")["timeline"]["clips"]) == 2
    result = store.undo_to_seq("proj_zero", 0)
    assert result["from_seq"] == 1
    assert result["to_seq"] == 0
    state = store.load("proj_zero")
    assert len(state["timeline"]["clips"]) == 1
    meta = store.load_meta("proj_zero")
    assert meta["patch_seq"] == 0
    assert meta["undo_log"][-1]["to_seq"] == 0


def test_cli_lumerai_undo_rejects_invalid_targets(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    first = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_bad",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_b1",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert first.returncode == 0, first.stderr

    def _undo(target: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable, "-m", "gemia", "lumerai-undo",
                "--project-id", "proj_bad",
                "--to-seq", target,
                "--root", str(repo_root),
            ],
            cwd=str(_REPO_ROOT),
            env={**os.environ, "LUMERAI_SCRIPT_MODE": "1", "PYTHONPATH": str(_REPO_ROOT)},
            capture_output=True,
            text=True,
            timeout=30,
        )

    beyond = _undo("99")
    assert beyond.returncode != 0
    assert json.loads(beyond.stdout)["error"]["code"] == "invalid_target_seq"

    missing = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-undo",
            "--project-id", "proj_missing",
            "--to-seq", "0",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "LUMERAI_SCRIPT_MODE": "1", "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert missing.returncode != 0
    assert json.loads(missing.stdout)["error"]["code"] == "project_not_found"

    flag_off = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-undo",
            "--project-id", "proj_bad",
            "--to-seq", "0",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env={k: v for k, v in os.environ.items() if k != "LUMERAI_SCRIPT_MODE"} | {"PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert flag_off.returncode != 0
    assert json.loads(flag_off.stdout)["error"]["code"] == "feature_flag_disabled"


def test_inspect_summarizes_fresh_project(sample_video_path: str, tmp_path: Path) -> None:
    from gemia.project_inspect import inspect_project
    from gemia.project_store import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    store.create("proj_fresh", seed=_project_with_clip(sample_video_path))
    summary = inspect_project(store, "proj_fresh")
    assert summary["project_id"] == "proj_fresh"
    assert summary["patch_seq"] == 0
    assert summary["timeline"]["clip_count"] == 1
    assert summary["timeline"]["clips"][0]["track_id"] == "V1"
    assert summary["asset_count"] == 1
    assert "recent_patches" not in summary  # history=0 by default


def test_cli_lumerai_inspect_after_apply_shows_growth(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    first = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_ins",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_ins_1",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert first.returncode == 0, first.stderr

    inspect = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-inspect",
            "--project-id", "proj_ins",
            "--history", "5",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert inspect.returncode == 0, inspect.stderr
    payload = json.loads(inspect.stdout)
    assert payload["patch_seq"] == 1
    assert payload["timeline"]["clip_count"] == 2
    assert payload["recent_patches"][0]["seq"] == 1
    assert payload["recent_patches"][0]["session_id"] == "sess_ins_1"
    assert payload["recent_patches"][0]["op_count"] >= 1


def test_cli_lumerai_inspect_text_format(sample_video_path: str, tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_txt",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_txt",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    inspect = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-inspect",
            "--project-id", "proj_txt",
            "--format", "text",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert inspect.returncode == 0, inspect.stderr
    assert inspect.stdout.startswith("Project proj_txt")
    assert "Timeline" in inspect.stdout
    assert "clips=2" in inspect.stdout
    assert "V1 " in inspect.stdout


def test_cli_lumerai_golden_command_round_trip(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    env = {**os.environ, "LUMERAI_SCRIPT_MODE": "1", "PYTHONPATH": str(_REPO_ROOT)}

    create = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_gold",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_gold_1",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert create.returncode == 0, create.stderr
    create_payload = json.loads(create.stdout)
    assert create_payload["patch_seq_end"] == 1
    assert create_payload["timeline_clip_count"] == 2

    inspect_1 = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-inspect",
            "--project-id", "proj_gold",
            "--history", "1",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert inspect_1.returncode == 0, inspect_1.stderr
    assert "Traceback" not in inspect_1.stdout + inspect_1.stderr
    inspect_payload_1 = json.loads(inspect_1.stdout)
    assert inspect_payload_1["patch_seq"] == 1
    assert inspect_payload_1["recent_patches"][0]["seq"] == 1

    undo = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-undo",
            "--project-id", "proj_gold",
            "--to-seq", "0",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert undo.returncode == 0, undo.stderr
    undo_payload = json.loads(undo.stdout)
    assert undo_payload["to_seq"] == 0
    assert undo_payload["discarded_count"] == 1
    discarded = list((repo_root / "projects" / "proj_gold" / "patches_discarded").glob("0001.*.json"))
    assert len(discarded) == 1

    rerun = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_gold",
            "--session-id", "sess_gold_2",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert rerun.returncode == 0, rerun.stderr
    rerun_payload = json.loads(rerun.stdout)
    assert rerun_payload["patch_seq_start"] == 1
    assert rerun_payload["patch_seq_end"] == 1
    assert rerun_payload["timeline_clip_count"] == 2

    inspect_2 = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-inspect",
            "--project-id", "proj_gold",
            "--format", "text",
            "--history", "1",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert inspect_2.returncode == 0, inspect_2.stderr
    assert "Project proj_gold" in inspect_2.stdout
    assert "clips=2" in inspect_2.stdout
    assert "Traceback" not in inspect_2.stdout + inspect_2.stderr


def test_runtime_kernel_smoke_script_runs_golden_workflow(
    sample_video_path: str, tmp_path: Path
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/lumerai_runtime_kernel_smoke.py",
            "--video", sample_video_path,
            "--workdir", str(tmp_path / "smoke"),
            "--project-id", "proj_smoke_script",
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["steps"]["first_run"]["patch_seq_end"] == 1
    assert Path(payload["steps"]["render_after_first_run"]["preview_path"]).exists()
    assert payload["steps"]["undo_to_zero"]["to_seq"] == 0
    assert payload["steps"]["rerun"]["patch_seq_start"] == 1
    assert Path(payload["steps"]["render_after_rerun"]["manifest_path"]).exists()
    assert payload["final"]["clip_count"] == 2
    assert Path(payload["final"]["preview_path"]).exists()
    assert Path(payload["seed_path"]).exists()
    assert "Traceback" not in result.stdout + result.stderr


def test_cli_lumerai_render_requires_feature_flag(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-render",
            "--project-id", "proj_render",
            "--root", str(tmp_path / "repo"),
        ],
        cwd=str(_REPO_ROOT),
        env={k: v for k, v in os.environ.items() if k != "LUMERAI_SCRIPT_MODE"} | {"PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "feature_flag_disabled"


def test_cli_lumerai_render_after_patch_outputs_playable_preview(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    env = {**os.environ, "LUMERAI_SCRIPT_MODE": "1", "PYTHONPATH": str(_REPO_ROOT)}

    script_result = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_render",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_render",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert script_result.returncode == 0, script_result.stderr

    render = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-render",
            "--project-id", "proj_render",
            "--max-long-edge", "320",
            "--label", "test",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert render.returncode == 0, render.stderr
    payload = json.loads(render.stdout)
    assert payload["status"] == "succeeded"
    assert payload["patch_seq"] == 1
    assert payload["source_clip_count"] == 2
    assert 1.8 <= payload["duration"] <= 2.2
    preview_path = Path(payload["preview_path"])
    manifest_path = Path(payload["manifest_path"])
    assert preview_path.exists()
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["preview_path"] == str(preview_path)
    assert manifest["resolution"]["width"] <= 320
    assert any(stream.get("codec_type") == "video" for stream in manifest["ffprobe"]["streams"])


def test_cli_lumerai_render_empty_project_is_structured_failure(tmp_path: Path) -> None:
    from gemia.project_store import ProjectStore

    repo_root = tmp_path / "repo"
    ProjectStore(repo_root / "projects").create("proj_empty")
    result = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-render",
            "--project-id", "proj_empty",
            "--root", str(repo_root),
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "LUMERAI_SCRIPT_MODE": "1", "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "no_video_clips"
    assert "Traceback" not in result.stdout + result.stderr


def test_cli_lumerai_inspect_missing_project_errors(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable, "-m", "gemia", "lumerai-inspect",
            "--project-id", "proj_nope",
            "--root", str(tmp_path / "repo"),
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "project_not_found"


class _CannedAdapter:
    """Returns canned scripts in sequence. Mimics adapter.generate_text."""

    def __init__(self, scripts: list[str]) -> None:
        self._scripts = list(scripts)
        self.calls: list[dict] = []

    async def generate_text(self, system_prompt, user_payload, tag):
        self.calls.append({"payload": user_payload, "tag": tag})
        if not self._scripts:
            raise RuntimeError("CannedAdapter exhausted")
        return "```python\n" + self._scripts.pop(0) + "\n```"


def _seeded_orchestrator(tmp_path: Path, sample_video_path: str):
    from gemia.orchestrator import GemiaOrchestrator

    repo_root = tmp_path / "repo"
    orch = GemiaOrchestrator(root_dir=repo_root)
    orch.project_store.create("proj_agent", seed=_project_with_clip(sample_video_path))
    return orch


def test_agent_loop_done_marker_stops_after_one_turn(
    monkeypatch: pytest.MonkeyPatch, sample_video_path: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("LUMERAI_SCRIPT_MODE", "1")
    from gemia.agent_loop import run_agent_loop
    from gemia.ai.ai_client import AIClient

    orch = _seeded_orchestrator(tmp_path, sample_video_path)
    canned = _CannedAdapter([
        "import lumerai as lm\n"
        "clip = lm.clip_load('demo.mp4')\n"
        "trimmed = lm.clip_trim(clip, start=0.0, end=1.0)\n"
        "lm.timeline_insert(trimmed, at=2.0)\n"
        "# DONE\n",
    ])
    client = AIClient(adapter=canned)  # type: ignore[arg-type]

    result = run_agent_loop(
        orch, client,
        project_id="proj_agent",
        goal="trim and insert",
        session_id="sess_done",
        max_turns=3,
    )
    assert result["status"] == "done_marker"
    assert result["turns"] == 1
    assert result["final_clip_count"] == 2

    events_path = Path(result["events_path"])
    assert events_path.exists()
    event_types = [json.loads(line)["type"] for line in events_path.read_text().splitlines() if line.strip()]
    assert event_types[0] == "agent.started"
    assert "script.generated" in event_types
    assert "sandbox.completed" in event_types
    assert "patch.applied" in event_types
    assert event_types[-1] == "agent.finished"


def test_agent_loop_recovers_after_sandbox_error(
    monkeypatch: pytest.MonkeyPatch, sample_video_path: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("LUMERAI_SCRIPT_MODE", "1")
    from gemia.agent_loop import run_agent_loop
    from gemia.ai.ai_client import AIClient

    orch = _seeded_orchestrator(tmp_path, sample_video_path)
    bad = (
        "import lumerai as lm\n"
        "clip = lm.clip_load('not-a-real-asset.mp4')\n"
        "lm.timeline_insert(clip, at=0.0)\n"
    )
    good = (
        "import lumerai as lm\n"
        "clip = lm.clip_load('demo.mp4')\n"
        "trimmed = lm.clip_trim(clip, start=0.2, end=1.0)\n"
        "lm.timeline_insert(trimmed, at=2.0)\n"
        "# DONE\n"
    )
    canned = _CannedAdapter([bad, good])
    client = AIClient(adapter=canned)  # type: ignore[arg-type]

    result = run_agent_loop(
        orch, client,
        project_id="proj_agent",
        goal="recover",
        session_id="sess_recover",
        max_turns=3,
    )
    assert result["status"] == "done_marker"
    assert result["turns"] == 2
    assert canned.calls[1]["payload"].get("previous_error", {}).get("stage") == "sandbox"
    turns = sorted((tmp_path / "repo" / "sessions" / "sess_recover" / "turns").glob("*.json"))
    assert len(turns) == 2
    first = json.loads(turns[0].read_text())
    second = json.loads(turns[1].read_text())
    assert first["status"] == "sandbox_failed"
    assert second["status"] == "succeeded"


def test_agent_loop_stops_at_max_turns(
    monkeypatch: pytest.MonkeyPatch, sample_video_path: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("LUMERAI_SCRIPT_MODE", "1")
    from gemia.agent_loop import run_agent_loop
    from gemia.ai.ai_client import AIClient

    orch = _seeded_orchestrator(tmp_path, sample_video_path)

    def _vary(seq: int) -> str:
        return (
            "import lumerai as lm\n"
            "clip = lm.clip_load('demo.mp4')\n"
            f"trimmed = lm.clip_trim(clip, start=0.0, end={0.5 + 0.1 * seq})\n"
            f"lm.timeline_insert(trimmed, at={float(seq)})\n"
        )

    canned = _CannedAdapter([_vary(1), _vary(2), _vary(3)])
    client = AIClient(adapter=canned)  # type: ignore[arg-type]

    result = run_agent_loop(
        orch, client,
        project_id="proj_agent",
        goal="never done",
        session_id="sess_maxed",
        max_turns=2,
    )
    assert result["status"] == "max_turns_reached"
    assert result["turns"] == 2
    assert result["final_clip_count"] == 3  # seed clip + 2 inserts


def test_agent_loop_stops_on_no_progress(
    monkeypatch: pytest.MonkeyPatch, sample_video_path: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("LUMERAI_SCRIPT_MODE", "1")
    from gemia.agent_loop import run_agent_loop
    from gemia.ai.ai_client import AIClient

    orch = _seeded_orchestrator(tmp_path, sample_video_path)
    repeated = (
        "import lumerai as lm\n"
        "clip = lm.clip_load('demo.mp4')\n"
        "trimmed = lm.clip_trim(clip, start=0.0, end=0.5)\n"
        "lm.timeline_insert(trimmed, at=2.0)\n"
    )
    canned = _CannedAdapter([repeated, repeated, repeated])
    client = AIClient(adapter=canned)  # type: ignore[arg-type]

    result = run_agent_loop(
        orch, client,
        project_id="proj_agent",
        goal="loop forever",
        session_id="sess_noprog",
        max_turns=5,
    )
    assert result["status"] == "no_progress"
    assert result["turns"] == 2  # second turn detects identical hash


def test_agent_loop_stops_on_no_patch(
    monkeypatch: pytest.MonkeyPatch, sample_video_path: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("LUMERAI_SCRIPT_MODE", "1")
    from gemia.agent_loop import run_agent_loop

    class NoPatchClient:
        async def generate_script(self, request, **kwargs):
            return "import lumerai as lm\nlm.timeline_state()\n"

    orch = _seeded_orchestrator(tmp_path, sample_video_path)
    result = run_agent_loop(
        orch, NoPatchClient(),
        project_id="proj_agent",
        goal="inspect only",
        session_id="sess_no_patch",
        max_turns=3,
    )
    assert result["status"] == "no_patch"
    assert result["turns"] == 1
    assert result["experimental"] is True
    assert result["final_clip_count"] == 1
    turns = sorted((tmp_path / "repo" / "sessions" / "sess_no_patch" / "turns").glob("*.json"))
    assert len(turns) == 1
    turn = json.loads(turns[0].read_text())
    assert turn["status"] == "no_patch"


def test_agent_loop_permission_denial_blocks_execute(
    monkeypatch: pytest.MonkeyPatch, sample_video_path: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("LUMERAI_SCRIPT_MODE", "1")
    from gemia.agent_loop import run_agent_loop
    from gemia.ai.ai_client import AIClient
    from gemia.permissions import DENY, PermissionSet

    orch = _seeded_orchestrator(tmp_path, sample_video_path)
    canned = _CannedAdapter([
        "import lumerai as lm\n"
        "clip = lm.clip_load('demo.mp4')\n"
        "trimmed = lm.clip_trim(clip, start=0.0, end=0.5)\n"
        "lm.timeline_insert(trimmed, at=2.0)\n"
        "# DONE\n",
    ])
    client = AIClient(adapter=canned)  # type: ignore[arg-type]
    perms = PermissionSet({"script:execute": DENY})

    result = run_agent_loop(
        orch, client,
        project_id="proj_agent",
        goal="blocked",
        session_id="sess_blocked",
        max_turns=2,
        permissions=perms,
    )
    assert result["status"] == "permission_denied"
    assert result["final_clip_count"] == 1  # no patch applied


def test_session_store_round_trip(tmp_path: Path) -> None:
    from gemia.session_store import SessionStore, SessionStoreError

    store = SessionStore(tmp_path / "sessions")
    store.create("sess_a", project_id="proj_a", goal="g", max_turns=3, ai_model="m")
    store.write_turn("sess_a", {"seq": 1, "status": "succeeded"})
    store.update_meta("sess_a", {"status": "done_marker", "turn_count": 1})
    meta = store.load_meta("sess_a")
    assert meta["status"] == "done_marker"
    assert meta["turn_count"] == 1
    assert [t["seq"] for t in store.read_turns("sess_a")] == [1]
    for bad in ["../evil", "", "foo/bar"]:
        with pytest.raises(SessionStoreError):
            store.session_dir(bad)


def test_permission_set_defaults_and_overrides() -> None:
    from gemia.permissions import ALLOW, DENY, ASK, PermissionSet, PermissionError

    perms = PermissionSet()
    assert perms.check("agent:run_turn").decision == ALLOW
    assert perms.check("agent:exceed_max_turns").decision == DENY
    perms2 = PermissionSet({"script:execute": DENY})
    with pytest.raises(PermissionError):
        perms2.require("script:execute")
    perms_ask = PermissionSet({"script:execute": ASK})
    assert perms_ask.check("script:execute").decision == DENY  # headless resolves to deny


def test_event_bus_jsonl_sink(tmp_path: Path) -> None:
    from gemia.events import EventBus, JsonlEventSink, MemoryEventSink

    log_path = tmp_path / "events.jsonl"
    bus = EventBus()
    mem = MemoryEventSink()
    bus.subscribe(JsonlEventSink(log_path))
    bus.subscribe(mem)
    bus.emit("a.started", {"x": 1})
    bus.emit("a.finished", {"x": 2})
    assert mem.types() == ["a.started", "a.finished"]
    lines = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    assert [l["type"] for l in lines] == ["a.started", "a.finished"]


def test_cli_lumerai_agent_requires_feature_flag(tmp_path: Path) -> None:
    result = _run_cli_agent([
        "--project-id", "proj_x",
        "--session-id", "sess_x",
        "--goal", "anything",
        "--root", str(tmp_path / "repo"),
    ])
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "feature_flag_disabled"


def test_bridge_agent_cli_default_adapter_is_claude_only(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable, "-m", "gemia", "bridge-agent-run-once",
            "--queue-root", str(tmp_path / "agent_queue"),
            "--timeout-sec", "1",
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["processed"] == 0
    assert "openclaw" not in result.stderr.lower()


def test_cli_lumerai_agent_canned_script_runs_experimental_loop(
    sample_video_path: str, tmp_path: Path
) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_project_with_clip(sample_video_path)), encoding="utf-8")
    script_path = _REPO_ROOT / "tests/scripts/lumerai_trim_grade_insert.py"
    repo_root = tmp_path / "repo"
    setup = _run_cli(
        [
            "--script", str(script_path),
            "--project-id", "proj_agent_cli",
            "--project-init-from", str(seed_path),
            "--session-id", "sess_agent_setup",
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert setup.returncode == 0, setup.stderr

    canned_script = tmp_path / "agent_script.py"
    canned_script.write_text(
        "import lumerai as lm\n"
        "clip = lm.clip_load('demo.mp4')\n"
        "trimmed = lm.clip_trim(clip, start=0.0, end=0.8)\n"
        "lm.timeline_insert(trimmed, at=2.2)\n"
        "# DONE\n",
        encoding="utf-8",
    )
    result = _run_cli_agent(
        [
            "--project-id", "proj_agent_cli",
            "--session-id", "sess_agent_cli",
            "--goal", "insert a trim",
            "--max-turns", "3",
            "--canned-script", str(canned_script),
            "--root", str(repo_root),
        ],
        env_overrides={"LUMERAI_SCRIPT_MODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["experimental"] is True
    assert payload["status"] == "done_marker"
    assert payload["turns"] == 1
    assert payload["final_clip_count"] == 3
    assert Path(payload["events_path"]).exists()


def _run_cli_agent(extra_args: list[str], *, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("LUMERAI_SCRIPT_MODE", None)
    if env_overrides:
        env.update(env_overrides)
    env.setdefault("PYTHONPATH", str(_REPO_ROOT))
    return subprocess.run(
        [sys.executable, "-m", "gemia", "lumerai-agent", *extra_args],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class _RuntimeScriptClient:
    def __init__(self, scripts: list[str]) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    async def generate_script(self, request, **kwargs):
        self.calls.append({"request": request, **kwargs})
        if not self.scripts:
            raise RuntimeError("RuntimeScriptClient exhausted")
        return self.scripts.pop(0)


def _runtime_valid_insert_script() -> str:
    return (
        "import lumerai as lm\n"
        "state = lm.timeline_state()\n"
        "target = state['timeline']['clips'][0]\n"
        "clip = lm.clip_load(target['id'])\n"
        "trimmed = lm.clip_trim(clip, start=0.0, end=0.8)\n"
        "lm.timeline_insert(trimmed, at=2.0)\n"
    )


def test_runtime_vnext_message_uses_ai_script_and_renders_preview(
    sample_video_path: str, tmp_path: Path
) -> None:
    from gemia.runtime_vnext import RuntimeService

    client = _RuntimeScriptClient([_runtime_valid_insert_script()])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    session = service.create_session(
        {
            "project_id": "proj_runtime_live",
            "session_id": "sess_runtime_live",
            "seed_project": _project_with_clip(sample_video_path),
        }
    )

    result = service.post_message(
        {
            "session_id": session["session_id"],
            "message": "截取第一个片段前 0.8 秒，放到时间轴末尾并渲染预览",
            "max_long_edge": 320,
        }
    )

    assert result["status"] == "succeeded"
    assert len(client.calls) == 1
    assert Path(result["render"]["preview_path"]).exists()
    assert Path(result["render"]["manifest_path"]).exists()
    assert result["task"]["status"] == "succeeded"
    project = result["project"]
    assert len(project["timeline"]["clips"]) == 2
    event_types = [event["type"] for event in result["events"]]
    assert "script_generated" in event_types
    assert event_types.count("sandbox_started") == 2
    for required in ["patch_applied", "render_started", "preview_ready", "review_note", "succeeded"]:
        assert required in event_types


def test_runtime_vnext_short_chat_does_not_generate_script(tmp_path: Path) -> None:
    from gemia.runtime_vnext import RuntimeService

    client = _RuntimeScriptClient([])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_chat",
            "session_id": "sess_runtime_chat",
        }
    )

    result = service.post_message({"session_id": "sess_runtime_chat", "message": "在吗"})

    assert result["status"] == "succeeded"
    assert client.calls == []
    assert result.get("task") is None
    event_types = [event["type"] for event in result["events"]]
    assert "script_generated" not in event_types
    assert "sandbox_started" not in event_types
    assert any(
        event["type"] == "agent_message" and "在，我在" in event["payload"].get("text", "")
        for event in result["events"]
    )


def test_runtime_vnext_identity_question_does_not_generate_script(tmp_path: Path) -> None:
    from gemia.runtime_vnext import RuntimeService

    client = _RuntimeScriptClient([])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_identity",
            "session_id": "sess_runtime_identity",
        }
    )

    result = service.post_message({"session_id": "sess_runtime_identity", "message": "你是谁"})

    assert result["status"] == "succeeded"
    assert client.calls == []
    event_types = [event["type"] for event in result["events"]]
    assert "script_generated" not in event_types
    assert "sandbox_started" not in event_types
    event_text = json.dumps(result["events"], ensure_ascii=False)
    assert "视频创作运行时助手" in event_text


def test_runtime_vnext_discussion_question_does_not_generate_script(tmp_path: Path) -> None:
    from gemia.runtime_vnext import RuntimeService

    client = _RuntimeScriptClient([])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_discussion",
            "session_id": "sess_runtime_discussion",
        }
    )

    result = service.post_message({"session_id": "sess_runtime_discussion", "message": "你觉得我们的第一个视频做什么"})

    assert result["status"] == "succeeded"
    assert client.calls == []
    event_types = [event["type"] for event in result["events"]]
    assert "script_generated" not in event_types
    assert "sandbox_started" not in event_types
    event_text = json.dumps(result["events"], ensure_ascii=False)
    assert "第一个视频" in event_text
    assert "Lumeri 宣言小样" in event_text


def test_runtime_vnext_prompt_only_fallback_script_is_valid() -> None:
    from gemia.runtime_vnext import _fallback_prompt_only_script

    script = _fallback_prompt_only_script("帮我做一个mg动画，效果是一个小球弹过地板", empty_project())

    assert "hyperframes_render" in script
    assert "timeline_insert" in script
    assert "帮我做" not in script
    validate_script(script)


@pytest.mark.skipif(not _real_hyperframes_available(), reason="real HyperFrames/FFmpeg tools are not installed")
def test_runtime_vnext_prompt_only_uses_fallback_after_bad_gemini_syntax(tmp_path: Path) -> None:
    from gemia.runtime_vnext import RuntimeService

    class BadSyntaxClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def generate_script(self, request, **kwargs):
            self.calls.append({"request": request, **kwargs})
            raise SyntaxError("unterminated triple-quoted string literal (detected at line 3)")

    client = BadSyntaxClient()
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_syntax_fallback",
            "session_id": "sess_runtime_syntax_fallback",
        }
    )

    result = service.post_message(
        {
            "session_id": "sess_runtime_syntax_fallback",
            "message": "帮我做一个mg动画，效果是一个小球弹过地板",
            "max_long_edge": 320,
            "timeout_sec": 120,
        }
    )

    assert result["status"] == "succeeded"
    assert len(client.calls) == 2
    assert Path(result["render"]["preview_path"]).exists()
    assert len(result["project"]["timeline"]["clips"]) == 1
    event_text = json.dumps(result["events"], ensure_ascii=False)
    assert "本地安全小样脚本" in event_text
    assert "脚本语法错误" not in event_text


@pytest.mark.skipif(not _real_hyperframes_available(), reason="real HyperFrames/FFmpeg tools are not installed")
def test_runtime_vnext_followup_execute_uses_generic_prompt_only_fallback(tmp_path: Path) -> None:
    from gemia.runtime_vnext import RuntimeService

    client = _RuntimeScriptClient([])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_followup_execute",
            "session_id": "sess_runtime_followup_execute",
        }
    )

    result = service.post_message(
        {
            "session_id": "sess_runtime_followup_execute",
            "message": "行，来吧",
            "max_long_edge": 320,
            "timeout_sec": 120,
        }
    )

    assert result["status"] == "succeeded"
    assert client.calls == []
    assert Path(result["render"]["preview_path"]).exists()
    assert len(result["project"]["timeline"]["clips"]) == 1
    event_text = json.dumps(result["events"], ensure_ascii=False)
    assert "我会让 Gemini" not in event_text
    assert "第一版脚本" not in event_text


def test_runtime_vnext_message_retries_once_after_no_patch_script(
    sample_video_path: str, tmp_path: Path
) -> None:
    from gemia.runtime_vnext import RuntimeService

    client = _RuntimeScriptClient([
        "import lumerai as lm\nlm.timeline_state()\n",
        _runtime_valid_insert_script(),
    ])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_retry",
            "session_id": "sess_runtime_retry",
            "seed_project": _project_with_clip(sample_video_path),
        }
    )

    result = service.post_message(
        {
            "session_id": "sess_runtime_retry",
            "message": "先试一个坏脚本，再局部修复",
            "max_long_edge": 320,
        }
    )

    assert result["status"] == "succeeded"
    assert len(client.calls) == 2
    assert client.calls[0]["previous_error"] is None
    assert client.calls[1]["previous_error"]["stage"] == "sandbox_execute"
    assert "没有创建时间线补丁" in client.calls[1]["previous_error"]["message"]
    event_types = [event["type"] for event in result["events"]]
    assert event_types.count("script_generated") == 2
    assert any(
        event["type"] == "runtime_notice" and "准备重试" in event["payload"].get("text", "")
        for event in result["events"]
    )


def test_runtime_vnext_script_syntax_failure_hides_python_detail(
    sample_video_path: str, tmp_path: Path
) -> None:
    from gemia.runtime_vnext import RuntimeService

    class BadSyntaxClient:
        async def generate_script(self, request, **kwargs):
            raise SyntaxError("unterminated string literal (detected at line 3)")

    service = RuntimeService(tmp_path / "repo", ai_client=BadSyntaxClient())
    service.create_session(
        {
            "project_id": "proj_runtime_seeded_syntax_failure",
            "session_id": "sess_runtime_seeded_syntax_failure",
            "seed_project": _project_with_clip(sample_video_path),
        }
    )

    result = service.post_message(
        {
            "session_id": "sess_runtime_seeded_syntax_failure",
            "message": "把这个视频做一点局部调整",
            "max_long_edge": 320,
        }
    )

    assert result["status"] == "failed"
    assert result["error"]["message"] == "这次脚本没有通过校验，我没有执行它。"
    event_text = json.dumps(result["events"], ensure_ascii=False)
    assert "unterminated" not in event_text
    assert "line 3" not in event_text
    assert "脚本语法错误" not in event_text


def test_runtime_vnext_message_failure_hides_raw_traceback(
    sample_video_path: str, tmp_path: Path
) -> None:
    from gemia.runtime_vnext import RuntimeService

    bad_script = (
        "import lumerai as lm\n"
        "clip = lm.clip_load('missing.mp4')\n"
        "lm.timeline_insert(clip, at=0.0)\n"
    )
    client = _RuntimeScriptClient([bad_script, bad_script])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_fail",
            "session_id": "sess_runtime_fail",
            "seed_project": _project_with_clip(sample_video_path),
        }
    )

    result = service.post_message(
        {
            "session_id": "sess_runtime_fail",
            "message": "调用一个不存在的素材，应该结构化失败",
            "max_long_edge": 320,
        }
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "sandbox_failed"
    payload_text = json.dumps(result, ensure_ascii=False)
    assert "Traceback" not in payload_text
    assert "stderr" not in payload_text
    assert result["task"]["status"] == "failed"
    assert [event["type"] for event in result["events"]][-1] == "failed"


def test_runtime_vnext_feedback_generates_revision_script_and_renders(
    sample_video_path: str, tmp_path: Path
) -> None:
    from gemia.runtime_vnext import RuntimeService

    revision_script = (
        "import lumerai as lm\n"
        "state = lm.timeline_state()\n"
        "target = state['timeline']['clips'][0]\n"
        "clip = lm.clip_load(target['id'])\n"
        "graded = lm.clip_color_grade(clip, preset='warm', strength=0.4)\n"
        "lm.timeline_replace(target['id'], graded)\n"
    )
    client = _RuntimeScriptClient([revision_script])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_runtime_feedback",
            "session_id": "sess_runtime_feedback",
            "seed_project": _project_with_clip(sample_video_path),
        }
    )

    result = service.feedback(
        {
            "session_id": "sess_runtime_feedback",
            "feedback": "这个片段太冷了，只把当前片段调暖一点",
            "render_id": "0001-runtime",
            "time_range": {"start": 0.0, "end": 1.0},
            "max_long_edge": 320,
        }
    )

    assert result["status"] == "succeeded"
    assert result["revision"] is True
    assert result["feedback_event"]["type"] == "review_note"
    assert len(client.calls) == 1
    request = client.calls[0]["request"]
    assert "用户反馈" in request
    assert "目标 render_id：0001-runtime" in request
    assert "自己根据可用 runtime API" in request
    assert "简体中文" in request
    assert "Prefer lm.timeline_replace" not in request
    assert Path(result["render"]["preview_path"]).exists()
    assert result["render"]["preview_path"].endswith("-revision.mp4")
    assert len(result["project"]["timeline"]["clips"]) == 1
    assert result["task"]["timeline_patches"][0]["ops"][0]["op"] == "replace_clip"
    event_types = [event["type"] for event in result["events"]]
    assert event_types.count("review_note") >= 2  # captured feedback + render review
    assert "preview_ready" in event_types
    assert "succeeded" in event_types


def test_ai_client_generate_script_rejects_script_without_timeline_patch() -> None:
    class FakeAdapter:
        async def generate_text(self, system_prompt, user_payload, tag):
            return "import lumerai as lm\nclip = lm.clip_load('demo.mp4')\n"

    client = AIClient(adapter=FakeAdapter())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="没有创建时间线补丁"):
        asyncio.run(client.generate_script("截取但忘了插入", project_state=empty_project()))


def test_ai_client_generate_script_allows_honest_value_error_for_missing_capability() -> None:
    class FakeAdapter:
        async def generate_text(self, system_prompt, user_payload, tag):
            assert "If no media is present" in system_prompt
            assert "Simplified Chinese" in system_prompt
            return "raise ValueError('当前运行时还缺少空画布生成 API，不能假装成功')\n"

    client = AIClient(adapter=FakeAdapter())  # type: ignore[arg-type]
    script = asyncio.run(client.generate_script("从空白项目做一个动画", project_state=empty_project()))
    assert script.startswith("raise ValueError")


def test_runtime_vnext_empty_project_reaches_script_generation(tmp_path: Path) -> None:
    from gemia.runtime_vnext import RuntimeService

    client = _RuntimeScriptClient([
        "raise ValueError('当前运行时还缺少空画布生成 API，不能假装成功')\n",
        "raise ValueError('当前运行时还缺少空画布生成 API，不能假装成功')\n",
    ])
    service = RuntimeService(tmp_path / "repo", ai_client=client)
    service.create_session(
        {
            "project_id": "proj_prompt_only",
            "session_id": "sess_prompt_only",
        }
    )

    result = service.post_message(
        {
            "session_id": "sess_prompt_only",
            "message": "不上传素材，直接做一个 3 秒 MG 视频",
            "max_long_edge": 320,
        }
    )

    assert len(client.calls) == 2
    assert client.calls[1]["previous_error"]["stage"] == "sandbox_execute"
    assert result["status"] == "failed"
    assert result["error"]["code"] == "sandbox_failed"
    assert "空画布" in result["error"]["message"]
    event_types = [event["type"] for event in result["events"]]
    assert "script_generated" in event_types
    assert "no_video_clips" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.skipif(not _real_hyperframes_available(), reason="real HyperFrames/FFmpeg tools are not installed")
def test_runtime_vnext_empty_project_hyperframes_message_creates_preview(tmp_path: Path) -> None:
    script = """
import lumerai as lm

clip = lm.hyperframes_render(
    '<section class="title">LUMERI</section>',
    css=".title{width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#101218;color:#f8fafc;font-size:42px;font-weight:800}",
    duration=0.5,
    width=320,
    height=180,
    fps=30,
    name="runtime-empty-title",
)
lm.timeline_insert(clip, at=0.0)
"""
    service = RuntimeService(tmp_path / "repo")
    service.create_session({"project_id": "proj_hf_empty", "session_id": "sess_hf_empty"})

    result = service.post_message(
        {
            "session_id": "sess_hf_empty",
            "message": "从空白项目开始做一个 Lumeri 标题卡",
            "script": script,
            "max_long_edge": 320,
            "timeout_sec": 120,
        }
    )

    assert result["status"] == "succeeded"
    assert Path(result["render"]["preview_path"]).exists()
    assert result["task"]["status"] == "succeeded"
    project = result["project"]
    assert len(project["timeline"]["clips"]) == 1
    asset = project["assets"][0]
    assert asset["metadata"]["generated_by"] == "hyperframes"
    assert Path(asset["metadata"]["hyperframes"]["render_path"]).exists()
    assert "no_video_clips" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.skipif(not _real_hyperframes_available(), reason="real HyperFrames/FFmpeg tools are not installed")
def test_runtime_vnext_feedback_replaces_hyperframes_clip_and_links_parent(tmp_path: Path) -> None:
    insert_script = """
import lumerai as lm

clip = lm.hyperframes_render(
    '<section class="title">FIRST</section>',
    css=".title{width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#101218;color:#f8fafc;font-size:42px;font-weight:800}",
    duration=0.5,
    width=320,
    height=180,
    fps=30,
    name="first-card",
)
lm.timeline_insert(clip, at=0.0)
"""
    replace_script = """
import lumerai as lm

state = lm.timeline_state()
target = state["timeline"]["clips"][0]
clip = lm.hyperframes_render(
    '<section class="title">REVISION</section>',
    css=".title{width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#182030;color:#f9fafb;font-size:38px;font-weight:800}",
    duration=0.5,
    width=320,
    height=180,
    fps=30,
    name="revised-card",
)
lm.timeline_replace(target["id"], clip)
"""
    service = RuntimeService(tmp_path / "repo")
    service.create_session({"project_id": "proj_hf_feedback", "session_id": "sess_hf_feedback"})
    first = service.post_message(
        {
            "session_id": "sess_hf_feedback",
            "message": "做第一版标题卡",
            "script": insert_script,
            "max_long_edge": 320,
            "timeout_sec": 120,
        }
    )
    assert first["status"] == "succeeded"
    original_clip_id = first["project"]["timeline"]["clips"][0]["id"]

    revised = service.feedback(
        {
            "session_id": "sess_hf_feedback",
            "feedback": "改成 revision 标题卡",
            "render_id": first["render"]["render_id"],
            "script": replace_script,
            "max_long_edge": 320,
            "timeout_sec": 120,
        }
    )

    assert revised["status"] == "succeeded"
    assert revised["revision"] is True
    assert revised["render"]["preview_path"].endswith("-revision.mp4")
    assert len(revised["project"]["timeline"]["clips"]) == 1
    replacement_clip = revised["project"]["timeline"]["clips"][0]
    assert replacement_clip["id"] == original_clip_id
    asset = next(item for item in revised["project"]["assets"] if item["id"] == replacement_clip["asset_id"])
    assert asset["metadata"]["generated_by"] == "hyperframes"
    assert asset["metadata"]["hyperframes"]["parent_clip_id"] == original_clip_id
    assert revised["task"]["timeline_patches"][0]["ops"][0]["op"] == "replace_clip"
