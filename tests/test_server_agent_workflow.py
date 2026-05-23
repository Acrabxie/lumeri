from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import server


class _TestServer:
    def __enter__(self) -> "_TestServer":
        self.httpd = HTTPServer(("127.0.0.1", 0), server._Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


def _patch_task_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(server, "_PLANS_DIR", tmp_path / "plans")


def test_run_prompt_agent_workflow_writes_concise_agent_events(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_run_agent_workflow(orch, **kwargs):
        assert kwargs["prompt"] == "调成冷色"
        assert kwargs["input_path"] is None
        assert kwargs["account_id"] == "acct_1"
        assert kwargs["scope"] == "auto"
        kwargs["event_callback"](
            {
                "id": "evt_exec",
                "phase": "execute",
                "label": "执行了 color_grade",
                "status": "running",
                "meta": "执行了 color_grade · 步骤 1/1 · 素材 1/1",
            }
        )
        kwargs["event_callback"](
            {
                "id": "evt_result",
                "phase": "result",
                "label": "完成汇报",
                "status": "succeeded",
                "body": "最终汇报\n生成了：1 个输出",
                "outputs": [str(tmp_path / "out.mp4")],
            }
        )
        return {
            "execution_mode": "agent_loop",
            "goal": kwargs["prompt"],
            "outputs": [str(tmp_path / "out.mp4")],
            "materials": [],
            "targets": [],
            "agent_plan": [],
            "child_tasks": [],
        }

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            "/run-prompt",
            {"prompt": "调成冷色", "project_state": {"clips": []}, "execution_scope": "auto"},
        )
        assert status == 200
        task_id = payload["task_id"]

        status, task = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert task["status"] == "succeeded"
        assert task["execution_mode"] == "agent_loop"
        assert task["agent_events"][0]["meta"] == "执行了 color_grade · 步骤 1/1 · 素材 1/1"
        assert task["agent_events"][-1]["body"].startswith("最终汇报")
        assert task["goal"] == "调成冷色"
        assert task["agent_report"]["brief"]["state"] == "preview_ready"
        assert task["agent_report"]["brief"]["primary_path"] == str(tmp_path / "out.mp4")
        assert task["agent_report"]["summary"]["log_count"] == 2
        assert task["agent_report"]["summary"]["output_count"] == 1


def test_run_prompt_stream_logs_writes_running_task_and_redacts_event_details(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    started = threading.Event()
    release = threading.Event()

    def fake_run_agent_workflow(_orch, **kwargs):
        kwargs["event_callback"](
            {
                "id": "evt_provider",
                "phase": "plan",
                "label": "Gemini 请求已发出",
                "status": "running",
                "body": "Authorization: Bearer sk-test-secret-token-123456789",
                "voice": "gemini",
                "headers": {"Authorization": "Bearer sk-test-secret-token-123456789"},
            }
        )
        started.set()
        assert release.wait(3)
        kwargs["event_callback"](
            {
                "id": "evt_result",
                "phase": "result",
                "label": "完成汇报",
                "status": "succeeded",
                "body": "最终汇报",
                "outputs": [str(tmp_path / "out.mp4")],
            }
        )
        return {
            "execution_mode": "agent_loop",
            "goal": kwargs["prompt"],
            "outputs": [str(tmp_path / "out.mp4")],
            "materials": [],
            "targets": [],
            "agent_plan": [],
            "child_tasks": [],
        }

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            "/run-prompt",
            {
                "prompt": "输出一段小样",
                "project_state": {"clips": []},
                "execution_scope": "auto",
                "stream_logs": True,
            },
        )
        assert status == 200
        assert payload["live_logs"] is True
        task_id = payload["task_id"]
        assert started.wait(2)

        status, task = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert task["status"] == "running"
        assert task["execution_logs"]
        serialized_logs = json.dumps(task["execution_logs"], ensure_ascii=False)
        assert "[redacted]" in serialized_logs
        assert "sk-test-secret-token" not in serialized_logs

        status, log_payload = app.request("GET", f"/task/{task_id}/logs")
        assert status == 200
        assert log_payload["logs"][0]["phase"] == "plan"

        release.set()
        for _ in range(20):
            status, task = app.request("GET", f"/task/{task_id}")
            if task.get("status") == "succeeded":
                break
            time.sleep(0.05)
        assert task["status"] == "succeeded"
        assert task["execution_logs"][-1]["phase"] == "result"


def test_run_prompt_without_video_defaults_to_creation_workflow(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_run_agent_workflow(orch, **kwargs):
        assert kwargs["prompt"] == "做一个三秒 Lumeri 广告标题动画"
        assert kwargs["input_path"] is None
        assert kwargs["project_state"] == {"clips": []}
        kwargs["event_callback"](
            {
                "id": "evt_plan",
                "phase": "plan",
                "label": "Gemini 规划",
                "status": "succeeded",
                "body": "我会从空画布开始创作。",
                "voice": "gemini",
            }
        )
        return {
            "execution_mode": "agent_loop",
            "goal": kwargs["prompt"],
            "outputs": [str(tmp_path / "created.mp4")],
            "materials": [],
            "targets": [{"origin": "prompt", "name": "Prompt-only canvas"}],
            "agent_plan": [],
            "child_tasks": [],
        }

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            "/run-prompt",
            {"prompt": "做一个三秒 Lumeri 广告标题动画", "project_state": {"clips": []}},
        )
        assert status == 200
        assert payload["task_id"]

        status, task = app.request("GET", f"/task/{payload['task_id']}")
        assert status == 200
        assert task["status"] == "succeeded"
        assert task["targets"][0]["origin"] == "prompt"
        assert task["agent_events"][0]["voice"] == "gemini"


def test_merge_clips_route_writes_preview_task(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    output = tmp_path / "merge_preview.mp4"
    output.write_bytes(b"preview")

    def fake_merge(orch, **kwargs):
        assert kwargs["account_id"] == "acct_1"
        assert len(kwargs["clips"]) == 2
        assert kwargs["clips"][0]["name"] == "A"
        kwargs["event_callback"](
            {
                "id": "evt_merge",
                "phase": "preview_ready",
                "label": "合并小样已生成",
                "status": "succeeded",
                "outputs": [str(output)],
            }
        )
        return {
            "execution_mode": "agent_loop",
            "goal": "合并保留片段",
            "outputs": [str(output)],
            "materials": [],
            "targets": [],
            "agent_plan": [],
            "child_tasks": [],
            "render_passes": [
                {
                    "render_pass_id": "pass_merge",
                    "kind": "timeline_merge_kept",
                    "status": "succeeded",
                    "preview_path": str(output),
                    "output_path": str(output),
                }
            ],
        }

    monkeypatch.setattr(server, "run_timeline_kept_clip_merge", fake_merge)

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            "/merge-clips",
            {
                "clips": [
                    {"id": "clip_a", "name": "A", "serverPath": str(tmp_path / "a.mp4"), "keep": True},
                    {"id": "clip_b", "name": "B", "serverPath": str(tmp_path / "b.mp4"), "keep": True},
                ],
                "project_state": {"clips": []},
            },
        )
        assert status == 200
        assert payload["task_id"]

        status, task = app.request("GET", f"/task/{payload['task_id']}")
        assert status == 200
        assert task["status"] in {"succeeded", "preview_ready"}
        assert task["goal"] == "合并保留片段"
        assert task["render_passes"][0]["kind"] == "timeline_merge_kept"
        assert task["outputs"] == [str(output)]


def test_run_prompt_persists_creative_runtime_fields_and_feedback(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_run_agent_workflow(orch, **kwargs):
        output = str(tmp_path / "preview.mp4")
        Path(output).with_suffix(".layer-flow.json").write_text(
            json.dumps(
                {
                    "authored_plan": {
                        "layers": [
                            {
                                "id": "headline",
                                "type": "html",
                                "html": "<div><style>@keyframes x{}</style><div>LUMERI</div></div>",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        kwargs["event_callback"](
            {
                "id": "evt_preview",
                "phase": "preview_ready",
                "label": "小样已生成",
                "status": "succeeded",
                "body": "这一小段效果已经渲染成可看的小样。",
                "voice": "gemini",
                "render_pass_id": "pass_1",
                "outputs": [output],
            }
        )
        return {
            "execution_mode": "agent_loop",
            "goal": kwargs["prompt"],
            "outputs": [output],
            "materials": [],
            "targets": [{"origin": "prompt", "name": "Prompt-only canvas"}],
            "agent_plan": [],
            "child_tasks": [{"task_id": "child_1", "render_pass_ids": ["pass_1"]}],
            "creative_mode": "prompt_only",
            "reference_assets": [{"reference_asset_id": "ref_1", "name": "brand.png"}],
            "layer_plan": {"schema_version": 1, "layers": [{"id": "headline", "type": "text"}]},
            "render_passes": [{"render_pass_id": "pass_1", "output_path": output, "status": "succeeded"}],
            "review_notes": [{"review_note_id": "review_1", "render_pass_id": "pass_1", "verdict": "pass"}],
            "human_feedback": [],
        }

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    def fake_render_centered_title_revision(output_path, *, title):
        assert title == "LUMERI"
        output_path.write_bytes(b"fake mp4")
        output_path.with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        output_path.with_suffix(".preview.json").write_text("{}", encoding="utf-8")
        return ["revision_title"]

    monkeypatch.setattr(server, "_render_centered_title_revision", fake_render_centered_title_revision)

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            "/run-prompt",
            {"prompt": "做一个图层广告小样", "project_state": {"clips": []}},
        )
        assert status == 200
        task_id = payload["task_id"]

        status, task = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert task["creative_mode"] == "prompt_only"
        assert task["reference_assets"][0]["reference_asset_id"] == "ref_1"
        assert task["layer_plan"]["layers"][0]["id"] == "headline"
        assert task["render_passes"][0]["render_pass_id"] == "pass_1"
        assert task["review_notes"][0]["verdict"] == "pass"

        status, feedback_payload = app.request(
            "POST",
            f"/task/{task_id}/feedback",
            {"feedback": "这个字太硬，柔一点", "render_pass_id": "pass_1", "layer_id": "headline"},
        )
        assert status == 200
        assert feedback_payload["human_feedback_count"] == 1
        assert feedback_payload["revision_plan"]["phase"] == "revision_plan"
        assert feedback_payload["revision_plan"]["voice"] == "gemini"
        assert feedback_payload["revision_plan"]["revision_render_pass_id"]
        assert feedback_payload["task"]["human_feedback"][0]["feedback"] == "这个字太硬，柔一点"
        assert feedback_payload["task"]["agent_events"][-1]["phase"] == "revision_plan"
        assert len(feedback_payload["task"]["render_passes"]) == 2
        assert feedback_payload["task"]["render_passes"][-1]["kind"] == "layer_preview_revision"
        assert feedback_payload["task"]["render_passes"][-1]["layer_ids"] == ["revision_title"]

        status, updated = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert updated["human_feedback"][0]["feedback"] == "这个字太硬，柔一点"
        assert updated["agent_events"][-1]["phase"] == "revision_plan"
        assert updated["outputs"][-1].endswith(".mp4")


def test_centered_title_revision_preserves_grid(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["overlay_layers"] = kwargs.get("overlay_layers")
        Path(output_path).write_bytes(b"fake mp4")

    monkeypatch.setattr("gemia.video.layer_flow.render_layer_workflow", fake_render_layer_workflow)

    layer_ids = server._render_centered_title_revision(tmp_path / "revision.mp4", title="NEON CUT")

    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    grid_ids = [layer["id"] for layer in overlay_layers if str(layer.get("id", "")).startswith("revision_grid_")]
    assert grid_ids
    assert all(grid_id in layer_ids for grid_id in grid_ids)
    assert {"revision_title", "revision_cyan_wipe", "revision_magenta_square"}.issubset(layer_ids)


def test_revision_title_prefers_feedback_lumeri_over_html_doctype() -> None:
    feedback = (
        "Review 不合格：ai_12225bb6_2.mp4 不是 prompt-only 3 秒 MG 标题动画；"
        "LUMERI 仍被挤在左侧竖排，请输出新的本地 MP4。"
    )
    plan = {
        "layers": [
            {
                "id": "headline",
                "type": "html",
                "html": "<!DOCTYPE html><html><body><div>LUMERI</div></body></html>",
            }
        ]
    }

    assert server._revision_title_from_feedback_or_plan(feedback, plan) == "LUMERI"


def test_revision_title_ignores_markup_and_file_format_tokens() -> None:
    plan = {"layers": [{"html": "<!DOCTYPE html><style>.x{}</style><div>NEON CUT</div>"}]}

    assert server._revision_title_from_feedback_or_plan("这个 MP4 画面需要标题更大", plan) == "NEON CUT"


def test_mg_ball_title_revision_uses_keyframed_ball_layers(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["overlay_layers"] = kwargs.get("overlay_layers")
        Path(output_path).write_bytes(b"fake mp4")

    monkeypatch.setattr("gemia.video.layer_flow.render_layer_workflow", fake_render_layer_workflow)

    layer_ids = server._render_mg_ball_title_revision(tmp_path / "revision.mp4", title="LUMERI")

    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    assert "revision_magenta_square" not in layer_ids
    assert "revision_title" in layer_ids
    ball_layers = [layer for layer in overlay_layers if str(layer.get("id", "")).startswith("revision_ball_")]
    hit_glows = [layer for layer in overlay_layers if str(layer.get("id", "")).startswith("revision_hit_glow_")]
    assert len(ball_layers) == 5
    assert len(hit_glows) == 5
    assert all(layer["id"] in layer_ids for layer in ball_layers)
    assert all(layer.get("type") == "image" for layer in ball_layers)
    assert all("position" in layer.get("keyframes", {}) for layer in ball_layers)
    assert all(Path(str(layer.get("source"))).exists() for layer in ball_layers)


def test_mg_ball_title_revision_renders_video(tmp_path: Path) -> None:
    output = tmp_path / "revision.mp4"

    layer_ids = server._render_mg_ball_title_revision(output, title="LUMERI")

    assert output.exists()
    assert output.with_suffix(".layer-flow.json").exists()
    assert {"revision_title", "revision_ball_0", "revision_hit_glow_0"}.issubset(layer_ids)


def test_feedback_ball_title_revision_routes_to_mg_renderer(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    source.with_suffix(".layer-flow.json").write_text(
        json.dumps({"authored_plan": {"layers": [{"id": "headline", "type": "html"}]}}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_mg_renderer(output_path, *, title):
        captured["output_path"] = output_path
        captured["title"] = title
        output_path.write_bytes(b"revision")
        output_path.with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return ["revision_title", "revision_ball_0", "revision_hit_glow_0"]

    def fail_centered_renderer(output_path, *, title):
        raise AssertionError("MG ball feedback should not use the generic centered-title renderer")

    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fake_mg_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_centered_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": "LUMERI 标题要水平居中，5 个彩色发光小球从左到右依次弹跳击中字母并产生明显光晕。",
        },
    )

    assert revision is not None
    assert captured["title"] == "LUMERI"
    assert revision["kind"] == "layer_preview_revision"
    assert "revision_ball_0" in revision["layer_ids"]
    assert revision["output_path"].endswith(".revision-12345678.mp4")


def test_feedback_ball_endpoint_preserves_source_layer_flow(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {"id": "scripted_bg", "type": "solid", "color": [0, 0, 0, 1], "size": [1280, 720], "duration": 90},
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96},
                "duration": 90,
            },
            {
                "id": "letter_hit_10_T",
                "type": "text",
                "text": "T",
                "position": [882, 360],
                "font_config": {"size": 96},
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
                "keyframes": {
                    "position": {
                        "points": [
                            {"frame": 0, "value": [280, 203]},
                            {"frame": 68, "value": [899, 314]},
                            {"frame": 89, "value": [899, 195]},
                        ]
                    }
                },
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["canvas"] = kwargs["canvas"]
        captured["max_long_edge"] = kwargs["max_long_edge"]
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("ball endpoint feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的小球终点：最后 0.5 秒黄色小球必须停在"
                "最后一个 T 的正下方，保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["bouncing_ball"]
    assert captured["canvas"] == {"width": 1280, "height": 720, "fps": 30, "total_frames": 90}
    assert captured["max_long_edge"] == 1280
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    assert next(layer for layer in overlay_layers if layer["id"] == "word_base")["text"] == "CLEAN START"
    revised_ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    points = revised_ball["keyframes"]["position"]["points"]
    assert points[-2:] == [
        {"frame": 75, "value": [886, 442], "easing": "ease_out"},
        {"frame": 89, "value": [886, 442], "easing": "linear"},
    ]


def test_feedback_text_color_preserves_source_layer_flow(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {"id": "scripted_bg", "type": "solid", "color": [0, 0, 0, 1], "size": [1280, 720], "duration": 90},
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "duration": 90,
            },
            {
                "id": "letter_hit_0_C",
                "type": "text",
                "text": "C",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.44, 0.91, 1, 1]},
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["canvas"] = kwargs["canvas"]
        captured["max_long_edge"] = kwargs["max_long_edge"]
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("text color feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的文字颜色：把 CLEAN START 主文字和逐字高亮文字"
                "都改成洋红粉紫 #ff4fd8；不要移动文字、小球、阴影、背景或基线，保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["word_base", "letter_hit_0_C"]
    assert captured["canvas"] == {"width": 1280, "height": 720, "fps": 30, "total_frames": 90}
    assert captured["max_long_edge"] == 1280
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    letter_hit = next(layer for layer in overlay_layers if layer["id"] == "letter_hit_0_C")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert word_base["text"] == "CLEAN START"
    assert word_base["position"] == [332, 360]
    assert word_base["font_config"]["size"] == 96
    assert word_base["font_config"]["color"] == [1.0, 0.3098, 0.8471, 1.0]
    assert letter_hit["font_config"]["color"] == [1.0, 0.3098, 0.8471, 1.0]
    assert ball["position"] == [280, 203]


def test_feedback_text_glow_preserves_text_color_and_other_layers(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {"id": "scripted_bg", "type": "solid", "color": [0, 0, 0, 1], "size": [1280, 720], "duration": 90},
            {
                "id": "baseline_glow",
                "type": "solid",
                "color": [0.0, 0.898, 0.7843, 0.62],
                "position": [260, 457],
                "size": [820, 8],
                "opacity": 0.62,
                "duration": 90,
            },
            {
                "id": "stage_floor",
                "type": "solid",
                "color": [0.1255, 0.1255, 0.3765, 0.92],
                "position": [0, 471],
                "size": [1280, 249],
                "opacity": 0.92,
                "duration": 90,
            },
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "opacity": 0.0,
                "duration": 90,
            },
            {
                "id": "letter_hit_0_C",
                "type": "text",
                "text": "C",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.44, 0.91, 1, 1]},
                "duration": 90,
            },
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/shadow.png",
                "position": [337, 431],
                "size": [74, 14],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "blur_radius": 0.0,
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("text glow feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 CLEAN START 文字外发光/aura：把 word_base 和 "
                "letter_hit_* 的文字外发光/描边模糊增强到 10px，颜色保持当前浅青白不变；不要移动或改颜色 "
                "baseline_glow、stage_floor、contact_shadow_*、小球、ball_trail、背景；保持 baseline_glow "
                "position [260,457]、size [820,8]、颜色 #00e5c8、不透明度 0.62 不变；不要添加字幕、水印或任何新文字；"
                "不要使用媒体池或历史视频；保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["word_base", "letter_hit_0_C"]
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    letter_hit = next(layer for layer in overlay_layers if layer["id"] == "letter_hit_0_C")
    baseline = next(layer for layer in overlay_layers if layer["id"] == "baseline_glow")
    stage_floor = next(layer for layer in overlay_layers if layer["id"] == "stage_floor")
    shadow = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert word_base["font_config"]["color"] == [0.86, 0.94, 1, 1]
    assert letter_hit["font_config"]["color"] == [0.44, 0.91, 1, 1]
    assert word_base["font_config"]["glow_radius"] == 10.0
    assert letter_hit["font_config"]["glow_radius"] == 10.0
    assert word_base["metadata"]["text_glow_radius"] == 10.0
    assert letter_hit["metadata"]["text_glow_radius"] == 10.0
    assert baseline["position"] == [260, 457]
    assert baseline["size"] == [820, 8]
    assert baseline["color"] == [0.0, 0.898, 0.7843, 0.62]
    assert baseline["opacity"] == 0.62
    assert stage_floor["color"] == [0.1255, 0.1255, 0.3765, 0.92]
    assert shadow["blur_radius"] == 0.0
    assert ball["position"] == [280, 203]


def test_feedback_baseline_glow_preserves_source_layer_flow(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {"id": "scripted_bg", "type": "solid", "color": [0, 0, 0, 1], "size": [1280, 720], "duration": 90},
            {
                "id": "baseline_glow",
                "type": "solid",
                "color": [0.22, 0.75, 0.92, 0.46],
                "position": [298, 453],
                "size": [709, 3],
                "opacity": 0.56,
                "duration": 90,
            },
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/shadow.png",
                "position": [337, 431],
                "size": [74, 14],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "duration": 90,
            },
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "duration": 90,
            },
            {
                "id": "letter_hit_0_C",
                "type": "text",
                "text": "C",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.44, 0.91, 1, 1]},
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["canvas"] = kwargs["canvas"]
        captured["max_long_edge"] = kwargs["max_long_edge"]
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("baseline glow feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 baseline_glow 基线光条：把基线光条从橙色改成"
                "青绿色 #00e5c8，不透明度改到 0.62；保持 baseline_glow 的位置、宽高、时间和 z-index 不变；"
                "不要移动或改颜色文字 CLEAN START、letter_hit_*、小球、stage_floor 舞台地面、"
                "contact_shadow_* 接触阴影、背景；不要改变 contact_shadow 的 blur radius/opacity/size；"
                "保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["baseline_glow"]
    assert captured["canvas"] == {"width": 1280, "height": 720, "fps": 30, "total_frames": 90}
    assert captured["max_long_edge"] == 1280
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    baseline = next(layer for layer in overlay_layers if layer["id"] == "baseline_glow")
    shadow = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    letter_hit = next(layer for layer in overlay_layers if layer["id"] == "letter_hit_0_C")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert baseline["color"] == [0.0, 0.898, 0.7843, 0.62]
    assert baseline["opacity"] == 0.62
    assert baseline["position"] == [298, 453]
    assert baseline["size"] == [709, 3]
    assert shadow["color"] == [0.0196, 0.0275, 0.0392, 1.0]
    assert shadow["opacity"] == 0.38
    assert word_base["font_config"]["color"] == [0.86, 0.94, 1, 1]
    assert letter_hit["font_config"]["color"] == [0.44, 0.91, 1, 1]
    assert ball["position"] == [280, 203]


def test_feedback_baseline_glow_geometry_preserves_color_and_other_layers(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {"id": "scripted_bg", "type": "solid", "color": [0, 0, 0, 1], "size": [1280, 720], "duration": 90},
            {
                "id": "baseline_glow",
                "type": "solid",
                "color": [0.0, 0.898, 0.7843, 0.62],
                "position": [298, 453],
                "size": [709, 3],
                "opacity": 0.62,
                "duration": 90,
                "z_index": 2,
            },
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/shadow.png",
                "position": [337, 431],
                "size": [74, 14],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "duration": 90,
            },
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("baseline glow geometry feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 baseline_glow 基线光条：保持颜色 #00e5c8 和"
                "不透明度 0.62 不变；把 baseline_glow 高度压到 8px，并向下移动 4px；保持 baseline_glow "
                "的宽度、时间和 z-index 不变；不要移动或改颜色文字 CLEAN START、contact_shadow_* 接触阴影、"
                "stage_floor 舞台地面、小球或背景；保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["baseline_glow"]
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    baseline = next(layer for layer in overlay_layers if layer["id"] == "baseline_glow")
    shadow = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert baseline["color"] == [0.0, 0.898, 0.7843, 0.62]
    assert baseline["opacity"] == 0.62
    assert baseline["position"] == [298, 457]
    assert baseline["size"] == [709, 8]
    assert shadow["position"] == [337, 431]
    assert shadow["size"] == [74, 14]
    assert shadow["opacity"] == 0.38
    assert word_base["font_config"]["color"] == [0.86, 0.94, 1, 1]
    assert ball["position"] == [280, 203]


def test_feedback_baseline_glow_width_preserves_position_color_and_other_layers(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {"id": "scripted_bg", "type": "solid", "color": [0, 0, 0, 1], "size": [1280, 720], "duration": 90},
            {
                "id": "baseline_glow",
                "type": "solid",
                "color": [0.0, 0.898, 0.7843, 0.62],
                "position": [298, 457],
                "size": [709, 8],
                "opacity": 0.62,
                "duration": 90,
                "z_index": 2,
            },
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/shadow.png",
                "position": [337, 431],
                "size": [74, 14],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "duration": 90,
            },
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("baseline glow width feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 baseline_glow 基线光条：保持颜色 #00e5c8、"
                "不透明度 0.62、位置、8px 高度、时间和 z-index 不变；把 baseline_glow 宽度改到 820px，"
                "并保持它的左侧 x=298 不变；不要移动或改颜色文字 CLEAN START、contact_shadow_* 接触阴影、"
                "stage_floor 舞台地面、小球或背景；保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["baseline_glow"]
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    baseline = next(layer for layer in overlay_layers if layer["id"] == "baseline_glow")
    shadow = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert baseline["color"] == [0.0, 0.898, 0.7843, 0.62]
    assert baseline["opacity"] == 0.62
    assert baseline["position"] == [298, 457]
    assert baseline["size"] == [820, 8]
    assert shadow["position"] == [337, 431]
    assert shadow["size"] == [74, 14]
    assert shadow["opacity"] == 0.38
    assert word_base["font_config"]["color"] == [0.86, 0.94, 1, 1]
    assert ball["position"] == [280, 203]


def test_feedback_baseline_glow_x_position_preserves_size_color_and_other_layers(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {"id": "scripted_bg", "type": "solid", "color": [0, 0, 0, 1], "size": [1280, 720], "duration": 90},
            {
                "id": "baseline_glow",
                "type": "solid",
                "color": [0.0, 0.898, 0.7843, 0.62],
                "position": [298, 457],
                "size": [820, 8],
                "opacity": 0.62,
                "duration": 90,
                "z_index": 2,
            },
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/shadow.png",
                "position": [337, 431],
                "size": [74, 14],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "duration": 90,
            },
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("baseline glow x-position feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 baseline_glow 基线光条：保持颜色 #00e5c8、"
                "不透明度 0.62、y=457、宽度 820px、高度 8px、时间和 z-index 不变；把 baseline_glow "
                "的左侧 x 位置改到 260px；不要移动或改颜色文字 CLEAN START、contact_shadow_* 接触阴影、"
                "stage_floor 舞台地面、小球或背景；保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["baseline_glow"]
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    baseline = next(layer for layer in overlay_layers if layer["id"] == "baseline_glow")
    shadow = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert baseline["color"] == [0.0, 0.898, 0.7843, 0.62]
    assert baseline["opacity"] == 0.62
    assert baseline["position"] == [260, 457]
    assert baseline["size"] == [820, 8]
    assert shadow["position"] == [337, 431]
    assert shadow["size"] == [74, 14]
    assert shadow["opacity"] == 0.38
    assert word_base["font_config"]["color"] == [0.86, 0.94, 1, 1]
    assert ball["position"] == [280, 203]


def test_feedback_contact_shadow_preserves_source_layer_flow_and_outranks_negative_stage_floor_clause(
    monkeypatch, tmp_path: Path
) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {
                "id": "stage_floor",
                "type": "solid",
                "color": [0.1255, 0.1255, 0.3765, 0.92],
                "position": [0, 471],
                "size": [1280, 249],
                "opacity": 0.92,
                "duration": 90,
            },
            {
                "id": "baseline_glow",
                "type": "solid",
                "color": [1.0, 0.6902, 0.0, 0.9],
                "position": [298, 453],
                "size": [709, 3],
                "opacity": 0.9,
                "duration": 90,
            },
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "duration": 90,
            },
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": str(tmp_path / "shadow.png"),
                "position": [337, 431],
                "size": [74, 17],
                "opacity": 0.0,
                "duration": 90,
                "keyframes": {"opacity": {"12": 0.0, "24": 0.72, "42": 0.0}},
            },
            {
                "id": "contact_shadow_1",
                "type": "image",
                "source": str(tmp_path / "shadow.png"),
                "position": [392, 431],
                "size": [74, 17],
                "opacity": 0.0,
                "duration": 90,
                "keyframes": {
                    "opacity": {"12": 0.0, "24": 0.72, "42": 0.0},
                    "scale": {"14": 0.62, "24": 1.08, "40": 0.7},
                },
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["canvas"] = kwargs["canvas"]
        captured["max_long_edge"] = kwargs["max_long_edge"]
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("contact shadow feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 contact_shadow 接触阴影：把文字下方所有 "
                "contact_shadow_* 阴影变成柔和黑色 #05070a，不透明度提高到 0.38；不要移动文字、"
                "小球、stage_floor 舞台地面、baseline_glow 基线光条或背景；保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["contact_shadow_0", "contact_shadow_1"]
    assert captured["canvas"] == {"width": 1280, "height": 720, "fps": 30, "total_frames": 90}
    assert captured["max_long_edge"] == 1280
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    stage_floor = next(layer for layer in overlay_layers if layer["id"] == "stage_floor")
    baseline = next(layer for layer in overlay_layers if layer["id"] == "baseline_glow")
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    shadow_0 = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    shadow_1 = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_1")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert shadow_0["color"] == [0.0196, 0.0275, 0.0392, 1.0]
    assert shadow_0["opacity"] == 0.38
    assert shadow_0["keyframes"]["opacity"] == {"12": 0.0, "24": 0.38, "42": 0.0}
    assert shadow_1["color"] == [0.0196, 0.0275, 0.0392, 1.0]
    assert shadow_1["opacity"] == 0.38
    assert shadow_1["keyframes"]["scale"] == {"14": 0.62, "24": 1.08, "40": 0.7}
    assert stage_floor["color"] == [0.1255, 0.1255, 0.3765, 0.92]
    assert stage_floor["opacity"] == 0.92
    assert baseline["color"] == [1.0, 0.6902, 0.0, 0.9]
    assert word_base["font_config"]["color"] == [0.86, 0.94, 1, 1]
    assert ball["position"] == [280, 203]


def test_feedback_stage_floor_preserves_source_layer_flow_and_outranks_negative_baseline_clause(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {
                "id": "stage_floor",
                "type": "solid",
                "color": [0.018, 0.036, 0.048, 0.88],
                "position": [0, 471],
                "size": [1280, 249],
                "duration": 90,
            },
            {
                "id": "baseline_glow",
                "type": "solid",
                "color": [1.0, 0.6902, 0.0, 0.9],
                "position": [298, 453],
                "size": [709, 3],
                "opacity": 0.9,
                "duration": 90,
            },
            {
                "id": "word_base",
                "type": "text",
                "text": "CLEAN START",
                "position": [332, 360],
                "font_config": {"size": 96, "color": [0.86, 0.94, 1, 1]},
                "duration": 90,
            },
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/ball.png",
                "position": [280, 203],
                "size": [58, 58],
                "duration": 90,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["canvas"] = kwargs["canvas"]
        captured["max_long_edge"] = kwargs["max_long_edge"]
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("stage floor feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_12345678",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 stage_floor 舞台地面：把舞台地面颜色改成"
                "深紫蓝 #202060，不透明度提高到 0.92；不要移动文字、小球、baseline_glow 基线光条、"
                "阴影或背景；保持 1280x720、30fps、3 秒。"
            ),
        },
    )

    assert revision is not None
    assert revision["kind"] == "layer_preview_revision"
    assert revision["layer_ids"] == ["stage_floor"]
    assert captured["canvas"] == {"width": 1280, "height": 720, "fps": 30, "total_frames": 90}
    assert captured["max_long_edge"] == 1280
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    stage_floor = next(layer for layer in overlay_layers if layer["id"] == "stage_floor")
    baseline = next(layer for layer in overlay_layers if layer["id"] == "baseline_glow")
    word_base = next(layer for layer in overlay_layers if layer["id"] == "word_base")
    ball = next(layer for layer in overlay_layers if layer["id"] == "bouncing_ball")
    assert stage_floor["color"] == [0.1255, 0.1255, 0.3765, 0.92]
    assert stage_floor["opacity"] == 0.92
    assert stage_floor["position"] == [0, 471]
    assert stage_floor["size"] == [1280, 249]
    assert baseline["color"] == [1.0, 0.6902, 0.0, 0.9]
    assert baseline["opacity"] == 0.9
    assert word_base["font_config"]["color"] == [0.86, 0.94, 1, 1]
    assert ball["position"] == [280, 203]


def test_feedback_contact_shadow_geometry_preserves_unchanged_opacity(
    monkeypatch, tmp_path: Path
) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": str(tmp_path / "shadow.png"),
                "position": [337, 431],
                "size": [74, 17],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "duration": 90,
                "keyframes": {
                    "opacity": {"12": 0.0, "24": 0.38, "42": 0.0},
                    "scale": {"14": 0.62, "24": 1.08, "40": 0.7},
                },
            },
            {
                "id": "contact_shadow_1",
                "type": "image",
                "source": str(tmp_path / "shadow.png"),
                "position": [392, 431],
                "size": [74, 17],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "duration": 90,
                "keyframes": {
                    "opacity": {"16": 0.0, "28": 0.38, "46": 0.0},
                    "scale": {"18": 0.62, "28": 1.08, "44": 0.7},
                },
            },
            {
                "id": "stage_floor",
                "type": "solid",
                "position": [0, 471],
                "size": [1280, 249],
                "color": [0.1255, 0.1255, 0.3765, 0.92],
                "opacity": 0.92,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_23456789",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 contact_shadow 接触阴影：保持所有 "
                "contact_shadow_* 的位置、时间、颜色和不透明度不变，只把阴影整体再柔和一点，"
                "横向缩放保持 1.08，纵向高度缩到 0.82；不要移动 stage_floor。"
            ),
        },
    )

    assert revision is not None
    assert revision["layer_ids"] == ["contact_shadow_0", "contact_shadow_1"]
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    shadow_0 = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    shadow_1 = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_1")
    stage_floor = next(layer for layer in overlay_layers if layer["id"] == "stage_floor")
    assert shadow_0["opacity"] == 0.38
    assert shadow_0["keyframes"]["opacity"] == {"12": 0.0, "24": 0.38, "42": 0.0}
    assert shadow_0["keyframes"]["scale"] == {"14": 0.62, "24": 1.08, "40": 0.7}
    assert shadow_0["size"] == [74, 14]
    assert shadow_0["metadata"]["contact_shadow_height_scale"] == 0.82
    assert shadow_1["opacity"] == 0.38
    assert shadow_1["keyframes"]["opacity"] == {"16": 0.0, "28": 0.38, "46": 0.0}
    assert shadow_1["size"] == [74, 14]
    assert stage_floor["opacity"] == 0.92


def test_feedback_contact_shadow_blur_preserves_geometry_and_opacity(
    monkeypatch, tmp_path: Path
) -> None:
    import gemia.video.layer_flow as layer_flow

    source = tmp_path / "preview.mp4"
    source.write_bytes(b"preview")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {
                "id": "contact_shadow_0",
                "type": "image",
                "source": str(tmp_path / "shadow.png"),
                "position": [337, 431],
                "size": [74, 14],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "blur_radius": 6.0,
                "metadata": {"contact_shadow_blur_radius": 6.0, "blur_radius": 6.0},
                "duration": 90,
                "keyframes": {
                    "opacity": {"12": 0.0, "24": 0.38, "42": 0.0},
                    "scale": {"14": 0.62, "24": 1.08, "40": 0.7},
                },
            },
            {
                "id": "contact_shadow_1",
                "type": "image",
                "source": str(tmp_path / "shadow.png"),
                "position": [392, 431],
                "size": [74, 14],
                "color": [0.0196, 0.0275, 0.0392, 1.0],
                "opacity": 0.38,
                "blur_radius": 6.0,
                "metadata": {"contact_shadow_blur_radius": 6.0, "blur_radius": 6.0},
                "duration": 90,
                "keyframes": {
                    "opacity": {"16": 0.0, "28": 0.38, "46": 0.0},
                    "scale": {"18": 0.62, "28": 1.08, "44": 0.7},
                },
            },
            {
                "id": "stage_floor",
                "type": "solid",
                "position": [0, 471],
                "size": [1280, 249],
                "color": [0.1255, 0.1255, 0.3765, 0.92],
                "opacity": 0.92,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        captured["overlay_layers"] = kwargs["overlay_layers"]
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    def fail_template_renderer(output_path, *, title):
        raise AssertionError("contact shadow blur feedback must preserve the source layer-flow plan")

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    monkeypatch.setattr(server, "_render_mg_ball_title_revision", fail_template_renderer)
    monkeypatch.setattr(server, "_render_centered_title_revision", fail_template_renderer)

    revision = server._create_local_feedback_revision(
        {
            "render_passes": [
                {"render_pass_id": "pass_1", "output_path": str(source), "status": "succeeded"}
            ]
        },
        {
            "feedback_id": "feedback_34567890",
            "render_pass_id": "pass_1",
            "feedback": (
                "只修改这个 prompt-only layer_preview 的 contact_shadow 接触阴影：保持所有 "
                "contact_shadow_* 的位置、时间、颜色、不透明度、size 宽高、scale keyframes、"
                "横向和纵向尺寸都完全不变；把阴影图像本身的真实高斯模糊/blur radius 从 6px 调到 20px，"
                "让边缘更柔和；不要通过缩放、变高、变矮或改变 opacity 来模拟柔和；不要移动 stage_floor。"
            ),
        },
    )

    assert revision is not None
    assert revision["layer_ids"] == ["contact_shadow_0", "contact_shadow_1"]
    overlay_layers = captured["overlay_layers"]
    assert isinstance(overlay_layers, list)
    shadow_0 = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_0")
    shadow_1 = next(layer for layer in overlay_layers if layer["id"] == "contact_shadow_1")
    stage_floor = next(layer for layer in overlay_layers if layer["id"] == "stage_floor")
    assert shadow_0["opacity"] == 0.38
    assert shadow_0["keyframes"]["opacity"] == {"12": 0.0, "24": 0.38, "42": 0.0}
    assert shadow_0["keyframes"]["scale"] == {"14": 0.62, "24": 1.08, "40": 0.7}
    assert shadow_0["position"] == [337, 431]
    assert shadow_0["size"] == [74, 14]
    assert shadow_0["color"] == [0.0196, 0.0275, 0.0392, 1.0]
    assert shadow_0["blur_radius"] == 20.0
    assert shadow_0["metadata"]["contact_shadow_blur_radius"] == 20.0
    assert shadow_1["opacity"] == 0.38
    assert shadow_1["keyframes"]["opacity"] == {"16": 0.0, "28": 0.38, "46": 0.0}
    assert shadow_1["keyframes"]["scale"] == {"18": 0.62, "28": 1.08, "44": 0.7}
    assert shadow_1["position"] == [392, 431]
    assert shadow_1["size"] == [74, 14]
    assert shadow_1["color"] == [0.0196, 0.0275, 0.0392, 1.0]
    assert shadow_1["blur_radius"] == 20.0
    assert stage_floor["opacity"] == 0.92
    assert "blur_radius" not in stage_floor


def test_feedback_multilayer_revision_plan_uses_compact_display_label(
    monkeypatch, tmp_path: Path
) -> None:
    import gemia.video.layer_flow as layer_flow

    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    source = tmp_path / "contact-shadow-source.mp4"
    source.write_bytes(b"source")
    contact_layers = [
        {
            "id": f"contact_shadow_{index}",
            "type": "image",
            "source": str(tmp_path / "shadow.png"),
            "position": [337 + index * 12, 431],
            "size": [74, 17],
            "color": [0.0196, 0.0275, 0.0392, 1.0],
            "opacity": 0.38,
            "duration": 90,
            "keyframes": {"opacity": {"12": 0.0, "24": 0.38, "42": 0.0}},
        }
        for index in range(11)
    ]
    source.with_suffix(".layer-flow.json").write_text(
        json.dumps(
            {
                "authored_plan": {
                    "width": 1280,
                    "height": 720,
                    "fps": 30,
                    "total_frames": 90,
                    "layers": contact_layers,
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    task_id = server._write_agent_workflow_task(
        prompt="prompt-only layer preview",
        result={
            "goal": "prompt-only layer preview",
            "outputs": [str(source)],
            "agent_plan": [],
            "render_passes": [
                {
                    "render_pass_id": "pass_1",
                    "kind": "layer_preview_revision",
                    "output_path": str(source),
                    "preview_path": str(source),
                    "status": "succeeded",
                    "layer_ids": [layer["id"] for layer in contact_layers],
                }
            ],
        },
        events=[],
    )

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            f"/task/{task_id}/feedback",
            {
                "render_pass_id": "pass_1",
                "layer_id": "contact_shadow_0",
                "feedback": (
                    "只修改这个 prompt-only layer_preview 的 contact_shadow 接触阴影：保持所有 "
                    "contact_shadow_* 的位置、时间、颜色和不透明度不变，纵向高度缩到 0.82。"
                ),
            },
        )

    assert status == 200
    latest_pass = payload["task"]["render_passes"][-1]
    assert latest_pass["layer_ids"] == [f"contact_shadow_{index}" for index in range(11)]
    note = payload["revision_plan"]
    assert note["layer_id"] == "contact_shadow_* (11 layers)"
    assert "图层 contact_shadow_* (11 layers)" in note["detail"]
    assert "contact_shadow_0, contact_shadow_1, contact_shadow_2" not in note["body"]


def test_feedback_stage_floor_revision_plan_reports_actual_changed_layer(monkeypatch, tmp_path: Path) -> None:
    import gemia.video.layer_flow as layer_flow

    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    source = tmp_path / "stage-source.mp4"
    source.write_bytes(b"source")
    plan = {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "total_frames": 90,
        "layers": [
            {
                "id": "stage_floor",
                "type": "solid",
                "position": [0, 471],
                "size": [1280, 249],
                "color": [0.04, 0.07, 0.12, 0.64],
                "opacity": 0.64,
            },
            {
                "id": "baseline_glow",
                "type": "solid",
                "position": [330, 438],
                "size": [620, 10],
                "color": [1.0, 0.6902, 0.0, 0.9],
                "opacity": 0.9,
            },
        ],
    }
    source.with_suffix(".layer-flow.json").write_text(json.dumps({"authored_plan": plan}), encoding="utf-8")

    def fake_render_layer_workflow(input_path, output_path, **kwargs):
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".layer-flow.json").write_text("{}", encoding="utf-8")
        return str(output_path)

    monkeypatch.setattr(layer_flow, "render_layer_workflow", fake_render_layer_workflow)
    task_id = server._write_agent_workflow_task(
        prompt="prompt-only layer preview",
        result={
            "goal": "prompt-only layer preview",
            "outputs": [str(source)],
            "agent_plan": [],
            "render_passes": [
                {
                    "render_pass_id": "pass_1",
                    "kind": "layer_preview_revision",
                    "output_path": str(source),
                    "preview_path": str(source),
                    "status": "succeeded",
                    "layer_ids": ["baseline_glow"],
                }
            ],
        },
        events=[],
    )

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            f"/task/{task_id}/feedback",
            {
                "render_pass_id": "pass_1",
                "layer_id": "baseline_glow",
                "feedback": (
                    "只修改这个 prompt-only layer_preview 的 stage_floor 舞台地面：把舞台地面颜色改成"
                    "深紫蓝 #202060，不透明度提高到 0.92；不要移动文字、小球、baseline_glow 基线光条、"
                    "阴影或背景；保持 1280x720、30fps、3 秒。"
                ),
            },
        )

    assert status == 200
    latest_pass = payload["task"]["render_passes"][-1]
    assert latest_pass["layer_ids"] == ["stage_floor"]
    note = payload["revision_plan"]
    assert note["layer_id"] == "stage_floor"
    assert "图层 stage_floor" in note["detail"]
    assert "图层 baseline_glow" not in note["detail"]
    assert "图层 stage_floor" in note["body"]


def test_feedback_on_transition_pass_creates_transition_revision(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    captured: dict[str, object] = {}
    input_a = tmp_path / "a.mp4"
    input_b = tmp_path / "b.mp4"
    preview = tmp_path / "preview.mp4"
    input_a.write_bytes(b"a")
    input_b.write_bytes(b"b")
    preview.write_bytes(b"preview")

    def fake_transition_shutter(input_a_arg, input_b_arg, output_path, **kwargs):
        captured["inputs"] = [input_a_arg, input_b_arg]
        captured["args"] = kwargs
        Path(output_path).write_bytes(b"revision")
        return output_path

    monkeypatch.setattr("gemia.video.transitions.transition_shutter", fake_transition_shutter)

    task_id = server._write_agent_workflow_task(
        prompt="做一个相机快门转场效果",
        result={
            "execution_mode": "agent_loop",
            "goal": "做一个相机快门转场效果",
            "outputs": [str(preview)],
            "agent_plan": [
                {
                    "plan": {
                        "input_path": str(input_a),
                        "steps": [
                            {
                                "id": "step_1",
                                "function": "gemia.video.transitions.transition_shutter",
                                "args": {"duration_sec": 1.0},
                                "input": ["$input", str(input_b)],
                                "output": "$output",
                            }
                        ],
                    }
                }
            ],
            "render_passes": [
                {
                    "render_pass_id": "pass_transition",
                    "kind": "render_preview",
                    "output_path": str(preview),
                    "preview_path": str(preview),
                    "status": "succeeded",
                    "manifest_paths": [],
                    "capabilities": ["transition"],
                    "step_functions": ["gemia.video.transitions.transition_shutter"],
                }
            ],
        },
        events=[],
    )

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            f"/task/{task_id}/feedback",
            {
                "feedback": "叶片加明显暗金属纹理和 motion blur，保持 0.10 秒全黑停顿",
                "render_pass_id": "pass_transition",
            },
        )

    assert status == 200
    latest_pass = payload["task"]["render_passes"][-1]
    assert latest_pass["kind"] == "transition_revision"
    assert latest_pass["source_render_pass_id"] == "pass_transition"
    assert latest_pass["manifest_paths"][0].endswith(".transition-revision.json")
    assert Path(latest_pass["output_path"]).exists()
    assert latest_pass["output_path"] in payload["task"]["outputs"]
    assert captured["inputs"] == [str(input_a), str(input_b)]
    args = captured["args"]
    assert args["blade_count"] == 6
    assert args["hold_sec"] >= 0.1
    assert args["edge_highlight"] is True
    assert args["highlight_strength"] >= 0.85
    assert payload["revision_plan"]["revision_render_pass_id"] == latest_pass["render_pass_id"]


def test_feedback_on_timeline_broll_pass_creates_local_revision(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    input_a = tmp_path / "a.mp4"
    input_b = tmp_path / "b.mp4"
    preview = tmp_path / "preview.mp4"
    input_a.write_bytes(b"a")
    input_b.write_bytes(b"b")
    preview.write_bytes(b"preview")
    captured: dict[str, object] = {}

    def fake_timeline_render(targets, *, output_path, duration_sec, transition_sec, grade_filter=None, prompt=""):
        captured["targets"] = targets
        captured["duration_sec"] = duration_sec
        captured["transition_sec"] = transition_sec
        captured["grade_filter"] = grade_filter
        captured["prompt"] = prompt
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".timeline-broll.json").write_text("{}", encoding="utf-8")
        return {
            "kind": "timeline_broll_preview",
            "output": output_path,
            "inputs": [item["source_path"] for item in targets],
            "duration_sec": duration_sec,
            "transition_sec": transition_sec,
            "grade_filter": grade_filter,
        }

    monkeypatch.setattr(server, "_render_timeline_broll_preview", fake_timeline_render)

    task_id = server._write_agent_workflow_task(
        prompt="使用当前时间线里的两个媒体池视频，剪成一个 3 秒横版 B-roll 小片",
        result={
            "execution_mode": "agent_loop",
            "goal": "使用当前时间线里的两个媒体池视频，剪成一个 3 秒横版 B-roll 小片",
            "outputs": [str(preview)],
            "agent_plan": [],
            "render_passes": [
                {
                    "render_pass_id": "pass_broll",
                    "kind": "timeline_broll_preview",
                    "output_path": str(preview),
                    "preview_path": str(preview),
                    "status": "succeeded",
                    "manifest_paths": [],
                    "capabilities": ["timeline", "transition", "color"],
                    "step_functions": ["gemia.agent_workflow.timeline_broll_concat"],
                    "source_materials": [
                        {"source_path": str(input_a), "name": "a.mp4", "source_in": 5.3, "source_out": 32.4},
                        {"source_path": str(input_b), "name": "b.mp4", "source_in": 0.0, "source_out": 6.0},
                    ],
                    "duration_sec": 3.0,
                    "transition_sec": 0.3,
                }
            ],
        },
        events=[],
    )

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            f"/task/{task_id}/feedback",
            {
                "feedback": "只改这个 timeline_broll_preview，压到 2.5 秒，叠化更短更顺，暖色电影感更强，不要字幕标题。",
                "render_pass_id": "pass_broll",
            },
        )

    assert status == 200
    latest_pass = payload["task"]["render_passes"][-1]
    assert latest_pass["kind"] == "timeline_broll_revision"
    assert latest_pass["source_render_pass_id"] == "pass_broll"
    assert latest_pass["output_path"] in payload["task"]["outputs"]
    assert latest_pass["duration_sec"] == 2.5
    assert latest_pass["transition_sec"] < 0.3
    assert "local-feedback-revision" in latest_pass["capabilities"]
    assert Path(latest_pass["output_path"]).exists()
    assert captured["targets"] == [
        {"source_path": str(input_a), "source_in": 5.3, "source_out": 32.4},
        {"source_path": str(input_b), "source_in": 0.0, "source_out": 6.0},
    ]
    assert captured["duration_sec"] == 2.5
    assert captured["transition_sec"] < 0.3
    assert "colorbalance" in str(captured["grade_filter"])
    assert captured["prompt"] == "只改这个 timeline_broll_preview，压到 2.5 秒，叠化更短更顺，暖色电影感更强，不要字幕标题。"
    assert payload["revision_plan"]["revision_render_pass_id"] == latest_pass["render_pass_id"]


def test_timeline_broll_feedback_compacts_long_revision_filename(monkeypatch, tmp_path: Path) -> None:
    input_a = tmp_path / "a.mp4"
    input_b = tmp_path / "b.mp4"
    input_a.write_bytes(b"a")
    input_b.write_bytes(b"b")
    long_name = (
        "ai_d855e88c_1.timeline-revision-c52a6db9.timeline-revision-1d0821f5"
        ".timeline-revision-5dad68ba.timeline-revision-c57ef51e.timeline-revision-9af00ef4"
        ".timeline-revision-ea9e953f.timeline-revision-53a54cf2.timeline-revision-39dbd2aa.mp4"
    )
    preview = tmp_path / long_name
    preview.write_bytes(b"preview")
    captured: dict[str, object] = {}

    def fake_timeline_render(targets, *, output_path, duration_sec, transition_sec, grade_filter=None, prompt=""):
        captured["output_path"] = output_path
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".timeline-broll.json").write_text("{}", encoding="utf-8")
        return {
            "kind": "timeline_broll_preview",
            "output": output_path,
            "inputs": [item["source_path"] for item in targets],
            "duration_sec": duration_sec,
            "transition_sec": transition_sec,
            "grade_filter": grade_filter,
        }

    monkeypatch.setattr(server, "_render_timeline_broll_preview", fake_timeline_render)

    revision = server._create_timeline_broll_feedback_revision(
        {},
        {
            "feedback_id": "feedback_12345678",
            "feedback": "后段 zoom 2.2，保持 crop_offset_x 0.45，让主体更居中。",
            "render_pass_id": "pass_long",
        },
        {
            "render_pass_id": "pass_long",
            "kind": "timeline_broll_revision",
            "output_path": str(preview),
            "preview_path": str(preview),
            "source_materials": [
                {"source_path": str(input_a), "source_in": 0.0, "source_out": 3.0},
                {"source_path": str(input_b), "source_in": 0.0, "source_out": 3.0},
            ],
            "duration_sec": 3.0,
            "transition_sec": 0.0,
        },
        str(preview),
    )

    assert revision is not None
    output_name = Path(str(captured["output_path"])).name
    assert len(output_name.encode("utf-8")) <= 240
    assert output_name.startswith("ai_d855e88c_1.timeline-revision-12345678-")
    assert Path(revision["output_path"]).exists()


def test_timeline_feedback_duration_prefers_target_over_rejected_output_duration() -> None:
    source_pass = {"duration_sec": 3.0, "metadata": {"duration_sec": 3.0}}
    feedback = (
        "Review 不合格：刚产出的 merge_87b49957.mp4 是 17 秒，太长。"
        "请导出一个严格 3 秒横版 MP4：第一段约 0.9 秒、第二段约 0.9 秒、第三段约 1.2 秒。"
    )

    assert server._timeline_feedback_duration(source_pass, feedback) == 3.0


def test_timeline_feedback_transition_respects_hard_cut_request() -> None:
    source_pass = {
        "transition_sec": 0.26,
        "metadata": {"transition_sec": 0.26, "duration_sec": 2.6},
    }
    feedback = "0.45 秒后硬切到机械主体，不要叠化、不要交叉淡化、不要叠加残影。"

    assert server._timeline_feedback_transition(source_pass, feedback, 2.6) == 0.0


def test_timeline_feedback_transition_preserves_existing_no_transition() -> None:
    source_pass = {
        "transition_sec": 0.0,
        "metadata": {"transition_sec": 0.0, "duration_sec": 2.6},
    }

    assert server._timeline_feedback_transition(source_pass, "保持当前剪辑，只压暗高光。", 2.6) == 0.0


def test_timeline_feedback_grade_filter_damps_overexposed_stock() -> None:
    feedback = (
        "Review 不合格：stock 段过曝，降低前段高光和饱和度，"
        "整体仍然保持暖色电影感。"
    )

    grade_filter = server._timeline_feedback_grade_filter(feedback)

    assert "curves=all" in grade_filter
    assert "saturation=0.86" in grade_filter
    assert "brightness=-0.012" in grade_filter
    assert "colorbalance" in grade_filter


def test_timeline_feedback_grade_filter_stronger_on_followup_damping() -> None:
    feedback = (
        "Review 仍需继续改：青绿色高光仍然太亮，饱和度偏大。"
        "请把青绿高光再压低约 25%，饱和度再降一点。"
    )

    grade_filter = server._timeline_feedback_grade_filter(feedback)

    assert "curves=all" in grade_filter
    assert "saturation=0.72" in grade_filter
    assert "brightness=-0.026" in grade_filter
    assert "1/0.68" in grade_filter


def test_timeline_feedback_grade_filter_damps_occluding_light_language() -> None:
    feedback = (
        "Review 仍不合格：青绿色光斑像前景遮罩一样盖住主体，"
        "请把抽象光效压到背景层，显著降低亮度和占比，让机械主体更清楚。"
    )

    grade_filter = server._timeline_feedback_grade_filter(feedback)

    assert "curves=all" in grade_filter
    assert "saturation=0.72" in grade_filter
    assert "brightness=-0.026" in grade_filter
    assert "1/0.68" in grade_filter


def test_feedback_on_timeline_merge_kept_pass_creates_local_revision(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    input_a = tmp_path / "a.mp4"
    input_b = tmp_path / "b.mp4"
    input_c = tmp_path / "c.mp4"
    preview = tmp_path / "merge.mp4"
    input_a.write_bytes(b"a")
    input_b.write_bytes(b"b")
    input_c.write_bytes(b"c")
    preview.write_bytes(b"preview")
    captured: dict[str, object] = {}

    def fake_merge_render(targets, *, output_path, min_clip_duration_sec=None):
        captured["targets"] = targets
        captured["min_clip_duration_sec"] = min_clip_duration_sec
        Path(output_path).write_bytes(b"revision")
        Path(output_path).with_suffix(".timeline-merge.json").write_text("{}", encoding="utf-8")
        return {
            "kind": "timeline_merge_kept",
            "output": output_path,
            "inputs": [item["source_path"] for item in targets],
            "duration_sec": 17.8,
            "clip_count": len(targets),
            "min_clip_duration_sec": min_clip_duration_sec,
            "source_ranges": [
                {"duration": 10.5, "extended_short_clip": False},
                {"duration": 0.8, "extended_short_clip": True},
                {"duration": 6.5, "extended_short_clip": False},
            ],
        }

    monkeypatch.setattr(server, "_render_timeline_kept_clip_merge", fake_merge_render)

    task_id = server._write_agent_workflow_task(
        prompt="合并保留片段",
        result={
            "execution_mode": "agent_loop",
            "goal": "合并保留片段",
            "outputs": [str(preview)],
            "agent_plan": [],
            "render_passes": [
                {
                    "render_pass_id": "pass_merge",
                    "kind": "timeline_merge_kept",
                    "output_path": str(preview),
                    "preview_path": str(preview),
                    "status": "succeeded",
                    "capabilities": ["timeline", "concat"],
                    "step_functions": ["gemia.agent_workflow.timeline_merge_kept"],
                    "source_materials": [
                        {
                            "source_path": str(input_a),
                            "name": "a.mp4",
                            "source_in": 0.0,
                            "source_out": 10.5,
                            "duration": 10.5,
                        },
                        {
                            "source_path": str(input_b),
                            "name": "b.mp4",
                            "source_in": 10.5,
                            "source_out": 10.6,
                            "duration": 0.1,
                        },
                        {
                            "source_path": str(input_c),
                            "name": "c.mp4",
                            "source_in": 0.0,
                            "source_out": 6.5,
                            "duration": 6.5,
                        },
                    ],
                    "duration_sec": 17.1,
                }
            ],
        },
        events=[],
    )

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            f"/task/{task_id}/feedback",
            {
                "feedback": "第二段 B 只有 0.1 秒，几乎看不到。请让 B 片段至少 0.8 秒可见，不要字幕标题。",
                "render_pass_id": "pass_merge",
            },
        )

    assert status == 200
    latest_pass = payload["task"]["render_passes"][-1]
    assert latest_pass["kind"] == "timeline_merge_kept_revision"
    assert latest_pass["source_render_pass_id"] == "pass_merge"
    assert latest_pass["output_path"] in payload["task"]["outputs"]
    assert latest_pass["min_clip_duration_sec"] == 0.8
    assert latest_pass["metadata"]["source_ranges"][1]["extended_short_clip"] is True
    assert "local-feedback-revision" in latest_pass["capabilities"]
    assert Path(latest_pass["output_path"]).exists()
    assert captured["targets"][1]["source_in"] == 10.5
    assert captured["targets"][1]["source_out"] == 10.6
    assert captured["min_clip_duration_sec"] == 0.8
    assert payload["revision_plan"]["revision_render_pass_id"] == latest_pass["render_pass_id"]


def test_task_payload_filters_non_media_artifacts_from_outputs(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    media_path = tmp_path / "preview.mp4"
    brief_path = tmp_path / "preview.lumeri-dev-brief.md"
    media_path.write_bytes(b"fake video")
    brief_path.write_text("# brief\n", encoding="utf-8")

    task_id = server._write_agent_workflow_task(
        prompt="写 brief 并生成预览",
        result={
            "execution_mode": "agent_loop",
            "goal": "写 brief 并生成预览",
            "outputs": [str(media_path), str(brief_path)],
            "render_passes": [
                {
                    "render_pass_id": "pass_doc",
                    "output_path": str(brief_path),
                    "preview_path": str(brief_path),
                    "status": "succeeded",
                }
            ],
        },
        events=[],
    )

    with _TestServer() as app:
        status, task = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert task["outputs"] == [str(media_path)]
        assert task["artifact_outputs"] == [str(brief_path)]
        assert task["all_outputs"] == [str(media_path), str(brief_path)]
        assert task["render_passes"][0]["preview_path"] == ""
        assert task["render_passes"][0]["artifact_path"] == str(brief_path)
        assert task["render_passes"][0]["status"] == "artifact_ready"

        status, assets = app.request("GET", f"/task/{task_id}/assets")
        assert status == 200
        assert [item["abs_path"] for item in assets["assets"]] == [str(media_path), str(brief_path)]
        assert [item["is_media"] for item in assets["assets"]] == [True, False]


def test_task_contract_rejects_invalid_video_in_product_outputs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_BASE_DIR", tmp_path)
    monkeypatch.setattr(server, "_ffprobe_video_ok", lambda path: (False, "ffprobe_failed"))
    _patch_task_dirs(monkeypatch, tmp_path)
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    bad_video = outputs_dir / "fake.mp4"
    bad_video.write_bytes(b"not a playable mp4")

    task_id = server._write_agent_workflow_task(
        prompt="生成坏视频",
        result={
            "execution_mode": "agent_loop",
            "goal": "生成坏视频",
            "outputs": [str(bad_video)],
            "targets": [
                {
                    "clip_id": "clip_bad",
                    "asset_id": "asset_bad",
                    "material_id": "asset_bad",
                    "name": "source.mp4",
                }
            ],
            "render_passes": [
                {
                    "render_pass_id": "pass_bad",
                    "output_path": str(bad_video),
                    "preview_path": str(bad_video),
                    "status": "succeeded",
                }
            ],
        },
        events=[],
        project_state={"selectedClipId": "clip_bad", "clips": [{"id": "clip_bad", "name": "source.mp4"}]},
    )

    with _TestServer() as app:
        status, task = app.request("GET", f"/task/{task_id}")

    assert status == 200
    assert task["status"] == "failed"
    assert task["outputs"] == []
    assert task["timeline_updates"] == []
    assert task["render_passes"][0]["preview_path"] == ""
    assert task["render_passes"][0]["status"] == "missing_output"
    assert task["agent_events"][-1]["error_code"]
    assert "ffprobe" in json.dumps(task["output_qc"], ensure_ascii=False)


def test_task_payload_includes_timeline_update_for_single_target(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    output = tmp_path / "graded.mp4"
    output.write_bytes(b"fake video")

    task_id = server._write_agent_workflow_task(
        prompt="把这个视频调亮一点",
        result={
            "execution_mode": "agent_loop",
            "goal": "把这个视频调亮一点",
            "outputs": [str(output)],
            "targets": [
                {
                    "clip_id": "clip_1",
                    "asset_id": "asset_1",
                    "material_id": "asset_1",
                    "name": "source.mp4",
                }
            ],
        },
        events=[],
        project_state={"selectedClipId": "clip_1", "clips": [{"id": "clip_1", "name": "source.mp4"}]},
    )

    with _TestServer() as app:
        status, task = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert task["timeline_updates"] == [
            {
                "mode": "replace_clip_media",
                "clip_id": "clip_1",
                "asset_id": "asset_1",
                "material_id": "asset_1",
                "output_path": str(output),
                "preview_path": str(output),
                "media_kind": "video",
                "mime_type": "video/mp4",
                "name": "graded.mp4",
            }
        ]


def test_task_payload_does_not_guess_multi_target_single_preview(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    output = tmp_path / "timeline_preview.mp4"
    output.write_bytes(b"fake video")

    task_id = server._write_agent_workflow_task(
        prompt="把两段素材剪成一个三秒小样",
        result={
            "execution_mode": "agent_loop",
            "goal": "把两段素材剪成一个三秒小样",
            "outputs": [str(output)],
            "targets": [
                {"clip_id": "clip_a", "asset_id": "asset_a"},
                {"clip_id": "clip_b", "asset_id": "asset_b"},
            ],
        },
        events=[],
        project_state={"selectedClipId": "clip_a", "clips": [{"id": "clip_a"}, {"id": "clip_b"}]},
    )

    with _TestServer() as app:
        status, task = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert task["outputs"] == [str(output)]
        assert task["timeline_updates"] == []


def test_run_prompt_agent_workflow_failure_returns_visible_task(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_run_agent_workflow(_orch, **_kwargs):
        raise RuntimeError("Veo API POST https://openrouter.ai/api/v1/video/generations returned empty response")

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            "/run-prompt",
            {"prompt": "生成一个视频", "project_state": {"clips": []}},
        )
        assert status == 200
        task_id = payload["task_id"]

        status, task = app.request("GET", f"/task/{task_id}")
        assert status == 200
        assert task["status"] == "failed"
        assert task["agent_events"][-1]["voice"] == "gemini"
        assert "外部生成服务" in task["agent_events"][-1]["body"]
        assert task["agent_events"][-1]["debug_id"].startswith("dbg_")
        assert task["agent_events"][-1]["technical_detail"]


def test_task_contract_converts_artifact_only_success_to_artifact_ready(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    brief_path = tmp_path / "plan.md"
    brief_path.write_text("# plan\n", encoding="utf-8")

    task_id = server._write_agent_workflow_task(
        prompt="只写计划",
        result={
            "execution_mode": "agent_loop",
            "goal": "只写计划",
            "outputs": [str(brief_path)],
        },
        events=[],
    )

    with _TestServer() as app:
        status, task = app.request("GET", f"/task/{task_id}")

    assert status == 200
    assert task["status"] == "artifact_ready"
    assert task["outputs"] == []
    assert task["artifact_outputs"] == [str(brief_path)]


def test_task_contract_fails_empty_success_with_human_error(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)

    task_id = server._write_agent_workflow_task(
        prompt="空结果",
        result={"execution_mode": "agent_loop", "goal": "空结果", "outputs": []},
        events=[],
    )

    with _TestServer() as app:
        status, task = app.request("GET", f"/task/{task_id}")

    assert status == 200
    assert task["status"] == "failed"
    assert task["agent_events"][-1]["user_message"]
    assert "technical_detail" in task["agent_events"][-1]


def test_error_firewall_hides_cannot_open_video_technical_detail(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_run_agent_workflow(_orch, **_kwargs):
        raise RuntimeError("Cannot open video: /Volumes/Extreme SSD/gemia/outputs/foo.lumeri-dev-brief.md")

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    with _TestServer() as app:
        status, payload = app.request(
            "POST",
            "/run-prompt",
            {"prompt": "继续", "project_state": {"clips": []}},
        )
        assert status == 200
        status, task = app.request("GET", f"/task/{payload['task_id']}")

    event = task["agent_events"][-1]
    assert status == 200
    assert task["status"] == "failed"
    assert event["error_code"] == "E_NOT_PLAYABLE_MEDIA"
    assert "Cannot open video" not in event["body"]
    assert "technical_detail" in event
    assert event["debug_id"].startswith("dbg_")


def test_health_endpoint_reports_stability_contract(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)

    with _TestServer() as app:
        status, payload = app.request("GET", "/health")

    assert status == 200
    assert "artifact_ready" in payload["task_statuses"]
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["media_artifact_contract"]["ok"] is True
    assert checks["outputs_dir"]["ok"] is True
    assert checks["tasks_dir"]["ok"] is True


def test_session_health_accepts_media_library_file_urls(monkeypatch) -> None:
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_load_current_session(*, account_id: str):
        assert account_id == "acct_1"
        return {
            "video_src": "http://127.0.0.1:7788/media-library/file/asset_123/original",
            "project_state": {
                "clips": [
                    {
                        "id": "clip_1",
                        "serverPath": "/media-library/file/asset_123/original",
                        "previewSrc": "http://127.0.0.1:7788/media-library/file/asset_123/cache/thumb.jpg",
                    }
                ]
            },
        }

    monkeypatch.setattr("gemia.session_history.load_current_session", fake_load_current_session)

    ok, detail = server._session_health()

    assert ok is True
    assert detail == "session video refs are media-only"


def test_session_health_rejects_document_refs(monkeypatch) -> None:
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_load_current_session(*, account_id: str):
        return {
            "project_state": {
                "clips": [
                    {
                        "id": "clip_1",
                        "serverPath": "/Volumes/Extreme SSD/gemia/outputs/notes.lumeri-dev-brief.md",
                    }
                ]
            }
        }

    monkeypatch.setattr("gemia.session_history.load_current_session", fake_load_current_session)

    ok, detail = server._session_health()

    assert ok is False
    assert "non-media video refs" in detail
    assert "notes.lumeri-dev-brief.md" in detail


def test_answer_ask_agent_workflow_replans_with_answers(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")
    calls: list[dict[str, Any]] = []

    def fake_run_agent_workflow(orch, **kwargs):
        calls.append(kwargs)
        if kwargs.get("answers") is None:
            questions = [{"id": "tone", "text": "要什么色调？"}]
            kwargs["event_callback"](
                {
                    "id": "evt_ask",
                    "phase": "ask",
                    "label": "需要补充信息",
                    "status": "asking",
                    "body": "要什么色调？",
                }
            )
            return {
                "execution_mode": "agent_loop",
                "status": "needs_input",
                "ask": True,
                "pending_ask": {"questions": questions},
                "_pending_ask_session": {
                    "prompt": kwargs["prompt"],
                    "video": kwargs["input_path"] or "",
                    "project_state": kwargs["project_state"],
                    "execution_scope": kwargs["scope"],
                },
            }

        assert kwargs["answers"] == {"tone": "冷色"}
        kwargs["event_callback"](
            {
                "id": "evt_exec",
                "phase": "execute",
                "label": "执行了 color_grade",
                "status": "running",
                "meta": "执行了 color_grade",
            }
        )
        return {
            "execution_mode": "agent_loop",
            "goal": kwargs["prompt"],
            "outputs": [str(tmp_path / "answer.mp4")],
            "materials": [],
            "targets": [],
            "agent_plan": [],
            "child_tasks": [],
        }

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    with _TestServer() as app:
        status, ask_payload = app.request(
            "POST",
            "/run-prompt",
            {"prompt": "帮我调色", "project_state": {"clips": []}, "execution_scope": "auto"},
        )
        assert status == 200
        assert ask_payload["ask"] is True

        status, answer_payload = app.request(
            "POST",
            f"/answer-ask/{ask_payload['ask_id']}",
            {"answers": {"tone": "冷色"}},
        )
        assert status == 200

        status, task = app.request("GET", f"/task/{answer_payload['task_id']}")
        assert status == 200
        assert len(calls) == 2
        assert task["agent_events"][0]["phase"] == "ask"
        assert task["agent_events"][-1]["meta"] == "执行了 color_grade"


def test_answer_ask_agent_workflow_stops_repeated_ask(monkeypatch, tmp_path: Path) -> None:
    _patch_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(server.accounts, "current_account_id", lambda: "acct_1")

    def fake_run_agent_workflow(_orch, **kwargs):
        questions = [{"id": "style", "text": "还要确认一次风格吗？"}]
        kwargs["event_callback"](
            {
                "id": "evt_ask",
                "phase": "ask",
                "label": "需要补充信息",
                "status": "asking",
                "body": "还要确认一次风格吗？",
            }
        )
        return {
            "execution_mode": "agent_loop",
            "status": "needs_input",
            "ask": True,
            "pending_ask": {"questions": questions},
            "_pending_ask_session": {
                "prompt": kwargs["prompt"],
                "video": kwargs["input_path"] or "",
                "project_state": kwargs["project_state"],
                "execution_scope": kwargs["scope"],
            },
        }

    monkeypatch.setattr(server, "run_agent_workflow", fake_run_agent_workflow)

    with _TestServer() as app:
        status, ask_payload = app.request(
            "POST",
            "/run-prompt",
            {"prompt": "帮我处理一下", "project_state": {"clips": []}, "execution_scope": "auto"},
        )
        assert status == 200
        assert ask_payload["ask"] is True

        status, answer_payload = app.request(
            "POST",
            f"/answer-ask/{ask_payload['ask_id']}",
            {"answers": {"style": "默认"}},
        )
        assert status == 200
        assert "ask" not in answer_payload

        status, task = app.request("GET", f"/task/{answer_payload['task_id']}")
        assert status == 200
        assert task["status"] == "failed"
        assert task["agent_events"][-1]["label"] == "停止反复确认"
