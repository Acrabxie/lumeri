# Vector Motion Design Engine — architecture & API

> **Lumeri Creative Module: Vector Creative Primitive.** 一句话:把 SVG 从"静态图片"
> 升维成"有生命的视觉元素"——点/线/曲线/面/文字/粒子各有位置、速度、生命周期与形态,
> 动画是被编舞的**视觉行为**(reveal / explode / assemble / flow / transform),
> 不是手推 keyframe。本文是该模块的架构契约;实现在 `lumenframe/vector/`。

Status: v1 implemented on branch `feat/vector-motion` (worktree, base 487c298).
Owner: claude-code. Companion: `lumenframe/vector/*`, `gemia/tools/vector_motion.py`,
`tests/test_vector_*.py`.

---

## 0. Philosophy

Do not treat SVG as a static image. A vector element is a living thing:

| element | carries |
|---|---|
| Point / Particle | position, velocity, lifecycle |
| Path / Curve | geometry, draw-progress, deformation |
| Shape | form, morph target, mass (visual weight) |
| Text | content, glyph field (particle targets), typography |
| Group | hierarchy, choreography membership |

Animation is not "moving elements". It is **controlling visual life**: a logo
does not "appear" — the scene builds expectation, guides the eye, converges,
reveals the brand, then *holds the silence*.

Three design rules inherited from the house product principles:

1. **Agent speaks creative language, never raw numbers.** `energy: 0.8`,
   never `x += 20`. Semantic parameters map internally to low-level values.
2. **Not a one-shot demo.** Every layer is a registry: behaviours, styles,
   archetypes and renderers are pluggable without forking the core.
3. **Deterministic.** Same brief + same seed ⇒ byte-identical SVG ⇒ the html
   layer's content-hash render cache hits. All randomness flows from one
   explicit `seed` (`random.Random(seed)`), never module-level state.

---

## 1. Layer map

```
                        agent brief ("playful AI logo reveal, 5s")
                              │
   ┌──────────────────────────▼──────────────────────────┐
   │ 6. Agent Interface        lumenframe/vector/api.py  │  brief → plan → scene
   │ 7. Feedback Loop          ("more playful", "calmer") │  semantic deltas
   └──────────────────────────┬──────────────────────────┘
   ┌──────────────────────────▼──────────────────────────┐
   │ 5. Composition Intelligence   choreography.py       │  phase arc · stagger ·
   │                                                     │  focal order · hold
   │ 4. Parameter System           params.py             │  energy/elegance/… → numbers
   │ 3. Visual Style Layer         styles.py             │  playful/minimal/luxury/tech
   │ 2. Motion Behaviors           behaviors/*.py        │  reveal/explode/assemble/
   │                                                     │  flow/transform verbs
   │ 1. Motion toolkit             motion.py             │  ease tokens · duration bands
   └──────────────────────────┬──────────────────────────┘
   ┌──────────────────────────▼──────────────────────────┐
   │ 0. VectorScene IR             scene.py              │  renderer-agnostic scene
   │    Vector math                geometry.py           │  graph + animation tracks
   └──────────────────────────┬──────────────────────────┘
                              │ compile
        ┌─────────────────────┼─────────────────────────┐
        ▼                     ▼                         ▼ (future)
   svg.py               render.py                 native lumenframe /
   animated SVG+CSS     html layer (HyperFrames/  Lottie / WebGL
   (a file, shareable)  Chrome → mp4, composited  adapters — same IR
                        like any video layer)
```

Dependency rule: strictly downward. `behaviors` never import `styles`;
`svg.py` reads only the IR; `api.py` is the only module that sees everything.

---

## 2. VectorScene IR (`scene.py`)

