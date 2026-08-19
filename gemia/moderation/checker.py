"""Prompt screening: evaluate text against the policy and return a verdict.

Entry point is :func:`check_prompt`. It is deliberately synchronous, dependency
free, and fast (pure regex over a normalized string) so it can sit directly in
the request path of every generation call without adding latency worth
measuring.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from gemia.moderation.normalize import variants
from gemia.moderation.policy import (
    CATEGORY_MESSAGES,
    CATEGORY_SEVERITY,
    COMBINATIONS,
    RULES,
    Category,
    Rule,
    Signal,
    Strength,
)


@dataclass(frozen=True)
class Verdict:
    """Outcome of screening one piece of text.

    Attributes:
        allowed: ``False`` when the request must be refused.
        category: The AUP category responsible, when refused.
        reason: User-facing explanation, when refused.
        rule_ids: Every rule that matched, including non-blocking ones.
        signals: Every signal raised, for the ledger and for debugging.
        blocking_id: The hard rule id or combination id that caused the refusal.
    """

    allowed: bool
    category: Category | None = None
    reason: str = ""
    rule_ids: tuple[str, ...] = ()
    signals: frozenset[Signal] = field(default_factory=frozenset)
    blocking_id: str = ""


_ALLOWED = Verdict(allowed=True)


def check_prompt(text: str) -> Verdict:
    """Screen ``text`` against the six prohibited categories.

    Args:
        text: The prompt as written by the user or by the model when it calls a
            generation tool.

    Returns:
        A :class:`Verdict`. ``allowed`` is ``True`` for the overwhelming
        majority of creative prompts; see :mod:`gemia.moderation.policy` for why
        single violence signals never block on their own.
    """
    if not text or not text.strip():
        return _ALLOWED

    canonical, despaced = variants(text)
    if not canonical:
        return _ALLOWED

    matched_ids: list[str] = []
    signals: set[Signal] = set()
    hard_hits: list[Rule] = []

    for rule in RULES:
        subject = despaced if rule.despaced else canonical
        if not rule.pattern.search(subject):
            # A despaced rule still has to see the ordinary form, otherwise
            # multi-word patterns containing a space can never match.
            if not (rule.despaced and rule.pattern.search(canonical)):
                continue
        matched_ids.append(rule.rule_id)
        signals.add(rule.signal)
        if rule.strength is Strength.HARD:
            hard_hits.append(rule)

    # Candidates are (category, reason, blocking_id). Hard rules and satisfied
    # combinations compete on equal footing: "13岁女孩的情色照片" trips the adult
    # pornography rule *and* the minor × sexual combination, and it has to be
    # filed as the latter. Deciding hard-first would let the lesser category win
    # purely because it was checked first.
    candidates: list[tuple[Category, str, str]] = [
        (rule.category, CATEGORY_MESSAGES[rule.category], rule.rule_id)
        for rule in hard_hits
    ]
    for combo in COMBINATIONS:
        if not combo.required.issubset(signals):
            continue
        if combo.any_of and not (combo.any_of & signals):
            continue
        candidates.append((combo.category, combo.reason, combo.combo_id))

    if candidates:
        category, reason, blocking_id = min(
            candidates, key=lambda c: CATEGORY_SEVERITY[c[0].value]
        )
        return Verdict(
            allowed=False,
            category=category,
            reason=reason,
            rule_ids=tuple(matched_ids),
            signals=frozenset(signals),
            blocking_id=blocking_id,
        )

    return Verdict(
        allowed=True,
        rule_ids=tuple(matched_ids),
        signals=frozenset(signals),
    )
