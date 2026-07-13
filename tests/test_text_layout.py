from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gemia.text.layout import (
    TextLayoutError,
    autofit_text,
    break_lines,
    measure_text,
    wrap_text,
)
from gemia.video.fonts import get_font_catalog


def _units(value: str) -> float:
    """Deterministic width: CJK=2, everything else=1."""
    return float(sum(2 if ord(char) > 127 else 1 for char in value))


def _weight_for_style(style: str) -> int | None:
    key = re.sub(r"[^a-z0-9]", "", style.casefold())
    match = re.search(r"w([1-9])", key)
    if match:
        return int(match.group(1)) * 100
    for marker, weight in (
        ("ultralight", 100), ("thin", 100), ("extralight", 200),
        ("demibold", 600), ("semibold", 600), ("demi", 600),
        ("extrabold", 800), ("heavy", 800), ("black", 900),
        ("light", 300), ("medium", 500), ("regular", 400),
        ("roman", 400), ("normal", 400), ("bold", 700),
    ):
        if marker in key:
            return weight
    return None


@pytest.fixture(scope="module")
def font_config() -> dict[str, object]:
    for record in get_font_catalog():
        weight = _weight_for_style(record.style)
        if weight is None:
            continue
        config = {"family": record.family, "path": record.path, "weight": weight}
        try:
            measure_text("Lumeri", font_config=config, size_px=20)
        except TextLayoutError:
            continue
        return config
    raise AssertionError("text layout goldens require one strictly resolvable local font")


@pytest.fixture(scope="module")
def cjk_font_config() -> dict[str, object]:
    for record in get_font_catalog():
        if not record.supports_cjk_hint:
            continue
        weight = _weight_for_style(record.style)
        if weight is None:
            continue
        config = {"family": record.family, "path": record.path, "weight": weight}
        try:
            measure_text("中文", font_config=config, size_px=20)
        except TextLayoutError:
            continue
        return config
    raise AssertionError("text layout goldens require one CJK-capable local font")


def test_break_lines_keeps_latin_words_indivisible_in_mixed_text() -> None:
    lines = break_lines("你好 OpenAI Studio 世界", 8, measure_width=_units)
    assert tuple(line.rstrip() for line in lines) == ("你好", "OpenAI", "Studio", "世界")
    assert "".join(lines).replace(" ", "") == "你好OpenAIStudio世界"


def test_break_lines_keeps_closing_punctuation_off_line_start() -> None:
    lines = break_lines("你好，世界。", 4, measure_width=_units)
    assert lines == ("你好，", "世界。")
    assert all(not line.startswith(("，", "。")) for line in lines)


def test_break_lines_keeps_opening_punctuation_off_line_end() -> None:
    lines = break_lines("你好《世界》", 6, measure_width=_units)
    assert lines == ("你好", "《世界》")
    assert all(not line.endswith("《") for line in lines)


def test_break_lines_preserves_explicit_and_blank_lines() -> None:
    lines = break_lines("第一行\n\nSecond line", 100, measure_width=_units)
    assert lines == ("第一行", "", "Second line")


def test_break_lines_preserves_whitespace_exactly_and_honors_no_break_space() -> None:
    text = "  A  B\tC　D  "
    lines = break_lines(text, 4, measure_width=len)
    assert "".join(lines) == text
    assert break_lines("A\u00a0B", 1, measure_width=len) == ("A\u00a0B",)
    assert break_lines("X A\u00a0B", 3, measure_width=len) == ("X ", "A\u00a0B")
    assert break_lines("A\u2060\u2060B", 1, measure_width=len) == (
        "A\u2060\u2060B",
    )


def test_break_lines_recognizes_crlf_cr_and_unicode_line_separators() -> None:
    assert break_lines("A\r\nB\rC\u2028D\u2029E", 20, measure_width=len) == (
        "A", "B", "C", "D", "E"
    )