Plain JSON-serialisable dicts, mirroring `lumenframe.model`'s design choice.
Scene: `{kind:"vector_scene", version, width, height, duration, background,
seed, nodes[], meta}`. Node kinds: `path | text | group | particles`; each node
carries `style` (fill/stroke/stroke_width/opacity/line_cap/line_join),
`transform` (x/y/scale/rotation — **canvas-centred**, y down, matching
lumenframe's CapCut convention) and `tracks`.

A **track** is `{prop: [{t, value, ease}, …]}` with `t` in seconds and `ease`
naming the curve *leaving* that keyframe (CSS semantics). Track vocabulary:

| prop | meaning | SVG realisation |
|---|---|---|
| `opacity` | 0..1 | CSS `opacity` |
| `x`,`y`,`scale`,`rotation` | delta over node transform | CSS `transform` |
| `draw` | stroke draw-on 0..1 | `pathLength="1"` + `stroke-dashoffset` |
| `d` | morph target (a geometry.Path) | CSS `d: path(…)` keyframes |
| `fill`,`stroke` | colour | CSS `fill`/`stroke` |

`geometry.py` is the math floor: cubic bezier eval/split, arc-length
`point_at`/`path_length`, shape generators (rect/ellipse/polygon/star/arc/
smooth_through/blob), `resample_path` + `align_for_morph` (exact de Casteljau
resampling so any two shapes become `d`-interpolation compatible), deterministic
`scatter`/`sample_on_path`, and `to_svg_d`.

Why not reuse lumenframe layer keyframes directly? The native compile path
animates only opacity/position/uniform-scale/rotation, has no bezier easing
plumbing, no path geometry, no draw-on, no morph (recon: compile.py
`_KEYFRAME_PROP_MAP`). The IR must be richer than the weakest renderer; each
adapter degrades honestly instead of capping the vocabulary.

## 3. Motion toolkit (`motion.py`)

* **Ease tokens** — named cubic-beziers, aligned with the design-manual motion
  layer: `enter` (0,0,0.2,1), `exit` (0.4,0,1,1), `move` (0.4,0,0.2,1),
  `dramatic` (0.34,1.56,0.64,1, 慎用), plus `linear`, `hold`, and parametric
  `bezier(x1,y1,x2,y2)` strings. Tokens resolve to CSS strings for the SVG
  compiler and to sampled curves for future non-CSS renderers.
* **Duration bands** — `{min, rec, max}` per move class (enter/exit/emphasis),
  scaled by style tempo and `energy`; behaviours request a band, never a number.
* **Track builders** — tiny pure helpers behaviours compose: `fade(node, t0,
  t1, from, to, ease)`, `move_by`, `scale_pop` (overshoot then settle),
  `draw_on`, `morph_to`, `orbit`, `shake` — each just writes keyframes via
  `scene.add_track`.

## 4. Behaviours (`behaviors/`)

A behaviour is a registered pure function:

```python
@behavior("reveal.draw_on", family="reveal", summary="strokes draw themselves in")
def draw_on(scene, nodes, window, level, rng): ...
```

* `window = (t0, t1)` — its choreography slot; it must not write outside it.
* `level: ResolvedParams` — the semantic parameter resolution (see §5/§6);
  behaviours read `level.energy`, `level.overshoot`, `level.stagger`, … and
  the per-node stagger offsets choreography computed.
* `rng` — the scene's seeded `random.Random`; module-level randomness is a bug.

Five families ship in v1 (names are the agent-facing vocabulary):

| family | verbs |
|---|---|
| **reveal** | `draw_on`, `fade_in`, `grow`, `unfold`, `rise` |
| **explode** | `burst`, `scatter`, `dissolve`, `energy_release` |
| **assemble** | `gather`, `magnetic`, `converge`, `form` (logo formation) |
| **flow** | `wave`, `breathe`, `liquid`, `drift`, `orbit` |
| **transform** | `morph`, `reshape`, `spin_swap`, `crossfade` |

Registry (`behaviors/__init__.py`) mirrors the template/element pattern:
`BEHAVIORS` dict + `BEHAVIOR_CATALOG` metadata + `describe_behaviors()` for the
agent prompt; a test pins catalog entries to real signatures so docs can't
drift (same discipline as `lumenframe/templates/__init__.py`).

## 5. Styles (`styles.py`)

A style archetype = **motion tokens + visual tokens + parameter baseline**,
following the `grading.presets` precedent (named flat objects, "用 preset,
不要手搓"). v1 archetypes (trademark-safe internal names; aliases accepted
by the API):

| archetype | alias | motion character | visual character |
|---|---|---|---|
| `playful` | google-like | elastic, overshoot, big stagger | saturated multi-hue, round caps, dot particles |
| `minimal` | apple-like | slow-fast-slow, no overshoot, few elements | mono/duotone, thin strokes, generous hold |
| `luxury` | — | slow, precise, long draw-ons | gold/ink palettes, hairline strokes, serif text |
| `tech` | ai-fluid | continuous flow, organic morph, glow | ice-blue on deep space (lumeri palette), gradients |
| `lumeri` | house | tech base tuned to brand tokens | #5FC6DE / #8BD8EA light-ribbon language |

Palettes reuse `lumenframe.templates.theme.PALETTES` roles (bg/text/accent/…)
so the whole existing palette system restyles vector scenes too. Styles set
the *baseline* semantic parameters; feelings and explicit overrides nudge from
there.

## 6. Parameter system (`params.py`)

Semantic axes, all 0..1: `energy, smoothness, playfulness, elegance,
complexity, density, organicness`. Resolution order:

```
style baseline  →  feeling adjectives (±0.15 nudges)  →  explicit overrides
```

`resolve(...) → ResolvedParams` exposes *derived* low-level values behaviours
consume: `tempo` (duration multiplier), `overshoot` (0 at elegance→1),
`stagger_spread`, `particle_count` (density × complexity, hard-capped),
`wobble`, `ease_enter/exit/move` (style curve set, smoothness-shifted),
`hold_fraction` (elegance-driven negative-space time). One mapping table,
unit-tested, documented — the *only* place semantics become numbers.

## 7. Composition intelligence (`choreography.py`)

* **Phase arc** — a scene is planned as phases, not a bag of animations:
  `anticipation → entrance → emphasis → hold` (loops: `cycle`; outros:
  `exit`). The allocator turns `duration` + style + energy into phase windows,
  enforcing `hold_fraction` at the end (留白 is enforced, not hoped for).
* **Stagger patterns** — `sequential | center_out | edges_in | random`
  (seeded); computes per-node/per-particle normalised delays.
* **Focal order** — nodes are ranked (primary mark → secondary → decoration);
  entrances lead with context and land on the focal element, decorations never
  animate after the focal hold begins.

## 8. Renderers

### 8.1 `svg.py` — animated SVG + CSS (primary)

Compiles a validated scene into ONE self-contained SVG document:

* viewBox in canvas space; scene coords translated from centred to top-left.
* Node transforms → CSS `transform` keyframe animations (`translate/rotate/
  scale`), `transform-box: fill-box; transform-origin: center` for groups.
* `draw` → `pathLength="1"`, `stroke-dasharray: 1`, animated
  `stroke-dashoffset` (renderer-portable, no measured lengths).
* `d` morph → CSS `@keyframes` on `d: path(…)` (Chromium supports; the target
  runtime IS Chromium via HyperFrames). Paths are pre-aligned by
  `geometry.align_for_morph`.
* Particles → one tiny element per instance with per-instance animation
  (delays from choreography), capped by `params.particle_count`.
* **Hard constraints** (from `gemia/hyperframes_adapter.py` validation): no
  external URLs, no `data:` URIs, no `url()` in CSS, no JS network calls,
  duration ≤ 60s. Gradients use SVG `<linearGradient>` + `fill="url(#id)"`
  presentation *attributes* (validator scans only media-tag src/href/poster
  and CSS text; presentation attributes pass). Fonts: system font stack only.
* Every animation carries `animation-fill-mode: both` and a
  `animation-delay`/`duration` derived from track keyframes; easing per
  keyframe segment via per-segment `@keyframes` percentage easing
  (`animation-timing-function` inside keyframe blocks).

### 8.2 `render.py` — lumenframe adapters

* `scene_to_html_layer(scene, *, id, name, start, lane) → layer dict` — an
  `html` layer whose `props.html` is the SVG (plus a minimal positioning
  wrapper). Rendering, caching (content-hash), sampling and compositing are
  inherited from the existing `html` path (`resolve_html.py`) untouched.
* `scene_svg_document(scene) → str` — the raw SVG file (deliverable in its
  own right: web/logo handoff).
* Future adapters (§11): native lumenframe ops (degraded), Lottie export,
  WebGL. The IR→adapter protocol is: read `nodes[*].tracks`, honour what you
  can, report what you dropped (`AdapterReport.dropped`), never silently
  approximate a *focal* behaviour.

### 8.3 Placement into the product

The html layer slots into the session's lumenframe doc via the standard op
path (`add_layer` with `type:"html"` + props), so **every existing surface
works immediately**: `lumen_seek` (frame previews), `lumen_render` /
`lumen_render_range` (mp4), the timeline via render-to-asset (and the
comp-ref bridge once merged). No new render tooling.

## 9. Agent interface

### 9.1 Python API (`api.py`)

```python
build_scene(brief) → {"scene": VectorScene, "plan": VisualPlan}
adjust_scene(brief_or_result, feedback: list[str]) → same, re-built
scene_to_svg(scene) → str
scene_to_html_layer(scene, ...) → layer dict
```

`brief` (all creative-language, everything optional but `subject`):

```jsonc
{
  "subject":  {"kind": "logo_text", "text": "Lumeri"},   // logo_text | title |
               // mark (named preset or explicit path spec) | abstract
  "intent":   "reveal",          // reveal | loop | intro | transition | outro
  "style":    "playful",         // archetype or alias ("google-like")
  "feeling":  ["creative", "energetic", "intelligent"],
  "duration": 5.0,
  "canvas":   {"width": 1920, "height": 1080},
  "palette":  "lumeri",          // theme.PALETTES name or {role: hex}
  "seed":     7,
  "params":   {"energy": 0.8}    // explicit semantic overrides (win)
}
```

The returned **plan** is the explainable middle artifact (visual plan → motion
sequence → vector structure → timeline): phases with windows, chosen
behaviours per phase, focal order, resolved parameters. The agent (or a human)
reads the plan, not the SVG.

### 9.2 Tool (`gemia/tools/vector_motion.py`)

ONE tool, `update_quantum` pattern (no flat-tool proliferation):

```
vector_motion {op: "create" | "adjust" | "catalog",
               brief?, feedback?, scene_id?, place?: {start, lane, name}}
```

* `create` — brief → scene → SVG → html layer written into the lumenframe doc
  (one atomic `apply_layer_patch`); returns plan summary + layer id + svg
  stats. `place` controls timeline placement of the layer.
* `adjust` — feedback phrases against a stored brief (kept in the layer's
  `props.vector_brief`); rebuilds deterministically, replaces the layer's
  html/css props in place (same layer id, undo-friendly single patch).
* `catalog` — behaviours/styles/feelings vocabulary for the model.

Registration (recipe confirmed by recon): `_schema.py` `_tool` entry +
`__init__.py` `_REAL` row + `plan_mode.py` **PLAN_BLOCKED** (writes the doc) +
`budget_guard.py` cost row (usd 0, eta ~1s; render cost is paid later by
lumen_render) + prose in `system_v3.md`. ⚠ These four files overlap the
`execution-intelligence-fix` exclusive paths in the MAIN worktree — this
branch touches them only in the isolated worktree; merge debt is recorded in
QUEUE.md (same situation comp-ref-bridge already navigated).

### 9.3 Feedback loop (§7 of the ask)

`feedback.py` maps human phrases → semantic deltas (bilingual, extensible):
`"more playful" → playfulness +0.2, energy +0.1`; `"less chaotic" →
complexity −0.2, smoothness +0.15`; `"more premium" → elegance +0.25, energy
−0.1, tempo slower`; `"more organic" → organicness +0.25`; `"more futuristic"
→ style-blend toward tech tokens`. Adjustment is **re-derivation**: feedback
edits the brief's resolved parameters, the whole scene rebuilds with the same
seed — never patching SVG text.

## 10. Determinism & caching

* One `random.Random(scene.seed)`; ids from a resettable counter;
  `scene_signature()` = sorted-key JSON for tests/dedup.
* Same brief ⇒ same SVG string ⇒ `resolve_html` content-hash cache hit ⇒
  zero re-render on repeated compiles. `adjust` changes the SVG ⇒ new hash ⇒
  honest re-render.
* No wall-clock, no environment reads anywhere in the engine.

## 11. Extension roadmap (post-v1)

1. **Native adapter** — degrade reveal/fade/rise/scale to lumenframe shape/
   text layers + keyframes for browserless environments; `AdapterReport`
   states what was dropped. Needs nothing new in the engine (IR unchanged).
2. **Glyph-accurate text** — vectorise text outlines (fonttools) so `draw_on`
   / a text-`form` behaviour could work on real glyph paths and particle
   targets could sample true glyph geometry. **Not in v1:** a text node renders
   as one `<text>` element with no per-glyph geometry, so `draw_on` on text
   degrades to a fade and there is no "particles form the wordmark" behaviour
   yet — particle fields form the *mark* (a real path) via `assemble.form`, or
   frame the composition as ambient decoration. This roadmap item adds the
   glyph-path substrate those behaviours need.
3. **Physics flavours** — spring/inertia solvers baked to keyframes at
   compile time (IR stays declarative; solvers are compile-time, so
   determinism holds).
4. **Lottie adapter** — the project already treats lottie as a media_kind;
   IR→bodymovin JSON export unlocks mobile/web runtime delivery.
5. **Style learning** — mine accepted scenes' resolved parameters per user to
   evolve personal style baselines (feeds the Lumeri memory layer).
6. **Comp-ref integration** — once `feat/comp-ref-bridge` merges, vector
   scenes ride `lumen_comp_to_timeline` as live comp clips with staleness
   tracking for free.

## 12. Testing & acceptance

* Unit: geometry (bezier/resample/morph alignment invariants), scene
  validation, params mapping table, choreography windows (phases partition
  duration, hold enforced), behaviour output (tracks in-window, t-sorted,
  in-vocabulary), SVG compiler (well-formed XML, constraint compliance: no
  url()/data:/remote refs, pathLength on draw targets), api (determinism:
  same brief+seed ⇒ identical SVG; feedback vocabulary total).
* Tool: dispatcher create/adjust/catalog round-trip against a session ctx
  (mirrors test_lumen_time_tools.py), plan-mode classification, schema
  presence.
* Acceptance (真机): brief → tool create → lumen_render → mp4 → frame
  extraction; visual check of ≥2 style variants of the same subject.
  Regression line: zero new pytest failures vs the 487c298 baseline
  (known-red: test_memory_log / test_v3_contract drift — pre-existing).
