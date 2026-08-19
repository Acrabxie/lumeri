"""Screening of generated media after generation, before display.

The companion to :mod:`gemia.moderation.surfaces`, which screens the *prompt*.
Waffo's AIGC requirements ask for both halves — "scan prompts before generation
and outputs after generation, before display" — and a prompt filter alone is
only the first half. A model can produce something its prompt did not ask for.

**Where the line is drawn.** Only media the *platform* paid to generate is
screened here. When a user brings their own key or their own model, the money
for that generation never passes through Waffo, so Waffo is not the legal seller
of the result and the platform is not the party answering for it; that path
stays covered by the prompt-side guard (which sits in the dispatcher and cannot
be bypassed by swapping models) plus the published AUP and its reporting
channel. :func:`platform_funded` is the single place that decision is made, so
introducing per-user keys or Lumeri credits later means editing one predicate
rather than hunting through call sites.

**What is checked and what deliberately is not.** Visual screening covers the
categories a detector can actually judge from pixels: sexual content, graphic
violence, and child-unsafe imagery. Copyright and trademark are *not* checked
here — Waffo lists those under the prompt-side blocklist ("copyrighted content
... trademark infringement" alongside "CSAM vocabulary"), which is where
``_IP_RULES`` already handles them. Asking a general detector to spot a brand
mark in a rendered frame produces false accusations at a rate that would make
the product unusable, and no serious platform enforces trademark that way; the
published AUP and a takedown channel carry that obligation instead.

**Failing closed.** When screening is required but the detector cannot answer,
the generation is refused rather than shown. An unavailable detector is the one
condition under which unscreened media would otherwise reach a viewer, and a
moderation step that silently disables itself under load is exactly what an
account review is looking for. Environments that have no detector configured
(every test run, local development) instead record an explicit *unscreened*
ledger entry, so "this asset was never screened" stays a provable fact rather
than an assumption.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from gemia.moderation.policy import Category

# Media kinds a visual detector can judge. ``audio`` is absent on purpose: the
# spoken-word case is served by screening the narration text on the prompt side,
# where the words are already available as text and no transcription step can
# mishear them into a false refusal.
SCREENED_OUTPUT_KINDS: frozenset[str] = frozenset({"image", "video"})

# Categories a pixel detector is asked to judge. Kept deliberately narrower than
# ``policy.Category`` — see the module docstring on why IP is prompt-side only.
VISUAL_CATEGORIES: frozenset[Category] = frozenset(
    {Category.SEXUAL, Category.VIOLENCE, Category.MINORS}
)

_REQUIRED_ENV = "GEMIA_OUTPUT_MODERATION_REQUIRED"


class OutputModerationUnavailable(RuntimeError):
    """No detector could answer for this asset.

    Not a policy decision — the content was never judged. The caller decides
    whether that means refusal (screening required) or an unscreened ledger
    entry (screening not configured in this environment).
    """


@dataclass(frozen=True)
class OutputVerdict:
    """Outcome of screening one generated asset.

    Attributes:
        allowed: ``False`` when the asset must not be shown.
        category: The AUP category responsible, when refused.
        reason: User-facing explanation, when refused.
        detector: Identifier of the backend that answered, for the ledger.
        scores: Raw per-category confidences, when the backend reports them.
            Kept for evidence: an account review asks how a decision was
            reached, and "the model said so" is a weaker answer than a number.
    """

    allowed: bool
    category: Category | None = None
    reason: str = ""
    detector: str = ""
    scores: dict[str, float] = field(default_factory=dict)


_ALLOWED = OutputVerdict(allowed=True)


class OutputBackend(Protocol):
    """A visual detector.

    Kept to one method so a hosted safety API, a local model, and the fake used
    in tests are interchangeable. Implementations must not raise for ordinary
    refusals — a refusal is a returned verdict. Raising is reserved for "I could
    not judge this", which the caller turns into a fail-closed refusal.
    """

    name: str

    async def inspect(self, kind: str, path: Path) -> OutputVerdict:
        """Judge one media file, or raise :class:`OutputModerationUnavailable`."""
        ...


def screening_required() -> bool:
    """Whether an unanswerable asset must be refused rather than let through.

    Set ``GEMIA_OUTPUT_MODERATION_REQUIRED=1`` in any deployment that takes
    payment. Left off, the absence of a detector is recorded rather than
    enforced, which is what test runs and local development need.
    """
    return os.environ.get(_REQUIRED_ENV, "").strip().lower() in {"1", "true", "yes"}


def platform_funded(ctx: object) -> bool:
    """Whether the platform paid for this generation.

    Today every generation runs on platform-held provider keys, so this is
    always true. It exists as a named predicate rather than an inline ``True``
    because the moment per-user keys or Lumeri credits land, this is the one
    place that has to change — and a reviewer asking "where do you decide whose
    content you screen?" gets a single answer.
    """
    funding = getattr(ctx, "extra", {}).get("funding") if ctx is not None else None
    if funding is None:
        return True
    return str(funding) == "platform"


async def check_output(
    kind: str,
    path: Path,
    *,
    backend: OutputBackend | None,
) -> OutputVerdict:
    """Screen one generated asset.

    Args:
        kind: Registry kind, e.g. ``"image"``. Kinds outside
            :data:`SCREENED_OUTPUT_KINDS` are allowed without inspection.
        path: The generated file.
        backend: The detector, or ``None`` when none is configured.

    Returns:
        An :class:`OutputVerdict`.

    Raises:
        OutputModerationUnavailable: When the asset is in scope but no backend
            could judge it. Never raised for a refusal.
    """
    if kind not in SCREENED_OUTPUT_KINDS:
        return _ALLOWED
    if backend is None:
        raise OutputModerationUnavailable(f"no detector configured for {kind}")
    if not path.exists():
        # A tool that reported an asset it did not write is a bug, not a policy
        # question — but it must not become a silent hole either.
        raise OutputModerationUnavailable(f"generated asset missing at {path}")
    return await backend.inspect(kind, path)


__all__ = [
    "OutputBackend",
    "OutputModerationUnavailable",
    "OutputVerdict",
    "SCREENED_OUTPUT_KINDS",
    "VISUAL_CATEGORIES",
    "check_output",
    "platform_funded",
    "screening_required",
]
