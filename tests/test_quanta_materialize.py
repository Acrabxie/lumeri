from __future__ import annotations

from io import BytesIO
import re
from urllib.parse import parse_qs, urlparse

from PIL import Image
import pytest

from gemia.quanta import (
    QuantaMaterializeError,
    build_quanta_pager_url,
    build_quanta_pager_url_from_manifest,
    render_quanta_frames,
)
from gemia.project_model import normalize_quanta
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


def _quanta(font_tokens):
    return normalize_quanta({
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


def test_render_quanta_frames_follows_default_path_then_build_order_and_is_stable(font_tokens) -> None:
    quanta = _quanta(font_tokens)
    first = render_quanta_frames(quanta)
    second = render_quanta_frames(quanta)
    assert [(f.scope_id, f.state_id, f.dwell_sec) for f in first] == [
        ("s2", "s2_b1", 3.0), ("s1", "s1_b1", 1.0), ("s1", "s1_b2", 2.0),
    ]
    assert [(f.scope_index, f.state_index) for f in first] == [(0, 0), (1, 0), (1, 1)]
    assert [frame.png_bytes for frame in first] == [frame.png_bytes for frame in second]
    assert all(Image.open(BytesIO(frame.png_bytes)).size == (1920, 1080) for frame in first)
    assert first[1].placed_slide["placed_blocks"] == []
    assert first[2].placed_slide["placed_blocks"]


def test_render_quanta_frames_tracks_image_lineage_and_scale(font_tokens) -> None:
    source = BytesIO()
    Image.new("RGB", (20, 10), "#5fc6de").save(source, format="PNG")
    quanta = normalize_quanta({
        "theme": {"tokens": font_tokens},
        "slides": [{
            "id": "image-slide", "layout": "full-bleed", "title": "",
            "blocks": [{"id": "hero", "kind": "image", "asset_id": "source_1"}],
        }],
    })
    (frame,) = render_quanta_frames(quanta, image_sources={"source_1": source.getvalue()}, scale=2)
    assert frame.source_asset_ids == ("source_1",)
    assert Image.open(BytesIO(frame.png_bytes)).size == (3840, 2160)
    assert frame.manifest_entry("img_009") == {
        "scope_index": 0, "state_index": 0, "scope_id": "image-slide", "state_id": "image-slide_b1",
        "dwell_sec": 3.0, "asset_id": "img_009", "source_asset_ids": ["source_1"],
        "overflow": [],
    }


def test_overflow_is_returned_or_can_fail_closed(font_tokens) -> None:
    quanta = normalize_quanta({
        "theme": {"tokens": font_tokens},
        "slides": [{
            "id": "overflow", "layout": "content", "title": "",
            "blocks": [{"id": "long", "kind": "text", "text": "unbreakable" * 500}],
        }],
    })
    (frame,) = render_quanta_frames(quanta)
    assert frame.overflow and "width" in frame.overflow[0]["reasons"]
    with pytest.raises(QuantaMaterializeError, match="overflowed"):
        render_quanta_frames(quanta, fail_on_overflow=True)


def test_missing_image_and_bad_default_path_are_actionable(font_tokens) -> None:
    image_quanta = normalize_quanta({
        "theme": {"tokens": font_tokens},
        "slides": [{
            "id": "s", "layout": "full-bleed", "title": "",
            "blocks": [{"id": "image", "kind": "image", "asset_id": "missing"}],
        }],
    })
    with pytest.raises(QuantaMaterializeError, match="missing from image_sources"):
        render_quanta_frames(image_quanta)

    # the canonical tree structurally eliminates bad default_path; the raw
    # flat-view contract at this seam still rejects one that slips through
    raw_flat = {
        "theme": {"tokens": font_tokens},
        "slides": [
            {"id": "s1", "layout": "content", "title": "", "blocks": [],
             "builds": [{"id": "b1", "dwell_sec": 1, "visible_block_ids": []}]},
            {"id": "s2", "layout": "content", "title": "", "blocks": [],
             "builds": [{"id": "b1", "dwell_sec": 1, "visible_block_ids": []}]},
        ],
        "default_path": ["s1"],
    }
    with pytest.raises(QuantaMaterializeError, match="cover every slide"):
        render_quanta_frames(raw_flat)


def test_pager_url_has_only_validated_session_and_frame_references(font_tokens) -> None:
    frames = render_quanta_frames(_quanta(font_tokens))
    url = build_quanta_pager_url("session_1", frames, ["img_001", "img_002", "img_003"])
    parsed = urlparse(url)
    assert parsed.path == "/v3/quanta.html"
    assert parse_qs(parsed.query) == {
        "session_id": ["session_1"],
        "frame": ["0:0:img_001", "1:0:img_002", "1:1:img_003"],
    }
    first_only = build_quanta_pager_url(
        "session_1", frames, ["img_001", "img_002", "img_003"], first_build_only=True,
    )
    assert parse_qs(urlparse(first_only).query)["frame"] == ["0:0:img_001", "1:0:img_002"]
    with pytest.raises(QuantaMaterializeError, match="session id"):
        build_quanta_pager_url("../bad", frames, ["img_001", "img_002", "img_003"])
    with pytest.raises(QuantaMaterializeError, match="asset id"):
        build_quanta_pager_url("session_1", frames, ["img_001", "中文", "img_003"])
    with pytest.raises(QuantaMaterializeError, match="one-for-one"):
        build_quanta_pager_url("session_1", frames, ["img_001"])


def test_pager_frame_limit_is_enforced(font_tokens) -> None:
    frame = render_quanta_frames(_quanta(font_tokens))[0]
    with pytest.raises(QuantaMaterializeError, match="at most 512"):
        build_quanta_pager_url("session_1", [frame] * 513, ["img_001"] * 513)


def test_manifest_pager_rejects_invalid_indices() -> None:
    with pytest.raises(QuantaMaterializeError, match="non-negative"):
        build_quanta_pager_url_from_manifest(
            "session_1",
            [{"scope_index": -1, "state_index": 0, "asset_id": "img_001"}],
        )
    with pytest.raises(QuantaMaterializeError, match="integers"):
        build_quanta_pager_url_from_manifest(
            "session_1",
            [{"scope_index": "bad", "state_index": 0, "asset_id": "img_001"}],
        )
