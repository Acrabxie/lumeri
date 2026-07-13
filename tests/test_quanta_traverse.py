"""Golden tests for the discrete-video traversal kernel (quanta-kernel-plan §3).

One ordered state tree, three faces: leaf_walk (structure), step (viewing),
and — in K2 — node-addressed ops (mutation). The continuous-video degenerate
case must fall out with zero special-casing.
"""
from __future__ import annotations

import pytest

from gemia.quanta.traverse import (
    END,
    QuantaTraverseError,
    Leaf,
    first_leaf_of,
    find_node,
    flat_view,
    flatten,
    flattened_interactions,
    leaf_walk,
    lift_flat_quanta,
    run,
    step,
)


def _flat_fixture() -> dict:
    """Canonical v1 flat quanta: 3 slides, builds, links, default_path."""
    return {
        "version": 1,
        "theme": {"tokens": {}, "mood": "calm-tech", "aspect": "16:9"},
        "slides": [
            {
                "id": "s1", "layout": "title", "title": "One",
                "blocks": [
                    {"id": "blk_t", "kind": "text", "text": "hi", "bullets": []},
                    {"id": "blk_cta", "kind": "shape", "shape": "rect"},
                ],
                "notes": "", "mood_override": None,
                "builds": [
                    {"id": "b1", "dwell_sec": 1.0, "visible_block_ids": ["blk_t"]},
                    {"id": "b2", "dwell_sec": 2.0, "visible_block_ids": ["blk_t", "blk_cta"]},
                ],
                "links": [{"trigger": "hotspot:blk_cta", "target": "slide:s3"}],
                "transition": {"kind": "cut"},
            },
            {
                "id": "s2", "layout": "content", "title": "Two",
                "blocks": [{"id": "blk_a", "kind": "text", "text": "x", "bullets": []}],
                "notes": "", "mood_override": None,
                "builds": [{"id": "b1", "dwell_sec": 3.0, "visible_block_ids": ["blk_a"]}],
                "links": [],
                "transition": {"kind": "fade"},
            },
            {
                "id": "s3", "layout": "content", "title": "Three",
                "blocks": [{"id": "blk_z", "kind": "text", "text": "z", "bullets": []}],
                "notes": "", "mood_override": None,
                "builds": [{"id": "b1", "dwell_sec": 1.5, "visible_block_ids": ["blk_z"]}],
                "links": [],
                "transition": {"kind": "cut"},
            },
        ],
        "default_path": ["s1", "s2", "s3"],
    }


def _tree_fixture() -> dict:
    """Hand-built v2 tree: group section + hidden appendix + jump edges."""
    return {
        "version": 2,
        "theme": {"tokens": {}, "mood": "", "aspect": "16:9"},
        "root": {"id": "root", "children": [
            {"id": "intro", "layout": "title", "title": "Intro",
             "blocks": [{"id": "blk_logo", "kind": "shape", "shape": "rect"}],
             "notes": "", "mood_override": None,
             "transition": {"kind": "cut"},
             "links": [{"trigger": "hotspot:blk_logo", "target": "quantum:appendix"}],
             "hidden": False,
             "children": [
                 {"id": "intro_b1", "visible_block_ids": ["blk_logo"],
                  "dwell_sec": 1.0, "advance": "auto"},
             ]},
            {"id": "chapter", "title": "Chapter", "hidden": False, "children": [
                {"id": "c1", "layout": "content", "title": "C1",
                 "blocks": [{"id": "blk_a", "kind": "text", "text": "a", "bullets": []}],
                 "notes": "", "mood_override": None,
                 "transition": {"kind": "fade"}, "links": [], "hidden": False,
                 "children": [
                     {"id": "c1_s1", "visible_block_ids": [], "dwell_sec": 1.0, "advance": "wait"},
                     {"id": "c1_s2", "visible_block_ids": ["blk_a"], "dwell_sec": 2.0, "advance": "wait"},
                 ]},
                {"id": "c2", "layout": "content", "title": "C2",
                 "blocks": [{"id": "blk_b", "kind": "text", "text": "b", "bullets": []}],
                 "notes": "", "mood_override": None,
                 "transition": {"kind": "cut"},
                 "links": [{"trigger": "advance", "target": "quantum:intro"}],
                 "hidden": False,
                 "children": [
                     {"id": "c2_s1", "visible_block_ids": ["blk_b"], "dwell_sec": 1.0, "advance": "wait"},
                 ]},
            ]},
            {"id": "appendix", "title": "Backup", "hidden": True, "children": [
                {"id": "x1", "layout": "content", "title": "X1",
                 "blocks": [{"id": "blk_x", "kind": "text", "text": "x", "bullets": []}],
                 "notes": "", "mood_override": None,
                 "transition": {"kind": "cut"}, "links": [], "hidden": False,
                 "children": [
                     {"id": "x1_s1", "visible_block_ids": ["blk_x"], "dwell_sec": 1.0, "advance": "wait"},
                     {"id": "x1_s2", "visible_block_ids": ["blk_x"], "dwell_sec": 1.0, "advance": "wait"},
                 ]},
            ]},
        ]},
    }


