"""F1 fix verification: max_tool_steps cap enforced within batched tool_calls.

Uses a FakeGeminiClient that returns 10 tool_calls in a single stream.
With max_tool_steps=8, asserts:

  (a) the fake dispatcher is invoked exactly 8 times,
  (b) calls 9 and 10 emit budget_gate without dispatch,
  (c) all 10 calls produce a tool_result message (8 real + 2 needs_approval),
  (d) FakeGeminiClient.stream_turn is called exactly once (outer cap stops the next round),
  (e) the turn ends with turn_error.

No real network, no real ffmpeg — this test isolates the loop's cap logic.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gemia.agent_loop_v3 import AgentLoopV3  # noqa: E402
from gemia.tools import DISPATCHER  # noqa: E402


class FakeGeminiClient:
    """Returns one stream with 10 tool_calls."""

    def __init__(self) -> None:
        self.model = "fake-gemini-v3"
        self.call_count = 0

    async def stream_turn(self, messages, *, tools=None, temperature=0.7):
        self.call_count += 1
        if self.call_count > 1:
            raise RuntimeError(
                f"FakeGeminiClient called {self.call_count}× — "
                f"outer cap should have stopped the loop before re-streaming."
            )
        for i in range(10):
            yield {
                "kind": "tool_call_start",
                "index": i,
                "id": f"call_{i:02d}",
                "name": "edit_video",
            }
            yield {
                "kind": "tool_call_args_delta",
                "index": i,
                "delta": json.dumps({
                    "asset_id": "v_001",
                    "operation": "trim",
                    "trim": {"start_sec": 0, "end_sec": 1},
                }),
            }
        yield {"kind": "finish", "reason": "tool_calls"}


dispatch_count = {"edit_video": 0}


async def fake_edit_video(args, ctx):
    dispatch_count["edit_video"] += 1
    src = ctx.registry.get(args["asset_id"])
    new_id = ctx.registry.allocate_id("video")
    record = ctx.registry.register_output(
        new_id,
        kind="video",
        path=src.path,
        summary=f"fake trim #{dispatch_count['edit_video']}",
        lineage=[args["asset_id"]],
    )
    return {"asset_id": new_id, "summary": record.summary, "metadata": {"fake": True}}


async def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="smoke-step-cap-"))
    src = work / "src.mp4"
    src.write_bytes(b"fake source bytes (registry only stores the path)")

    original = DISPATCHER["edit_video"]
    DISPATCHER["edit_video"] = fake_edit_video

    events: list[dict] = []

    try:
        client = FakeGeminiClient()
        agent = AgentLoopV3(
            session_id="smoke-step-cap",
            output_dir=work,
            gemini_client=client,
            emit_event=events.append,
            max_tool_steps=8,
        )
        agent.add_external_asset(src, summary="fake source")
        await agent.run_turn("trim a bunch")
    finally:
        DISPATCHER["edit_video"] = original

    by_kind: dict[str, int] = {}
    for ev in events:
        by_kind[ev["kind"]] = by_kind.get(ev["kind"], 0) + 1

    tool_messages = [m for m in agent._messages if m.get("role") == "tool"]

    print(f"FakeGeminiClient.call_count: {client.call_count}")
    print(f"dispatch_count[edit_video]:  {dispatch_count['edit_video']}")
    print(f"events emitted by kind:")
    for k, v in sorted(by_kind.items()):
        print(f"  {k:>25}: {v}")
    print(f"tool messages in conversation: {len(tool_messages)}")

    assert client.call_count == 1, f"expected exactly 1 stream_turn call, got {client.call_count}"
    assert dispatch_count["edit_video"] == 8, f"expected 8 dispatches, got {dispatch_count['edit_video']}"
    assert by_kind.get("budget_gate", 0) == 2, f"expected 2 budget_gate, got {by_kind.get('budget_gate', 0)}"
    assert by_kind.get("tool_exec_start", 0) == 8, f"expected 8 tool_exec_start, got {by_kind.get('tool_exec_start', 0)}"
    assert by_kind.get("tool_exec_result", 0) == 8, f"expected 8 tool_exec_result, got {by_kind.get('tool_exec_result', 0)}"
    assert by_kind.get("turn_error", 0) == 1, f"expected 1 turn_error, got {by_kind.get('turn_error', 0)}"
    assert len(tool_messages) == 10, f"expected 10 tool messages (8 real + 2 needs_approval), got {len(tool_messages)}"

    approval_msgs = [
        m for m in tool_messages
        if "needs_approval" in (m.get("content") or "")
    ]
    assert len(approval_msgs) == 2, f"expected 2 needs_approval tool messages, got {len(approval_msgs)}"

    print("\nPASS: F1 fix verified")


if __name__ == "__main__":
    asyncio.run(main())
