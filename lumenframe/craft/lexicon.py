"""Shared creative lexicon — one vocabulary, spoken the way people actually speak.

Every point library used to carry *two* hand-kept adjective tables (one for the
opening brief's ``feelings``, one for ``adjust`` feedback) and the vector engine
carried a third. They drifted: "cinematic" meant *elegance-first* when creating
and *drama-first* when adjusting, so "更电影感" pushed a look sideways instead of
deeper. This module is the single source of truth both paths read, so that class
of drift is structurally impossible rather than merely discouraged.

Two responsibilities:

* :data:`ANCHORS` — the **calibration anchors**. Not an attempt to enumerate
  every word a person might use (that race is unwinnable); a curated set of
  well-understood words with hand-tuned axis meanings. The agent translates
  arbitrary phrasing into these anchors, or into axis values directly; the
  anchors keep that translation honest and deterministic.
* :func:`candidates` — **spoken-form normalisation**. Chinese puts its
  intensifiers in places a ``^(更|再)`` prefix match never sees: 「快一点」
  「节奏再快一点」「别那么花」. This yields the (sign, word) readings of a phrase,
  strongest-first, for the caller to look up against its own table.

Normalisation is *attempt-based*: a stripped reading only counts if the caller
finds it in a real table, so trimming can never invent a meaning ("灵感" is
never read as "灵").
"""
from __future__ import annotations

import re
from typing import Iterator, NamedTuple


class Reading(NamedTuple):
    """One way to read a phrase.

    ``magnitude`` is a *default step*, not a measurement — see
    :data:`DEFAULT_STEP_DOC`. ``degree`` is the wording that produced it
    ("一点", "much", "" when the phrase named no degree at all), so callers can
    tell the agent exactly what was guessed and on what evidence.
    """

    magnitude: float
    word: str
    degree: str = ""

# --------------------------------------------------------------------------
# Calibration anchors
# --------------------------------------------------------------------------