def test_break_lines_keeps_graphemes_and_splits_extension_b_cjk() -> None:
    decomposed = "e\u0301clair"
    assert break_lines(decomposed, 1, measure_width=len) == (decomposed,)
    assert break_lines("𠀀𠀁", 1, measure_width=len) == ("𠀀", "𠀁")
    emoji = "👩\u200d💻"
    assert break_lines(
        emoji + "X", 1, measure_width=lambda value: 1 if value in {emoji, "X"} else 2
    ) == (emoji, "X")
    hangul = "\u1100\u1161"
    assert break_lines(
        hangul + "X", 1, measure_width=lambda value: 1 if value in {hangul, "X"} else 2
    ) == (hangul, "X")
    tag_flag = "🏴\U000e0067\U000e0062\U000e007f"
    assert break_lines(
        tag_flag + "X", 1, measure_width=lambda value: 1 if value in {tag_flag, "X"} else 2
    ) == (tag_flag, "X")


def test_break_lines_covers_bopomofo_and_cjk_extension_i() -> None:
    assert break_lines("ㄅㄆ", 1, measure_width=len) == ("ㄅ", "ㄆ")
    assert break_lines("\U0002ebf0\U0002ebf1", 1, measure_width=len) == (
        "\U0002ebf0", "\U0002ebf1"
    )


def test_measure_and_wrap_use_the_same_resolved_font(font_config: dict[str, object]) -> None:
    whole = measure_text("Lumeri text", font_config=font_config, size_px=30)
    lines = wrap_text(
        "Lumeri text",
        font_config=font_config,
        size_px=30,
        max_width_px=max(whole.width_px - 1, 1),
    )
    assert tuple(line.rstrip() for line in lines) == ("Lumeri", "text")
    assert "".join(lines) == "Lumeri text"
    assert whole.width_px > 0
    assert whole.line_height_px > 0


def test_autofit_uses_only_the_two_discrete_type_steps(font_config: dict[str, object]) -> None:
    large = measure_text("Lumeri", font_config=font_config, size_px=40).width_px
    small = measure_text("Lumeri", font_config=font_config, size_px=20).width_px
    result = autofit_text(
        "Lumeri",
        font_config=font_config,
        size_steps_px=(40, 20),
        max_width_px=(large + small) / 2,
        max_height_px=100,
        max_lines=1,
    )
    assert result.size_px == 20
    assert result.preferred_size_px == 40
    assert result.fallback_size_px == 20
    assert result.selected_step == "fallback"
    assert result.overflow is False
    assert result.lines == ("Lumeri",)


def test_autofit_returns_structured_overflow_without_splitting_long_word(
    font_config: dict[str, object],
) -> None:
    result = autofit_text(
        "Supercalifragilisticexpialidocious",
        font_config=font_config,
        size_steps_px=(32, 20),
        max_width_px=20,
        max_height_px=100,
        max_lines=1,
    )
    assert result.size_px == 20
    assert result.lines == ("Supercalifragilisticexpialidocious",)
    assert result.overflow is True
    assert result.overflow_reasons == ("width",)


def test_autofit_reports_line_count_and_never_truncates(
    font_config: dict[str, object],
) -> None:
    result = autofit_text(
        "one two three four five six",
        font_config=font_config,
        size_steps_px=(28, 18),
        max_width_px=70,
        max_height_px=500,
        max_lines=1,
    )
    assert result.overflow is True
    assert "line_count" in result.overflow_reasons
    assert len(result.lines) > 1
    assert "".join(result.lines) == "one two three four five six"


def test_layout_payload_is_stable_and_carries_actual_font_token(
    cjk_font_config: dict[str, object],
) -> None:
    kwargs = {
        "font_config": cjk_font_config,
        "size_steps_px": (30, 22),
        "max_width_px": 240,
        "max_height_px": 100,
        "max_lines": 2,
    }
    first = autofit_text("中英 mixed layout", **kwargs).to_dict()
    second = autofit_text("中英 mixed layout", **kwargs).to_dict()
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert first["style"]["family"] == cjk_font_config["family"]
    assert first["style"]["path"] == cjk_font_config["path"]
    assert first["style"]["weight"] == cjk_font_config["weight"]
    assert first["style"]["size_px"] == 30
    assert isinstance(first["style"]["face_index"], int)
    assert first["style"]["font_style"]
    assert first["autofit"]["size_steps_px"] == [30, 22]


