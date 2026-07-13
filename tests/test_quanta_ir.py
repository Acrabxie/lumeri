"""Quanta IR: the state tree's ops, persistence, validation, and drafting.

Covers the load-bearing behaviors of quanta-kernel-plan §2/§4/§6:
- the GOLDEN persistence tests (§6.1): set_quanta → ProjectStore reload →
  every field of the state tree survives — flat sugar AND hand-built tree
  (groups, hidden, advance) — killing the silent normalize strip;
- strict reference-integrity validation (§2.2): duplicate quantum/block ids,
  invalid/non-monotonic state visibility, dangling link targets (enumerated),
  hotspot blocks outside their scope, links mounted on groups, nested content
  scopes, dwell_sec <= 0, all-hidden trees — all TimelinePatchError E_BAD_ARG
  — while structural gaps backfill;
- the edit tree (§4): update_quantum patch/insert/remove/move, atomic multi-op
  batches (remove + retarget in one undoable patch), timeline_undo rewind;
- draft_quanta theme mode (pitch structure) and from_shotlist migration;
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
from gemia.project_model import empty_project, normalize_quanta
from gemia.project_store import ProjectHandle, ProjectStore
from gemia.quanta.traverse import flat_view, leaf_walk
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


_PROJECT_ID = "v3-quanta01"


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", _PROJECT_ID, session_id=_PROJECT_ID)
    return ToolContext(
        session_id=_PROJECT_ID, output_dir=tmp_path, registry=AssetRegistry(),
        emit_progress=lambda _u: None, project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def _scopes(quanta: dict[str, Any]) -> list[dict[str, Any]]:
    return flat_view(quanta, include_hidden=True)["slides"]


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


def _assert_explicit_build_contract(flat: dict[str, Any]) -> None:
    for slide in flat["slides"]:
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


# NOTE: hotspot blocks are strictly validated against their scope now, so the
# fixture's link targets reference REAL block ids (blk_cta / blk_url).
_QUANTA = {
    "version": 1,
    "theme": {"tokens": {"color.accent": "#5FC6DE"}, "mood": "calm-tech", "aspect": "16:9"},
    "slides": [
        {"id": "s1", "layout": "title", "title": "One Lumen",
         "blocks": [
             {"kind": "text", "role": "title", "text": "One Lumen", "style_token": "type.display"},
             {"id": "blk_cta", "kind": "shape", "shape": "rect", "role": "accent", "fill_token": "color.accent"},
         ],
         "notes": "开场：一句话点出主题。",
         "builds": [{"id": "b1", "dwell_sec": 1.2}, {"id": "b2", "dwell_sec": 2.0}],
         "links": [{"trigger": "hotspot:blk_cta", "target": "slide:s2"}],
         "transition": {"kind": "cut"}},
        {"id": "s2", "layout": "stat", "title": "数据",
         "blocks": [
             {"kind": "stat", "value": "97", "label": "工具数"},
             {"id": "blk_url", "kind": "image", "asset_id": "img_003", "role": "hero", "source": "search"},
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


# ── golden persistence (§6.1: kills the silent normalize strip) ──────────
def test_set_quanta_survives_store_reload_field_by_field(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("set_quanta", {"quanta": _QUANTA}, ctx)
    assert out["applied"] is True
    assert out["scope_count"] == 2 and out["state_count"] == 3

    # A BRAND-NEW store reading state.json from disk — the full load() path
    # (normalize_project → _normalize_canonical_project) must pass quanta through.
    quanta = ProjectStore(tmp_path / "project").load(_PROJECT_ID)["quanta"]

    assert quanta["version"] == 2
    assert quanta["theme"]["mood"] == "calm-tech"
    assert quanta["theme"]["aspect"] == "16:9"
    assert quanta["theme"]["tokens"] == {"color.accent": "#5FC6DE"}
    assert quanta["root"]["id"] == "root"
    s1, s2 = quanta["root"]["children"]
    assert [s1["id"], s2["id"]] == ["s1", "s2"]
    assert s1["layout"] == "title" and s1["title"] == "One Lumen"
    assert s1["blocks"][0]["kind"] == "text"
    assert [block["id"] for block in s1["blocks"]] == ["blk_1", "blk_cta"]
    assert s1["blocks"][0]["text"] == "One Lumen"
    assert s1["blocks"][0]["style_token"] == "type.display"
    assert s1["blocks"][1]["kind"] == "shape"
    assert s1["blocks"][1]["fill_token"] == "color.accent"
    assert s1["notes"] == "开场：一句话点出主题。"
    assert s1["hidden"] is False
    # builds lift into state children with document-unique prefixed ids
    assert [state["id"] for state in s1["children"]] == ["s1_b1", "s1_b2"]
    assert [state["dwell_sec"] for state in s1["children"]] == [1.2, 2.0]
    assert [state["visible_block_ids"] for state in s1["children"]] == [
        ["blk_1", "blk_cta"], ["blk_1", "blk_cta"],
    ]  # legacy builds without visibility mean the whole scope
    assert all(state["advance"] == "wait" for state in s1["children"])
    # slide: link grammar lifts to quantum:
    assert s1["links"] == [{"trigger": "hotspot:blk_cta", "target": "quantum:s2"}]
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

    # load() must be idempotent on the quanta (a second read changes nothing).
    assert ProjectStore(tmp_path / "project").load(_PROJECT_ID)["quanta"] == quanta


def test_tree_shape_with_groups_and_hidden_survives_reload(tmp_path):
    """The v2-only fields — groups, hidden, advance — survive the store."""
    ctx = _ctx(tmp_path)
    tree = {
        "theme": {"mood": "calm-tech"},
        "root": {"id": "root", "children": [
            {"id": "intro", "layout": "title", "title": "Intro",
             "blocks": [{"id": "t", "kind": "text", "text": "hi"}],
             "children": [{"id": "intro_b1", "dwell_sec": 1.0,
                           "visible_block_ids": ["t"], "advance": "auto"}]},
            {"id": "chapter", "title": "第一章", "children": [
                {"id": "c1", "layout": "content",
                 "blocks": [{"id": "x", "kind": "text", "text": "x"}]},
            ]},
            {"id": "appendix", "title": "Backup", "hidden": True, "children": [
                {"id": "a1", "layout": "content",
                 "blocks": [{"id": "z", "kind": "text", "text": "z"}]},
            ]},
        ]},
    }
    _call("set_quanta", {"quanta": tree}, ctx)
    quanta = ProjectStore(tmp_path / "project").load(_PROJECT_ID)["quanta"]
    intro, chapter, appendix = quanta["root"]["children"]
    assert intro["children"][0]["advance"] == "auto"
    assert chapter["title"] == "第一章" and "blocks" not in chapter
    assert chapter["children"][0]["id"] == "c1"
    # content without states backfills one implicit full state
    assert chapter["children"][0]["children"][0]["visible_block_ids"] == ["x"]
    assert appendix["hidden"] is True
    assert appendix["children"][0]["id"] == "a1"
    # hidden scopes stay out of the visible walk but exist in the tree
    assert [leaf.scope_id for leaf in leaf_walk(quanta)] == ["intro", "c1"]
    assert [leaf.scope_id for leaf in leaf_walk(quanta, include_hidden=True)] == [
        "intro", "c1", "a1",
    ]
    assert ProjectStore(tmp_path / "project").load(_PROJECT_ID)["quanta"] == quanta


def test_empty_project_has_quanta_and_normalize_is_idempotent():
    quanta = empty_project()["quanta"]
    assert quanta["version"] == 2 and quanta["root"] == {"id": "root", "children": []}
    once = normalize_quanta(_QUANTA)
    assert once["root"]["children"]
    assert normalize_quanta(once) == once


def test_normalize_assigns_recursive_path_ids_and_preserves_explicit_empty_visibility():
    quanta = normalize_quanta({"slides": [{
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
    (scope,) = quanta["root"]["children"]
    assert scope["id"] == "s1"
    assert [block["id"] for block in _walk_blocks(scope["blocks"])] == [
        "blk_1", "blk_1_1", "blk_1_2", "blk_1_2_1", "hero",
    ]
    assert [state["id"] for state in scope["children"]] == ["s1_intro", "s1_full"]
    assert [state["visible_block_ids"] for state in scope["children"]] == [
        [], ["blk_1_1", "blk_1_2_1", "hero"],
    ]
    assert normalize_quanta(quanta) == quanta


def test_legacy_or_wrong_type_visibility_and_missing_builds_backfill_full_leaves():
    quanta = normalize_quanta({"slides": [
        {"blocks": [{"kind": "text", "text": "a"}, {"kind": "shape"}],
         "builds": [
             {"id": "legacy", "dwell_sec": 1},
             {"id": "wrong", "dwell_sec": 1, "visible_block_ids": "blk_1"},
         ]},
        {"blocks": [{"id": "only", "kind": "stat"}]},
    ]})
    first, second = quanta["root"]["children"]
    assert [state["visible_block_ids"] for state in first["children"]] == [
        ["blk_1", "blk_2"], ["blk_1", "blk_2"],
    ]
    assert second["children"] == [{
        "id": "s2_b1", "dwell_sec": 3.0, "visible_block_ids": ["only"],
        "advance": "wait", "hidden": False,
    }]


def test_bad_default_path_falls_back_to_slide_order():
    """default_path is retired: an incomplete flat path can no longer poison
    the doc — the lift falls back to slide order and the field disappears.
    Reordering is move_quantum's job now."""
    quanta = normalize_quanta({**_QUANTA, "default_path": ["s1"]})
    assert [node["id"] for node in quanta["root"]["children"]] == ["s1", "s2"]
    assert "default_path" not in quanta
    reordered = normalize_quanta({**_QUANTA, "default_path": ["s2", "s1"]})
    assert [node["id"] for node in reordered["root"]["children"]] == ["s2", "s1"]