#: Anchor word → axis deltas, read as "more <word>" (negated for "less").
#: Bilingual on purpose — the tool surface is driven from Chinese and English
#: briefs alike. Deltas may name axes a given library does not declare; those
#: are silently inert there (a ``warmth`` nudge is a no-op for a space with no
#: ``warmth`` axis), which is what lets one shared table serve six domains.
ANCHORS: dict[str, dict[str, float]] = {
    # --- energy / drive ------------------------------------------------
    "energetic": {"energy": +0.2}, "活力": {"energy": +0.2},
    "dynamic": {"energy": +0.15}, "动感": {"energy": +0.15},
    "fast": {"energy": +0.2, "pace": +0.2}, "快": {"energy": +0.2, "pace": +0.2},
    "slow": {"energy": -0.2, "pace": -0.2}, "慢": {"energy": -0.2, "pace": -0.2},
    "calm": {"energy": -0.2, "smoothness": +0.15}, "平静": {"energy": -0.2, "smoothness": +0.15},
    "冷静": {"energy": -0.2, "smoothness": +0.15}, "安静": {"energy": -0.2, "smoothness": +0.15},
    "gentle": {"energy": -0.15, "smoothness": +0.2}, "轻柔": {"energy": -0.15, "smoothness": +0.2},
    "subtle": {"energy": -0.15, "elegance": +0.1}, "克制": {"energy": -0.15, "elegance": +0.1},
    "bold": {"energy": +0.15, "drama": +0.1},
    "punchy": {"energy": +0.2, "contrast": +0.15, "drama": +0.1},
    "有力": {"energy": +0.2, "weight": +0.15, "drama": +0.1},
    "冲击力": {"drama": +0.25, "energy": +0.2, "contrast": +0.15},
    "沉稳": {"energy": -0.2, "smoothness": +0.15, "elegance": +0.1},
    "明快": {"energy": +0.2, "saturation": +0.1, "lift": +0.05},

    # --- drama / mood --------------------------------------------------
    "dramatic": {"drama": +0.2, "energy": +0.1}, "戏剧": {"drama": +0.2, "energy": +0.1},
    "moody": {"drama": +0.15, "energy": -0.1}, "氛围": {"drama": +0.15, "energy": -0.1},
    # Reconciled: create-path once said elegance-first, adjust-path drama-first.
    # Both readings were defensible, so both carry equal weight now.
    "cinematic": {"elegance": +0.15, "drama": +0.15, "energy": -0.05},
    "电影感": {"elegance": +0.15, "drama": +0.15, "energy": -0.05},
    "压抑": {"lift": -0.15, "drama": +0.2, "energy": -0.15},
    "tension": {"tension": +0.25, "drama": +0.15},
    "张力": {"tension": +0.25, "drama": +0.15},

    # --- character / taste ---------------------------------------------
    "playful": {"playfulness": +0.2}, "俏皮": {"playfulness": +0.2},
    "fun": {"playfulness": +0.2},
    "serious": {"playfulness": -0.2, "elegance": +0.1}, "严肃": {"playfulness": -0.2, "elegance": +0.1},
    "premium": {"elegance": +0.25, "energy": -0.1}, "高级": {"elegance": +0.25, "energy": -0.1},
    "elegant": {"elegance": +0.25, "smoothness": +0.1}, "优雅": {"elegance": +0.25, "smoothness": +0.1},
    "refined": {"elegance": +0.2, "complexity": +0.05}, "精致": {"elegance": +0.2, "complexity": +0.05},
    "sleek": {"elegance": +0.2, "smoothness": +0.15}, "洋气": {"elegance": +0.2, "saturation": -0.05},
    "cheap": {"elegance": -0.25}, "土": {"elegance": -0.2, "saturation": +0.15},
    "gritty": {"elegance": -0.2, "filmic": +0.15}, "粗糙": {"elegance": -0.2, "filmic": +0.15},
    "textured": {"filmic": +0.2, "elegance": +0.15, "contrast": +0.05},
    "质感": {"filmic": +0.2, "elegance": +0.15, "contrast": +0.05},
    "大气": {"elegance": +0.2, "negative_space": +0.15, "density": -0.1, "energy": -0.05},
    "retro": {"filmic": +0.25, "saturation": -0.1, "warmth": +0.1},
    "复古": {"filmic": +0.25, "saturation": -0.1, "warmth": +0.1},
    "modern": {"elegance": +0.15, "complexity": -0.1, "filmic": -0.15},
    "现代": {"elegance": +0.15, "complexity": -0.1, "filmic": -0.15},
    "科技感": {"elegance": +0.15, "warmth": -0.2, "smoothness": +0.1},
    "性冷淡": {"saturation": -0.25, "warmth": -0.15, "elegance": +0.2, "density": -0.15},

    # --- surface / motion quality --------------------------------------
    "smooth": {"smoothness": +0.2}, "顺滑": {"smoothness": +0.2},
    "soft": {"smoothness": +0.2, "contrast": -0.15, "drama": -0.1},
    "柔和": {"smoothness": +0.2, "contrast": -0.15, "drama": -0.1},
    "温柔": {"smoothness": +0.2, "energy": -0.15, "warmth": +0.1, "drama": -0.1},
    "crisp": {"contrast": +0.2, "smoothness": -0.1}, "锐利": {"contrast": +0.2, "smoothness": -0.15, "energy": +0.1},
    "steady": {"smoothness": +0.2, "drift": -0.15, "energy": -0.1},
    "稳": {"smoothness": +0.2, "drift": -0.15, "energy": -0.1},
    "shaky": {"smoothness": -0.2, "drift": +0.2, "energy": +0.1},
    "晃": {"smoothness": -0.2, "drift": +0.2, "energy": +0.1},
    "heavy": {"weight": +0.25, "drama": +0.1, "energy": -0.05},
    "厚重": {"weight": +0.25, "drama": +0.1, "energy": -0.05},
    "light": {"weight": -0.25, "energy": +0.1, "smoothness": +0.1},
    "轻盈": {"weight": -0.25, "energy": +0.1, "smoothness": +0.1},

    # --- density / complexity ------------------------------------------
    "minimal": {"complexity": -0.2, "density": -0.15, "elegance": +0.1},
    "极简": {"complexity": -0.2, "density": -0.15, "elegance": +0.1},
    "simple": {"complexity": -0.2}, "简单": {"complexity": -0.2},
    "clean": {"complexity": -0.15, "elegance": +0.1}, "干净": {"complexity": -0.15, "elegance": +0.1},
    "rich": {"density": +0.2, "complexity": +0.15}, "丰富": {"density": +0.2, "complexity": +0.15},
    "dense": {"density": +0.2}, "密": {"density": +0.2},
    "busy": {"complexity": +0.25, "density": +0.2, "elegance": -0.1},
    "花": {"complexity": +0.25, "density": +0.2, "elegance": -0.1},
    "花哨": {"complexity": +0.25, "density": +0.2, "elegance": -0.1},
    "sparse": {"density": -0.2, "negative_space": +0.2},
    "素": {"complexity": -0.2, "saturation": -0.15, "density": -0.15},
    "airy": {"negative_space": +0.2, "density": -0.15, "pace": -0.1},
    "呼吸感": {"negative_space": +0.2, "density": -0.15, "pace": -0.1},
    "留白": {"negative_space": +0.25, "density": -0.2},
    "空灵": {"negative_space": +0.2, "density": -0.15, "lift": +0.1, "smoothness": +0.15},

    # --- framing / balance ---------------------------------------------
    "tight": {"tightness": +0.25, "density": +0.1, "pace": +0.1},
    "紧凑": {"tightness": +0.25, "density": +0.1, "pace": +0.1},
    "loose": {"tightness": -0.2, "pace": -0.15, "energy": -0.1},
    "松弛": {"tightness": -0.2, "pace": -0.15, "energy": -0.1},
    "balanced": {"balance": +0.25}, "平衡": {"balance": +0.25},
    "comfortable": {"smoothness": +0.2, "balance": +0.15, "energy": -0.1},
    "舒服": {"smoothness": +0.2, "balance": +0.15, "energy": -0.1},

    # --- colour ---------------------------------------------------------
    "warm": {"warmth": +0.2}, "暖": {"warmth": +0.2},
    "cool": {"warmth": -0.2}, "冷": {"warmth": -0.2},
    "通透": {"lift": -0.1, "contrast": +0.1, "saturation": +0.05},
    "清新": {"saturation": +0.05, "warmth": -0.1, "lift": +0.1, "energy": +0.05},
    "高对比": {"contrast": +0.25}, "低饱和": {"saturation": -0.25},
    "对比": {"contrast": +0.2}, "饱和": {"saturation": +0.2},
    "亮": {"lift": +0.2}, "暗": {"lift": -0.15, "drama": +0.1},
    "vivid": {"saturation": +0.2, "contrast": +0.1}, "鲜艳": {"saturation": +0.2, "contrast": +0.1},
    "muted": {"saturation": -0.2}, "灰": {"saturation": -0.2, "lift": +0.1},
}


