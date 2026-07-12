"""Pure deck-to-frame materialization and static-pager manifest helpers."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

from gemia.deck.layout import DeckLayoutError, layout_slide
from gemia.deck.raster import DeckRasterError, rasterize_slide


class DeckMaterializeError(ValueError):
    """Raised when a deck cannot deterministically become build-state PNGs."""


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class RenderedDeckFrame:
    slide_index: int
    build_index: int
    slide_id: str
    build_id: str
    dwell_sec: float
    png_bytes: bytes
    placed_slide: dict[str, Any]
    source_asset_ids: tuple[str, ...]

    @property
    def overflow(self) -> tuple[dict[str, Any], ...]:
        raw = self.placed_slide.get("overflow") or []
        return tuple(dict(item) for item in raw if isinstance(item, Mapping))

    def manifest_entry(self, asset_id: str) -> dict[str, Any]:
        asset = _asset_id(asset_id)
        return {
            "slide_index": self.slide_index,
            "build_index": self.build_index,
            "slide_id": self.slide_id,
            "build_id": self.build_id,
            "dwell_sec": self.dwell_sec,
            "asset_id": asset,
            "source_asset_ids": list(self.source_asset_ids),
            "overflow": [dict(item) for item in self.overflow],
        }


def _asset_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise DeckMaterializeError(f"invalid pager asset id {text!r}")
    return text


def _session_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _SESSION_RE.fullmatch(text):
        raise DeckMaterializeError(f"invalid pager session id {text!r}")
    return text


def _ordered_slides(deck: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_slides = deck.get("slides")
    if not isinstance(raw_slides, list) or any(not isinstance(item, Mapping) for item in raw_slides):
        raise DeckMaterializeError("deck.slides must be a list of slide mappings")
    slides = list(raw_slides)
    by_id: dict[str, Mapping[str, Any]] = {}
    for slide in slides:
        slide_id = str(slide.get("id") or "").strip()
        if not slide_id:
            raise DeckMaterializeError("every slide must have a non-empty id")
        if slide_id in by_id:
            raise DeckMaterializeError(f"duplicate slide id {slide_id!r}")
        by_id[slide_id] = slide
    raw_path = deck.get("default_path")
    path = [str(value or "").strip() for value in raw_path] if isinstance(raw_path, list) else []
    if not path:
        path = list(by_id)
    if len(path) != len(by_id) or len(set(path)) != len(path) or set(path) != set(by_id):
        raise DeckMaterializeError("deck.default_path must cover every slide exactly once")
    return [by_id[slide_id] for slide_id in path]


def _build_ids(slide: Mapping[str, Any]) -> list[str]:
    raw_builds = slide.get("builds")
    builds = [item for item in raw_builds if isinstance(item, Mapping)] if isinstance(raw_builds, list) else []
    if not builds:
        return [""]
    result: list[str] = []
    for build in builds:
        build_id = str(build.get("id") or "").strip()
        if not build_id:
            raise DeckMaterializeError(f"slide {slide.get('id')!r} has a build without an id")
        if build_id in result:
            raise DeckMaterializeError(f"slide {slide.get('id')!r} has duplicate build id {build_id!r}")
        result.append(build_id)
    return result


def render_deck_frames(
    deck: Mapping[str, Any],
    *,
    image_sources: Mapping[str, bytes | bytearray | memoryview] | None = None,
    scale: int = 1,
    fail_on_overflow: bool = False,
) -> tuple[RenderedDeckFrame, ...]:
    """Render every build in ``default_path`` order without filesystem access."""
    if not isinstance(deck, Mapping):
        raise DeckMaterializeError("deck must be a mapping")
    theme = deck.get("theme")
    theme = theme if isinstance(theme, Mapping) else {}
    tokens = theme.get("tokens")
    if tokens is not None and not isinstance(tokens, Mapping):
        raise DeckMaterializeError("deck.theme.tokens must be a mapping")
    sources = image_sources or {}
    frames: list[RenderedDeckFrame] = []
    try:
        for slide_index, slide in enumerate(_ordered_slides(deck)):
            slide_id = str(slide.get("id"))
            for build_index, build_id in enumerate(_build_ids(slide)):
                placed = layout_slide(
                    slide,
                    theme_tokens=tokens,
                    build_id=build_id or None,
                )
                if fail_on_overflow and placed.get("overflow"):
                    raise DeckMaterializeError(
                        f"slide {slide_id} build {placed.get('build_id')} overflowed; refine the copy"
                    )
                source_ids = tuple(dict.fromkeys(
                    str(item.get("asset_id"))
                    for item in placed.get("placed_blocks") or []
                    if isinstance(item, Mapping)
                    and item.get("kind") == "image"
                    and item.get("asset_id")
                ))
                png = rasterize_slide(placed, image_sources=sources, scale=scale)
                frames.append(RenderedDeckFrame(
                    slide_index=slide_index,
                    build_index=build_index,
                    slide_id=slide_id,
                    build_id=str(placed.get("build_id") or "b1"),
                    dwell_sec=float(placed.get("dwell_sec") or 0.0),
                    png_bytes=png,
                    placed_slide=placed,
                    source_asset_ids=source_ids,
                ))
    except DeckMaterializeError:
        raise
    except (DeckLayoutError, DeckRasterError) as exc:
        raise DeckMaterializeError(str(exc)) from exc
    return tuple(frames)


def build_deck_pager_url(
    session_id: str,
    frames: Sequence[RenderedDeckFrame],
    frame_asset_ids: Sequence[str],
    *,
    first_build_only: bool = False,
) -> str:
    """Build the only URL shape accepted by ``static/v3/deck.js``."""
    session = _session_id(session_id)
    if len(frames) != len(frame_asset_ids):
        raise DeckMaterializeError("frame_asset_ids must match rendered frames one-for-one")
    pairs = list(zip(frames, frame_asset_ids))
    if first_build_only:
        pairs = [(frame, asset) for frame, asset in pairs if frame.build_index == 0]
    if len(pairs) > 512:
        raise DeckMaterializeError("pager supports at most 512 frames")
    params: list[tuple[str, str]] = [("session_id", session)]
    params.extend(
        (
            "frame",
            f"{frame.slide_index}:{frame.build_index}:{_asset_id(asset_id)}",
        )
        for frame, asset_id in pairs
    )
    return "/v3/deck.html?" + urlencode(params)


__all__ = [
    "DeckMaterializeError",
    "RenderedDeckFrame",
    "build_deck_pager_url",
    "render_deck_frames",
]