# ── strict validation: the E_BAD_ARG classes (§2.2) ─────────────────────
def _quanta_with(**overrides: Any) -> dict[str, Any]:
    import copy

    quanta = copy.deepcopy(_QUANTA)
    quanta.update(overrides)
    return quanta


def test_duplicate_quantum_id_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _quanta_with()
    bad["slides"][1]["id"] = "s1"
    with pytest.raises(ValueError, match="E_BAD_ARG.*duplicate quantum id"):
        _call("set_quanta", {"quanta": bad}, ctx)
    # the failed patch never landed
    assert ctx.project.load()["quanta"]["root"]["children"] == []


def test_dangling_link_target_rejected_with_enumerated_positions(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _quanta_with()
    bad["slides"][0]["links"] = [{"trigger": "hotspot:blk_cta", "target": "slide:ghost"}]
    with pytest.raises(ValueError, match=r"E_BAD_ARG.*dangling link target.*s1\.links\[0\] → quantum:ghost"):
        _call("set_quanta", {"quanta": bad}, ctx)


def test_hotspot_block_outside_scope_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _quanta_with()
    bad["slides"][0]["links"] = [{"trigger": "hotspot:nope", "target": "slide:s2"}]
    with pytest.raises(ValueError, match="E_BAD_ARG.*hotspot references a\\s+block outside its scope"):
        _call("set_quanta", {"quanta": bad}, ctx)


def test_links_mounted_on_group_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    tree = {
        "root": {"id": "root", "children": [
            {"id": "sec", "title": "Sec",
             "links": [{"trigger": "advance", "target": "next"}],
             "children": [
                 {"id": "c1", "layout": "content",
                  "blocks": [{"id": "x", "kind": "text", "text": "x"}]},
             ]},
        ]},
    }
    with pytest.raises(ValueError, match="E_BAD_ARG.*group; interaction links mount only"):
        _call("set_quanta", {"quanta": tree}, ctx)


def test_nested_content_scope_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    tree = {
        "root": {"id": "root", "children": [
            {"id": "outer", "layout": "content",
             "blocks": [{"id": "x", "kind": "text", "text": "x"}],
             "children": [
                 {"id": "inner", "blocks": [{"id": "y", "kind": "text", "text": "y"}]},
             ]},
        ]},
    }
    with pytest.raises(ValueError, match="E_BAD_ARG.*nests a content scope"):
        _call("set_quanta", {"quanta": tree}, ctx)


def test_all_hidden_tree_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    bad = _quanta_with()
    bad["slides"][0]["hidden"] = True
    bad["slides"][1]["hidden"] = True
    with pytest.raises(ValueError, match="E_BAD_ARG.*every state is hidden"):
        _call("set_quanta", {"quanta": bad}, ctx)


def test_non_positive_dwell_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    for dwell in (0, float("inf"), -1.5):
        bad = _quanta_with()
        bad["slides"][0]["builds"] = [{"id": "b1", "dwell_sec": dwell}]
        with pytest.raises(ValueError, match="E_BAD_ARG.*dwell_sec"):
            _call("set_quanta", {"quanta": bad}, ctx)


def _one_slide_quanta(blocks: list[dict[str, Any]], builds: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "slides": [{"id": "s1", "blocks": blocks, "builds": builds}],
        "default_path": ["s1"],
    }


def test_recursive_block_ids_and_state_ids_must_be_unique(tmp_path):
    ctx = _ctx(tmp_path)
    duplicate_blocks = _one_slide_quanta(
        [{"id": "dup", "kind": "group", "children": [
            {"id": "dup", "kind": "text", "text": "child"},
        ]}],
        [{"id": "b1", "dwell_sec": 1}],
    )
    with pytest.raises(ValueError, match="E_BAD_ARG.*duplicate block id: dup"):
        _call("set_quanta", {"quanta": duplicate_blocks}, ctx)

    duplicate_states = _one_slide_quanta(
        [{"id": "leaf", "kind": "text", "text": "x"}],
        [{"id": "same", "dwell_sec": 1}, {"id": "same", "dwell_sec": 1}],
    )
    with pytest.raises(ValueError, match="E_BAD_ARG.*duplicate quantum id: s1_same"):
        _call("set_quanta", {"quanta": duplicate_states}, ctx)


def test_visible_refs_must_be_nonempty_unique_existing_leaves(tmp_path):
    ctx = _ctx(tmp_path)
    blocks = [{"id": "leaf", "kind": "text", "text": "x"}]
    cases = [
        ([""], "visible block id must be non-empty"),
        (["leaf", "leaf"], "duplicate visible block id"),
        (["ghost"], "references missing leaf block"),
    ]
    for visible, message in cases:
        bad = _one_slide_quanta(
            blocks, [{"id": "b1", "dwell_sec": 1, "visible_block_ids": visible}],
        )
        with pytest.raises(ValueError, match=f"E_BAD_ARG.*{message}"):
            _call("set_quanta", {"quanta": bad}, ctx)

    group_ref = _one_slide_quanta(
        [{"id": "group", "kind": "group", "children": [
            {"id": "leaf", "kind": "text", "text": "x"},
        ]}],
        [{"id": "b1", "dwell_sec": 1, "visible_block_ids": ["group"]}],
    )
    with pytest.raises(ValueError, match="E_BAD_ARG.*references missing leaf block: group"):
        _call("set_quanta", {"quanta": group_ref}, ctx)


def test_state_snapshots_must_be_monotonic_and_finish_with_exact_leaf_cover(tmp_path):
    ctx = _ctx(tmp_path)
    blocks = [
        {"id": "a", "kind": "text", "text": "a"},
        {"id": "b", "kind": "text", "text": "b"},
    ]
    nonmonotonic = _one_slide_quanta(blocks, [
        {"id": "b1", "dwell_sec": 1, "visible_block_ids": ["a", "b"]},
        {"id": "b2", "dwell_sec": 1, "visible_block_ids": ["b"]},
    ])
    with pytest.raises(ValueError, match="E_BAD_ARG.*visibility must be monotonic"):
        _call("set_quanta", {"quanta": nonmonotonic}, ctx)

    incomplete = _one_slide_quanta(blocks, [
        {"id": "b1", "dwell_sec": 1, "visible_block_ids": ["a"]},
    ])
    with pytest.raises(ValueError, match="E_BAD_ARG.*final state must exactly cover"):
        _call("set_quanta", {"quanta": incomplete}, ctx)

    valid = _one_slide_quanta(blocks, [
        {"id": "b1", "dwell_sec": 0.5, "visible_block_ids": []},
        {"id": "b2", "dwell_sec": 0.5, "visible_block_ids": ["a"]},
        {"id": "b3", "dwell_sec": 1.0, "visible_block_ids": ["a", "b"]},
    ])
    _call("set_quanta", {"quanta": valid}, ctx)
    (scope,) = ctx.project.load()["quanta"]["root"]["children"]
    assert [state["visible_block_ids"] for state in scope["children"]] == [
        [], ["a"], ["a", "b"],
    ]


# ── structural tolerance: gaps backfill, garbage drops, scopes survive ───
def test_structural_defaults_backfill_without_dropping_scopes(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": {"slides": [
        {"title": "只有标题", "garbage_key": 1,
         "blocks": [{"kind": "nope"}, {"kind": "text", "text": "ok"}, "junk"]},
    ]}}, ctx)
    quanta = ctx.project.load()["quanta"]
    assert quanta["version"] == 2
    assert quanta["theme"] == {"tokens": {}, "mood": "", "aspect": "16:9"}
    (scope,) = quanta["root"]["children"]           # garbage never drops a scope
    assert scope["id"] == "s1"                      # id backfilled
    assert scope["layout"] == "content"
    assert "garbage_key" not in scope               # unknown keys dropped
    assert [b["kind"] for b in scope["blocks"]] == ["text"]  # garbage blocks dropped
    assert scope["blocks"][0]["id"] == "blk_2"  # source-path id survives garbage before it
    assert scope["children"] == [{
        "id": "s1_b1", "dwell_sec": 3.0, "visible_block_ids": ["blk_2"],
        "advance": "wait", "hidden": False,
    }]  # one full state backfilled
    assert scope["links"] == [] and scope["transition"] == {"kind": "cut"}