class Interpretation(NamedTuple):
    """How a phrase was read, with the evidence behind every part of it.

    Carried all the way out to the agent so nothing about the reading stays
    implicit: which anchor the wording landed on, which degree word (if any)
    set the size of the move, and whether that size was measured or defaulted.
    """

    phrase: str
    word: str
    magnitude: float
    degree: str
    nudges: dict[str, float]

    @property
    def guessed_degree(self) -> bool:
        """True when nothing in the wording pinned the size of the move."""
        return True  # wording alone never measures a degree; see DEFAULT_STEP_DOC

    def describe(self) -> dict[str, object]:
        return {
            "phrase": self.phrase,
            "read_as": self.word,
            "direction": "more" if self.magnitude > 0 else "less",
            "degree_word": self.degree or None,
            "step": round(abs(self.magnitude), 4),
            "step_is": DEFAULT_STEP_DOC,
        }


def interpret(phrase: str, table: dict) -> "Interpretation | None":
    """First reading of ``phrase`` that lands on a word ``table`` really holds."""
    for reading in candidates(phrase):
        nudges = table.get(reading.word)
        if nudges is not None:
            return Interpretation(
                phrase=str(phrase), word=reading.word,
                magnitude=reading.magnitude, degree=reading.degree,
                nudges=nudges,
            )
    return None


