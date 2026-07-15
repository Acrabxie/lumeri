"""FM3 (library side) gate — deliverable + typed/recoverable errors
(charter §10 failure-mode-3, P10; interface §4.2).

A creative library's tool must COOPERATE with the host turn-ledger so an
editing turn never dead-ends opaquely: every terminal success carries an asset
handle plus a ``next`` verification pointer, and every error is structured data
(``applied: False`` + ``error_code``), never a raised exception. (The host-side
projection of a recoverable failure into an honest partial answer is the
turn-ledger's own contract — see ``test_recoverable_library_failure_degrades_to_partial``
in ``test_v3_ledger_partial_disclosure.py``; the library only *feeds* typed
errors + a final asset.) Reference library: ``vector_motion``.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import vector_motion as vm


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id=f"test_ledger_{uuid.uuid4().hex[:8]}",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


def _brief() -> dict:
    return {"subject": {"kind": "logo_text", "text": "Lumeri"}, "intent": "reveal",
            "duration": 3.0, "seed": 7}


def test_success_carries_asset_handle_and_next(ctx: ToolContext) -> None:
    out = asyncio.run(vm.dispatch({"op": "create", "brief": _brief()}, ctx))
    assert out.get("applied") is True
    # asset handle the model can address later
    assert out.get("layer_id"), f"terminal success must carry an asset handle: {out}"
    # verification pointer so the model knows how to check its own work
    nxt = out.get("next")
    assert isinstance(nxt, str) and nxt, f"terminal success must carry a 'next' pointer: {out}"
    assert any(k in nxt for k in ("lumen_seek", "lumen_render", "verify")), (
        f"'next' must point at a real verification path, got: {nxt!r}"
    )


@pytest.mark.parametrize(
    ("args", "expected_code"),
    [
        ({"op": "definitely_not_an_op"}, "E_ARG"),
        ({"op": "create"}, "E_ARG"),                       # missing brief
        ({"op": "create", "brief": "not-an-object"}, "E_ARG"),
        ({"op": "adjust", "feedback": ["more playful"]}, "E_ARG"),   # missing layer_id
        ({"op": "adjust", "layer_id": "vm_x"}, "E_ARG"),            # missing feedback
        ({"op": "adjust", "layer_id": "ghost", "feedback": ["more playful"]}, "E_NOT_FOUND"),
    ],
)
def test_errors_are_typed_data_never_exceptions(ctx: ToolContext, args, expected_code) -> None:
    # Must return structured data, not raise.
    out = asyncio.run(vm.dispatch(args, ctx))
    assert isinstance(out, dict), f"error must be a dict, got {type(out)}"
    assert out.get("applied") is False, f"error must report applied=False: {out}"
    assert out.get("error_code") == expected_code, (
        f"expected typed error {expected_code}, got {out.get('error_code')}: {out}"
    )
    assert out.get("error_message"), "a typed error must carry a human-readable message"


def test_bad_args_error_is_marked_recoverable(ctx: ToolContext) -> None:
    # A create with a structurally-valid but semantically-bad brief must be a
    # recoverable typed error (the model can fix its args), never a crash.
    out = asyncio.run(
        vm.dispatch({"op": "create", "brief": {"subject": {"kind": "unknown_kind_zzz"}}}, ctx)
    )
    assert out.get("applied") is False
    assert out.get("error_code") == "E_ARG"
    assert out.get("recovery") == "fix_args", (
        f"a fixable-args error should hint recovery='fix_args': {out}"
    )
