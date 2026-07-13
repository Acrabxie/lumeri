from __future__ import annotations

import json
import re
from typing import Any

import pytest

from gemia.quanta import DEFAULT_QUANTA_TOKENS, QuantaLayoutError, layout_slide
from gemia.project_model import normalize_quanta
from gemia.text import TextLayoutError, measure_text
from gemia.video.fonts import get_font_catalog


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


def _find_font(sample: str, *, cjk: bool) -> dict[str, Any]:
    for record in get_font_catalog():
        if cjk and not record.supports_cjk_hint:
            continue
        weight = _weight_for_style(record.style)
        if weight is None:
            continue
        config = {"family": record.family, "path": record.path, "weight": weight}
        try:
            measure_text(sample, font_config=config, size_px=24)
        except TextLayoutError:
            continue
        return config
    raise AssertionError(f"no strictly resolvable {'CJK' if cjk else 'Latin'} font")


@pytest.fixture(scope="module")
def theme_tokens() -> dict[str, Any]:
    latin = _find_font("Lumeri", cjk=False)
    cjk = _find_font("中文", cjk=True)
    return {
        "font.latin.display": latin,
        "font.latin.body": latin,
        "font.latin.strong": latin,
        "font.cjk.display": cjk,
        "font.cjk.body": cjk,
    }


def _slide(raw: dict[str, Any]) -> dict[str, Any]:
    return normalize_quanta({"slides": [raw]})["slides"][0]


def _placed(result: dict[str, Any], *, block_ref: str, kind: str, slot: str | None = None):
    matches = [
        item for item in result["placed_blocks"]
        if item["block_ref"] == block_ref
        and item["kind"] == kind
        and (slot is None or item["slot"] == slot)
    ]
    assert len(matches) == 1, matches
    return matches[0]


def test_title_template_exact_geometry_and_builds_do_not_reflow(theme_tokens) -> None:
    slide = _slide({
        "id": "s-title", "layout": "title", "title": "Lumeri",
        "blocks": [
            {"id": "hero", "kind": "image", "role": "hero", "asset_id": "img_001"},
            {"id": "title", "kind": "text", "role": "title", "text": "Lumeri"},
            {"id": "subtitle", "kind": "text", "role": "subtitle", "text": "Create naturally"},
            {"id": "accent", "kind": "shape", "role": "accent", "fill_token": "color.accent"},
        ],
        "builds": [
            {"id": "b1", "dwell_sec": 1, "visible_block_ids": ["hero", "accent"]},
            {"id": "b2", "dwell_sec": 2,
             "visible_block_ids": ["hero", "accent", "title", "subtitle"]},
        ],
    })
    first = layout_slide(slide, theme_tokens=theme_tokens, build_id="b1")
    final = layout_slide(slide, theme_tokens=theme_tokens, build_id="b2")

    assert first["safe_rect_px"] == [160, 120, 1600, 840]
    assert _placed(first, block_ref="hero", kind="image")["rect_px"] == [0, 0, 1920, 1080]
    assert _placed(first, block_ref="accent", kind="shape")["rect_px"] == [160, 360, 104, 8]
    assert not any(item["kind"] == "text" for item in first["placed_blocks"])
    assert _placed(final, block_ref="title", kind="text")["rect_px"] == [160, 408, 1192, 240]
    assert _placed(final, block_ref="subtitle", kind="text")["rect_px"] == [160, 680, 1192, 96]
    assert _placed(final, block_ref="title", kind="shape", slot="scrim")["rect_px"] == [0, 0, 1920, 1080]
    for key in (("hero", "image"), ("accent", "shape")):
        assert _placed(first, block_ref=key[0], kind=key[1])["rect_px"] == _placed(
            final, block_ref=key[0], kind=key[1]
        )["rect_px"]


def test_slide_title_is_chrome_but_matching_semantic_heading_is_not_duplicated(theme_tokens) -> None:
    chrome_slide = _slide({
        "id": "s-chrome", "layout": "content", "title": "Always visible",
        "blocks": [{"id": "body", "kind": "text", "role": "body", "text": "Later"}],
        "builds": [
            {"id": "b1", "dwell_sec": 1, "visible_block_ids": []},
            {"id": "b2", "dwell_sec": 1, "visible_block_ids": ["body"]},
        ],
    })
    first = layout_slide(chrome_slide, theme_tokens=theme_tokens, build_id="b1")
    chrome = _placed(first, block_ref="slide:s-chrome:title", kind="text")
    assert chrome["rect_px"] == [160, 120, 1600, 160]

    semantic_slide = _slide({
        "id": "s-semantic", "layout": "content", "title": "Delayed title",
        "blocks": [
            {"id": "heading", "kind": "text", "role": "title", "text": "Delayed title"},
            {"id": "body", "kind": "text", "role": "body", "text": "Body"},
        ],
        "builds": [
            {"id": "b1", "dwell_sec": 1, "visible_block_ids": []},
            {"id": "b2", "dwell_sec": 1, "visible_block_ids": ["heading"]},
            {"id": "b3", "dwell_sec": 1, "visible_block_ids": ["heading", "body"]},
        ],
    })
    hidden = layout_slide(semantic_slide, theme_tokens=theme_tokens, build_id="b1")
    shown = layout_slide(semantic_slide, theme_tokens=theme_tokens, build_id="b2")
    assert hidden["placed_blocks"] == []
    assert [item["block_ref"] for item in shown["placed_blocks"]] == ["heading"]


