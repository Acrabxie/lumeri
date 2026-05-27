# 04 — v2 Skills system archaeology

> Date: 2026-05-28
> Question: is v2's "Skills" related to v4 "build", or is the name just colliding?

## What v2 Skills actually is

**Two completely different things share the "skill" name in v2.** Confused this for myself for an hour until I separated them.

### Layer A: built-in skill manifests (planner routing)

23 hand-written manifests on disk at `gemia/ai/skills/<slug>/SKILL.md`:

```
gemia/ai/skills/
├── _validate.py
├── _combos/                          (skill-pair compatibility manifests)
│   ├── stock-media+timeline-ops.yaml
│   ├── timeline-ops+color-grade.yaml
│   ├── timeline-ops+html-graphics.yaml
│   └── transition+color-grade.yaml
├── ad-graphics/SKILL.md
├── analysis/SKILL.md
├── blemish-removal/SKILL.md
├── blender-link/SKILL.md
├── blur-defocus/SKILL.md
├── cinefocus/SKILL.md
├── color-grade/SKILL.md
├── composite-blend/SKILL.md
├── creative-runtime/SKILL.md
├── face-age/SKILL.md
├── face-reshaper/SKILL.md
├── face-tracking/SKILL.md
├── generative/SKILL.md
├── html-graphics/SKILL.md
├── layer-flow/SKILL.md
├── lumeri-execution/SKILL.md
├── motion-deblur/SKILL.md
├── slate-id/SKILL.md
├── stock-media/SKILL.md
└── stylize-art/SKILL.md
```

Each `SKILL.md` is YAML frontmatter + Markdown prose. Example
(`gemia/ai/skills/ad-graphics/SKILL.md`):

```yaml
---
id: ad-graphics
description: |
  用于商业广告风格的文字、图案、产品卖点、CTA 卡、价格/促销标签、产品标注和扫光动效。
triggers:
  primary: [商业广告, 广告图文, 广告动效, 产品卖点, CTA, cta card, ...]
  secondary: [卖点, 促销, 价格标签, 按钮, 购买按钮, ...]
primitives:
  - gemia.video.ad_graphics.render_ad_title_pack
  - gemia.video.ad_graphics.render_lower_third
  - gemia.video.ad_graphics.render_cta_card
  - gemia.video.ad_graphics.render_product_callout
  - gemia.video.ad_graphics.render_shimmer_sweep
  - gemia.video.ad_graphics.compose_overlay_on_video
est_tokens: 720
---

# ad-graphics

## 何时使用
用户要"商业广告感"的图文动效时使用 ...

## 工作流
优先把广告效果拆成明确图层和时间段 ...
```

So a "skill" here is **a curated primitive subset + when-to-use prose + keyword triggers**. The runtime support:

| Module | LOC | Job |
|---|---:|---|
| `gemia/ai/skill_router.py` | 389 | Keyword match `request → SKILL.md.triggers`. Fallback to LLM routing. Returns 1+ skill ids the planner should scope to. |
| `gemia/ai/skill_yaml.py` | 156 | Tiny hand-rolled YAML loader (avoids the PyYAML dep). |
| `gemia/ai/skill_context.py` | 244 | Assembles the planner prompt: pulls the chosen skill's SKILL.md, includes its primitive list and prose, returns a `SkillPromptBundle`. |
| `gemia/ai/skill_telemetry.py` | 221 | SQLite log of route events (which skill was picked, what was actually planned, did the user retry). For tuning the router. |
| `gemia/ai/skills/_validate.py` | small | Schema validator for SKILL.md frontmatter. |

**Total: ~1010 lines of skill-routing infrastructure.** It is the v2 planner's way of saying "don't show Gemini all 813 primitives; show it the ~10 relevant to this request." Without this, v2's plan-JSON generation would have an unmanageable catalog in the prompt.

### Layer B: user-saved skills (template store)

A separate, much smaller thing at `gemia/skill_store.py` (267 lines). It looks at a completed v2 task and snapshots it as a reusable template:

```python
# from gemia/skill_store.py:73-95
skill = {
    "name": name,
    "description": description or plan.get("goal", ""),
    "version": "2.0",
    "origin_task_id": task_id,
    "created_at": datetime.now().isoformat(),
    "models_used": models_used,
    "parameters": parameters,
    "plan": {
        "version": "2.0",
        "goal": plan.get("goal", name),
        "steps": template_steps,   # concrete paths stripped, params extracted
    },
}
```

