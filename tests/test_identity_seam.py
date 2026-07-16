"""Per-request identity seam (docs/identity-architecture-plan.md Phase 1).

The seam kills cross-client identity bleed: a request that pins
``X-Lumeri-Account`` acts as that account regardless of the process-global
``active.json``; an explicit pin to an unknown/malformed account resolves to
None (the 401 path), never a silent fallback to whoever is globally active.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gemia import accounts, identity


class _Headers(dict):
    def get(self, key, default=None):  # http.client.HTTPMessage-like
        return super().get(key, default)


def _handler(pin: str | None):
    headers = _Headers()
    if pin is not None:
        headers[identity.PIN_HEADER] = pin
    return SimpleNamespace(headers=headers)


@pytest.fixture
def accounts_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "accounts"
    monkeypatch.setattr(accounts, "ACCOUNTS_ROOT", root)
    monkeypatch.setattr(accounts, "ACTIVE_ACCOUNT_PATH", root / "active.json")
    return root


def _mk_account(root: Path, account_id: str) -> None:
    d = root / account_id
    d.mkdir(parents=True)
    (d / "profile.json").write_text(
        '{"account_id": "%s"}' % account_id, encoding="utf-8"
    )


def _set_global(root: Path, account_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "active.json").write_text(
        '{"account_id": "%s"}' % account_id, encoding="utf-8"
    )


def test_pin_overrides_global(accounts_root) -> None:
    _mk_account(accounts_root, "acct_pinned_11")
    _mk_account(accounts_root, "acct_global_22")
    _set_global(accounts_root, "acct_global_22")

    assert identity.resolve_account_id(_handler("acct_pinned_11")) == "acct_pinned_11"
    # Other clients (no pin) still see the global — no behavior change.
    assert identity.resolve_account_id(_handler(None)) == "acct_global_22"
    assert identity.resolve_account_id(None) == "acct_global_22"


def test_unknown_pin_is_none_not_fallback(accounts_root) -> None:
    _mk_account(accounts_root, "acct_global_22")
    _set_global(accounts_root, "acct_global_22")

    # Explicit pin to a nonexistent account must NOT silently act as the
    # globally-active one — that is the bug class the seam kills.
    assert identity.resolve_account_id(_handler("acct_missing_99")) is None


def test_malformed_pin_is_none_not_500(accounts_root) -> None:
    _mk_account(accounts_root, "acct_global_22")
    _set_global(accounts_root, "acct_global_22")

    for bad in ("../../etc", "short", "x" * 200, "spaces in id"):
        assert identity.resolve_account_id(_handler(bad)) is None


def test_blank_pin_falls_back_to_global(accounts_root) -> None:
    _mk_account(accounts_root, "acct_global_22")
    _set_global(accounts_root, "acct_global_22")

    assert identity.resolve_account_id(_handler("  ")) == "acct_global_22"


def test_two_requests_with_different_pins_are_isolated(accounts_root) -> None:
    """The point of per-request resolution: concurrent handlers resolve
    independently — no shared mutable state between them."""
    _mk_account(accounts_root, "acct_alpha_11")
    _mk_account(accounts_root, "acct_beta_222")
    _set_global(accounts_root, "acct_alpha_11")

    a = _handler("acct_alpha_11")
    b = _handler("acct_beta_222")
    assert identity.resolve_account_id(a) == "acct_alpha_11"
    assert identity.resolve_account_id(b) == "acct_beta_222"
    assert identity.resolve_account_id(a) == "acct_alpha_11"


def test_remote_requests_share_global_and_ignore_browser_pins(accounts_root) -> None:
    _mk_account(accounts_root, "acct_global_22")
    _mk_account(accounts_root, "acct_pinned_11")
    _set_global(accounts_root, "acct_global_22")

    unpinned = _handler(None)
    unpinned.headers["X-Lumeri-Remote"] = "1"
    pinned = _handler("acct_pinned_11")
    pinned.headers["X-Lumeri-Remote"] = "1"

    assert identity.resolve_account_id(unpinned) == "acct_global_22"
    assert identity.resolve_account_id(pinned) == "acct_global_22"
