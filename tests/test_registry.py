"""Tests for the registry catalog cache and domain scoping."""
from __future__ import annotations

from gemia.registry import (
    _CATALOG_DOMAINS,
    catalog_for_categories,
    catalog_for_prompt,
    clear_catalog_cache,
)


def test_catalog_full_contains_all_domain_headers() -> None:
    clear_catalog_cache()
    text = catalog_for_prompt()
    assert "## gemia.picture" in text
    assert "## gemia.video" in text


def test_catalog_picture_only_excludes_audio_video() -> None:
    clear_catalog_cache()
    text = catalog_for_prompt("picture")
    assert "## gemia.picture" in text
    assert "## gemia.audio" not in text
    assert "## gemia.video" not in text


def test_catalog_audio_only_scope() -> None:
    clear_catalog_cache()
    text = catalog_for_prompt("audio")
    assert "## gemia.picture" not in text
    assert "## gemia.video" not in text


def test_catalog_is_cached() -> None:
    clear_catalog_cache()
    first = catalog_for_prompt("picture")
    second = catalog_for_prompt("picture")
    # Identical content is fine; identical object identity proves the cache is hit.
    assert first is second


def test_catalog_picture_smaller_than_full() -> None:
    clear_catalog_cache()
    full = catalog_for_prompt()
    picture_only = catalog_for_prompt("picture")
    assert len(picture_only) < len(full)


def test_unknown_domain_returns_empty() -> None:
    clear_catalog_cache()
    text = catalog_for_prompt("nonexistent")
    assert text == ""


def test_category_catalog_core_is_slim_and_useful() -> None:
    clear_catalog_cache()
    full = catalog_for_prompt()
    core = catalog_for_categories(["core"])
    assert "gemia.video.timeline.cut" in core
    assert "gemia.picture.color.color_grade" in core
    assert "gemia.video.generative.generate_video" in core
    assert "gemia.video.blender_link.render_blender_link_operation" in core
    assert len(core) < len(full) // 4


def test_category_catalog_routes_specific_buckets() -> None:
    clear_catalog_cache()
    color = catalog_for_categories(["color"])
    spatial = catalog_for_categories(["spatial"])
    assert "gemia.picture.color.color_grade" in color
    assert "gemia.video.blender_link.render_blender_link_operation" in spatial