# ── lift ────────────────────────────────────────────────────────────────


def test_lift_builds_tree_with_document_unique_state_ids():
    doc = lift_flat_quanta(_flat_fixture())
    assert doc["version"] == 2
    scopes = doc["root"]["children"]
    assert [node["id"] for node in scopes] == ["s1", "s2", "s3"]
    assert [state["id"] for state in scopes[0]["children"]] == ["s1_b1", "s1_b2"]
    assert [state["id"] for state in scopes[1]["children"]] == ["s2_b1"]
    assert "default_path" not in doc


def test_lift_rewrites_slide_link_targets_to_quantum():
    doc = lift_flat_quanta(_flat_fixture())
    links = doc["root"]["children"][0]["links"]
    assert links == [{"trigger": "hotspot:blk_cta", "target": "quantum:s3"}]


def test_lift_is_idempotent_and_roundtrip_stable():
    doc = lift_flat_quanta(_flat_fixture())
    assert lift_flat_quanta(doc) == doc
    # tree → flat projection → lift again: no id drift (s1_b1 never becomes s1_s1_b1)
    recycled = lift_flat_quanta(flat_view(doc))
    assert recycled == doc


def test_lift_orders_scopes_by_default_path():
    flat = _flat_fixture()
    flat["default_path"] = ["s2", "s1", "s3"]
    doc = lift_flat_quanta(flat)
    assert [node["id"] for node in doc["root"]["children"]] == ["s2", "s1", "s3"]


# ── leaf walk / flat view ───────────────────────────────────────────────


def test_leaf_walk_order_and_pager_indices():
    doc = lift_flat_quanta(_flat_fixture())
    walk = leaf_walk(doc)
    assert [leaf.state_id for leaf in walk] == ["s1_b1", "s1_b2", "s2_b1", "s3_b1"]
    assert [(leaf.scope_index, leaf.state_index) for leaf in walk] == [
        (0, 0), (0, 1), (1, 0), (2, 0),
    ]
    assert walk[0].scope_id == "s1"
    assert walk[0].ancestor_ids[-1] == "s1"


def test_leaf_walk_skips_hidden_subtrees_unless_included():
    doc = _tree_fixture()
    assert [leaf.state_id for leaf in leaf_walk(doc)] == [
        "intro_b1", "c1_s1", "c1_s2", "c2_s1",
    ]
    assert [leaf.state_id for leaf in leaf_walk(doc, include_hidden=True)] == [
        "intro_b1", "c1_s1", "c1_s2", "c2_s1", "x1_s1", "x1_s2",
    ]


def test_leaf_walk_rejects_nested_content_scopes():
    doc = _tree_fixture()
    doc["root"]["children"][0]["children"].append({
        "id": "bad", "layout": "content", "blocks": [], "children": [],
    })
    with pytest.raises(QuantaTraverseError, match="nests another content node"):
        leaf_walk(doc)


