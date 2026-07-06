import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import server
from tests_http_harness import create_raw_request, run_server_handler

def make_request(method, path, headers=None, body=None):
    raw_request = create_raw_request(method, path, headers, body)
    response = run_server_handler(server._Handler, raw_request)
    return response["status"], response["headers"].get("cache-control", ""), response["body"]


def test_web_index_prefers_tauri_dist(monkeypatch, tmp_path: Path) -> None:
    dist = tmp_path / "tauri-app" / "dist"
    static = tmp_path / "static"
    dist.mkdir(parents=True)
    static.mkdir()
    (dist / "index.html").write_text("<title>Lumeri</title>", encoding="utf-8")
    (static / "index.html").write_text("<title>Gemia</title>", encoding="utf-8")

    monkeypatch.setattr(server, "_WEB_DIST_DIR", dist)
    monkeypatch.setattr(server, "_STATIC_DIR", static)

    assert server._web_index_path() == dist / "index.html"


def test_web_index_falls_back_to_static(monkeypatch, tmp_path: Path) -> None:
    dist = tmp_path / "tauri-app" / "dist"
    static = tmp_path / "static"
    dist.mkdir(parents=True)
    static.mkdir()
    (static / "index.html").write_text("<title>Gemia</title>", encoding="utf-8")

    monkeypatch.setattr(server, "_WEB_DIST_DIR", dist)
    monkeypatch.setattr(server, "_STATIC_DIR", static)

    assert server._web_index_path() == static / "index.html"


def test_next_alias_serves_primary_web_ui(monkeypatch, tmp_path: Path) -> None:
    dist = tmp_path / "tauri-app" / "dist"
    static = tmp_path / "static"
    dist.mkdir(parents=True)
    static.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><title>Lumeri</title><div id="root"></div><script src="/assets/minimal-ui.js"></script>',
        encoding="utf-8",
    )
    (static / "next.html").write_text("<!doctype html><title>Lumeri vNext</title>", encoding="utf-8")
    monkeypatch.setattr(server, "_WEB_DIST_DIR", dist)
    monkeypatch.setattr(server, "_STATIC_DIR", static)
    monkeypatch.delenv("LUMERAI_VNEXT", raising=False)

    assert server._vnext_index_path() == server._web_index_path()

    response = run_server_handler(server._Handler, create_raw_request("GET", "/next"))
    assert response["status"] == 200
    assert b"<title>Lumeri</title>" in response["body"]
    assert b"minimal-ui.js" in response["body"]
    assert b"Lumeri vNext" not in response["body"]


def test_creative_runtime_panel_renders_agent_report_card() -> None:
    asset = Path("tauri-app/dist/assets/creative-runtime-ui.js")
    source = asset.read_text(encoding="utf-8")
    assert "Agent report" in source
    assert "agent_report" in source
    assert "crt-report" in source
    assert "Open preview" in source
    assert "Copy path" in source
    assert "data-copy-primary" in source
    assert "assetUrl(primaryPath)" in source


def test_creative_runtime_panel_asset_has_source_of_truth() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "tauri-app" / "src" / "assets" / "creative-runtime-ui.js"
    served = root / "tauri-app" / "dist" / "assets" / "creative-runtime-ui.js"

    assert source.exists()
    assert source.read_text(encoding="utf-8") == served.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/sync_creative_runtime_asset.py", "--check"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_api_is_feature_flagged(monkeypatch) -> None:
    monkeypatch.delenv("LUMERAI_VNEXT", raising=False)

    response = run_server_handler(
        server._Handler,
        create_raw_request("POST", "/runtime/dev/workspace", body={"session_id": "proj_rt"}),
    )

    assert response["status"] == 404
    assert response["body_json"]["error"] == "vNext runtime is disabled"


def test_next_alias_points_at_built_lumeri_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "tauri-app" / "dist" / "index.html").read_text(encoding="utf-8")

    assert "<title>Lumeri</title>" in html
    assert 'id="root"' in html
    assert "/assets/index-0sE5_Od1.js" in html
    assert "/assets/agent-links.js" in html
    assert "/assets/creative-runtime-ui.js" in html
    assert "/assets/minimal-ui.js" in html
    assert "/assets/index-BH3QfU6L.css" in html
    assert "Lumeri vNext" not in html


def test_web_asset_path_rejects_traversal(monkeypatch, tmp_path: Path) -> None:
    assets = tmp_path / "tauri-app" / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "index.js").write_text("console.log('lumeri')", encoding="utf-8")

    monkeypatch.setattr(server, "_WEB_ASSETS_DIR", assets)

    assert server._web_asset_path("index.js") == (assets / "index.js").resolve()
    assert server._web_asset_path("../secret.txt") is None