Saved to `<repo_root>/skills_v2/<slug>.json`. The intent: "user runs a workflow they like, says save this as a skill, gets a reusable parametrized plan."

`grep -rln 'from gemia.skill_store'` returns exactly **2 files**: `server.py` and `gemia/skill_store.py` itself. The server exposes endpoints for it; nothing else in the codebase consumes saved skills. There is no evidence in the repo that the v2 UI actually drives this end-to-end. It looks like an unfinished or abandoned feature.

## Can users write their own skills?

**Layer A — built-in manifests:** technically yes (they're plain YAML files), but the experience is unfinished. The directory has 23 hand-curated skills; there's no UI to add a 24th. A user editing `~/.gemia/skills/my-skill/SKILL.md` would need to know the YAML schema, find the right primitive FQNs, and the schema validator wouldn't catch their typos until plan-time.

**Layer B — saved skills:** in principle yes (save_from_task is a real method), but with no UI surfacing it and the limited callers, this seems to never have been used in practice.

## Persistence

- Layer A: read-only, on-disk SKILL.md files in the package.
- Layer B: writeable JSON files in `<repo>/skills_v2/`.
- SkillStore also touches `<repo>/tasks/` and `<repo>/plans/` to derive templates from completed tasks (so it depends on the v2 task/plan persistence which v3 doesn't use).

## Reusable for v4?

This is the load-bearing question. My read:

### What's *not* directly reusable

- **The router (`skill_router.py`).** v4 build means the model writes new code rather than picking a pre-canned skill. Routing logic doesn't apply.
- **The keyword triggers + synonyms tables.** Designed for routing JSON-plan to a primitive subset; v4 doesn't have that step.
- **The skill_telemetry SQLite logger.** Tied to the v2 route-event shape.

### What's potentially reusable, with care

- **SKILL.md format itself** (YAML frontmatter + Markdown prose, with declared `primitives:` list and `est_tokens`). This is actually a clean format. If v4 wants to **persist what the model built** (so the next session can reuse a script), the SKILL.md format extended with `script:` + `inputs:` schema is a reasonable shape. ~3 hours of work to adapt.
- **`gemia/ai/skill_yaml.py`** (the lightweight YAML loader). Zero deps. Reusable as-is if v4 needs YAML.
- **The 23 SKILL.md files as a curated primitive-subset list.** Each SKILL.md names 5-10 primitives that go together for a task family. That curation is human knowledge worth not throwing away — v4's `lumeri.*` package could expose primitives organized by these same families.

### What v4 should *not* try to be

v4 "build" is fundamentally **the model writes new code**. v2 Skills is **the model picks from a 23-item canned menu**. These are different products. Don't conflate them just because both call the unit a "skill".

The cleanest framing: v3 already has the 15-verb canned menu (`build` will be the 16th). v4's `build` is the escape hatch when the menu doesn't have what you want. v2 Skills is a routing optimization for a planner architecture v3 deliberately replaced; it doesn't fit on top of v3.

## The right `build`-output persistence pattern

If we want "save what the model built so it can be reused":

1. After a successful `build` call, surface a `Save as template?` button in the v3 frontend.
2. On save, write a `<sid>-<slug>.skill.md` to `~/.gemia/v3-templates/` with YAML frontmatter (id, description, triggers — model-suggested, user-editable, primitives — discovered from the script's imports) + the script body.
3. Future sessions: when system prompt is assembled, include a compact list of saved templates ("you have these saved templates available: ..."). Model can call a new `apply_template(template_id, inputs)` verb, which is essentially `build` with the script pre-filled.

This reuses the SKILL.md format and `skill_yaml.py` loader. Estimated work: ~6-8 hours additional once `build` itself is working.

## Bottom line

- v2 Skills is **planner-side routing** + an **abandoned task-template store**. Neither directly fits v4 build.
- The SKILL.md *format* is reusable for v4 build-output persistence (~6-8 hours of adaptation).
- The 23 hand-written SKILL.md files are useful as **curated primitive groupings** when carving the `lumeri.*` package's public API surface.
- Don't try to make v4 build "extend" v2 Skills — they're solving different problems.

---

*Verified by reading `gemia/ai/skills/ad-graphics/SKILL.md`, `gemia/ai/skill_router.py:21-80`, `gemia/skill_store.py:30-110`, and grepping for consumers.*
