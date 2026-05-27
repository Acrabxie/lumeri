"""Smoke test for all 5 v3 verbs implemented in milestone B.

Generates a 5-second testsrc clip via ffmpeg, registers it as v_001,
then drives each verb in sequence:

    1. edit_video  trim 1..4s            (5s -> 3s)
    2. color_grade warm  intensity=1.0
    3. add_overlay text  bottom_center
    4. analyze_media                     (no ffmpeg progress; spinner)
    5. export      mp4 1080p

Each section prints tool_exec_start, every progress callback the
dispatcher emits, and the tool_exec_result payload. After every output
file we run ffprobe to confirm the file exists and report duration,
resolution, and codec.

No mocks. All ffmpeg/ffprobe is real.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gemia.tools import DISPATCHER, AssetRegistry, ToolContext  # noqa: E402
from gemia.tools._context import ProgressUpdate  # noqa: E402


def banner(title: str) -> None:
    print(f"\n{'='*72}\n{title}\n{'='*72}", flush=True)


def make_progress_printer(verb: str):
    start = time.monotonic()

    def cb(u: ProgressUpdate) -> None:
        ts = time.monotonic() - start
        pct = f"{u.percent:5.1f}%" if u.percent is not None else "  ?? %"
        msg = u.message or ""
        print(f"  [{ts:6.2f}s] {verb} tool_exec_progress {pct}  {msg}", flush=True)

    return cb


def ffprobe_summary(path: Path) -> str:
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    data = json.loads(out)
    fmt = data.get("format") or {}
    duration = fmt.get("duration", "?")
    vstream = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {})
    w = vstream.get("width", "?")
    h = vstream.get("height", "?")
    codec = vstream.get("codec_name", "?")
    return f"{duration}s {w}x{h} {codec}"


def generate_test_clip(out: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


async def run_verb(verb: str, args: dict, ctx: ToolContext) -> dict:
    banner(f"VERB: {verb}")
    print(f"  args = {json.dumps(args, ensure_ascii=False)}", flush=True)
    start = time.monotonic()
    print(f"  [{0.00:6.2f}s] {verb} tool_exec_start", flush=True)
    ctx.emit_progress = make_progress_printer(verb)
    result = await DISPATCHER[verb](args, ctx)
    elapsed = time.monotonic() - start
    print(f"  [{elapsed:6.2f}s] {verb} tool_exec_result:", flush=True)
    print(f"    {json.dumps(result, ensure_ascii=False, default=str)}", flush=True)
    return result


async def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="lumeri-v3-smoke-"))
    print(f"work dir: {work}", flush=True)

    source = work / "source.mp4"
    generate_test_clip(source)
    print(f"generated test clip: {source} ({ffprobe_summary(source)})", flush=True)

    registry = AssetRegistry()
    record = registry.add_external(source, summary="5s testsrc 1280x720 30fps + 440Hz sine")
    print(f"registered: {record.asset_id} -> {record.path}", flush=True)

    ctx = ToolContext(
        session_id="smoke",
        output_dir=work,
        registry=registry,
        emit_progress=lambda _u: None,
    )

    # 1. edit_video trim
    r1 = await run_verb("edit_video", {
        "asset_id": record.asset_id,
        "operation": "trim",
        "trim": {"start_sec": 1.0, "end_sec": 4.0},
    }, ctx)
    print(f"  ffprobe: {ffprobe_summary(Path(registry.get(r1['asset_id']).path))}", flush=True)

    # 2. color_grade warm
    r2 = await run_verb("color_grade", {
        "asset_id": r1["asset_id"],
        "look": "warm",
        "intensity": 1.0,
    }, ctx)
    print(f"  ffprobe: {ffprobe_summary(Path(registry.get(r2['asset_id']).path))}", flush=True)

    # 3. add_overlay text
    r3 = await run_verb("add_overlay", {
        "asset_id": r2["asset_id"],
        "kind": "text",
        "text": "Lumeri v3",
        "position": "bottom_center",
        "start_sec": 0.0,
        "end_sec": 3.0,
        "font_size": 40,
        "font_color": "white",
    }, ctx)
    print(f"  ffprobe: {ffprobe_summary(Path(registry.get(r3['asset_id']).path))}", flush=True)

    # 4. analyze_media (no progress events expected)
    r4 = await run_verb("analyze_media", {
        "asset_id": r3["asset_id"],
        "focus": "color palette and any visible text",
    }, ctx)
    thumb = Path(r4["thumbnail_path"])
    print(f"  thumbnail exists: {thumb.exists()}  size={thumb.stat().st_size if thumb.exists() else '?'}B", flush=True)

    # 5. export 1080p
    r5 = await run_verb("export", {
        "asset_id": r3["asset_id"],
        "format": "mp4",
        "quality": "1080p",
        "platform": "youtube",
    }, ctx)
    print(f"  ffprobe: {ffprobe_summary(Path(registry.get(r5['asset_id']).path))}", flush=True)

    banner("FINAL REGISTRY")
    print(registry.compact_text(), flush=True)
    print(f"\nwork dir kept for inspection: {work}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