def anchor_words() -> list[str]:
    """Every calibration anchor, sorted — the agent-facing vocabulary."""
    return sorted(ANCHORS)


#: What each axis means, in one line, for agents translating unknown phrasing
#: into axis values directly. Keyed by axis name across every library; a caller
#: filters this to the axes its own space declares.
AXIS_GLOSSARY: dict[str, str] = {
    "energy": "overall drive; 0 still and quiet, 1 fast and forceful",
    "elegance": "restraint and polish; 0 rough and cheap, 1 refined and premium",
    "smoothness": "easing and continuity; 0 abrupt and jittery, 1 flowing",
    "complexity": "how many things happen at once; 0 one clear idea, 1 layered",
    "density": "how full the frame/timeline is; 0 sparse, 1 packed",
    "drama": "emotional contrast and weight; 0 neutral, 1 heightened",
    "warmth": "colour temperature; 0 cool/blue, 1 warm/amber",
    "playfulness": "wit and bounce; 0 serious, 1 whimsical",
    "contrast": "tonal separation; 0 flat, 1 hard blacks and bright highlights",
    "saturation": "colour intensity; 0 monochrome, 1 vivid",
    "lift": "shadow lift; 0 crushed blacks, 1 milky faded shadows",
    "filmic": "photochemical character (grain, halation); 0 clinical, 1 filmstock",
    "weight": "perceived mass of motion; 0 feather-light, 1 heavy and grounded",
    "pace": "how quickly beats/cuts arrive; 0 languid, 1 rapid",
    "invisibility": "how unnoticeable the cut craft is; 0 showy, 1 transparent",
    "variety": "how much the technique changes shot to shot; 0 uniform, 1 varied",
    "drift": "unanchored float in a camera move; 0 locked off, 1 wandering",
    "tension": "compositional unease; 0 settled and centred, 1 edge-loaded",
    "balance": "visual equilibrium; 0 lopsided, 1 evenly weighted",
    "negative_space": "breathing room around the subject; 0 filled, 1 airy",
    "tightness": "how closely events hug the grid/subject; 0 loose, 1 locked",
    "drive": "forward momentum of the pulse; 0 drifting, 1 insistent",
    "build": "how much intensity escalates over time; 0 flat, 1 rising",
    "organicness": "natural vs geometric form language; 0 hard-edged, 1 organic",
}


def axis_glossary(axes: tuple[str, ...] | list[str]) -> dict[str, str]:
    """One-line meanings for ``axes``, for surfacing alongside unknown words."""
    return {a: AXIS_GLOSSARY[a] for a in axes if a in AXIS_GLOSSARY}


# --------------------------------------------------------------------------
# Spoken-form normalisation
# --------------------------------------------------------------------------

#: Chinese direction words. Sign carries the direction; magnitude the emphasis.
_ZH_PREFIX: dict[str, float] = {
    "更加": +1.0, "更": +1.0, "再": +1.0, "还要": +1.0, "还得": +1.0,
    "多一点": +0.6, "多点": +0.6, "多些": +0.6,
    "少一点": -0.6, "少点": -0.6, "少些": -0.6, "少": -1.0,
    "特别": +1.5, "非常": +1.5, "超": +1.5, "极": +1.5, "很": +1.3,
    "稍微": +0.5, "稍稍": +0.5, "稍": +0.5, "略微": +0.5, "略": +0.5,
    "有点": +0.5, "有些": +0.5,
}