# ── the edit tree: update_quantum patch/insert/remove/move + undo ────────
def test_update_quantum_patch_and_undo(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _QUANTA}, ctx)
    out = _call("update_quantum", {"quantum_id": "s2", "fields": {
        "notes": "改后的讲稿。", "builds": [{"id": "b1", "dwell_sec": 5.0}],
    }}, ctx)
    assert out["applied"] is True and out["updated_quanta"] == ["s2"]

    s1, s2 = ctx.project.load()["quanta"]["root"]["children"]
    assert s2["notes"] == "改后的讲稿。"
    assert [state["dwell_sec"] for state in s2["children"]] == [5.0]
    assert s2["title"] == "数据"                    # untouched fields survive
    assert s1["notes"] == "开场：一句话点出主题。"  # sibling untouched

    # invalid partial edits are rejected by the same strict validation
    with pytest.raises(ValueError, match="E_BAD_ARG.*dangling link target"):
        _call("update_quantum", {"quantum_id": "s2", "fields": {
            "links": [{"trigger": "hotspot:blk_url", "target": "quantum:ghost"}]}}, ctx)
    with pytest.raises(ValueError, match="E_NOT_FOUND"):
        _call("update_quantum", {"quantum_id": "ghost", "fields": {"notes": "x"}}, ctx)
    with pytest.raises(ValueError):
        _call("update_quantum", {"quantum_id": "s2", "fields": {}}, ctx)
    with pytest.raises(ValueError, match="E_BAD_ARG.*cannot change 'children'"):
        _call("update_quantum", {"quantum_id": "s2", "fields": {"children": []}}, ctx)

    # timeline_undo rewinds the quanta patch like any other patch-log entry
    _call("timeline_undo", {"steps": 1}, ctx)
    s2_back = ctx.project.load()["quanta"]["root"]["children"][1]
    assert s2_back["notes"] == "数据页讲稿。"
    assert [state["dwell_sec"] for state in s2_back["children"]] == [3.0]


