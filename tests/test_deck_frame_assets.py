from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from PIL import Image
import pytest

from gemia.deck import DeckMaterializeError
from gemia.project_model import normalize_deck
from gemia.text import TextLayoutError, measure_text
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools.deck_frames import materialize_deck_frame_assets
from gemia.video.fonts import get_font_catalog


def _weight(style: str) -> int | None:
    key = re.sub(r"[^a-z0-9]", "", style.casefold())
    for marker, value in (
        ("demibold", 600), ("semibold", 600), ("demi", 600), ("medium", 500),
        ("regular", 400), ("roman", 400), ("normal", 400), ("bold", 700),
        ("light", 300),
    ):
        if marker in key:
            return value
    match = re.search(r"w([1-9])", key)
    return int(match.group(1)) * 100 if match else None


@pytest.fixture(scope="module")
def tokens():
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


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="session_1",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _update: None,
    )


def test_session_adapter_registers_every_frame_with_lineage_and_safe_pager(tmp_path, tokens) -> None:
    ctx = _ctx(tmp_path)
    source_path = tmp_path / "source.png"
    Image.new("RGB", (32, 18), "#5fc6de").save(source_path)
    source_id = ctx.registry.add_external(source_path, summary="deck hero").asset_id
    deck = normalize_deck({
        "theme": {"tokens": tokens},
        "slides": [{
            "id": "s1", "layout": "full-bleed", "title": "",
            "blocks": [
                {"id": "hero", "kind": "image", "asset_id": source_id},
                {"id": "accent", "kind": "shape", "role": "accent"},
            ],
            "builds": [
                {"id": "b1", "dwell_sec": 1, "visible_block_ids": ["hero"]},
                {"id": "b2", "dwell_sec": 2, "visible_block_ids": ["hero", "accent"]},
            ],
        }],
    })
    result = materialize_deck_frame_assets(deck, ctx)

    assert result["kind"] == "deck" and result["frame_count"] == 2
    assert result["slide_count"] == 1 and result["asset_id"] == result["frame_asset_ids"][0]
    assert result["overflow"] == []
    for asset_id in result["frame_asset_ids"]:
        record = ctx.registry.get(asset_id)
        assert record.kind == "image" and record.lineage == (source_id,)
        image = Image.open(record.path)
        assert image.size == (1920, 1080) and image.format == "PNG"
    assert all("path" not in str(entry).casefold() for entry in result["frames"])

    parsed = urlparse(result["pager_url"])
    query = parse_qs(parsed.query)
    assert parsed.path == "/v3/deck.html"
    assert query["session_id"] == ["session_1"]
    assert query["frame"] == [
        f"0:0:{result['frame_asset_ids'][0]}",
        f"0:1:{result['frame_asset_ids'][1]}",
    ]
    first = parse_qs(urlparse(result["first_build_pager_url"]).query)
    assert first["frame"] == [f"0:0:{result['frame_asset_ids'][0]}"]


def test_session_adapter_fails_before_allocating_when_source_is_missing(tmp_path, tokens) -> None:
    ctx = _ctx(tmp_path)
    deck = normalize_deck({
        "theme": {"tokens": tokens},
        "slides": [{
            "id": "s1", "layout": "full-bleed", "title": "",
            "blocks": [{"id": "hero", "kind": "image", "asset_id": "img_999"}],
        }],
    })
    with pytest.raises(DeckMaterializeError, match="not in this session registry"):
        materialize_deck_frame_assets(deck, ctx)
    assert ctx.registry.list_records() == []
    assert list(tmp_path.glob("img_*.png")) == []
