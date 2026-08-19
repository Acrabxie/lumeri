"""Append-only record of refused requests.

Waffo's merchant terms require a platform to be able to show that its policy is
enforced, not merely published. That evidence is this ledger.

What is recorded: when, which surface, which category, which rule or
combination fired, and a SHA-256 of the normalized prompt.

What is deliberately *not* recorded: the prompt text. Writing a refused
prompt to disk means the platform now stores the very content it just refused —
for the minors category that would be actively harmful, and for every category
it turns a log file into a liability. The hash still supports the two things
the evidence has to do: prove the refusal happened, and recognise the same
prompt arriving again.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gemia.moderation.output import OutputVerdict  # pragma: no cover — typing only
    from gemia.moderation.checker import Verdict

_LOCK = threading.Lock()

_DEFAULT_RELATIVE = Path("logs") / "moderation" / "rejections.jsonl"


def _ledger_path() -> Path:
    """Resolve the ledger file, honouring ``LUMERI_MODERATION_LEDGER``."""
    override = os.environ.get("LUMERI_MODERATION_LEDGER", "").strip()
    if override:
        return Path(override).expanduser()
    # gemia/moderation/ledger.py → repository root
    return Path(__file__).resolve().parents[2] / _DEFAULT_RELATIVE


def prompt_fingerprint(text: str) -> str:
    """Stable SHA-256 of the normalized prompt, used instead of the text."""
    from gemia.moderation.normalize import normalize

    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def record_rejection(verdict: "Verdict", *, surface: str, text: str) -> None:
    """Append one refusal to the ledger.

    Never raises: a ledger that cannot be written must not turn into a failed
    user request, and must not become a way to disable enforcement by making
    the path unwritable.

    Args:
        verdict: The refusing verdict.
        surface: Where the refusal happened, e.g. ``"image.generate"``.
        text: The refused prompt. Hashed, never stored.
    """
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "surface": surface,
        "category": verdict.category.value if verdict.category else "",
        "blocking_id": verdict.blocking_id,
        "rule_ids": list(verdict.rule_ids),
        "signals": sorted(signal.value for signal in verdict.signals),
        "prompt_sha256": prompt_fingerprint(text),
    }
    _append(entry)


def _append(entry: dict) -> None:
    """Write one ledger line. Never raises — see :func:`record_rejection`."""
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        path = _ledger_path()
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        # Enforcement already happened; losing the audit line is the lesser
        # failure and must stay silent to the user.
        pass


def asset_fingerprint(path: Path) -> str:
    """SHA-256 of a generated file, read in chunks so a long video is cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_output_rejection(
    verdict: "OutputVerdict",
    *,
    surface: str,
    asset_id: str,
    kind: str,
    path: "Path | None" = None,
) -> None:
    """Append one refused *generation* to the ledger.

    Waffo asks for moderation logs covering model outputs, not only prompts.
    The evidence recorded is the same shape as a prompt refusal: when, which
    surface, which category, and a fingerprint — never the media itself.

    The fingerprint is omitted for the child-safety categories. For every other
    category a hash is a useful way to recognise the same asset arriving again;
    for this one, building a durable index of such files is not something a
    general-purpose platform should be doing, and the record only needs to show
    that the refusal happened.
    """
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "surface": surface,
        "stage": "output",
        "kind": kind,
        "asset_id": asset_id,
        "category": verdict.category.value if verdict.category else "",
        "detector": verdict.detector,
        "scores": dict(verdict.scores),
    }
    sensitive = verdict.category is not None and verdict.category.value in {"minors", "csam"}
    if path is not None and not sensitive:
        try:
            entry["asset_sha256"] = asset_fingerprint(path)
        except OSError:
            pass
    _append(entry)


def record_unscreened(*, surface: str, asset_id: str, kind: str, reason: str) -> None:
    """Append one asset that reached the caller without being screened.

    Deployments that take payment set ``GEMIA_OUTPUT_MODERATION_REQUIRED`` and
    never produce these lines. Everywhere else — test runs, local development —
    the gap is written down rather than assumed away, so "was this screened?"
    is answerable from the ledger instead of from memory.
    """
    _append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "surface": surface,
            "stage": "output",
            "kind": kind,
            "asset_id": asset_id,
            "unscreened": True,
            "reason": reason,
        }
    )