def test_update_quantum_patches_a_single_state_leaf(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _QUANTA}, ctx)
    _call("update_quantum", {"quantum_id": "s1_b2", "fields": {
        "dwell_sec": 9.0, "advance": "auto",
    }}, ctx)
    s1 = ctx.project.load()["quanta"]["root"]["children"][0]
    assert [state["dwell_sec"] for state in s1["children"]] == [1.2, 9.0]
    assert s1["children"][1]["advance"] == "auto"
    assert s1["children"][0]["dwell_sec"] == 1.2   # sibling state untouched


def test_update_quantum_insert_remove_move_structure(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _QUANTA}, ctx)

    # insert a group, then move both scopes under it
    _call("update_quantum", {"op": "insert", "parent_id": "root", "index": 0,
                             "quantum": {"id": "chapter", "title": "第一章", "children": []}}, ctx)
    _call("update_quantum", {"ops": [
        {"op": "move", "quantum_id": "s1", "parent_id": "chapter"},
        {"op": "move", "quantum_id": "s2", "parent_id": "chapter"},
    ]}, ctx)
    quanta = ctx.project.load()["quanta"]
    (chapter,) = quanta["root"]["children"]
    assert [node["id"] for node in chapter["children"]] == ["s1", "s2"]
    assert [leaf.state_id for leaf in leaf_walk(quanta)] == ["s1_b1", "s1_b2", "s2_b1"]

    # move as reorder (the default_path replacement)
    _call("update_quantum", {"op": "move", "quantum_id": "s2",
                             "parent_id": "chapter", "index": 0}, ctx)
    quanta = ctx.project.load()["quanta"]
    assert [leaf.scope_id for leaf in leaf_walk(quanta)] == ["s2", "s1", "s1"]


