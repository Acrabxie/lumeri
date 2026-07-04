# lumenframe — the layered editing core

> Status: **M0 + M1 landed** — model + op vocabulary + extension registry +
> catalogue, plus the compile bridge that renders a document to real pixels
> (48 tests green). Branch `feat/lumenframe-core`. Pure-additive; no existing
> file touched.

## Why

Lumeri already had three layer-ish things but no *editable layer document* tying
them together:

| existing | what it is | the gap |
| --- | --- | --- |
| `lumerai/patches.py` `TimelinePatch` | clips/tracks edit vocabulary on the **time** axis | only time, never frame-internal layers |
| `gemia/video/layers.py` `LayerStack` | the **render backend** (`content_fn`/`mask_fn` are closures) | not serialisable, not editable |
| `compositing_graph.py` + layer-flow manifest | compiles a *layer plan* for rendering | a render product, no edit ops |

lumenframe is the missing core: a canonical, JSON-serialisable, **editable layer
document** plus an atomic **operation vocabulary** that both the UI and the agent
drive, and that third-party GitHub repos extend.

## The one decision: everything is a layer

The project root is a `composition` layer; its `children` are the layers. **Time
is a property of every layer** (`start` / `duration` / `source_in` / `source_out`
/ `speed`); the classic "timeline" is just a *view* over the root composition's
children. CapCut's main-track / picture-in-picture / audio / text / effects all
collapse into one recursive layer tree. Nesting (precompose) is just a layer with
children.

`lane` is an editor hint (which timeline track a layer shows on); authoritative
**z-order is `children` list order, bottom → top** (last child composites on top,
matching `LayerStack`).

## Surfaces

### `lumenframe.model` — the document
- `empty_doc()`, `new_layer()`, `normalize_doc()` — tolerant, fully-defaulted.
- `LumenDoc` dict: `{version, id, title, canvas{width,height,fps,background}, root, assets[], selection[]}`.
- `LayerNode` dict: `id, type, name, children[], start, duration, source_in,
  source_out, speed, lane, transform{x,y,scale_x,scale_y,rotation,anchor_x,
  anchor_y}, opacity, blend_mode, visible, locked, mask, clip_to_below, effects[],
  keyframes{}, asset_id, props{}, merged`.
- tree helpers: `walk`, `find_layer`, `find_parent`, `locate`, `doc_duration`.
- Unknown authored keys fold into `props` (never dropped) — friendly to
  agent-authored layers.

### `lumenframe.ops` — the LayerPatch vocabulary
`apply_layer_patch(doc, patches)` deep-copies, applies every op in order, then
validates the whole tree. **Atomic**: any failure leaves the caller's doc
untouched and raises `LayerPatchError(code, message)`.

- **layer mgmt**: add_layer, delete_layer, duplicate_layer, select, move_layer
  (universal: reparent + reorder + retime + relane), reorder_layer, group_layers,
  ungroup_layer, merge_layers (flatten), rename_layer, set_visibility, set_lock
- **time**: set_time, trim (source-tracking), split, set_speed
- **transform/compositing**: set_transform, set_opacity, set_blend_mode
- **inter-layer**: set_mask (shape / alpha-matte / luma-matte), clip_to_below,
  add_adjustment_layer
- **effects/colour**: add_effect, remove_effect, set_effect_params, color_grade,
  add_transition
- **keyframes**: set_keyframe, remove_keyframe

Validation invariants: unique ids, known layer types, container-only children,
non-negative duration, positive speed, track-matte source must be an existing
non-self layer.

### `lumenframe.registry` — third-party / GitHub extension surface
- `register_op(name, handler)` / `register_layer_type(name, spec)` — programmatic
  or decorator.
- Entry-point discovery: a distribution declaring
  `[project.entry-points."lumenframe.extensions"]` is loaded on first use, so
  `pip install git+https://github.com/...` lights up new ops / layer types.
- Core ops are protected from silent override (pass `override=True` to replace).
- One bad plugin is logged and skipped, never breaks the editor.

### `lumenframe.catalog` — self-describing vocabulary
`describe_ops()` renders a compact prompt block for the agent; `op_catalog()`
returns structured metadata. A test enforces catalogue ⇄ registry parity.

## Roadmap (next milestones)

- **M1 compile bridge** — ✅ landed (`lumenframe/compile.py`). `compile_to_layer_stack`
  maps a doc to `gemia.video.layers.LayerStack`: built-in solid + nested
  composition content, resolver hook for media/text/extension types, centre-origin
  transform (translate + scale + centred rotation), opacity/position/scale/rotation
  keyframes, and alpha/luma track mattes (matte source auto-hidden, AE-style).
  *Deferred to M1.1:* non-uniform scale, anchor≠centre, adjustment-layer & effect-chain
  application, merge/flatten raster baking, shape-mask rasterisation, `CompositingGraph` path.
- **M2 agent tools** — expose LayerPatch ops as verbs (like `gemia/tools/timeline.py`),
  inject `describe_ops()` + `{{lumenframe}}` doc state into the loop.
- **M3 timeline interop** — adapter so the existing `project_model` timeline is a
  view over a lumenframe doc (or import/export between them).
- **M4 UI** — a layer panel + canvas that emits LayerPatches (DaVinci/CapCut bar).