#: Negating openers — "别那么花" / "不要太密" are *less*, not *more*.
_ZH_NEGATORS: tuple[str, ...] = (
    "别那么", "别太", "不要那么", "不要太", "不那么", "没那么",
    "别", "不要", "不够", "不",
)

#: Trailing degree markers Chinese attaches after the adjective. The reason a
#: ``^(更|再)`` prefix match alone could never read 「快一点」.
_ZH_SUFFIX: tuple[tuple[str, float], ...] = (
    ("一点点", 0.4), ("一丁点", 0.4), ("一点儿", 0.6), ("一点", 0.6),
    ("一些", 0.6), ("些许", 0.5), ("点儿", 0.6), ("点", 0.6), ("些", 0.6),
    ("得多", 1.5), ("多了", 1.5), ("很多", 1.5),
    # Degree complements that follow a noun-ish core: 「氛围感强一点」
    # 「张力弱一些」. A negative factor flips the direction.
    ("强", 1.0), ("弱", -1.0), ("足", 1.0), ("够", 1.0),
    ("高", 1.0), ("低", -1.0),
)

#: Pure mood/aspect particles carrying no degree information.
_ZH_PARTICLES: tuple[str, ...] = (
    "的", "感", "性", "度", "了", "吧", "啊", "呀", "一下", "儿",
)

#: English lead-ins people wrap a request in.
_EN_LEADINS: tuple[str, ...] = (
    "can you make it", "could you make it", "please make it", "make it feel",
    "make it look", "make it", "i want it", "i'd like it", "let's make it",
    "feels too", "feel more", "feels more", "looks more", "look more",
)

_EN_DIRECTIONS: dict[str, float] = {
    "slightly more": +0.5, "slightly less": -0.5,
    "a bit more": +0.6, "a bit less": -0.6,
    "a little more": +0.6, "a little less": -0.6,
    "much more": +1.6, "much less": -1.6,
    "a lot more": +1.6, "a lot less": -1.6,
    "way more": +1.6, "way less": -1.6,
    "far more": +1.6, "far less": -1.6,
    "too": -1.0,  # "too busy" is a complaint: less busy
    "more": +1.0, "less": -1.0,
    "much": +1.5, "very": +1.3, "really": +1.3, "a lot": +1.5,
    "slightly": +0.5, "a bit": +0.6, "a little": +0.6, "kind of": +0.5,
    "not so": -1.0, "not too": -1.0, "less of a": -1.0,
}

# Longest-first so "slightly more" wins over "more" and "别那么" over "别".
_EN_DIR_RE = re.compile(
    r"^\s*(?P<dir>" + "|".join(
        re.escape(k) for k in sorted(_EN_DIRECTIONS, key=len, reverse=True)
    ) + r")\s+(?P<word>.+?)\s*$",
    re.IGNORECASE,
)

#: A reading floor: compounded hedges ("稍微…一点") stay a real, if small, move.
_MIN_MAGNITUDE = 0.35

#: **These numbers are a fallback, not an understanding.** "一点" is not 0.6 —
#: how much "a bit warmer" means depends on where the value already sits, what
#: the user pushed back on last time, and what the footage looks like. None of
#: that is visible from inside this module, so what it offers is a *default
#: step*: a defensible move when nobody better-informed has decided. The agent
#: sees the current axis value and the conversation, so it is the one that can
#: actually read the degree — it should set ``params`` directly and treat these
#: magnitudes as a floor to fall back on, never as the answer.
DEFAULT_STEP_DOC = (
    "default step — the degree was not measured, only guessed from wording"
)


def _strip_en_leadin(text: str) -> str:
    for lead in sorted(_EN_LEADINS, key=len, reverse=True):
        if text.startswith(lead + " "):
            return text[len(lead) + 1:].strip()
    return text


def _en_comparatives(word: str) -> Iterator[str]:
    """Conservative comparative de-inflection: warmer→warm, moodier→moody."""
    if word.endswith("ier") and len(word) > 4:
        yield word[:-3] + "y"
    if word.endswith("er") and len(word) > 3:
        yield word[:-2]
        yield word[:-1]
    if word.endswith("est") and len(word) > 4:
        yield word[:-3]