def test_insert_state_that_breaks_exact_cover_is_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _QUANTA}, ctx)
    # appending an empty state after the full one violates monotonicity (and
    # would also break exact cover) — strict validation rejects the batch
    with pytest.raises(ValueError, match="E_BAD_ARG.*visibility must be monotonic"):
        _call("update_quantum", {"op": "insert", "parent_id": "s2",
                                 "quantum": {"visible_block_ids": [], "dwell_sec": 0.5}}, ctx)
    # inserting it FIRST keeps the cover contract
    _call("update_quantum", {"op": "insert", "parent_id": "s2", "index": 0,
                             "quantum": {"visible_block_ids": [], "dwell_sec": 0.5}}, ctx)
    s2 = ctx.project.load()["quanta"]["root"]["children"][1]
    assert [state["visible_block_ids"] for state in s2["children"]] == [
        [], ["blk_1", "blk_url", "blk_3_1", "blk_3_2"],
    ]


def test_remove_quantum_dangling_edges_and_atomic_retarget(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _QUANTA}, ctx)
    # s1 links hotspot→quantum:s2, so removing s2 alone must fail loudly…
    with pytest.raises(ValueError, match=r"E_BAD_ARG.*s1\.links\[0\] → quantum:s2"):
        _call("update_quantum", {"op": "remove", "quantum_id": "s2"}, ctx)
    # …and succeed atomically with the retarget in the SAME batch, remove first
    out = _call("update_quantum", {"ops": [
        {"op": "remove", "quantum_id": "s2"},
        {"op": "patch", "quantum_id": "s1", "fields": {"links": []}},
    ]}, ctx)
    assert out["applied"] is True
    quanta = ctx.project.load()["quanta"]
    assert [node["id"] for node in quanta["root"]["children"]] == ["s1"]
    # one atomic patch = one undo step brings both back
    _call("timeline_undo", {"steps": 1}, ctx)
    quanta = ctx.project.load()["quanta"]
    assert [node["id"] for node in quanta["root"]["children"]] == ["s1", "s2"]
    assert quanta["root"]["children"][0]["links"] == [
        {"trigger": "hotspot:blk_cta", "target": "quantum:s2"},
    ]


