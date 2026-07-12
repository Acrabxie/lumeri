"""Deck IR (Slice 1): ops, persistence, validation, and drafting end-to-end.

Covers the load-bearing behaviors of deck-interactive-video-plan §2:
- the GOLDEN persistence test (§2.4): set_deck → ProjectStore reload → every
  deck field survives — this is the test that kills the silent normalize
  strip (`_normalize_canonical_project` rebuilding state without the deck);
- strict reference-integrity validation (§2.3): duplicate slide/block/build
  ids, invalid/non-monotonic build visibility, dangling link targets, a
  default_path that is not an exact cover, dwell_sec <= 0 — all
  TimelinePatchError E_BAD_ARG — while structural gaps backfill;
- update_slide partial edits + timeline_undo rolling the deck back;
- draft_deck theme mode (pitch structure) and from_shotlist migration (§2.2);
- the real dispatch registrations (no stubs).

Everything runs against tmp_path-rooted ProjectStores; nothing touches the
real ~/.gemia.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gemia.errors import ToolError
from gemia.project_model import empty_project, normalize_deck
from gemia.project_store import ProjectHandle, ProjectStore
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


_PROJECT_ID = "v3-deck01"


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", _PROJECT_ID, session_id=_PROJECT_ID)
    return ToolContext(
        session_id=_PROJECT_ID, output_dir=tmp_path, registry=AssetRegistry(),
        emit_progress=lambda _u: None, project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def _walk_blocks(blocks: Any):
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        yield block
        if block.get("kind") == "group":
            yield from _walk_blocks(block.get("children"))


def _leaf_ids(slide: dict[str, Any]) -> list[str]:
    return [
        str(block["id"])
        for block in _walk_blocks(slide.get("blocks"))
        if block.get("kind") != "group"
    ]


def _assert_explicit_build_contract(deck: dict[str, Any]) -> None:
    for slide in deck["slides"]:
        all_ids = [str(block.get("id") or "") for block in _walk_blocks(slide["blocks"])]
        assert all(all_ids) and len(all_ids) == len(set(all_ids))
        leaves = set(_leaf_ids(slide))
        previous: set[str] = set()
        build_ids: list[str] = []
        for build in slide["builds"]:
            build_ids.append(build["id"])
            assert "visible_block_ids" in build
            visible = build["visible_block_ids"]
            assert len(visible) == len(set(visible))
            current = set(visible)
            assert previous <= current <= leaves
            previous = current
        assert len(build_ids) == len(set(build_ids))
        assert previous == leaves


_DECK = {
    "version": 1,
    "theme": {"tokens": {"color.accent": "#5FC6DE"}, "mood": "calm-tech", "aspect": "16:9"},
    "slides": [
        {"id": "s1", "layout": "title", "title": "One Lumen",
         "blocks": [
             {"kind": "text", "role": "title", "text": "One Lumen", "style_token": "type.display"},
             {"kind": "shape", "shape": "rect", "role": "accent", "fill_token": "color.accent"},
         ],
         "notes": "开场：一句话点出主题。",
         "builds": [{"id": "b1", "dwell_sec": 1.2}, {"id": "b2", "dwell_sec": 2.0}],
         "links": [{"trigger": "hotspot:blk_cta", "target": "slide:s2"}],
         "transition": {"kind": "cut"}},
        {"id": "s2", "layout": "stat", "title": "数据",
         "blocks": [
             {"kind": "stat", "value": "97", "label": "工具数"},
             {"kind": "image", "asset_id": "img_003", "role": "hero", "source": "search"},
             {"kind": "group", "role": "cards", "children": [
                 {"kind": "text", "role": "card", "text": "卡一"},
                 {"kind": "text", "role": "card", "text": "卡二"},
             ]},
         ],
         "notes": "数据页讲稿。",
         "mood_override": "energetic",
         "builds": [{"id": "b1", "dwell_sec": 3.0}],
         "links": [{"trigger": "advance", "target": "next"},
                   {"trigger": "hotspot:blk_url", "target": "url:https://lumeri.app"}],
         "transition": {"kind": "fade"}},
    ],
    "default_path": ["s1", "s2"],
}


# ── golden persistence (§2.4: kills the silent normalize strip) ──────────
def test_set_deck_survives_store_reload_field_by_field(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("set_deck", {"deck": _DECK}, ctx)
    assert out["applied"] is True and out["slide_count"] == 2

    # A BRAND-NEW store reading state.json from disk — the full load() path
    # (normalize_project → _normalize_canonical_project) must pass deck through.
    deck = ProjectStore(tmp_path / "project").load(_PROJECT_ID)["deck"]

    assert deck["version"] == 1
    assert deck["theme"]["mood"] == "calm-tech"
    assert deck["theme"]["aspect"] == "16:9"
    assert deck["theme"]["tokens"] == {"color.accent": "#5FC6DE"}
    assert [s["id"] for s in deck["slides"]] == ["s1", "s2"]
    s1, s2 = deck["slides"]
    assert s1["layout"] == "title" and s1["title"] == "One Lumen"
    assert s1["blocks"][0]["kind"] == "text"
    assert [block["id"] for block in s1["blocks"]] == ["blk_1", "blk_2"]
    assert s1["blocks"][0]["text"] == "One Lumen"
    assert s1["blocks"][0]["style_token"] == "type.display"
    assert s1["blocks"][1]["kind"] == "shape"
    assert s1["blocks"][1]["fill_token"] == "color.accent"
    assert s1["notes"] == "开场：一句话点出主题。"
    assert [b["dwell_sec"] for b in s1["builds"]] == [1.2, 2.0]
    assert [b["visible_block_ids"] for b in s1["builds"]] == [
        ["blk_1", "blk_2"], ["blk_1", "blk_2"],
    ]  # legacy builds without visibility mean full slide
    assert s1["links"] == [{"trigger": "hotspot:blk_cta", "target": "slide:s2"}]
    assert s1["transition"] == {"kind": "cut"}
    assert s2["blocks"][0] == {
        "id": "blk_1", "kind": "stat", "value": "97", "label": "工具数",
    }
    assert s2["blocks"][1]["asset_id"] == "img_003"
    assert s2["blocks"][2]["kind"] == "group"
    assert s2["blocks"][2]["id"] == "blk_3"
    assert [c["text"] for c in s2["blocks"][2]["children"]] == ["卡一", "卡二"]
    assert [c["id"] for c in s2["blocks"][2]["children"]] == ["blk_3_1", "blk_3_2"]
    assert s2["mood_override"] == "energetic"
    assert s2["links"][1] == {"trigger": "hotspot:blk_url", "target": "url:https://lumeri.app"}
    assert s2["transition"] == {"kind": "fade"}
    assert deck["default_path"] == ["s1", "s2"]

    # load() must be idempotent on the deck (a second read changes nothing).
    assert ProjectStore(tmp_path / "project").load(_PROJECT_ID)["deck"] == deck


def test_empty_project_has_deck_and_normalize_is_idempotent():
    deck = empty_project()["deck"]
    assert deck["version"] == 1 and deck["slides"] == [] and deck["default_path"] == []
    once = normalize_deck(_DECK)
    assert normalize_deck(once) == once


def test_normalize_assigns_recursive_path_ids_and_preserves_explicit_empty_visibility():
    deck = normalize_deck({"slides": [{
        "blocks": [
            {"kind": "group", "children": [
                {"kind": "text", "text": "one"},
                {"kind": "group", "children": [{"kind": "shape"}]},
            ]},
            {"id": "hero", "kind": "image"},
        ],
        "builds": [
            {"id": "intro", "dwell_sec": 1, "visible_block_ids": []},
            {"id": "full", "dwell_sec": 2,
             "visible_block_ids": ["blk_1_1", "blk_1_2_1", "hero"]},
        ],
    }]})
    slide = deck["slides"][0]
    assert [block["id"] for block in _walk_blocks(slide["blocks"])] == [
        "blk_1", "blk_1_1", "blk_1_2", "blk_1_2_1", "hero",
    ]
    assert [build["visible_block_ids"] for build in slide["builds"]] == [
        [], ["blk_1_1", "blk_1_2_1", "hero"],
    ]
    assert normalize_deck(deck) == deck


def test_legacy_or_wrong_type_visibility_and_missing_builds_backfill_full_leaves():
    deck = normalize_deck({"slides": [
        {"blocks": [{"kind": "text", "text": "a"}, {"kind": "shape"}],
         "builds": [
             {"id": "legacy", "dwell_sec": 1},
             {"id": "wrong", "dwell_sec": 1, "visible_block_ids": "blk_1"},
         ]},
        {"blocks": [{"id": "only", "kind": "stat"}]},
    ]})
    first, second = deck["slides"]
    assert [build["visible_block_ids"] for build in first["builds"]] == [
        ["blk_1", "blk_2"], ["blk_1", "blk_2"],
    ]
    assert second["builds"] == [{
        "id": "b1", "dwell_sec": 3.0, "visible_block_ids": ["only"],
    }]


# ── strict validation: the four E_BAD_ARG classes (§2.3) ─────────────────
def _deck_with(**overrides: Any) -> dict[str, Any]:
    import copy

    deck = copy.deepcopy(_DECK)
    deck.update(overrides)
    return deck


def test_duplicate_slide_id_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _deck_with()
    bad["slides"][1]["id"] = "s1"
    with pytest.raises(ValueError, match="E_BAD_ARG.*duplicate slide id"):
        _call("set_deck", {"deck": bad}, ctx)
    # the failed patch never landed
    assert ctx.project.load()["deck"]["slides"] == []


def test_dangling_link_target_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _deck_with()
    bad["slides"][0]["links"] = [{"trigger": "hotspot:blk_cta", "target": "slide:ghost"}]
    with pytest.raises(ValueError, match="E_BAD_ARG.*missing slide"):
        _call("set_deck", {"deck": bad}, ctx)


def test_default_path_must_cover_all_slides_exactly(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="E_BAD_ARG.*default_path"):
        _call("set_deck", {"deck": _deck_with(default_path=["s1"])}, ctx)
    with pytest.raises(ValueError, match="E_BAD_ARG.*default_path"):
        _call("set_deck", {"deck": _deck_with(default_path=["s1", "s1", "s2"])}, ctx)


def test_non_positive_dwell_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _deck_with()
    bad["slides"][0]["builds"] = [{"id": "b1", "dwell_sec": 0}]
    with pytest.raises(ValueError, match="E_BAD_ARG.*dwell_sec"):
        _call("set_deck", {"deck": bad}, ctx)
    bad["slides"][0]["builds"] = [{"id": "b1", "dwell_sec": float("inf")}]
    with pytest.raises(ValueError, match="E_BAD_ARG.*dwell_sec"):
        _call("set_deck", {"deck": bad}, ctx)
    bad["slides"][0]["builds"] = [{"id": "b1", "dwell_sec": -1.5}]
    with pytest.raises(ValueError, match="E_BAD_ARG.*dwell_sec"):
        _call("set_deck", {"deck": bad}, ctx)


def _one_slide_deck(blocks: list[dict[str, Any]], builds: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "slides": [{"id": "s1", "blocks": blocks, "builds": builds}],
        "default_path": ["s1"],
    }


def test_recursive_block_ids_and_build_ids_must_be_unique(tmp_path):
    ctx = _ctx(tmp_path)
    duplicate_blocks = _one_slide_deck(
        [{"id": "dup", "kind": "group", "children": [
            {"id": "dup", "kind": "text", "text": "child"},
        ]}],
        [{"id": "b1", "dwell_sec": 1}],
    )
    with pytest.raises(ValueError, match="E_BAD_ARG.*duplicate block id: dup"):
        _call("set_deck", {"deck": duplicate_blocks}, ctx)

    duplicate_builds = _one_slide_deck(
        [{"id": "leaf", "kind": "text", "text": "x"}],
        [{"id": "same", "dwell_sec": 1}, {"id": "same", "dwell_sec": 1}],
    )
    with pytest.raises(ValueError, match="E_BAD_ARG.*duplicate build id: same"):
        _call("set_deck", {"deck": duplicate_builds}, ctx)


def test_visible_refs_must_be_nonempty_unique_existing_leaves(tmp_path):
    ctx = _ctx(tmp_path)
    blocks = [{"id": "leaf", "kind": "text", "text": "x"}]
    cases = [
        ([""], "visible block id must be non-empty"),
        (["leaf", "leaf"], "duplicate visible block id"),
        (["ghost"], "references missing leaf block"),
    ]
    for visible, message in cases:
        bad = _one_slide_deck(
            blocks, [{"id": "b1", "dwell_sec": 1, "visible_block_ids": visible}],
        )
        with pytest.raises(ValueError, match=f"E_BAD_ARG.*{message}"):
            _call("set_deck", {"deck": bad}, ctx)

    group_ref = _one_slide_deck(
        [{"id": "group", "kind": "group", "children": [
            {"id": "leaf", "kind": "text", "text": "x"},
        ]}],
        [{"id": "b1", "dwell_sec": 1, "visible_block_ids": ["group"]}],
    )
    with pytest.raises(ValueError, match="E_BAD_ARG.*references missing leaf block: group"):
        _call("set_deck", {"deck": group_ref}, ctx)


def test_build_snapshots_must_be_monotonic_and_finish_with_exact_leaf_cover(tmp_path):
    ctx = _ctx(tmp_path)
    blocks = [
        {"id": "a", "kind": "text", "text": "a"},
        {"id": "b", "kind": "text", "text": "b"},
    ]
    nonmonotonic = _one_slide_deck(blocks, [
        {"id": "b1", "dwell_sec": 1, "visible_block_ids": ["a", "b"]},
        {"id": "b2", "dwell_sec": 1, "visible_block_ids": ["b"]},
    ])
    with pytest.raises(ValueError, match="E_BAD_ARG.*visibility must be monotonic"):
        _call("set_deck", {"deck": nonmonotonic}, ctx)

    incomplete = _one_slide_deck(blocks, [
        {"id": "b1", "dwell_sec": 1, "visible_block_ids": ["a"]},
    ])
    with pytest.raises(ValueError, match="E_BAD_ARG.*final build must exactly cover"):
        _call("set_deck", {"deck": incomplete}, ctx)

    valid = _one_slide_deck(blocks, [
        {"id": "b1", "dwell_sec": 0.5, "visible_block_ids": []},
        {"id": "b2", "dwell_sec": 0.5, "visible_block_ids": ["a"]},
        {"id": "b3", "dwell_sec": 1.0, "visible_block_ids": ["a", "b"]},
    ])
    _call("set_deck", {"deck": valid}, ctx)
    assert [build["visible_block_ids"] for build in ctx.project.load()["deck"]["slides"][0]["builds"]] == [
        [], ["a"], ["a", "b"],
    ]


# ── structural tolerance: gaps backfill, garbage drops, slides survive ───
def test_structural_defaults_backfill_without_dropping_slides(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_deck", {"deck": {"slides": [
        {"title": "只有标题", "garbage_key": 1,
         "blocks": [{"kind": "nope"}, {"kind": "text", "text": "ok"}, "junk"]},
    ]}}, ctx)
    deck = ctx.project.load()["deck"]
    assert deck["version"] == 1
    assert deck["theme"] == {"tokens": {}, "mood": "", "aspect": "16:9"}
    (slide,) = deck["slides"]                      # garbage never drops a slide
    assert slide["id"] == "s1"                     # id backfilled
    assert slide["layout"] == "content"
    assert "garbage_key" not in slide              # unknown keys dropped
    assert [b["kind"] for b in slide["blocks"]] == ["text"]  # garbage blocks dropped
    assert slide["blocks"][0]["id"] == "blk_2"  # source-path id survives garbage before it
    assert slide["builds"] == [{
        "id": "b1", "dwell_sec": 3.0, "visible_block_ids": ["blk_2"],
    }]  # one full build backfilled
    assert slide["links"] == [] and slide["transition"] == {"kind": "cut"}
    assert deck["default_path"] == ["s1"]          # path backfilled to cover


# ── update_slide + undo ──────────────────────────────────────────────────
def test_update_slide_partial_edit_and_undo(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_deck", {"deck": _DECK}, ctx)
    out = _call("update_slide", {"slide_id": "s2", "fields": {
        "notes": "改后的讲稿。", "builds": [{"id": "b1", "dwell_sec": 5.0}],
    }}, ctx)
    assert out["applied"] is True and out["updated_slide"] == "s2"

    deck = ctx.project.load()["deck"]
    s1, s2 = deck["slides"]
    assert s2["notes"] == "改后的讲稿。"
    assert [b["dwell_sec"] for b in s2["builds"]] == [5.0]
    assert s2["title"] == "数据"                    # untouched fields survive
    assert s1["notes"] == "开场：一句话点出主题。"  # sibling untouched

    # invalid partial edits are rejected by the same strict validation
    with pytest.raises(ValueError, match="E_BAD_ARG.*missing slide"):
        _call("update_slide", {"slide_id": "s2", "fields": {
            "links": [{"trigger": "hotspot:x", "target": "slide:ghost"}]}}, ctx)
    with pytest.raises(ValueError, match="E_NOT_FOUND"):
        _call("update_slide", {"slide_id": "ghost", "fields": {"notes": "x"}}, ctx)
    with pytest.raises(ValueError):
        _call("update_slide", {"slide_id": "s2", "fields": {}}, ctx)

    # timeline_undo rewinds the deck patch like any other patch-log entry
    _call("timeline_undo", {"steps": 1}, ctx)
    s2_back = ctx.project.load()["deck"]["slides"][1]
    assert s2_back["notes"] == "数据页讲稿。"
    assert [b["dwell_sec"] for b in s2_back["builds"]] == [3.0]


# ── draft_deck: theme mode ───────────────────────────────────────────────
def test_draft_deck_pitch_structure(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("draft_deck", {"theme": "Lumeri 产品介绍"}, ctx)
    assert out["persisted"] is True and out["source"] == "theme"
    assert out["template"] == "pitch" and out["language"] == "zh"

    deck = ctx.project.load()["deck"]
    slides = deck["slides"]
    assert len(slides) == 6                                   # Hook→…→CTA
    assert slides[0]["layout"] == "title"                     # cover first
    kinds = {b["kind"] for s in slides for b in s["blocks"]}
    assert {"text", "stat", "image", "shape", "group"} <= kinds  # every v1 block kind used
    assert any(b["kind"] == "stat" for b in slides[4]["blocks"])  # numbers page
    assert all(s["notes"] for s in slides)                    # speaker notes everywhere
    assert deck["default_path"] == [s["id"] for s in slides]  # exact cover
    assert all(b["dwell_sec"] > 0 for s in slides for b in s["builds"])
    _assert_explicit_build_contract(deck)


@pytest.mark.parametrize("template", ["pitch", "report", "teach"])
def test_draft_templates_progressively_reveal_grouped_bullets_and_cards(tmp_path, template):
    ctx = _ctx(tmp_path)
    deck = _call("draft_deck", {
        "theme": "A focus timer", "template": template, "replace": False,
    }, ctx)["deck"]
    _assert_explicit_build_contract(deck)
    groups = [
        block
        for slide in deck["slides"]
        for block in _walk_blocks(slide["blocks"])
        if block.get("kind") == "group" and block.get("role") in {"bullets", "cards"}
    ]
    assert groups
    for group in groups:
        slide = next(
            slide for slide in deck["slides"]
            if any(block is group for block in _walk_blocks(slide["blocks"]))
        )
        child_ids = _leaf_ids({"blocks": group["children"]})
        first_seen = [
            next(
                index for index, build in enumerate(slide["builds"])
                if child_id in build["visible_block_ids"]
            )
            for child_id in child_ids
        ]
        assert first_seen == list(range(len(child_ids)))


def test_draft_deck_language_and_templates(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("draft_deck", {"theme": "A minimalist focus timer", "template": "report"}, ctx)
    assert out["language"] == "en" and out["template"] == "report"
    deck = ctx.project.load()["deck"]
    assert not any("一" <= c <= "鿿" for c in deck["slides"][0]["notes"])
    # teach is the third template and validation still guards inputs
    with pytest.raises(ValueError):
        _call("draft_deck", {"theme": "ok", "template": "nope"}, ctx)
    with pytest.raises(ValueError):
        _call("draft_deck", {"theme": "   "}, ctx)


def test_draft_deck_replace_false_previews_without_persisting(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("draft_deck", {"theme": "A focus timer", "replace": False}, ctx)
    assert out["persisted"] is False and out["deck"]["slides"]
    assert ctx.project.load()["deck"]["slides"] == []          # nothing written


# ── draft_deck: from_shotlist migration (§2.2) ───────────────────────────
_STORYBOARD = {
    "logline": "极简专注计时器宣传",
    "style": "cinematic, warm",
    "target_duration_sec": 12,
    "scenes": [
        {"id": "sc1", "title": "Hook", "shots": [
            {"id": "hook", "description": "city sunrise timelapse", "duration_sec": 4,
             "source": "search", "search_query": "city sunrise timelapse",
             "on_screen_text": "每个清晨", "narration": "从清晨的一分钟开始。",
             "mood": "hopeful", "asset_id": "v_001", "status": "filled",
             "transition_after": {"kind": "dissolve", "duration_sec": 0.5}},
        ]},
        {"id": "sc2", "title": "Problem", "shots": [
            {"id": "problem", "description": "cluttered desk chaos", "duration_sec": 3,
             "source": "search", "search_query": "cluttered desk chaos",
             "narration": "干扰无处不在。", "mood": "tense"},
        ]},
        {"id": "sc3", "title": "Turn", "shots": [
            {"id": "turn", "description": "calm timer interface", "duration_sec": 5,
             "source": "generate", "narration": "一个计时器，安静地开始。",
             "mood": "hopeful"},
        ]},
    ],
}


def test_draft_deck_from_shotlist_maps_per_spec(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_shotlist", {"shotlist": _STORYBOARD}, ctx)
    out = _call("draft_deck", {"from_shotlist": True}, ctx)
    assert out["persisted"] is True and out["source"] == "shotlist"

    deck = ctx.project.load()["deck"]
    slides = deck["slides"]
    assert len(slides) == 4                                    # auto cover + 3 shots
    cover = slides[0]
    assert cover["layout"] == "title" and cover["title"] == "极简专注计时器宣传"

    hook = slides[1]
    assert hook["notes"] == "从清晨的一分钟开始。"              # narration → notes
    assert hook["title"] == "每个清晨"                          # on_screen_text → title
    text_blocks = [b for b in hook["blocks"] if b["kind"] == "text"]
    assert text_blocks and text_blocks[0]["text"] == "每个清晨"  # …and a text block
    hook_img = next(b for b in hook["blocks"] if b["kind"] == "image")
    assert hook_img["asset_id"] == "v_001"                     # filled shot → asset ref
    assert [b["dwell_sec"] for b in hook["builds"]] == [4.0]   # duration → dwell
    assert hook["transition"] == {"kind": "fade"}              # dissolve → fade

    problem = slides[2]
    problem_img = next(b for b in problem["blocks"] if b["kind"] == "image")
    assert problem_img["query"] == "cluttered desk chaos"      # unfilled → search query
    assert problem_img.get("asset_id") in (None, "")
    assert [b["dwell_sec"] for b in problem["builds"]] == [3.0]

    assert deck["theme"]["mood"] == "hopeful"                  # mood mode of shots
    assert deck["default_path"] == [s["id"] for s in slides]
    _assert_explicit_build_contract(deck)
    assert all(len(slide["builds"]) == 1 for slide in slides)
    assert all(
        set(slide["builds"][0]["visible_block_ids"]) == set(_leaf_ids(slide))
        for slide in slides
    )


def test_draft_deck_from_empty_shotlist_raises_tool_error(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolError, match="shotlist is empty"):
        _call("draft_deck", {"from_shotlist": True}, ctx)


# ── registration ─────────────────────────────────────────────────────────
def test_deck_dispatchers_are_real_not_stubs():
    for name in ("draft_deck", "set_deck", "update_slide", "get_deck"):
        assert name in DISPATCHER
        assert "stub" not in getattr(DISPATCHER[name], "__qualname__", "").lower()


def test_get_deck_reads_back_text_and_ir(tmp_path):
    ctx = _ctx(tmp_path)
    empty = _call("get_deck", {}, ctx)
    assert empty["slide_count"] == 0 and "deck empty" in empty["deck_text"]
    _call("set_deck", {"deck": _DECK}, ctx)
    got = _call("get_deck", {}, ctx)
    assert got["slide_count"] == 2
    assert "[s1]" in got["deck_text"] and "One Lumen" in got["deck_text"]
    assert [s["id"] for s in got["deck"]["slides"]] == ["s1", "s2"]