def test_content_template_media_split_and_no_media_expansion(theme_tokens) -> None:
    with_media = _slide({
        "id": "s-content", "layout": "content", "title": "Content",
        "blocks": [
            {"id": "body", "kind": "text", "role": "body", "text": "One clear idea"},
            {"id": "media", "kind": "image", "role": "hero", "asset_id": "img_002"},
        ],
    })
    result = layout_slide(with_media, theme_tokens=theme_tokens)
    assert _placed(result, block_ref="body", kind="text")["rect_px"] == [160, 328, 920, 632]
    assert _placed(result, block_ref="media", kind="image")["rect_px"] == [1112, 328, 648, 632]

    no_media = _slide({
        "id": "s-wide", "layout": "content", "title": "Wide",
        "blocks": [{"id": "body", "kind": "text", "role": "body", "text": "Wide body"}],
    })
    wide = layout_slide(no_media, theme_tokens=theme_tokens)
    assert _placed(wide, block_ref="body", kind="text")["rect_px"] == [160, 328, 1192, 632]


@pytest.mark.parametrize("count", [3, 4, 5])
def test_content_homogeneous_groups_are_horizontal_cards(theme_tokens, count) -> None:
    children = [
        {"id": f"card-{index}", "kind": "text", "role": "card", "text": f"Card {index}"}
        for index in range(1, count + 1)
    ]
    slide = _slide({
        "id": f"s-cards-{count}", "layout": "content", "title": "Cards",
        "blocks": [{"id": "cards", "kind": "group", "role": "cards", "children": children}],
    })
    result = layout_slide(slide, theme_tokens=theme_tokens)
    cards = [
        item for item in result["placed_blocks"]
        if item["kind"] == "shape" and item["slot"].endswith(".card")
    ]
    assert len(cards) == count
    assert cards[0]["rect_px"][0] == 160
    assert cards[-1]["rect_px"][0] + cards[-1]["rect_px"][2] == 1352
    assert all(item["rect_px"][1:] == [464, item["rect_px"][2], 360] for item in cards)
    for left, right in zip(cards, cards[1:]):
        assert right["rect_px"][0] - (left["rect_px"][0] + left["rect_px"][2]) == 32


def test_progressive_bullet_groups_remain_a_vertical_reading_flow(theme_tokens) -> None:
    slide = _slide({
        "id": "s-bullets", "layout": "content", "title": "Bullets",
        "blocks": [{"id": "bullets", "kind": "group", "role": "bullets", "children": [
            {"id": "one", "kind": "text", "role": "bullet", "text": "First"},
            {"id": "two", "kind": "text", "role": "bullet", "text": "Second"},
            {"id": "three", "kind": "text", "role": "bullet", "text": "Third"},
        ]}],
    })
    result = layout_slide(slide, theme_tokens=theme_tokens)
    items = [_placed(result, block_ref=block_ref, kind="text") for block_ref in ("one", "two", "three")]
    assert [item["rect_px"][0] for item in items] == [160, 160, 160]
    assert all(item["rect_px"][2] == 1192 for item in items)
    assert [item["text"] for item in items] == ["• First", "• Second", "• Third"]
    assert [item["rect_px"][3] for item in items] == [96, 96, 96]
    for first, second in zip(items, items[1:]):
        assert second["rect_px"][1] - (first["rect_px"][1] + first["rect_px"][3]) == 16


def test_stat_and_full_bleed_template_goldens(theme_tokens) -> None:
    stat = _slide({
        "id": "s-stat", "layout": "stat", "title": "Numbers",
        "blocks": [
            {"id": "a", "kind": "stat", "value": "97", "label": "Tools"},
            {"id": "b", "kind": "stat", "value": "4", "label": "Templates"},
        ],
    })
    stat_result = layout_slide(stat, theme_tokens=theme_tokens)
    assert _placed(stat_result, block_ref="a", kind="shape")["rect_px"] == [160, 464, 784, 360]
    assert _placed(stat_result, block_ref="b", kind="shape")["rect_px"] == [976, 464, 784, 360]

    full = _slide({
        "id": "s-full", "layout": "full-bleed", "title": "One Lumen",
        "blocks": [
            {"id": "image", "kind": "image", "asset_id": "img_003"},
            {"id": "title", "kind": "text", "role": "title", "text": "One Lumen"},
        ],
    })
    full_result = layout_slide(full, theme_tokens=theme_tokens)
    assert _placed(full_result, block_ref="image", kind="image")["rect_px"] == [0, 0, 1920, 1080]
    assert _placed(full_result, block_ref="title", kind="text")["rect_px"] == [160, 720, 784, 240]
    scrim = _placed(full_result, block_ref="title", kind="shape", slot="scrim")
    assert scrim["fill_token"] == "color.scrim"