def test_server_defaults_to_lan_bind(monkeypatch) -> None:
    monkeypatch.delenv("GEMIA_HOST", raising=False)
    monkeypatch.delenv("LUMERI_HOST", raising=False)

    assert server._configured_server_host() == "0.0.0.0"
    assert "http://127.0.0.1:7788" in server._server_urls("0.0.0.0", 7788)


def test_favicon_request_is_not_a_browser_console_404() -> None:
    status, cache_control, raw = make_request("GET", "/favicon.ico")

    assert status == 204
    assert cache_control == "no-store"
    assert raw == b""


def test_static_asset_responses_close_connections(monkeypatch, tmp_path: Path) -> None:
    assets = tmp_path / "tauri-app" / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "index.js").write_text("console.log('lumeri')", encoding="utf-8")
    monkeypatch.setattr(server, "_WEB_ASSETS_DIR", assets)

    status, headers_cache_control, body = make_request("GET", "/assets/index.js")
    response_headers = run_server_handler(server._Handler, create_raw_request("GET", "/assets/index.js"))["headers"]

    assert status == 200
    assert response_headers.get("connection") == "close"
    assert body == b"console.log('lumeri')"


def test_file_responses_support_byte_ranges(monkeypatch, tmp_path: Path) -> None:
    assets = tmp_path / "tauri-app" / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "index.js").write_bytes(b"0123456789")
    monkeypatch.setattr(server, "_WEB_ASSETS_DIR", assets)

    response = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/assets/index.js", headers={"Range": "bytes=2-5"}),
    )

    assert response["status"] == 206
    assert response["headers"].get("accept-ranges") == "bytes"
    assert response["headers"].get("content-range") == "bytes 2-5/10"
    assert response["headers"].get("content-length") == "4"
    assert response["body"] == b"2345"


def test_file_responses_reject_invalid_byte_ranges(monkeypatch, tmp_path: Path) -> None:
    assets = tmp_path / "tauri-app" / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "index.js").write_bytes(b"0123456789")
    monkeypatch.setattr(server, "_WEB_ASSETS_DIR", assets)

    response = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/assets/index.js", headers={"Range": "bytes=20-30"}),
    )

    assert response["status"] == 416
    assert response["headers"].get("content-range") == "bytes */10"
    assert response["headers"].get("accept-ranges") == "bytes"
    assert response["body"] == b""


def test_file_route_serves_temp_outputs_without_allowing_escape(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_BASE_DIR", tmp_path)
    output = tmp_path / "temp" / "veo" / "preview.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"video")

    response = run_server_handler(server._Handler, create_raw_request("GET", "/file/temp/veo/preview.mp4"))
    assert response["status"] == 200
    assert response["body"] == b"video"

    escaped = run_server_handler(server._Handler, create_raw_request("GET", "/file/temp/../server.py"))
    assert escaped["status"] == 403

    unknown_root = run_server_handler(server._Handler, create_raw_request("GET", "/file/private/secret.mp4"))
    assert unknown_root["status"] == 403


def test_active_lumeri_css_allows_text_selection() -> None:
    root = Path(__file__).resolve().parents[1]
    source_css = (root / "tauri-app" / "src" / "global.css").read_text(encoding="utf-8")
    dist_css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.css")
    )

    assert "user-select: text" in source_css
    assert "-webkit-user-select: text" in source_css
    assert "body{background:var(--bg)" in dist_css
    assert "user-select:text" in dist_css
    assert "-webkit-user-select:text" in dist_css
    old_body_rule = (
        "body{background:var(--bg);color:var(--text);font-family:var(--font-ui);"
        "font-size:13px;line-height:1.5;font-weight:400;height:100vh;"
        "overflow:hidden;user-select:none"
    )
    assert old_body_rule not in dist_css


def test_active_lumeri_chat_separates_gemini_and_execution_tones() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.js")
    )
    dist_css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.css")
    )
    source_chat = (root / "tauri-app" / "src" / "components" / "ChatPanel.tsx").read_text(encoding="utf-8")

    assert "@keyframes lumeri-exec-flow" in dist_css
    assert "lumeri-exec-flow" in source_chat
    assert "lastLiveStatusIndex" in source_chat
    assert "isLive={index === lastLiveStatusIndex}" in source_chat
    assert "lumeri-exec-flow 3.8s ease-in-out infinite" in dist_js
    assert "T=t?e.reduce" in dist_js
    assert "isLive:t&&P===T" in dist_js
    assert "rgba(205,216,224,.62)" in dist_js
    assert '"think","read","execute","capability_call","preview_ready","dev_brief","revision_plan"' in dist_js
    assert 'e.voice!=="gemini"' in dist_js
    assert 'fontSize:14.5' in dist_js


