from __future__ import annotations

from io import BytesIO
import re
from urllib.parse import parse_qs, urlparse

from PIL import Image
import pytest

from gemia.deck import (
    DeckMaterializeError,
    build_deck_pager_url,
    build_deck_pager_url_from_manifest,
    render_deck_frames,
)
from gemia.project_model import normalize_deck
from gemia.text import TextLayoutError, measure_text
from gemia.video.fonts import get_font_catalog


def _weight(style: str) -> int | None:
    key = re.sub(r"[^a-z0-9]", "", style.casefold())
    match = re.search(r"w([1-9])", key)
    if match:
        return int(match.group(1)) * 100
    for marker, value in (
        ("demibold", 600), ("semibold", 600), ("demi", 600), ("medium", 500),
        ("regular", 400), ("roman", 400), ("normal", 400), ("bold", 700),
        ("light", 300), ("heavy", 800), ("black", 900),
    ):
        if marker in key:
            return value
    return None


@pytest.fixture(scope="module")
def font_tokens():
    for record in get_font_catalog():
        weight = _weight(record.style)
        if weight is None:
            continue
        config = {"family": record.family, "path": record.path, "weight": weight}
        try:
            measure_text("Lumeri", font_config=config, size_px=20)
        except TextLayoutError:
            continue
        return {
            "font.latin.display": config, "font.latin.body": config,
            "font.latin.strong": config, "font.cjk.display": config,
            "font.cjk.body": config,
        }
    raise AssertionError("no strictly resolvable local font")


def _deck(font_tokens):
    return normalize_deck({
        "theme": {"tokens": font_tokens},
        "slides": [
            {
                "id": "s1", "layout": "content", "title": "",
                "blocks": [{"id": "one", "kind": "shape", "role": "accent"}],
                "builds": [
                    {"id": "b1", "dwell_sec": 1, "visible_block_ids": []},
                    {"id": "b2", "dwell_sec": 2, "visible_block_ids": ["one"]},
                ],
            },
            {
                "id": "s2", "layout": "content", "title": "",
                "blocks": [{"id": "two", "kind": "shape", "role": "accent"}],
                "builds": [{"id": "b1", "dwell_sec": 3, "visible_block_ids": ["two"]}],
            },
        ],
        "default_path": ["s2", "s1"],
    })


def test_render_deck_frames_follows_default_path_then_build_order_and_is_stable(font_tokens) -> None:
    deck = _deck(font_tokens)
    first = render_deck_frames(deck)
    second = render_deck_frames(deck)
    assert [(f.slide_id, f.build_id, f.dwell_sec) for f in first] == [
        ("s2", "b1", 3.0), ("s1", "b1", 1.0), ("s1", "b2", 2.0),
    ]
    assert [(f.slide_index, f.build_index) for f in first] == [(0, 0), (1, 0), (1, 1)]
    assert [frame.png_bytes for frame in first] == [frame.png_bytes for frame in second]
    assert all(Image.open(BytesIO(frame.png_bytes)).size == (1920, 1080) for frame in first)
    assert first[1].placed_slide["placed_blocks"] == []
    assert first[2].placed_slide["placed_blocks"]


def test_render_deck_frames_tracks_image_lineage_and_scale(font_tokens) -> None:
    source = BytesIO()
    Image.new("RGB", (20, 10), "#5fc6de").save(source, format="PNG")
    deck = normalize_deck({
        "theme": {"tokens": font_tokens},
        "slides": [{
            "id": "image-slide", "layout": "full-bleed", "title": "",
            "blocks": [{"id": "hero", "kind": "image", "asset_id": "source_1"}],
        }],
    })
    (frame,) = render_deck_frames(deck, image_sources={"source_1": source.getvalue()}, scale=2)
    assert frame.source_asset_ids == ("source_1",)
    assert Image.open(BytesIO(frame.png_bytes)).size == (3840, 2160)
    assert frame.manifest_entry("img_009") == {
        "slide_index": 0, "build_index": 0, "slide_id": "image-slide", "build_id": "b1",
        "dwell_sec": 3.0, "asset_id": "img_009", "source_asset_ids": ["source_1"],
        "overflow": [],
    }


def test_overflow_is_returned_or_can_fail_closed(font_tokens) -> None:
    deck = normalize_deck({
        "theme": {"tokens": font_tokens},
        "slides": [{
            "id": "overflow", "layout": "content", "title": "",
            "blocks": [{"id": "long", "kind": "text", "text": "unbreakable" * 500}],
        }],
    })
    (frame,) = render_deck_frames(deck)
    assert frame.overflow and "width" in frame.overflow[0]["reasons"]
    with pytest.raises(DeckMaterializeError, match="overflowed"):
        render_deck_frames(deck, fail_on_overflow=True)


def test_missing_image_and_bad_default_path_are_actionable(font_tokens) -> None:
    image_deck = normalize_deck({
        "theme": {"tokens": font_tokens},
        "slides": [{
            "id": "s", "layout": "full-bleed", "title": "",
            "blocks": [{"id": "image", "kind": "image", "asset_id": "missing"}],
        }],
    })
    with pytest.raises(DeckMaterializeError, match="missing from image_sources"):
        render_deck_frames(image_deck)

    deck = _deck(font_tokens)
    deck["default_path"] = ["s1"]
    with pytest.raises(DeckMaterializeError, match="cover every slide"):
        render_deck_frames(deck)


def test_pager_url_has_only_validated_session_and_frame_references(font_tokens) -> None:
    frames = render_deck_frames(_deck(font_tokens))
    url = build_deck_pager_url("session_1", frames, ["img_001", "img_002", "img_003"])
    parsed = urlparse(url)
    assert parsed.path == "/v3/deck.html"
    assert parse_qs(parsed.query) == {
        "session_id": ["session_1"],
        "frame": ["0:0:img_001", "1:0:img_002", "1:1:img_003"],
    }
    first_only = build_deck_pager_url(
        "session_1", frames, ["img_001", "img_002", "img_003"], first_build_only=True,
    )
    assert parse_qs(urlparse(first_only).query)["frame"] == ["0:0:img_001", "1:0:img_002"]
    with pytest.raises(DeckMaterializeError, match="session id"):
        build_deck_pager_url("../bad", frames, ["img_001", "img_002", "img_003"])
    with pytest.raises(DeckMaterializeError, match="asset id"):
        build_deck_pager_url("session_1", frames, ["img_001", "中文", "img_003"])
    with pytest.raises(DeckMaterializeError, match="one-for-one"):
        build_deck_pager_url("session_1", frames, ["img_001"])


def test_pager_frame_limit_is_enforced(font_tokens) -> None:
    frame = render_deck_frames(_deck(font_tokens))[0]
    with pytest.raises(DeckMaterializeError, match="at most 512"):
        build_deck_pager_url("session_1", [frame] * 513, ["img_001"] * 513)


def test_manifest_pager_rejects_invalid_indices() -> None:
    with pytest.raises(DeckMaterializeError, match="non-negative"):
        build_deck_pager_url_from_manifest(
            "session_1",
            [{"slide_index": -1, "build_index": 0, "asset_id": "img_001"}],
        )
    with pytest.raises(DeckMaterializeError, match="integers"):
        build_deck_pager_url_from_manifest(
            "session_1",
            [{"slide_index": "bad", "build_index": 0, "asset_id": "img_001"}],
        )