def test_flat_view_projects_groups_away_and_drops_hidden():
    doc = _tree_fixture()
    view = flat_view(doc)
    assert [slide["id"] for slide in view["slides"]] == ["intro", "c1", "c2"]
    assert view["default_path"] == ["intro", "c1", "c2"]
    c1 = view["slides"][1]
    assert [build["id"] for build in c1["builds"]] == ["c1_s1", "c1_s2"]
    included = flat_view(doc, include_hidden=True)
    assert [slide["id"] for slide in included["slides"]] == ["intro", "c1", "c2", "x1"]


# ── step: the interaction interpreter ───────────────────────────────────


def test_step_entry_and_structural_advance():
    doc = lift_flat_quanta(_flat_fixture())
    entry = step(doc, None, {})
    assert entry.to == "s1_b1"
    assert step(doc, "s1_b1", {"kind": "advance"}).to == "s1_b2"
    crossing = step(doc, "s1_b2", {"kind": "advance"})
    assert crossing.to == "s2_b1"
    assert crossing.transition == {"kind": "fade"}
    assert step(doc, "s3_b1", {"kind": "advance"}).to == END


def test_step_back_lands_on_previous_scope_final_state():
    doc = lift_flat_quanta(_flat_fixture())
    assert step(doc, "s2_b1", {"kind": "back"}).to == "s1_b2"
    assert step(doc, "s1_b1", {"kind": "back"}).to == "s1_b1"  # first leaf holds


def test_content_mounted_advance_is_the_scope_exit_edge():
    doc = _tree_fixture()
    # c2's advance edge fires from its final state → jumps to intro
    assert step(doc, "c2_s1", {"kind": "advance"}).to == "intro_b1"
    # c1 has no advance edge: structural next within/out of scope
    assert step(doc, "c1_s1", {"kind": "advance"}).to == "c1_s2"
    assert step(doc, "c1_s2", {"kind": "advance"}).to == "c2_s1"


def test_state_mounted_advance_overrides_only_its_own_state():
    doc = _tree_fixture()
    c1 = find_node(doc, "c1")
    c1["children"][0] = dict(c1["children"][0])
    c1["children"][0]["links"] = [{"trigger": "advance", "target": "quantum:c2"}]
    assert step(doc, "c1_s1", {"kind": "advance"}).to == "c2_s1"


def test_hotspot_matches_scope_links_and_noops_otherwise():
    doc = lift_flat_quanta(_flat_fixture())
    hit = step(doc, "s1_b2", {"kind": "hotspot", "block_id": "blk_cta"})
    assert hit.to == "s3_b1"
    miss = step(doc, "s2_b1", {"kind": "hotspot", "block_id": "blk_cta"})
    assert miss.to == "s2_b1"


def test_url_target_keeps_cursor_and_emits_effect():
    doc = lift_flat_quanta(_flat_fixture())
    scope = find_node(doc, "s2")
    scope["links"] = [{"trigger": "hotspot:blk_a", "target": "url:https://lumeri.example"}]
    result = step(doc, "s2_b1", {"kind": "hotspot", "block_id": "blk_a"})
    assert result.to == "s2_b1"
    assert result.effect == {"url": "https://lumeri.example"}


def test_goto_enters_hidden_subtree_and_advance_detours_back():
    doc = _tree_fixture()
    jumped = step(doc, "intro_b1", {"kind": "goto", "target_id": "appendix"})
    assert jumped.to == "x1_s1"
    # inside the hidden subtree advance walks its own leaves…
    assert step(doc, "x1_s1", {"kind": "advance"}).to == "x1_s2"
    # …and past its last leaf resumes at END (appendix is the last subtree)
    assert step(doc, "x1_s2", {"kind": "advance"}).to == END
    # back from inside the detour returns to the hidden sibling first
    assert step(doc, "x1_s2", {"kind": "back"}).to == "x1_s1"


