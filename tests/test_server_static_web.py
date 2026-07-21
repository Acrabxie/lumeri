from pathlib import Path

import server
from tests_http_harness import create_raw_request, run_server_handler

def make_request(method, path, headers=None, body=None):
    raw_request = create_raw_request(method, path, headers, body)
    response = run_server_handler(server._Handler, raw_request)
    return response["status"], response["headers"].get("cache-control", ""), response["body"]


def test_server_startup_does_not_eagerly_expand_the_lumerai_video_package() -> None:
    source = Path(server.__file__).read_text(encoding="utf-8")

    # Importing lumerai.sandbox executes lumerai/__init__.py, which expands the
    # complete video surface.  That context variable was unused here and made
    # cold start depend on hundreds of small reads from the external volume.
    assert "from lumerai.sandbox import sandbox_ctx as _sandbox_ctx" not in source


def test_root_is_intentionally_blank() -> None:
    """Root is reserved for the future Lumeri family portal; Video lives at /video."""
    response = run_server_handler(server._Handler, create_raw_request("GET", "/"))

    assert response["status"] == 200
    html = response["body"].decode("utf-8")
    assert "v3.js" not in html
    assert "<body></body>" in html


def test_video_serves_frontend() -> None:
    """The Lumeri Video UI (static/v3/ on disk) is served under /video."""
    video = run_server_handler(server._Handler, create_raw_request("GET", "/video/"))

    assert video["status"] == 200
    html = video["body"].decode("utf-8")
    assert "/video/v3.js" in html
    assert "/video/v3.css" in html


def test_video_serves_lumeri_working_indicator_assets() -> None:
    for path, expected in (
        ("/video/lumeri-working.svg", "Lumeri 正在工作"),
        ("/video/lumeri-working-static.svg", "Lumeri"),
    ):
        response = run_server_handler(server._Handler, create_raw_request("GET", path))

        assert response["status"] == 200
        assert response["headers"].get("content-type", "").startswith("image/svg+xml")
        svg = response["body"].decode("utf-8")
        assert expected in svg
        assert "width=\"90\"" in svg


def test_video_working_indicator_renders_under_assistant_output() -> None:
    root = Path(server.__file__).resolve().parent
    css = (root / "static/v3/v3.css").read_text(encoding="utf-8")
    source = (root / "static/v3/v3.js").read_text(encoding="utf-8")

    assert "assistantMarkHtml" in source
    assert "startedAt: Date.now()" in source
    assert "completedAt: null" in source
    assert "formatWorkElapsed(turn, isActiveTurn)" in source
    assert "const shouldShowMark = isActiveTurn || hasAssistant" in source
    assert 'isActiveTurn ? "lumeri-working.svg" : "lumeri-working-static.svg"' in source
    assert "${assistantHtml}" in source
    assert "${assistantMarkHtml}" in source
    assert source.count("${assistantMarkHtml}") == 1
    assert source.index("${assistantHtml}") < source.index("${assistantMarkHtml}")
    assert ".assistant-workmark" in css
    assert ".assistant-workmark.is-static" in css
    assert "width: 24px" in css
    assert "height: 12px" in css
    assert "font-variant-numeric: tabular-nums" in css
    assert "border-radius: var(--shape-sm)" in css


def test_signed_in_avatar_opens_a_real_account_menu() -> None:
    root = Path(server.__file__).resolve().parent
    html = (root / "static/v3/index.html").read_text(encoding="utf-8")
    css = (root / "static/v3/v3.css").read_text(encoding="utf-8")
    source = (root / "static/v3/v3.js").read_text(encoding="utf-8")

    assert 'aria-controls="account-menu"' in html
    assert 'id="account-menu"' in html
    for action in ("settings", "setup", "help", "logout"):
        assert f'data-account-action="{action}"' in html
    assert '<use href="#i-sliders"/>' in html
    assert '<use href="#i-spark"/>' in html
    assert "openAccountMenu" in source
    assert 'action === "settings"' in source and "openModelPicker()" in source
    assert 'action === "setup"' in source and "openSetupPanel()" in source
    assert 'postAuth("/auth/logout", {})' in source
    assert ".account-menu" in css
    assert "account-menu-in" in css


