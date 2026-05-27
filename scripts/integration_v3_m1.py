"""End-to-end HTTP integration test for v3-A.M1.

Spawns server.py on a free localhost port with a scratch
LUMERI_V3_OUTPUT_ROOT, then drives the entire v3 session lifecycle
via real HTTP calls:

  1. POST /sessions                          -> session_id
  2. POST /sessions/{id}/assets              -> asset_id (raw body + X-Filename)
  3. GET  /sessions/{id}/stream              -> SSE reader (background thread)
  4. POST /sessions/{id}/turn                -> milestone prompt (async accept)
  5. wait for turn_complete event
  6. GET  /sessions/{id}/assets              -> list
  7. GET  /sessions/{id}/assets/{aid}        -> Range request + full download
  8. ffprobe final asset
  9. reconnect SSE with Last-Event-ID       -> replay buffered events
 10. POST /sessions/{id}/close               -> graceful shutdown

All evidence written to docs/v3-A-M1-evidence.log. Real Gemini,
real ffmpeg, no mocks.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

EVIDENCE_LOG = REPO_ROOT / "docs" / "v3-A-M1-evidence.log"
PROMPT = "把 /tmp/clip.mp4 前 5 秒裁掉,加暖色调,导出 1080p"
CLIP = Path("/tmp/clip.mp4")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def generate_clip(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=10:size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ], check=True, capture_output=True)


def ffprobe_dict(path: Path) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], text=True)
    return json.loads(out)


def wait_ready(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = HTTPConnection(host, port, timeout=1)
            conn.request("GET", "/health", headers={"Host": f"{host}:{port}"})
            r = conn.getresponse()
            r.read()
            if r.status == 200:
                return
        except Exception as exc:
            last_err = exc
        time.sleep(0.3)
    raise RuntimeError(f"server did not become ready in {timeout}s: {last_err!r}")


def http_request(host, port, method, path, *, body=None, extra_headers=None):
    conn = HTTPConnection(host, port, timeout=30)
    headers = {"Host": f"{host}:{port}"}
    if extra_headers:
        headers.update(extra_headers)
    if body is not None:
        headers.setdefault("Content-Length", str(len(body)))
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    return resp.status, dict(resp.getheaders()), raw, conn


def sse_reader(host, port, session_id, log_handle, events_collected: list, started_event: threading.Event,
               *, last_event_id: int | None = None, label: str = "primary",
               stop_event: threading.Event | None = None) -> None:
    headers = {"Host": f"{host}:{port}", "Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    conn = HTTPConnection(host, port, timeout=300)
    conn.request("GET", f"/sessions/{session_id}/stream", headers=headers)
    resp = conn.getresponse()
    log_handle.write(f"\n# SSE {label} connected, HTTP {resp.status}\n")
    log_handle.flush()
    started_event.set()
    if resp.status != 200:
        log_handle.write(resp.read().decode("utf-8", errors="replace") + "\n")
        return
    # Read SSE frames: 'id: N\n', 'data: {...}\n', '\n'
    current_id: int | None = None
    current_data: str | None = None
    t0 = time.monotonic()
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            raw = resp.fp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("id:"):
                current_id = int(line[3:].strip())
            elif line.startswith("data:"):
                current_data = line[5:].strip()
            elif line == "":
                if current_data is not None:
                    ts = time.monotonic() - t0
                    log_handle.write(f"# {label} t+{ts:7.3f}s\n")
                    log_handle.write(f"id: {current_id}\n")
                    log_handle.write(f"data: {current_data}\n\n")
                    log_handle.flush()
                    try:
                        ev = json.loads(current_data)
                        events_collected.append((current_id, ev))
                    except json.JSONDecodeError:
                        pass
                current_id = None
                current_data = None
    except Exception as exc:
        log_handle.write(f"# SSE {label} reader exception: {exc!r}\n")


def main() -> int:
    EVIDENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = EVIDENCE_LOG.open("w", encoding="utf-8")
    log.write(f"# Lumeri v3-A.M1 integration evidence\n")
    log.write(f"# generated: {datetime.now(timezone.utc).isoformat()}\n")
    log.write(f"# prompt:    {PROMPT}\n\n")
    log.flush()

    print(f"[1/12] generating /tmp/clip.mp4", flush=True)
    generate_clip(CLIP)
    clip_size = CLIP.stat().st_size
    log.write(f"# input clip: {CLIP} ({clip_size} bytes)\n")

    port = free_port()
    work_root = Path(tempfile.mkdtemp(prefix="v3-A-M1-"))
    host = "127.0.0.1"
    log.write(f"# server port: {port}\n# work root:  {work_root}\n\n")

    print(f"[2/12] spawning server.py on port {port}", flush=True)
    server_proc = subprocess.Popen(
        ["python3", "server.py", "--port", str(port), "--host", host],
        cwd=str(REPO_ROOT),
        env={**os.environ, "LUMERI_V3_OUTPUT_ROOT": str(work_root)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        print(f"[3/12] waiting for /health", flush=True)
        wait_ready(host, port, timeout=45)

        print(f"[4/12] POST /sessions", flush=True)
        status, headers, body, _ = http_request(host, port, "POST", "/sessions",
                                                body=b"", extra_headers={"Content-Length": "0"})
        log.write(f"## POST /sessions → {status}\n{body.decode()}\n\n")
        assert status == 201, f"expected 201, got {status}: {body[:200]!r}"
        session = json.loads(body)
        sid = session["session_id"]
        print(f"  session_id = {sid}", flush=True)

        print(f"[5/12] POST /sessions/{sid}/assets (raw upload)", flush=True)
        clip_bytes = CLIP.read_bytes()
        status, _, body, _ = http_request(
            host, port, "POST", f"/sessions/{sid}/assets",
            body=clip_bytes,
            extra_headers={
                "X-Filename": quote(CLIP.name),
                "Content-Type": "video/mp4",
                "Origin": f"http://{host}:{port}",
            },
        )
        log.write(f"## POST /assets → {status}\n{body.decode()}\n\n")
        assert status == 201, f"upload failed {status}: {body[:200]!r}"
        upload = json.loads(body)
        input_aid = upload["asset_id"]
        print(f"  input asset_id = {input_aid}", flush=True)

        print(f"[6/12] opening SSE primary reader", flush=True)
        events: list[tuple[int, dict]] = []
        started = threading.Event()
        sse_thread = threading.Thread(
            target=sse_reader,
            args=(host, port, sid, log, events, started),
            kwargs={"label": "primary"},
            daemon=True,
        )
        sse_thread.start()
        started.wait(timeout=5)
        time.sleep(0.3)  # let SSE settle

        print(f"[7/12] POST /sessions/{sid}/turn", flush=True)
        status, _, body, _ = http_request(
            host, port, "POST", f"/sessions/{sid}/turn",
            body=json.dumps({"message": PROMPT}).encode("utf-8"),
            extra_headers={
                "Content-Type": "application/json",
                "Origin": f"http://{host}:{port}",
            },
        )
        log.write(f"## POST /turn → {status}\n{body.decode()}\n\n")
        assert status == 202, f"turn submit failed {status}: {body[:200]!r}"

        print(f"[8/12] waiting for turn_complete (≤180s)", flush=True)
        deadline = time.monotonic() + 180
        turn_complete = None
        while time.monotonic() < deadline:
            for _eid, ev in events:
                if ev.get("kind") == "turn_complete":
                    turn_complete = ev
                    break
                if ev.get("kind") == "turn_error":
                    log.write(f"\n# turn_error: {ev}\n")
                    raise RuntimeError(f"turn_error: {ev.get('error')}")
            if turn_complete is not None:
                break
            time.sleep(0.5)
        if turn_complete is None:
            raise RuntimeError(f"timeout waiting for turn_complete (got {len(events)} events)")
        final_aids = turn_complete.get("final_asset_ids") or []
        print(f"  turn_complete: final_asset_ids = {final_aids}", flush=True)
        assert final_aids, "turn_complete had no final_asset_ids"

        print(f"[9/12] GET /sessions/{sid}/assets (list)", flush=True)
        status, _, body, _ = http_request(host, port, "GET", f"/sessions/{sid}/assets")
        log.write(f"## GET /assets → {status}\n{body.decode()[:600]}...\n\n")
        assert status == 200
        registry_assets = json.loads(body)["assets"]
        print(f"  registry has {len(registry_assets)} assets", flush=True)

        final_aid = final_aids[-1]
        print(f"[10/12] Range + full download for {final_aid}", flush=True)
        status, hdrs, partial, _ = http_request(
            host, port, "GET", f"/sessions/{sid}/assets/{final_aid}",
            extra_headers={"Range": "bytes=0-1023"},
        )
        log.write(f"## GET /assets/{final_aid} Range bytes=0-1023 → {status}\n")
        log.write(f"   Content-Range: {hdrs.get('Content-Range')}\n")
        log.write(f"   Content-Length: {hdrs.get('Content-Length')}\n")
        log.write(f"   bytes received: {len(partial)}\n\n")
        assert status == 206, f"expected 206 for Range, got {status}"
        assert len(partial) == 1024, f"expected 1024 bytes, got {len(partial)}"

        status, hdrs, full, _ = http_request(host, port, "GET", f"/sessions/{sid}/assets/{final_aid}")
        log.write(f"## GET /assets/{final_aid} full → {status} ({len(full)} bytes)\n\n")
        assert status == 200
        downloaded = work_root / "downloaded_final.mp4"
        downloaded.write_bytes(full)

        print(f"[11/12] ffprobe downloaded final asset", flush=True)
        meta = ffprobe_dict(downloaded)
        vstream = next(s for s in meta["streams"] if s["codec_type"] == "video")
        log.write(f"## ffprobe of downloaded final asset:\n")
        log.write(f"   width:    {vstream['width']}\n")
        log.write(f"   height:   {vstream['height']}\n")
        log.write(f"   codec:    {vstream['codec_name']}\n")
        log.write(f"   duration: {vstream['duration']}s\n")
        log.write(f"   size:     {downloaded.stat().st_size} bytes\n\n")

        print(f"[12/12] SSE reconnect with Last-Event-ID", flush=True)
        latest = max(eid for eid, _ in events) if events else 0
        replay_target = max(0, latest - 5)
        replay_events: list[tuple[int, dict]] = []
        replay_started = threading.Event()
        replay_stop = threading.Event()
        replay_thread = threading.Thread(
            target=sse_reader,
            args=(host, port, sid, log, replay_events, replay_started),
            kwargs={"label": "reconnect", "last_event_id": replay_target, "stop_event": replay_stop},
            daemon=True,
        )
        replay_thread.start()
        replay_started.wait(timeout=5)
        time.sleep(1.5)  # let replay drain
        replay_stop.set()
        # The replay should yield events with id > replay_target.
        replay_ids = sorted(eid for eid, _ in replay_events)
        log.write(f"## Reconnect with Last-Event-ID={replay_target}\n")
        log.write(f"   live stream had {len(events)} events, latest id = {latest}\n")
        log.write(f"   replay yielded {len(replay_events)} events: ids {replay_ids}\n\n")
        assert all(rid > replay_target for rid in replay_ids), \
            f"replay yielded ids ≤ {replay_target}: {replay_ids}"

        # POST close
        status, _, body, _ = http_request(
            host, port, "POST", f"/sessions/{sid}/close",
            body=b"", extra_headers={
                "Content-Length": "0",
                "Origin": f"http://{host}:{port}",
            },
        )
        log.write(f"## POST /close → {status}\n{body.decode()}\n\n")

        log.write(f"# ──────────────────────────────────────────────────────\n")
        log.write(f"# total live SSE events: {len(events)}\n")
        log.write(f"# all assertions PASSED\n")
        print(f"\nintegration test PASSED — evidence at {EVIDENCE_LOG}", flush=True)
        return 0

    finally:
        log.close()
        try:
            server_proc.terminate()
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()


if __name__ == "__main__":
    sys.exit(main())
