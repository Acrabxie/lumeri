#!/usr/bin/env python3
"""Live evidence collector for batch-2.1 generate_image.

Runs two real-API tests against AI Studio Nano Banana 2, requires
``gemini_studio_api_key`` in ``~/.gemia/config.json``:

  1. Direct dispatcher call — fires generate_image once, materializes the
     PNG, prints file path + size + `file` magic-byte output + ffprobe
     metadata + provider usage_metadata as cost proxy.

  2. End-to-end agent loop — drives a real AgentLoopV3 turn with the
     prompt "生成一张赛博朋克城市的图". Captures every SSE event the
     loop emits via a custom sink and prints them. Confirms (a) Gemini
     3.1 Pro picks generate_image, (b) the dispatcher fires for real,
     (c) the event payload carries asset_id + asset_url and **no
     base64**, (d) turn_complete arrives with the new asset as
     deliverable.

  3. Budget gate evidence — constructs a BudgetGuard with max_usd=0.05
     and shows the budget_gate event the agent loop emits when
     generate_image is requested at that cap (no API call fired).

Cost budget: roughly **$0.20 + ~50K OpenRouter input tokens** total.

Usage:
    python3 scripts/v3_live_generate_image_evidence.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Resolve repo root so the script works from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gemia.agent_loop_v3 import AgentLoopV3
from gemia.budget_guard import BudgetGuard
from gemia.tools import generate_image as generate_image_tool
from gemia.tools._context import AssetRegistry, ToolContext


HEAD = "═" * 70
SUB = "─" * 70


def banner(text: str) -> None:
    print(f"\n{HEAD}\n  {text}\n{HEAD}")


def sub(text: str) -> None:
    print(f"\n{SUB}\n  {text}\n{SUB}")


def file_magic(path: Path) -> str:
    try:
        result = subprocess.run(
            ["file", str(path)], capture_output=True, text=True, check=False
        )
        return result.stdout.strip()
    except Exception as exc:
        return f"file(1) failed: {exc}"


def ffprobe_image(path: Path) -> str:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    except Exception as exc:
        return f"ffprobe failed: {exc}"


# ───────────────────────── 1. direct dispatcher call ─────────────────────────


def run_direct_dispatcher_call() -> Path:
    banner("1. Direct dispatcher call")
    work = Path(tempfile.mkdtemp(prefix="lumeri-live-gi-direct-"))
    ctx = ToolContext(
        session_id="live-direct",
        output_dir=work,
        registry=AssetRegistry(),
        emit_progress=lambda u: print(f"  [progress] {u}"),
        extra={},
    )

    prompt = "A single red apple on a white plate, studio lighting, photorealistic"
    print(f"  prompt: {prompt!r}")
    print(f"  workdir: {work}")
    started = time.monotonic()
    result = asyncio.run(
        generate_image_tool.dispatch(
            {"prompt": prompt, "aspect_ratio": "1:1"}, ctx
        )
    )
    elapsed = time.monotonic() - started
    asset = ctx.registry.get(result["asset_id"])

    sub(f"dispatcher returned in {elapsed:.2f}s")
    print(f"  asset_id:           {result['asset_id']}")
    print(f"  summary:            {result['summary']}")
    print(f"  path:               {asset.path}")
    print(f"  size bytes:         {asset.path.stat().st_size:,}")
    print(f"  metadata:           {json.dumps(result['metadata'], indent=2, ensure_ascii=False)}")
    print(f"  file(1):            {file_magic(asset.path)}")
    print(f"  ffprobe:            {ffprobe_image(asset.path)}")

    # Negative assertion the way the user demands: walk result, fail if
    # any base64-looking string > 1 KB shows up.
    sub("base64 leakage check on dispatcher return value")
    leaked = _scan_for_base64(result)
    if leaked:
        print(f"  ✗ FAIL — base64-shaped data found in result: {leaked!r}")
        sys.exit(1)
    print("  ✓ no base64 in dispatcher return value (would fail loud if present)")

    return asset.path


def _scan_for_base64(value, path: str = "$") -> str:
    if isinstance(value, (bytes, bytearray)):
        return f"raw bytes at {path} ({len(value)}B)"
    if isinstance(value, str) and len(value) > 1024:
        prefix = value[:20]
        if prefix.startswith("iVBOR") or prefix.startswith("/9j/"):
            return f"base64-image-shaped at {path}"
    if isinstance(value, dict):
        for k, v in value.items():
            hit = _scan_for_base64(v, f"{path}.{k}")
            if hit:
                return hit
    elif isinstance(value, list):
        for i, v in enumerate(value):
            hit = _scan_for_base64(v, f"{path}[{i}]")
            if hit:
                return hit
    return ""


# ───────────────────────── 2. end-to-end agent loop ─────────────────────────


def run_agent_loop_e2e() -> None:
    banner("2. End-to-end agent loop (Gemini 3.1 Pro picks generate_image)")
    work = Path(tempfile.mkdtemp(prefix="lumeri-live-gi-loop-"))
    session_id = f"live-loop-{uuid.uuid4().hex[:6]}"

    events: list[dict] = []

    def sink(event: dict) -> None:
        events.append(event)
        # Live-print a compact line per event so progress is visible.
        kind = event.get("kind", "?")
        if kind == "model_text_delta":
            sys.stdout.write(event.get("delta") or "")
            sys.stdout.flush()
            return
        extras = {k: v for k, v in event.items() if k != "kind"}
        # Truncate long fields so stdout stays human-readable.
        for k in list(extras):
            if isinstance(extras[k], str) and len(extras[k]) > 200:
                extras[k] = extras[k][:200] + f"…[+{len(extras[k]) - 200}B]"
        print(f"\n  [event:{kind}] {json.dumps(extras, ensure_ascii=False)[:500]}")

    loop = AgentLoopV3(
        session_id=session_id,
        output_dir=work,
        max_tool_steps=4,
        budget_max_usd=1.00,         # plenty for one generate_image
        budget_max_seconds=180.0,
        emit_event=sink,
    )

    prompt = "生成一张赛博朋克城市夜景的图,雨夜霓虹"
    print(f"  session_id: {session_id}")
    print(f"  workdir:    {work}")
    print(f"  prompt:     {prompt!r}\n")

    started = time.monotonic()
    asyncio.run(loop.run_turn(prompt))
    elapsed = time.monotonic() - started

    sub(f"turn finished in {elapsed:.2f}s, {len(events)} events")

    # Pull the key events.
    tool_calls = [e for e in events if e.get("kind") == "model_tool_call_ready"]
    tool_results = [e for e in events if e.get("kind") == "tool_exec_result"]
    tool_errors = [e for e in events if e.get("kind") == "tool_exec_error"]
    completes = [e for e in events if e.get("kind") == "turn_complete"]

    print(f"  tool_calls:    {[c.get('tool_name') for c in tool_calls]}")
    print(f"  successes:     {[r.get('tool_name') for r in tool_results]}")
    print(f"  errors:        {[r.get('tool_name') for r in tool_errors]}")
    if completes:
        c = completes[0]
        print(f"  deliverables:  {c.get('deliverable_asset_ids')}")
        print(f"  created:       {c.get('created_asset_ids')}")
    else:
        print("  ✗ no turn_complete event — turn may have crashed")

    # Hard SSE-base64 check
    sub("base64 leakage check on every SSE event")
    leaks: list[str] = []
    for i, e in enumerate(events):
        hit = _scan_for_base64(e, path=f"events[{i}]")
        if hit:
            leaks.append(hit)
    if leaks:
        print(f"  ✗ FAIL — base64 in SSE events: {leaks}")
        sys.exit(1)
    print(f"  ✓ no base64 in any of the {len(events)} SSE events")

    # Confirm the dispatcher fired
    gi_results = [r for r in tool_results if r.get("tool_name") == "generate_image"]
    if not gi_results:
        print("  ⚠  model did NOT pick generate_image — agent loop integration unverified.")
        print("     (This is a model decision; the dispatcher itself was verified in part 1.)")
    else:
        asset_id = (gi_results[0].get("result") or {}).get("asset_id")
        asset_url = (gi_results[0].get("result") or {}).get("asset_url")
        print(f"  ✓ generate_image fired, asset_id={asset_id} asset_url={asset_url}")

    # Dump events to a file for later evidence pasting
    log_path = work / "sse_events.jsonl"
    log_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in events),
        encoding="utf-8",
    )
    print(f"\n  full event log written to: {log_path}")


# ───────────────────────── 3. budget gate evidence ─────────────────────────


def run_budget_gate_demo() -> None:
    banner("3. Budget gate — generate_image at cap=$0.05 (no API call)")
    guard = BudgetGuard(max_usd=0.05, max_seconds=600.0)
    decision = guard.check("generate_image")
    payload = decision.to_dict()
    print(f"  cap_usd:       ${guard.max_usd:.2f}")
    print(f"  estimated:     ${decision.estimated_cost_usd:.4f}")
    print(f"  decision.ok:   {decision.ok}")
    print(f"  reason:        {decision.reason}")
    print(f"  alternatives:  {decision.alternatives}")
    sub("budget_gate event the agent loop would emit (synthesized from decision)")
    event_shape = {
        "kind": "budget_gate",
        "call_id": "call_demo",
        "tool_name": "generate_image",
        "reason": decision.reason,
        "alternatives": decision.alternatives,
        "estimated_cost_usd": decision.estimated_cost_usd,
        "estimated_eta_sec": decision.estimated_eta_sec,
    }
    print(json.dumps(event_shape, indent=2, ensure_ascii=False))
    sub("tool_result fed back to the model")
    tool_result_shape = {
        "needs_approval": True,
        "reason": decision.reason,
        "alternatives": decision.alternatives,
        "estimated_cost_usd": decision.estimated_cost_usd,
        "estimated_eta_sec": decision.estimated_eta_sec,
    }
    print(json.dumps(tool_result_shape, indent=2, ensure_ascii=False))
    if decision.ok:
        print("  ✗ FAIL — budget gate did NOT block (expected ok=False)")
        sys.exit(1)
    print("  ✓ budget gate blocks generate_image at cap=$0.05 as expected")


# ──────────────────────────────── main ────────────────────────────────


def main() -> int:
    # Pre-flight: verify config has the key (cheap, no API call).
    config_path = Path.home() / ".gemia" / "config.json"
    if not config_path.exists():
        print(f"✗ {config_path} missing. Create it with gemini_studio_api_key first.")
        return 1
    try:
        cfg = json.loads(config_path.read_text())
    except Exception as exc:
        print(f"✗ failed to parse {config_path}: {exc}")
        return 1
    key = (cfg.get("gemini_studio_api_key") or "").strip()
    if not key:
        print("✗ gemini_studio_api_key is empty or missing in config.json.")
        print("  Add: \"gemini_studio_api_key\": \"<AI Studio key>\"")
        return 1
    print(f"✓ gemini_studio_api_key found ({len(key)} chars)")

    direct_path = run_direct_dispatcher_call()
    run_agent_loop_e2e()
    run_budget_gate_demo()

    banner("DONE")
    print(f"  direct-call image: {direct_path}")
    print("  copy the three banner sections above into the evidence reply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