def test_settings_opens_a_split_page_with_real_sections() -> None:
    root = Path(server.__file__).resolve().parent
    css = (root / "static/v3/v3.css").read_text(encoding="utf-8")
    source = (root / "static/v3/v3.js").read_text(encoding="utf-8")

    assert 'overlay.className = "settings-overlay"' in source
    assert 'class="settings-sidebar"' in source
    assert 'class="settings-detail"' in source
    for section in ("model", "safety", "account"):
        assert f'data-settings-section="{section}"' in source
        assert f'data-settings-panel="{section}"' in source
    assert 'id="settings-sandbox-toggle"' in source
    assert 'id="settings-plan-toggle"' in source
    assert 'id="settings-account-detail"' in source
    assert "els.sandboxBtn?.click()" in source
    assert "els.planBtn?.click()" in source
    assert 'fetch("/auth/session")' in source
    assert 'fetch("/auth/logout"' in source
    assert ".settings-page" in css
    assert "grid-template-columns: 252px minmax(0, 1fr)" in css


def test_legacy_v3_redirects_to_video() -> None:
    index = run_server_handler(server._Handler, create_raw_request("GET", "/v3/"))
    assert index["status"] == 301
    assert index["headers"].get("location") == "/video"

    asset = run_server_handler(server._Handler, create_raw_request("GET", "/v3/v3.js"))
    assert asset["status"] == 301
    assert asset["headers"].get("location") == "/video/v3.js"


def test_retired_tauri_routes_are_gone() -> None:
    for method, path in (
        ("GET", "/tasks"),
        ("GET", "/task/some-task"),
        ("GET", "/assets/index.js"),
        ("GET", "/next"),
        ("POST", "/run-prompt"),
        ("POST", "/run-skill"),
        ("POST", "/quick-action"),
        ("POST", "/merge-clips"),
        ("POST", "/answer-ask/abc"),
        ("POST", "/revise-task/abc"),
        ("POST", "/task/abc/feedback"),
    ):
        response = run_server_handler(server._Handler, create_raw_request(method, path))
        assert response["status"] == 404, f"{method} {path} should be retired, got {response['status']}"


def test_runtime_api_is_feature_flagged(monkeypatch) -> None:
    monkeypatch.delenv("LUMERAI_VNEXT", raising=False)

    response = run_server_handler(
        server._Handler,
        create_raw_request("POST", "/runtime/dev/workspace", body={"session_id": "proj_rt"}),
    )

    assert response["status"] == 404
    assert response["body_json"]["error"] == "vNext runtime is disabled"


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


def test_video_asset_responses_close_connections() -> None:
    response = run_server_handler(server._Handler, create_raw_request("GET", "/video/v3.js"))

    assert response["status"] == 200
    assert response["headers"].get("connection") == "close"


def test_file_responses_support_byte_ranges(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_BASE_DIR", tmp_path)
    output = tmp_path / "temp" / "range.bin"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"0123456789")

    response = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/file/temp/range.bin", headers={"Range": "bytes=2-5"}),
    )

    assert response["status"] == 206
    assert response["headers"].get("accept-ranges") == "bytes"
    assert response["headers"].get("content-range") == "bytes 2-5/10"
    assert response["headers"].get("content-length") == "4"
    assert response["body"] == b"2345"


def test_file_responses_reject_invalid_byte_ranges(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_BASE_DIR", tmp_path)
    output = tmp_path / "temp" / "range.bin"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"0123456789")

    response = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/file/temp/range.bin", headers={"Range": "bytes=20-30"}),
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


def test_quanta_pager_static_files_support_get_head_and_query_strings() -> None:
    path = "/video/quanta.html?session_id=session_1&frame=0:0:img_001"
    get_response = run_server_handler(server._Handler, create_raw_request("GET", path))
    head_response = run_server_handler(server._Handler, create_raw_request("HEAD", path))

    assert get_response["status"] == 200
    assert get_response["headers"].get("content-type", "").startswith("text/html")
    assert b'src="quanta.js"' in get_response["body"]
    assert head_response["status"] == 200
    assert head_response["body"] == b""
    assert head_response["headers"].get("content-length") == str(len(get_response["body"]))

    for asset_path, content_type in (
        ("/video/quanta.css?cache=1", "text/css"),
        ("/video/quanta.js?cache=1", "text/javascript"),
    ):
        response = run_server_handler(server._Handler, create_raw_request("GET", asset_path))
        assert response["status"] == 200
        assert response["headers"].get("content-type", "").startswith(content_type)