def _zh_peel(word: str) -> Iterator[tuple[float, str, str]]:
    """Yield (magnitude, core, degree_marker) by peeling Chinese degree words."""
    seen: set[str] = set()

    def emit(mag: float, core: str, marker: str) -> Iterator[tuple[float, str, str]]:
        core = core.strip()
        if core and core not in seen:
            seen.add(core)
            yield mag, core, marker

    stack: list[tuple[float, str, str]] = [(1.0, word, "")]
    while stack:
        mag, cur, marker = stack.pop()
        yield from emit(mag, cur, marker)
        for suffix, factor in _ZH_SUFFIX:
            if cur.endswith(suffix) and len(cur) > len(suffix):
                stack.append((mag * factor, cur[: -len(suffix)], suffix))
        for particle in _ZH_PARTICLES:
            if cur.endswith(particle) and len(cur) > len(particle):
                stack.append((mag, cur[: -len(particle)], marker))


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def candidates(phrase: str) -> list[Reading]:
    """Readings of ``phrase`` as ``(signed_magnitude, word)``, strongest first.

    The caller looks each ``word`` up in its own table and takes the first hit,
    so a peeled reading only ever counts when it lands on a word that genuinely
    exists. Handles bare adjectives ("premium" == "more premium"), English
    directions and comparatives ("much more cinematic", "warmer"), and the
    Chinese forms a prefix-only matcher missed: 「快一点」「节奏再快一点」
    「别那么花」「稍微暖一些」.

    Script decides the reading path — mixing them lets one language's peeling
    manufacture nonsense words in the other.
    """
    raw = str(phrase or "").strip().lower()
    if not raw:
        return []

    readings: list[Reading] = []
    seen: set[tuple[float, str]] = set()

    def add(mag: float, word: str, degree: str = "") -> None:
        word = word.strip().strip(",.!?;:，。！？、 ")
        if not word:
            return
        if abs(mag) < _MIN_MAGNITUDE:
            mag = _MIN_MAGNITUDE if mag >= 0 else -_MIN_MAGNITUDE
        key = (round(mag, 4), word)
        if key not in seen:
            seen.add(key)
            readings.append(Reading(round(mag, 4), word, degree))

    if _CJK_RE.search(raw):
        # Direction words sit mid-sentence in speech ("节奏再快一点"), so scan
        # rather than anchor. Negators go first: "别那么" must beat its "别".
        sign, directed, degree = 1.0, False, ""
        segments: list[str] = [raw]
        for marker, marker_sign in (
            [(n, -1.0) for n in _ZH_NEGATORS]
            + [(k, _ZH_PREFIX[k]) for k in sorted(_ZH_PREFIX, key=len, reverse=True)]
        ):
            idx = raw.find(marker)
            if idx == -1:
                continue
            sign, directed, degree = marker_sign, True, marker
            # The adjective sits on either side: 「节奏再快一点」puts it after,
            # 「留白多一点」before. Read the tail first — speech favours it.
            segments = [raw[idx + len(marker):], raw[:idx]]
            break

        for segment in segments:
            for magnitude, core, marker in _zh_peel(segment):
                add(sign * magnitude, core, marker or degree)
                if not directed:
                    # No direction word means a topic may still lead the phrase
                    # ("节奏快"). With one, the topic is already set aside.
                    for cut in (1, 2):
                        if len(core) > cut:
                            add(sign * magnitude, core[cut:], marker or degree)
    else:
        text = _strip_en_leadin(raw)
        sign, body = 1.0, text
        m = _EN_DIR_RE.match(text)
        if m:
            sign = _EN_DIRECTIONS[m.group("dir").lower()]
            body = m.group("word").strip()
        degree = m.group("dir").lower() if m else ""
        add(sign, body, degree)
        for base in _en_comparatives(body):
            add(sign, base, degree)

    readings.sort(key=lambda r: -abs(r[0]))
    return readings