def test_move_quantum_rejects_cycles_and_kind_violations(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _QUANTA}, ctx)
    _call("update_quantum", {"op": "insert", "parent_id": "root",
                             "quantum": {"id": "sec", "title": "Sec", "children": []}}, ctx)
    _call("update_quantum", {"op": "move", "quantum_id": "s1", "parent_id": "sec"}, ctx)
    with pytest.raises(ValueError, match="E_BAD_ARG.*cycle"):
        _call("update_quantum", {"op": "move", "quantum_id": "sec", "parent_id": "s1"}, ctx)
    with pytest.raises(ValueError, match="E_BAD_ARG.*state can only live under a content"):
        _call("update_quantum", {"op": "move", "quantum_id": "s1_b1", "parent_id": "root"}, ctx)
    with pytest.raises(ValueError, match="E_BAD_ARG.*only states can live under a content"):
        _call("update_quantum", {"op": "move", "quantum_id": "sec", "parent_id": "s2"}, ctx)
    with pytest.raises(ValueError, match="E_BAD_ARG.*cannot move the root"):
        _call("update_quantum", {"op": "move", "quantum_id": "root", "parent_id": "sec"}, ctx)
    with pytest.raises(ValueError, match="E_NOT_FOUND"):
        _call("update_quantum", {"op": "remove", "quantum_id": "ghost"}, ctx)
    with pytest.raises(ValueError, match="E_BAD_ARG.*cannot remove the root"):
        _call("update_quantum", {"op": "remove", "quantum_id": "root"}, ctx)


