from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
from typing import Any

from PIL import Image
import pytest

from gemia.deck import DeckRasterError, layout_slide, rasterize_slide
from gemia.project_model import normalize_deck
from gemia.text import TextLayoutError, measure_text
from gemia.video.fonts import get_font_catalog


def _decode(payload: bytes) -> Image.Image:
    image = Image.open(BytesIO(payload))
    image.load()
    return image


def _png(size: tuple[int, int], color: Any) -> bytes:
    output = BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def _placed(*blocks: dict[str, Any], size=(100, 60), background="#101419") -> dict[str, Any]:
    return {
        "canvas_px": list(size),
        "background_color": background,
        "placed_blocks": list(blocks),
    }


def _shape(rect, fill, *, shape="rect", radius=0, ref="shape") -> dict[str, Any]:
    return {
        "kind": "shape", "block_ref": ref, "slot": "shape", "rect_px": list(rect),
        "z_index": 0, "source_order": 0, "shape": shape, "fill": fill,
        "corner_radius_px": radius,
    }


def _image(rect, asset="img_001", *, fit="cover", anchor="center") -> dict[str, Any]:
    return {
        "kind": "image", "block_ref": "image", "slot": "image", "rect_px": list(rect),
        "z_index": 0, "source_order": 0, "asset_id": asset, "fit": fit, "anchor": anchor,
    }


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


def _font(sample="Lumeri") -> tuple[dict[str, Any], int]:
    for record in get_font_catalog():
        weight = _weight(record.style)
        if weight is None:
            continue
        config = {"family": record.family, "path": record.path, "weight": weight}
        try:
            measure_text(sample, font_config=config, size_px=20)
        except TextLayoutError:
            continue
        # Resolve the exact TTC face through the public layout result.
        slide = normalize_deck({"slides": [{
            "id": "font", "layout": "content", "blocks": [
                {"id": "text", "kind": "text", "text": sample},
            ],
        }]})["slides"][0]
        overrides = {
            "font.latin.display": config, "font.latin.body": config,
            "font.latin.strong": config, "font.cjk.display": config,
            "font.cjk.body": config,
        }
        try:
            placed = layout_slide(slide, theme_tokens=overrides)
        except Exception:
            continue
        primitive = next(item for item in placed["placed_blocks"] if item.get("block_ref") == "text")
        return primitive["style"], primitive["line_height_px"]
    raise AssertionError("no strictly resolvable raster font")


def test_shape_png_is_byte_stable_rgb_and_metadata_free() -> None:
    placed = _placed(_shape([10, 10, 30, 20], "#ff0000"), size=(80, 50), background="#0000ff")
    first = rasterize_slide(placed)
    second = rasterize_slide(placed)
    assert first == second
    image = _decode(first)
    assert image.size == (80, 50) and image.mode == "RGB" and image.info == {}
    assert image.getpixel((0, 0)) == (0, 0, 255)
    assert image.getpixel((20, 15)) == (255, 0, 0)


def test_vertical_scrim_gradient_alpha_composites_over_background() -> None:
    placed = _placed(
        _shape(
            [0, 0, 10, 10],
            "linear-gradient(180deg, rgba(10,12,16,0) 0%, rgba(10,12,16,0.72) 100%)",
        ),
        size=(10, 10),
        background="#ffffff",
    )
    image = _decode(rasterize_slide(placed))
    assert image.getpixel((5, 0)) == (255, 255, 255)
    bottom = image.getpixel((5, 9))
    assert 70 <= bottom[0] <= 80 and 70 <= bottom[1] <= 82 and 70 <= bottom[2] <= 85


def test_image_contain_cover_anchor_and_alpha_are_not_stretched() -> None:
    source = Image.new("RGBA", (4, 2), (255, 0, 0, 255))
    for x in range(2, 4):
        for y in range(2):
            source.putpixel((x, y), (0, 0, 255, 255))
    encoded = BytesIO()
    source.save(encoded, format="PNG")

    contain = _decode(rasterize_slide(
        _placed(_image([0, 0, 10, 10], fit="contain"), size=(10, 10), background="#000000"),
        image_sources={"img_001": encoded.getvalue()},
    ))
    assert contain.getpixel((1, 0)) == (0, 0, 0)
    assert contain.getpixel((1, 5))[0] > 200
    assert contain.getpixel((8, 5))[2] > 200

    cover = _decode(rasterize_slide(
        _placed(_image([0, 0, 8, 8], fit="cover"), size=(8, 8), background="#000000"),
        image_sources={"img_001": encoded.getvalue()},
    ))
    assert cover.getpixel((0, 4))[0] > cover.getpixel((0, 4))[2]
    assert cover.getpixel((7, 4))[2] > cover.getpixel((7, 4))[0]

    translucent = _decode(rasterize_slide(
        _placed(_image([0, 0, 4, 4], asset="alpha"), size=(4, 4), background="#0000ff"),
        image_sources={"alpha": _png((2, 2), (255, 0, 0, 128))},
    ))
    red, green, blue = translucent.getpixel((2, 2))
    assert 126 <= red <= 129 and green == 0 and 126 <= blue <= 129


