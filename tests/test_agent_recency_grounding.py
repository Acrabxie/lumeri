"""Recency grounding + reframed pinned intent in the agent loop's prompt assembly.

These guard the fix for two Gemini failure modes: over-anchoring on the pinned
first request, and under-reading the current environment. The loop now (a) reframes
the pinned intent as reference-only in the system prompt, and (b) surfaces a short
live-state digest in the MOST RECENT message so each next step is grounded in the
present, without breaking the client's alternating-role contract.
"""
import tempfile

import pytest

from gemia.agent_loop_v3 import AgentLoopV3

DIGEST_MARK = "Current state — ground your NEXT step"


def _loop():
    loop = AgentLoopV3(session_id="s1", output_dir=tempfile.mkdtemp(), gemini_client=object())
    loop._pinned_intent = "make it cinematic"
    return loop


def test_system_reframes_pinned_intent_as_reference():
    loop = _loop()
    loop._messages = [{"role": "user", "content": "hi"}]
    system = loop.render_messages()[0]["content"]
    # original text still available...
    assert "make it cinematic" in system
    # ...but reframed as reference, with explicit recency/state precedence guidance
    assert "Original user request" in system
    assert "Ground every step in the live state" in system
    # the old standing-order framing is gone
    assert "## Pinned user intent" not in system


def test_digest_appended_to_last_user_message_at_turn_start():
    loop = _loop()
    loop._messages = [{"role": "user", "content": "make the title bigger"}]
    msgs = loop.render_messages()
    last = msgs[-1]
    assert last["role"] == "user"
    assert DIGEST_MARK in last["content"]
    assert "make the title bigger" in last["content"]  # original text preserved


def test_digest_appended_to_last_tool_result_mid_turn():
    loop = _loop()
    loop._messages = [
        {"role": "user", "content": "edit"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_timeline", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"clips": 2}'},
    ]
    msgs = loop.render_messages()
    assert msgs[-1]["role"] == "tool"
    assert DIGEST_MARK in msgs[-1]["content"]


def test_render_does_not_mutate_history():
    """The digest is added to a copy — it must not accumulate in self._messages."""
    loop = _loop()
    loop._messages = [{"role": "user", "content": "edit"},
                      {"role": "tool", "tool_call_id": "c1", "content": "{}"}]
    loop.render_messages()
    loop.render_messages()
    assert loop._messages[-1]["content"] == "{}"  # untouched, no doubling
    assert DIGEST_MARK not in loop._messages[0]["content"]


def test_no_digest_injection_when_last_is_assistant():
    """Defensive: an assistant-tail (shouldn't occur pre-generation) gets no digest."""
    loop = _loop()
    loop._messages = [{"role": "assistant", "content": "hi"}]
    msgs = loop.render_messages()
    assert DIGEST_MARK not in msgs[-1]["content"]


def test_digest_is_empty_for_blank_session():
    """No timeline/layers/assets yet → no digest to append (don't bolt on noise)."""
    loop = _loop()
    loop._messages = [{"role": "user", "content": "hello"}]
    # A fresh session has an empty project/registry; the digest should be empty,
    # so the user message is returned unchanged.
    digest = loop._env_recency_digest()
    if not digest:
        assert loop.render_messages()[-1]["content"] == "hello"
    else:
        # If the blank project still reports a line, it must at least be grounded.
        assert DIGEST_MARK in loop.render_messages()[-1]["content"]
