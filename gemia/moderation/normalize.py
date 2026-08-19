"""Text normalization for content policy matching.

A raw keyword match is trivially defeated: ``c-h-i-l-d``, ``ch1ld``,
``ｃｈｉｌｄ``, and ``chiiild`` all read the same to a person and all miss a
naive ``"child" in text`` check. Every rule in :mod:`gemia.moderation.policy`
matches against the *normalized* form produced here, so an evasion has to
survive all of these transforms at once.

The transforms are deliberately lossy and are only ever used for matching —
the original text is what reaches the model when a prompt is allowed.
"""
from __future__ import annotations

import re
import unicodedata

# Characters used to break a word apart while keeping it readable. Stripped
# only when they sit *between* letters, so ordinary punctuation and hyphenated
# words in real prompts stay intact for the word-boundary checks.
_INTRA_WORD_SEPARATORS = r"\-_.·•*~^'`\"\\/|+=,:;!?()\[\]{}<>@#$%&"

# Zero-width and other invisible characters that survive copy-paste and split
# a word without any visible trace.
_INVISIBLE = re.compile(
    r"[​-‏‪-‮⁠-⁤﻿­᠎]"
)

# Homoglyph folding: visually identical characters from other scripts. Kept
# small and high-confidence — Cyrillic/Greek lookalikes are the ones actually
# used to slip past filters.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
        "і": "i", "ѕ": "s", "ј": "j", "ԁ": "d", "ɡ": "g", "ⅼ": "l", "ν": "v",
        "α": "a", "ε": "e", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "ι": "i",
        "κ": "k", "μ": "m", "η": "n",
    }
)

# Leetspeak. Applied only to runs that already look like an obfuscated word
# (see _deleet) so that ordinary numerals in a prompt are not mangled.
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

_WORD_WITH_DIGITS = re.compile(r"\b(?=[a-z]*[0-9@$])[a-z0-9@$]{3,}\b")
_REPEATED_CHAR = re.compile(r"(.)\1{2,}")
_WHITESPACE = re.compile(r"\s+")


# Whitespace is deliberately NOT in this class. Including it splices every
# adjacent word pair together ("a naked" → "anaked"), which silently defeats
# every word-boundary rule in the policy — an English prompt would sail past a
# screen that still looked like it was working. Space-separated evasion
# ("c h i l d") is the despaced variant's job instead.
_SEPARATOR_RUN = re.compile(rf"(?<=[a-z])[{_INTRA_WORD_SEPARATORS}]{{1,3}}(?=[a-z])")


def _strip_intra_word_separators(text: str) -> str:
    """Remove separators that sit between two letters: ``c-h-i-l-d`` → ``child``."""
    previous = None
    current = text
    # One pass only collapses every other gap in ``c-h-i-l-d``; repeat until stable.
    while current != previous:
        previous = current
        current = _SEPARATOR_RUN.sub("", current)
    return current


def _deleet(text: str) -> str:
    """Fold leetspeak inside words that already mix letters and digits."""
    return _WORD_WITH_DIGITS.sub(lambda m: m.group(0).translate(_LEET), text)


def normalize(text: str) -> str:
    """Return the canonical form used for all policy matching.

    Applies, in order: Unicode NFKC (full-width → ASCII, ligatures), invisible
    character removal, case folding, homoglyph folding, leetspeak folding,
    intra-word separator removal, repeated-character collapsing, and whitespace
    collapsing.

    Args:
        text: Raw user or model-authored text.

    Returns:
        Normalized text. Never ``None``; empty input yields ``""``.
    """
    if not text:
        return ""
    result = unicodedata.normalize("NFKC", text)
    result = _INVISIBLE.sub("", result)
    result = result.casefold()
    result = result.translate(_HOMOGLYPHS)
    result = _deleet(result)
    result = _strip_intra_word_separators(result)
    # ``chiiiild`` → ``child``. Only runs of three or more collapse, and they
    # collapse to one: no English word triples a letter, so a run that long is
    # a padded single letter. Genuine doubles ("bookkeeper", "spell") never
    # match the pattern and pass through untouched. Collapsing to two instead
    # would leave "chiiild" as "chiild", which matches no rule at all.
    result = _REPEATED_CHAR.sub(r"\1", result)
    return _WHITESPACE.sub(" ", result).strip()


def variants(text: str) -> tuple[str, str]:
    """Return ``(normalized, despaced)`` forms.

    The despaced form additionally removes all spaces, which catches evasions
    that split a term across a space (``"chi ld"``). It is only consulted by
    rules that opt in, because despacing creates false substrings across word
    boundaries otherwise.
    """
    canonical = normalize(text)
    # Strip separators again after despacing: "c - h - i - l - d" survives
    # normalize() untouched (each dash sits between two spaces, not two
    # letters), and only collapses once the spaces are gone.
    despaced = _strip_intra_word_separators(canonical.replace(" ", ""))
    return canonical, despaced