def test_text_uses_final_size_and_exact_face_index(monkeypatch) -> None:
    style, line_height = _font()
    style = {**style, "color": "#ffffff", "final_size_px": 20}
    primitive = {
        "kind": "text", "block_ref": "text", "slot": "body", "rect_px": [5, 5, 110, 40],
        "z_index": 1, "source_order": 0, "line_breaks": ["Lumeri"],
        "line_height_px": line_height, "style": style,
    }
    import gemia.deck.raster as raster_module

    original = raster_module.ImageFont.truetype
    calls: list[tuple[str, int, int]] = []

    def capture(path, size, index=0, *args, **kwargs):
        calls.append((str(path), int(size), int(index)))
        return original(path, size, index=index, *args, **kwargs)

    monkeypatch.setattr(raster_module.ImageFont, "truetype", capture)
    image = _decode(rasterize_slide(_placed(primitive, size=(120, 50), background="#000000")))
    assert calls == [(style["path"], 20, style["face_index"])]
    assert any(image.getpixel((x, y)) != (0, 0, 0) for x in range(120) for y in range(50))


def test_raster_scale_multiplies_canvas_and_geometry() -> None:
    image = _decode(rasterize_slide(
        _placed(_shape([2, 3, 4, 5], "#ffffff"), size=(10, 10), background="#000000"),
        scale=2,
    ))
    assert image.size == (20, 20)
    assert image.getpixel((4, 6)) == (255, 255, 255)
    assert image.getpixel((11, 15)) == (255, 255, 255)
    assert image.getpixel((12, 16)) == (0, 0, 0)


@pytest.mark.parametrize("scale", [0, 5, 1.5, True])
def test_invalid_scale_is_rejected(scale) -> None:
    with pytest.raises(DeckRasterError, match="scale"):
        rasterize_slide(_placed(size=(10, 10)), scale=scale)


def test_missing_bad_or_unresolved_images_fail_closed() -> None:
    no_asset = _placed(_image([0, 0, 10, 10], asset=""), size=(10, 10))
    with pytest.raises(DeckRasterError, match="no asset_id"):
        rasterize_slide(no_asset)
    missing = _placed(_image([0, 0, 10, 10]), size=(10, 10))
    with pytest.raises(DeckRasterError, match="missing from image_sources"):
        rasterize_slide(missing)
    with pytest.raises(DeckRasterError, match="must be bytes"):
        rasterize_slide(missing, image_sources={"img_001": Path("/tmp/image.png")})  # type: ignore[dict-item]
    with pytest.raises(DeckRasterError, match="not a decodable image"):
        rasterize_slide(missing, image_sources={"img_001": b"not-image"})


def test_bad_primitives_and_colors_fail_closed() -> None:
    with pytest.raises(DeckRasterError, match="escapes canvas"):
        rasterize_slide(_placed(_shape([9, 9, 2, 2], "#fff"), size=(10, 10)))
    with pytest.raises(DeckRasterError, match="unsupported .*color"):
        rasterize_slide(_placed(_shape([0, 0, 5, 5], "rgba(nope)"), size=(10, 10)))
    with pytest.raises(DeckRasterError, match="background_color must be opaque"):
        rasterize_slide(_placed(size=(10, 10), background="rgba(0,0,0,0.5)"))
    with pytest.raises(DeckRasterError, match="image_sources must be a mapping"):
        rasterize_slide(_placed(size=(10, 10)), image_sources=[])  # type: ignore[arg-type]
    with pytest.raises(DeckRasterError, match="unsupported image fit"):
        rasterize_slide(
            _placed(_image([0, 0, 10, 10], fit="stretch"), size=(10, 10)),
            image_sources={"img_001": _png((2, 2), "red")},
        )
    with pytest.raises(DeckRasterError, match="unsupported placed primitive"):
        rasterize_slide(_placed({
            "kind": "video", "block_ref": "v", "rect_px": [0, 0, 5, 5],
        }, size=(10, 10)))


def test_layout_to_raster_cjk_gold_standard_when_hiragino_is_available() -> None:
    hiragino = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
    if not hiragino.exists():
        pytest.skip("macOS Hiragino fixture unavailable")
    slide = normalize_deck({"slides": [{
        "id": "cjk", "layout": "content", "title": "中文标题",
        "blocks": [{"id": "body", "kind": "text", "text": "中文首帧清晰可见"}],
    }]})["slides"][0]
    placed = layout_slide(slide)
    body = next(item for item in placed["placed_blocks"] if item.get("block_ref") == "body")
    assert body["contains_cjk"] is True
    assert body["style"]["family"] == "Hiragino Sans GB"
    assert body["style"]["face_index"] > 0
    image = _decode(rasterize_slide(placed))
    background = (16, 20, 25)
    x, y, width, height = body["rect_px"]
    assert any(
        image.getpixel((px, py)) != background
        for px in range(x, min(x + width, x + 320))
        for py in range(y, min(y + height, y + 120))
    )