def test_active_lumeri_chat_restores_runtime_task_messages() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.js")
    )

    assert "function lumeriRestoreChatFromTask" in dist_js
    assert "creative_runtime_task_id" in dist_js
    assert "agent_events" in dist_js
    assert "role:\"status\",content:he,statusType:Vh(H)" in dist_js
    assert "lumeriRestoreChatFromTask(u,Lr)" in dist_js


def test_active_lumeri_composer_keyboard_shortcuts() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.js")
    )
    source_chat = (root / "tauri-app" / "src" / "components" / "ChatPanel.tsx").read_text(encoding="utf-8")

    assert "Enter 运行，Shift+Enter 换行" in source_chat
    assert "Enter 运行，Shift+Enter 换行" in dist_js
    assert "Ctrl+C 中止" in source_chat
    assert "Ctrl+C 中止" in dist_js
    assert "e.key === \"Enter\" && !e.shiftKey" in source_chat
    assert 'S.key==="Enter"&&!S.shiftKey' in dist_js
    assert "window.addEventListener(\"keydown\", handleGlobalKeyDown)" in source_chat
    assert 'window.addEventListener("keydown",S)' in dist_js
    assert 'String(P.key).toLowerCase()==="c"' in dist_js


def test_active_lumeri_sidebar_has_new_session_action() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.js")
    )
    source_sidebar = (root / "tauri-app" / "src" / "components" / "MediaHistorySidebar.tsx").read_text(encoding="utf-8")

    assert "onNewSession" in source_sidebar
    assert "onOpenSession" in source_sidebar
    assert "aria-label=\"新会话\"" in source_sidebar
    assert "打开会话：" in source_sidebar
    assert "<span>新会话</span>" in source_sidebar
    assert 'window.dispatchEvent(new CustomEvent("lumeri:new-session"))' in source_sidebar
    assert "onNewSession:zf" in dist_js
    assert "onOpenSession:openSessionHistory" in dist_js
    assert 'title:"新会话"' in dist_js
    assert 'children:"新会话"' in dist_js
    assert 'new CustomEvent("lumeri:new-session")' in dist_js
    assert "function zf()" in dist_js
    assert 'function openSessionHistory' in dist_js
    assert '/session-history/${encodeURIComponent(u)}' in dist_js


def test_active_lumeri_minimal_ui_shell_hides_extra_panels_by_default() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_index = (root / "tauri-app" / "dist" / "index.html").read_text(encoding="utf-8")
    minimal_js = (root / "tauri-app" / "dist" / "assets" / "minimal-ui.js").read_text(encoding="utf-8")

    assert "/assets/minimal-ui.js" in dist_index
    assert "lumeri-minimal-ui" in minimal_js
    assert "lumeri-side-drawer" in minimal_js
    assert "lumeri-quick-tools" in minimal_js
    assert "lumeri-chat-panel" in minimal_js
    assert "lumeri-chat-log" in minimal_js
    assert ".alink-trigger-row" in minimal_js
    assert "#lumeri-crt-panel" in minimal_js
    assert "lumeri-preview-pane" in minimal_js
    assert "素材和历史" in minimal_js
    assert "高级工具" in minimal_js
    assert "display: none !important;" in minimal_js
    assert "body.lumeri-minimal-ui.lumeri-show-library .lumeri-side-drawer" in minimal_js
    assert "body.lumeri-minimal-ui.lumeri-show-tools .lumeri-quick-tools" in minimal_js
    assert "body.lumeri-minimal-ui.lumeri-show-tools .lumeri-chat-log" in minimal_js
    assert "body.lumeri-minimal-ui.lumeri-show-tools #lumeri-crt-panel.visible" in minimal_js


def test_active_lumeri_timeline_uses_real_clip_durations_and_sequence_playback() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.js")
    )

    assert "function Gm(e){const t=Number(e.duration)" in dist_js
    assert "i||n>.01?l||o:o||l||.1" in dist_js
    assert "J=_.useMemo(()=>{let u=0;return N.clips.map(p=>{const m=Gm(p)" in dist_js
    assert 'function Md(e,t){if(t==="image")return zd;const n=Number(e.duration)' in dist_js
    assert 'source_out:y==="image"?zd:kn(a.trimmed||((Number(a.inPoint)||0)>.01)?a.outPoint:a.duration||a.outPoint)' in dist_js
    assert "outPoint:m.trimmed?Number(m.outPoint??mt):mt,trimmed:!!m.trimmed" in dist_js
    assert "playhead:p.clips.reduce((m,v)=>m+Gm(v),0)" in dist_js
    assert "const v=!!m.trimmed||Math.abs(Number(m.inPoint)||0)>.01" in dist_js
    assert "if(R&&Se.previewSrc&&R!==Se.previewSrc)return" in dist_js
    assert "},[Se,R]),af=_.useCallback" in dist_js
    assert 'u.addEventListener("ended",p)' in dist_js
    assert "selectedClipId:he.clip.id,playhead:he.start" in dist_js
    assert "qt.current={clipId:he.clip.id,time:he.clip.inPoint}" in dist_js
    assert 'he.clip.mediaKind!=="image"' in dist_js
    assert 'u.play().then(()=>tt(!0)).catch(()=>tt(!1))' in dist_js
    assert "[ee?.id,R,et]" in dist_js