def test_undo_converges_after_structural_edits(tmp_path):
    """§6.3 (IR half): edit → undo returns the exact prior tree."""
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _QUANTA}, ctx)
    original = ctx.project.load()["quanta"]
    _call("update_quantum", {"ops": [
        {"op": "insert", "parent_id": "root", "index": 0,
         "quantum": {"id": "sec", "title": "Sec", "children": []}},
        {"op": "move", "quantum_id": "s1", "parent_id": "sec"},
    ]}, ctx)
    assert ctx.project.load()["quanta"] != original
    _call("timeline_undo", {"steps": 1}, ctx)
    assert ctx.project.load()["quanta"] == original


# ── draft_quanta: theme mode ───────────────────────────────────────────────
def test_draft_quanta_pitch_structure(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("draft_quanta", {"theme": "Lumeri 产品介绍"}, ctx)
    assert out["persisted"] is True and out["source"] == "theme"
    assert out["template"] == "pitch" and out["language"] == "zh"

    quanta = ctx.project.load()["quanta"]
    flat = flat_view(quanta, include_hidden=True)
    slides = flat["slides"]
    assert out["scope_count"] == len(slides)
    assert len(slides) == 6                                   # Hook→…→CTA
    assert slides[0]["layout"] == "title"                     # cover first
    kinds = {b["kind"] for s in slides for b in s["blocks"]}
    assert {"text", "stat", "image", "shape", "group"} <= kinds  # every v1 block kind used
    assert any(b["kind"] == "stat" for b in slides[4]["blocks"])  # numbers page
    assert all(s["notes"] for s in slides)                    # speaker notes everywhere
    assert all(b["dwell_sec"] > 0 for s in slides for b in s["builds"])
    _assert_explicit_build_contract(flat)


@pytest.mark.parametrize("template", ["pitch", "report", "teach"])
def test_draft_templates_progressively_reveal_grouped_bullets_and_cards(tmp_path, template):
    ctx = _ctx(tmp_path)
    quanta = _call("draft_quanta", {
        "theme": "A focus timer", "template": template, "replace": False,
    }, ctx)["quanta"]
    # replace=false previews the FLAT draft (authoring sugar), unpersisted
    _assert_explicit_build_contract(quanta)
    groups = [
        block
        for slide in quanta["slides"]
        for block in _walk_blocks(slide["blocks"])
        if block.get("kind") == "group" and block.get("role") in {"bullets", "cards"}
    ]
    assert groups
    for group in groups:
        slide = next(
            slide for slide in quanta["slides"]
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


def test_draft_quanta_language_and_templates(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("draft_quanta", {"theme": "A minimalist focus timer", "template": "report"}, ctx)
    assert out["language"] == "en" and out["template"] == "report"
    quanta = ctx.project.load()["quanta"]
    first_scope = _scopes(quanta)[0]
    assert not any("一" <= c <= "鿿" for c in first_scope["notes"])
    # teach is the third template and validation still guards inputs
    with pytest.raises(ValueError):
        _call("draft_quanta", {"theme": "ok", "template": "nope"}, ctx)
    with pytest.raises(ValueError):
        _call("draft_quanta", {"theme": "   "}, ctx)


def test_draft_quanta_replace_false_previews_without_persisting(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("draft_quanta", {"theme": "A focus timer", "replace": False}, ctx)
    assert out["persisted"] is False and out["quanta"]["slides"]
    assert ctx.project.load()["quanta"]["root"]["children"] == []   # nothing written


# ── draft_quanta: from_shotlist migration ─────────────────────────────────
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


def test_draft_quanta_from_shotlist_maps_per_spec(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_shotlist", {"shotlist": _STORYBOARD}, ctx)
    out = _call("draft_quanta", {"from_shotlist": True}, ctx)
    assert out["persisted"] is True and out["source"] == "shotlist"

    quanta = ctx.project.load()["quanta"]
    flat = flat_view(quanta, include_hidden=True)
    slides = flat["slides"]
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

    assert quanta["theme"]["mood"] == "hopeful"                  # mood mode of shots
    _assert_explicit_build_contract(flat)
    # the degenerate continuous-video shape: one full state per scope
    assert all(len(slide["builds"]) == 1 for slide in slides)
    assert all(
        set(slide["builds"][0]["visible_block_ids"]) == set(_leaf_ids(slide))
        for slide in slides
    )


def test_draft_quanta_from_empty_shotlist_raises_tool_error(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolError, match="shotlist is empty"):
        _call("draft_quanta", {"from_shotlist": True}, ctx)


# ── registration ─────────────────────────────────────────────────────────
def test_quanta_dispatchers_are_real_not_stubs():
    for name in ("draft_quanta", "set_quanta", "update_quantum", "get_quanta"):
        assert name in DISPATCHER
        assert "stub" not in getattr(DISPATCHER[name], "__qualname__", "").lower()


def test_get_quanta_reads_back_text_and_ir(tmp_path):
    ctx = _ctx(tmp_path)
    empty = _call("get_quanta", {}, ctx)
    assert empty["scope_count"] == 0 and "quanta empty" in empty["quanta_text"]
    _call("set_quanta", {"quanta": _QUANTA}, ctx)
    got = _call("get_quanta", {}, ctx)
    assert got["scope_count"] == 2 and got["state_count"] == 3
    assert "[s1]" in got["quanta_text"] and "One Lumen" in got["quanta_text"]
    assert [node["id"] for node in got["quanta"]["root"]["children"]] == ["s1", "s2"]
