"""Smoke test for GeminiClientV3 — verify real streaming.

Sends a short prompt and prints each delta with a monotonic timestamp.
Sub-second deltas between chunks prove bytes are flowing as Gemini
emits them, not buffered into one batch at the end.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gemia.gemini_client import GeminiClientV3  # noqa: E402


async def main() -> None:
    client = GeminiClientV3()
    print(f"model={client.model}", flush=True)
    print(f"proxy={client.proxy!r}", flush=True)
    start = time.monotonic()
    messages = [
        {
            "role": "user",
            "content": (
                "Write a 200-word fable about a fox and a streaming river. "
                "Plain prose, no lists, no headings."
            ),
        },
    ]
    chunk_count = 0
    async for event in client.stream_turn(messages, temperature=0.2):
        ts = time.monotonic() - start
        if event["kind"] == "text_delta":
            chunk_count += 1
            print(f"[{ts:7.3f}s] text_delta: {event['text']!r}", flush=True)
        elif event["kind"] == "finish":
            print(f"[{ts:7.3f}s] finish: {event['reason']}", flush=True)
        elif event["kind"] == "error":
            print(f"[{ts:7.3f}s] ERROR: {event['error']}", flush=True)
        else:
            print(f"[{ts:7.3f}s] {event}", flush=True)
    print(f"[{time.monotonic() - start:7.3f}s] done. chunk_count={chunk_count}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
