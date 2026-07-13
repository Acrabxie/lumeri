from pathlib import Path

import server
from tests_http_harness import create_raw_request, run_server_handler

def make_request(method, path, headers=None, body=None):
    raw_request = create_raw_request(method, path, headers, body)
    response = run_server_handler(server._Handler, raw_request)
    return response["status"], response["headers"].get("cache-control", ""), response["body"]


def test_root_serves_v3_frontend() -> None:
    """The default page is the Lumeri v3 frontend (tauri UI was retired 2026-07-06)."""
    response = run_server_handler(server._Handler, create_raw_request("GET", "/"))

    assert response["status"] == 200
    html = response["body"].decode("utf-8")
    assert "/v3/v3.js" in html
    assert "/v3/v3.css" in html


def test_root_and_v3_serve_same_index() -> None:
    root = run_server_handler(server._Handler, create_raw_request("GET", "/"))
    v3 = run_server_handler(server._Handler, create_raw_request("GET", "/v3/"))

    assert root["status"] == 200
    assert v3["status"] == 200
    assert root["body"] == v3["body"]


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


def test_v3_asset_responses_close_connections() -> None:
    response = run_server_handler(server._Handler, create_raw_request("GET", "/v3/v3.js"))

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
    path = "/v3/quanta.html?session_id=session_1&frame=0:0:img_001"
    get_response = run_server_handler(server._Handler, create_raw_request("GET", path))
    head_response = run_server_handler(server._Handler, create_raw_request("HEAD", path))

    assert get_response["status"] == 200
    assert get_response["headers"].get("content-type", "").startswith("text/html")
    assert b'/v3/quanta.js' in get_response["body"]
    assert head_response["status"] == 200
    assert head_response["body"] == b""
    assert head_response["headers"].get("content-length") == str(len(get_response["body"]))

    for asset_path, content_type in (
        ("/v3/quanta.css?cache=1", "text/css"),
        ("/v3/quanta.js?cache=1", "text/javascript"),
    ):
        response = run_server_handler(server._Handler, create_raw_request("GET", asset_path))
        assert response["status"] == 200
        assert response["headers"].get("content-type", "").startswith(content_type)