def test_layout_is_json_deterministic_scales_and_uses_script_font(theme_tokens) -> None:
    slide = _slide({
        "id": "s-font", "layout": "content", "title": "Fonts",
        "blocks": [
            {"id": "latin", "kind": "text", "text": "Latin body"},
            {"id": "cjk", "kind": "text", "text": "中文 mixed body"},
        ],
    })
    first = layout_slide(slide, theme_tokens=theme_tokens)
    second = layout_slide(slide, theme_tokens=theme_tokens)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert _placed(first, block_ref="latin", kind="text")["style"]["family"] == theme_tokens["font.latin.body"]["family"]
    assert _placed(first, block_ref="cjk", kind="text")["style"]["family"] == theme_tokens["font.cjk.body"]["family"]
    doubled = layout_slide(slide, theme_tokens=theme_tokens, canvas=(3840, 2160))
    assert doubled["safe_rect_px"] == [320, 240, 3200, 1680]
    assert _placed(doubled, block_ref="latin", kind="text")["rect_px"] == [320, 656, 2384, 608]


def test_overflow_is_structured_and_never_continuously_shrinks(theme_tokens) -> None:
    slide = _slide({
        "id": "s-overflow", "layout": "content", "title": "Overflow",
        "blocks": [{
            "id": "long", "kind": "text", "role": "body",
            "text": "Supercalifragilisticexpialidocious" * 80,
        }],
    })
    result = layout_slide(slide, theme_tokens=theme_tokens)
    primitive = _placed(result, block_ref="long", kind="text")
    assert primitive["style"]["final_size_px"] in primitive["autofit"]["size_steps_px"]
    assert primitive["autofit"]["selected_step"] == "fallback"
    assert primitive["autofit"]["overflow"] is True
    assert result["overflow"] == [{
        "block_ref": "long",
        "slot": "body.item-1",
        "reasons": primitive["autofit"]["overflow_reasons"],
        "selected_step": "fallback",
        "measured_bounds_px": primitive["autofit"]["measured_bounds_px"],
        "rect_px": primitive["rect_px"],
    }]


def test_layout_rejects_bad_tokens_unknown_templates_and_media_conflicts(theme_tokens, monkeypatch) -> None:
    slide = _slide({
        "id": "s", "layout": "content", "title": "Title",
        "blocks": [{"id": "body", "kind": "text", "text": "Body"}],
    })
    with pytest.raises(QuantaLayoutError, match="unsupported theme token"):
        layout_slide(slide, theme_tokens={**theme_tokens, "grid.gutter": 40})
    with pytest.raises(QuantaLayoutError, match="does not exist"):
        layout_slide(slide, theme_tokens={
            **theme_tokens,
            "font.latin.body": {"family": "Missing", "path": "/missing.ttf", "weight": 400},
        })
    with pytest.raises(QuantaLayoutError, match="CSS"):
        layout_slide(slide, theme_tokens={**theme_tokens, "color.accent": "not-a-color"})

    bad_layout = dict(slide)
    bad_layout["layout"] = "freeform"
    with pytest.raises(QuantaLayoutError, match="unknown quanta layout"):
        layout_slide(bad_layout, theme_tokens=theme_tokens)

    conflict = _slide({
        "id": "s-conflict", "layout": "content", "blocks": [
            {"id": "one", "kind": "image", "asset_id": "img_1"},
            {"id": "two", "kind": "image", "asset_id": "img_2"},
        ],
    })
    with pytest.raises(QuantaLayoutError, match="at most one"):
        layout_slide(conflict, theme_tokens=theme_tokens)

    nested_heading = _slide({
        "id": "s-nested", "layout": "content", "title": "Nested",
        "blocks": [{"id": "group", "kind": "group", "children": [
            {"id": "heading", "kind": "text", "role": "title", "text": "Nested"},
        ]}],
    })
    with pytest.raises(QuantaLayoutError, match="heading at top level"):
        layout_slide(nested_heading, theme_tokens=theme_tokens)

    monkeypatch.setitem(DEFAULT_QUANTA_TOKENS, "grid.gutter", 31)
    with pytest.raises(QuantaLayoutError, match="grid identity"):
        layout_slide(slide, theme_tokens=theme_tokens)