def test_active_lumeri_frontend_uses_local_font_stack_only() -> None:
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "tauri-app" / "index.html",
        root / "tauri-app" / "dist" / "index.html",
        root / "tauri-app" / "src-tauri" / "tauri.conf.json",
        root / "tauri-app" / "dist" / "assets" / "index-BH3QfU6L.css",
    ]

    for path in files:
        content = path.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in content
        assert "fonts.gstatic.com" not in content


def test_active_lumeri_task_output_loader_uses_video_outputs_only() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.js")
    )
    creative_js = (root / "tauri-app" / "dist" / "assets" / "creative-runtime-ui.js").read_text(encoding="utf-8")
    source_app = (root / "tauri-app" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "firstPlayableVideoOutput" in source_app
    assert "PLAYABLE_VIDEO_RE" in source_app
    assert "serverRelativeOutputPath" in source_app
    assert "outputs|frames|styled|demo|inputs|uploads|temp|timeline" in source_app
    assert "taskArtifactSummary" in source_app
    assert "preview:" in source_app
    assert "artifacts:" in source_app
    assert "lumeriTaskArtifactSummary" in dist_js
    assert "preview:" in dist_js
    assert "artifacts:" in dist_js
    assert "(outputs|frames|styled|demo|inputs|uploads|temp|timeline)" in dist_js
    assert "stream_logs: true" in source_app
    assert "stream_logs:!0" in dist_js
    assert 'Array.isArray(v)?v.find(H=>/\\.(mp4|mov|m4v|webm)$/i.test(String(H).split("?")[0]))' in dist_js
    assert 'v[0].split("/").pop()' not in dist_js
    assert '"outputs/"+H' not in dist_js
    assert 'const H=Fd(L)||"outputs/"+(L.split("/").pop()??""),B=await Aa(H,"video","video/mp4")' in dist_js
    assert "function applyTaskOutputsToTimeline" in dist_js
    assert "timeline_updates" in dist_js
    assert "replace_clip_media" in dist_js
    assert "B&&applyTaskOutputsToTimeline(p.data,L,B)" in dist_js
    assert "isPreviewableMedia" in creative_js
    assert '"outputs", "frames", "styled", "demo", "inputs", "uploads", "temp", "timeline"' in creative_js
    assert 'pass.preview_path || (isPreviewableMedia(rawOutput) ? rawOutput : "") || pass.artifact_path || rawOutput' in creative_js
    assert 'payload?.task && Array.isArray(payload.task.render_passes)' in creative_js
    assert "已收到反馈：" in creative_js
    assert "revisionPlanFor" in creative_js
    assert "creative_runtime_task_id" in creative_js
    assert "hydrateSessionTask" in creative_js
    assert "attachTaskToSessionSave" in creative_js
    assert 'window.addEventListener("lumeri:new-session", clearRuntimeTask)' in creative_js
    assert "window.__lumeriCreativeRuntimeClear = clearRuntimeTask" in creative_js
    assert "function clearRuntimeTask()" in creative_js
    assert "execution_logs" in creative_js
    assert "Gemini / execution logs" in creative_js
    assert "crt-log-stream" in creative_js
    assert 'pathname === "/session-history"' in creative_js
    assert "#lumeri-crt-panel{position:fixed;right:14px;bottom:92px" in creative_js


def test_active_lumeri_frontend_uses_human_error_and_artifact_statuses() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "tauri-app" / "dist" / "assets").glob("index-*.js")
    )
    source_app = (root / "tauri-app" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "friendlyError" in source_app
    assert "taskUserMessage" in source_app
    assert "user_message" in dist_js
    assert "artifact_ready" in dist_js
    assert "preview_ready" in dist_js
    assert "lumeriTaskMessage" in dist_js
    assert 'pt(`错误: ${m}`,"error")' not in dist_js
    assert 'pt(`错误: ${z}`,"error")' not in dist_js