def test_font_token_rejects_missing_path_family_and_weight(
    font_config: dict[str, object], tmp_path: Path
) -> None:
    with pytest.raises(TextLayoutError, match="does not exist"):
        measure_text(
            "text",
            font_config={"family": "Missing", "path": tmp_path / "missing.ttf", "weight": 400},
            size_px=20,
        )
    with pytest.raises(TextLayoutError, match="does not match"):
        measure_text(
            "text",
            font_config={**font_config, "family": "Definitely Missing Family"},
            size_px=20,
        )
    with pytest.raises(TextLayoutError, match="no face"):
        measure_text(
            "text",
            font_config={**font_config, "weight": 999},
            size_px=20,
        )


def test_cjk_text_rejects_non_cjk_font_when_available() -> None:
    arial = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    if not arial.exists():
        pytest.skip("macOS Arial fixture is not available")
    config = {"family": "Arial", "path": str(arial), "weight": 400}
    for text in ("中文", "ㄅ", "\U0002ebf0"):
        with pytest.raises(TextLayoutError, match="lacks CJK glyph"):
            measure_text(text, font_config=config, size_px=24)


def test_ttc_weight_selects_the_actual_hiragino_face_when_available() -> None:
    hiragino = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
    if not hiragino.exists():
        pytest.skip("macOS Hiragino TTC fixture is not available")
    result = autofit_text(
        "中文标题",
        font_config={"family": "Hiragino Sans GB", "path": str(hiragino), "weight": 600},
        size_steps_px=(40, 32),
        max_width_px=500,
        max_height_px=200,
    )
    assert result.family == "Hiragino Sans GB"
    assert result.font_style == "W6"
    assert result.weight == 600
    assert result.face_index > 0


@pytest.mark.parametrize("steps", [(), (20,), (20, 10, 8), (10, 20), (20, 20)])
def test_autofit_rejects_non_two_step_scales(
    font_config: dict[str, object], steps: tuple[int, ...]
) -> None:
    with pytest.raises(TextLayoutError, match="exactly two"):
        autofit_text(
            "text",
            font_config=font_config,
            size_steps_px=steps,
            max_width_px=100,
            max_height_px=100,
        )


def test_public_numeric_validation_always_raises_text_layout_error(
    font_config: dict[str, object],
) -> None:
    with pytest.raises(TextLayoutError):
        measure_text("text", font_config=font_config, size_px=20.5)  # type: ignore[arg-type]
    with pytest.raises(TextLayoutError):
        measure_text("text", font_config="bad", size_px=20)  # type: ignore[arg-type]
    with pytest.raises(TextLayoutError, match="explicitly provide"):
        measure_text("text", font_config=None, size_px=20)  # type: ignore[arg-type]
    with pytest.raises(TextLayoutError):
        autofit_text(
            "text",
            font_config=font_config,
            size_steps_px=None,  # type: ignore[arg-type]
            max_width_px=100,
            max_height_px=100,
        )
    with pytest.raises(TextLayoutError):
        autofit_text(
            "text",
            font_config=font_config,
            size_steps_px=(20.0, 10),  # type: ignore[arg-type]
            max_width_px=100,
            max_height_px=100,
        )
    with pytest.raises(TextLayoutError):
        autofit_text(
            "text",
            font_config=font_config,
            size_steps_px=(20, 10),
            max_width_px="100",  # type: ignore[arg-type]
            max_height_px=100,
        )
    with pytest.raises(TextLayoutError):
        autofit_text(
            "text",
            font_config=font_config,
            size_steps_px=(20, 10),
            max_width_px=100,
            max_height_px=100,
            line_spacing="1.2",  # type: ignore[arg-type]
        )