def test_hotspot_jump_can_cycle_without_divergence():
    doc = _tree_fixture()
    seq = run(doc, [
        {"kind": "hotspot", "block_id": "blk_logo"},   # intro → appendix (hidden)
        {"kind": "advance"},                             # x1_s1 → x1_s2
        {"kind": "goto", "target_id": "intro"},        # back to intro
        {"kind": "hotspot", "block_id": "blk_logo"},   # cycle again
    ])
    assert seq == ("intro_b1", "x1_s1", "x1_s2", "intro_b1", "x1_s1")


def test_run_starts_at_given_state():
    doc = _tree_fixture()
    assert run(doc, [{"kind": "advance"}], start="c1_s2") == ("c1_s2", "c2_s1")
    with pytest.raises(QuantaTraverseError, match="unknown start state"):
        run(doc, [], start="nope")


def test_step_rejects_unknown_cursor_and_event():
    doc = _tree_fixture()
    with pytest.raises(QuantaTraverseError, match="unknown cursor state"):
        step(doc, "ghost", {"kind": "advance"})
    with pytest.raises(QuantaTraverseError, match="unknown traversal event"):
        step(doc, "intro_b1", {"kind": "teleport"})


# ── flatten ─────────────────────────────────────────────────────────────


def test_flatten_projects_visible_walk_with_transitions():
    doc = _tree_fixture()
    frames = flatten(doc)
    assert [frame.state_id for frame in frames] == [
        "intro_b1", "c1_s1", "c1_s2", "c2_s1",
    ]
    assert [frame.transition_in for frame in frames] == ["cut", "fade", "cut", "cut"]
    assert frames[1].scope_index == 1 and frames[2].state_index == 1


def test_flatten_rejects_non_positive_dwell():
    doc = _tree_fixture()
    find_node(doc, "c1")["children"][0]["dwell_sec"] = 0
    with pytest.raises(QuantaTraverseError, match="dwell_sec must be > 0"):
        flatten(doc)


def test_flattened_interactions_reports_discarded_edges():
    doc = _tree_fixture()
    assert set(flattened_interactions(doc)) == {"intro", "c2"}


def test_degenerate_continuous_video_needs_no_special_case():
    """A linear chain of single-state scopes, all auto — the classical limit."""
    flat = {
        "version": 1,
        "theme": {"tokens": {}, "mood": "", "aspect": "16:9"},
        "slides": [
            {
                "id": f"s{i}", "layout": "content", "title": "",
                "blocks": [{"id": f"blk{i}", "kind": "text", "text": "t", "bullets": []}],
                "notes": "", "mood_override": None,
                "builds": [{
                    "id": "b1", "dwell_sec": 2.0,
                    "visible_block_ids": [f"blk{i}"], "advance": "auto",
                }],
                "links": [], "transition": {"kind": "cut"},
            }
            for i in range(1, 4)
        ],
        "default_path": ["s1", "s2", "s3"],
    }
    doc = lift_flat_quanta(flat)
    frames = flatten(doc)
    assert [frame.state_id for frame in frames] == ["s1_b1", "s2_b1", "s3_b1"]
    assert all(frame.dwell_sec == 2.0 for frame in frames)
    assert all(leaf.advance == "auto" for leaf in leaf_walk(doc))
    assert flattened_interactions(doc) == ()
    # pure advance replay traverses the same linear chain to END
    assert run(doc, [{"kind": "advance"}] * 3) == ("s1_b1", "s2_b1", "s3_b1", END)


# ── lookup helpers ──────────────────────────────────────────────────────


def test_find_node_and_first_leaf_of():
    doc = _tree_fixture()
    assert find_node(doc, "chapter")["title"] == "Chapter"
    assert find_node(doc, "nope") is None
    assert first_leaf_of(doc, "chapter").state_id == "c1_s1"
    assert first_leaf_of(doc, "c1_s2").state_id == "c1_s2"
    assert first_leaf_of(doc, "appendix").state_id == "x1_s1"
