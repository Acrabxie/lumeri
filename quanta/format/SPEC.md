# The `.qua` file format (Quanta v4)

Revision 2026-10-31. Acrabxie dedicates this document and the sample files next to
it to the public domain under [CC0 1.0](LICENSE). You may implement, copy, extend
or redistribute them for any purpose, without asking and without attribution.

A `.qua` file is a Lumeri Quanta presentation: a canvas that plays, waits for the
viewer, branches and remembers what the viewer did. The file is a stream of JSON
records, so a player can show the first screen while the rest is still arriving.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as in
RFC 2119.

| | |
|---|---|
| Extension | `.qua` |
| Media type | `application/vnd.lumeri.quanta+ndjson` |
| Format (`manifest.format`) | `lumeri.quanta.project-stream` |
| Version (`manifest.version`) | `4` |
| Profiles (`manifest.protocol`) | `quanta.presentation/v4`, `quanta.canvas/v4` |

Version 4 has two profiles. They share the container and integrity rules in
section 1 and are otherwise separate: a record type that belongs to one profile
MUST NOT appear in the other.

- **Presentation** (`quanta.presentation/v4`, section 2): scenes made of laid-out
  text, shapes and images, keyframed motion inside each scene, embedded audio and
  video, and a directed graph of scenes that responds to clicks and keys.
- **Canvas** (`quanta.canvas/v4`, section 3): a recorded sequence of edits to one
  canvas, with the rendered picture after every edit embedded, so that any viewer
  can play it and a full implementation can check every step.

## 1. Container

1. The file is UTF-8. Invalid UTF-8 MUST be rejected.
2. Each line is one JSON object (NDJSON). Every line, including the last, ends with
   `\n`. Empty lines are not allowed.
3. JSON is strict: `NaN` and `Infinity` are not allowed. Writers MUST NOT emit
   duplicate keys.
4. The first record has `"type": "manifest"` and the last has `"type": "end"`. A file
   whose last record is not `end` is truncated and MUST be rejected; it MUST NOT be
   shown as if it were complete.
5. `end.record_count` is the number of records before `end` (the manifest counts,
   `end` does not).
6. `end.content_sha256` is the lowercase hexadecimal SHA-256 of the file bytes from
   the start of the file up to, but not including, the `end` line: every earlier
   line together with its `\n`.
7. A reader checks `format`, `version`, `media_type` and `protocol` exactly and MUST
   reject a file whose values it does not support. It MUST NOT fall back to a lower
   version or to the other profile.
8. Unknown record types, capabilities, animatable properties and enumerated values
   MUST be rejected. Objects that this document calls *closed* MUST NOT contain any
   other fields. Other objects MAY carry additional descriptive fields; those never
   change what is shown.
9. Integrity does not make content valid. A file whose hashes were recomputed after
   an edit is still rejected if it refers to missing blocks, assets or states, uses
   a capability it may not use, or mixes records from the two profiles.

## 2. Presentation profile: `quanta.presentation/v4`

### 2.1 Records and order

```text
manifest
skill*             every skill before the first scope
asset*             every asset before the first scope
edge*              anywhere before end; writers put them before the first scope
scope, state+      each state comes after the scope it belongs to
end
```

Only `skill`, `asset`, `edge`, `scope` and `state` records may appear between
`manifest` and `end`. Skills and media therefore arrive before the first scene, so
the entry scene is fully usable as soon as it is read. Ids are unique within each
record type.

Limits: at most 64 MiB per file, 8192 lines and 24 MiB per line; at most 1024
scopes, 4096 states and 512 Skills. All media is embedded: each asset is at most
256 MiB, and audio and video together at most 512 MiB.

An id (`scope_id`, state `id`, `asset_id`, edge `id`, Skill `id`, block `id`)
matches `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`.

### 2.2 Manifest

```json
{"type":"manifest","format":"lumeri.quanta.project-stream","version":4,
 "protocol":"quanta.presentation/v4","player_contract":"quanta-v4-player/0.3",
 "media_type":"application/vnd.lumeri.quanta+ndjson","title":"Quanta",
 "canvas":{"width":1920,"height":1080,"fps":60},"playback":{"autoplay":false},
 "entry":"intro_s1","state_order":["intro_s1"],"state_count":1,"scope_count":1,
 "skill_count":0,"graph":{"model":"directed-state-graph","edge_count":0},
 "required_capabilities":[],"outline":{"id":"root","kind":"root","children":[]},
 "theme":{"aspect":"16:9"}}
```

- `player_contract` is `quanta-v4-player/0.3`.
- `canvas` is closed: `width` and `height` are integers in 16..8192 whose product is
  at most 16,777,216, and `fps` is in 1..120. `fps` is a display rate; time is
  continuous.
- `playback` is closed: `autoplay` (boolean).
- `entry` is the id of the first state.
- `state_order` is the default linear path, in reading order. It MUST contain
  `entry`, and every state it lists MUST be in the file. States that are reached
  only through edges MAY be left out.
- `state_count`, `scope_count`, `skill_count` and `graph.edge_count` MUST equal the
  number of records of each type.
- `graph.model` is `directed-state-graph`.
- `required_capabilities` lists at most 64 capabilities from section 2.7.
- `title`, `created_at`, `outline`, `theme` and `loading` (default
  `{"progressive": true}`) are descriptive.
- `requires_external_assets`, if present, MUST be false.

### 2.3 Scope

A scope is one scene. It holds the scene's semantic content and one fixed layout
of it.

```json
{"type":"scope","scope_id":"intro","ancestors":[],
 "scope":{"id":"intro","title":"Quanta","layout":"content","hidden":false,
          "blocks":[{"id":"title","kind":"text","role":"title"}],
          "links":[],"notes":"","transition":{"kind":"cut"}},
 "display":{"schema":"quanta.display/v1","background":"#101419","primitives":[
   {"kind":"text","block_ref":"title","rect_px":[160,408,1192,240],"z_index":20,
    "line_breaks":["Quanta"],"line_height_px":101,"align":"left",
    "style":{"family":"Avenir Next","weight":600,"final_size_px":96,"color":"#F2F5F7"}}]}}
```

`scope.id` MUST equal `scope_id`. `ancestors` (optional, at most 32 entries of
`{id, title}`) places the scene in the outline.

**Blocks.** `scope.blocks` is a tree of semantic blocks with `kind` `text`, `shape`,
`image`, `stat` or `group`. A `group` holds `children`; every other block is a leaf
with an `id` unique in the scope. The tree is at most 32 levels deep and holds at
most 4096 blocks. Other block fields (text, role, data) describe the content and do
not change what is drawn.

**Display list.** `display` is the layout, computed once by the author. Players draw
it as given and MUST NOT re-wrap text or move blocks, so a scene looks the same in
every player.

- `display` is closed: `schema` (`quanta.display/v1`), `background` (a paint) and
  `primitives` (at most 16384).
- Every primitive has `kind` (`text`, `shape` or `image`), `block_ref`, `rect_px`
  `[x, y, width, height]` in canvas pixels (each in -32768..32768, width and height
  greater than 0) and `z_index` (-10000..10000). Primitives are closed: each kind
  allows only the fields listed for it here.
- `block_ref` names a leaf block of this scope, or `slide:<scope_id>:title` or
  `slide:<scope_id>:scrim`. A block may produce several primitives (for example a
  card background, a number and a label). Every leaf block MUST have at least one.
- **text** adds `line_breaks` (at most 128 strings of at most 8192 characters, one
  per drawn line), `line_height_px` (1..8192), `style` and the optional `align`
  (`left`, default, `center` or `right`) and `text` (the plain text, for search and
  accessibility). `style` is closed: `family` (at most 160 characters, without `;`,
  `{`, `}` or line breaks), `weight` (1..1000), `final_size_px` (1..8192) and
  `color`.
- **shape** adds `shape` (`rect`, `line`, `ellipse` or `rounded-rect`), `fill` (a
  paint) and the optional `corner_radius_px` (0..8192).
- **image** adds `asset_id` (an embedded image asset), `fit` (`cover` or `contain`)
  and the optional `anchor` (`center`, default, `top`, `bottom`, `left`, `right`,
  `top-left`, `top-right`, `bottom-left` or `bottom-right`).

A **color** is `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, or `rgb(r, g, b)` /
`rgba(r, g, b, a)` with components 0..255 and alpha 0..1. A **paint** is a color or
exactly `linear-gradient(180deg, <color> 0%, <color> 100%)` (top to bottom).

**Drawing.** For the current state, a player fills the canvas with the scope's
`background`, draws the state's video (while it is playing) over the whole canvas
with its `fit`, and then draws, in ascending `z_index`, every primitive whose
`block_ref` is visible in the state, plus the scope's `slide:` primitives, which are
always drawn.

- Text is drawn with `weight`, `final_size_px` and `family`, falling back to a
  sans-serif face. Line `i` of `line_breaks` has its top at `y + i × line_height_px`
  and is aligned to the left edge, the center or the right edge of the rectangle.
- `rounded-rect` uses a radius of `min(corner_radius_px, width / 2, height / 2)`;
  `rect` and `line` fill the rectangle.
- Images are scaled to `cover` or `contain` the rectangle, placed by `anchor` and
  clipped to the rectangle.

### 2.4 State

A state is a span of time inside a scope.

```json
{"type":"state","scope_id":"intro","state":{"id":"intro_s1","duration_sec":4,
 "visible_block_ids":["title"],"advance":"auto",
 "track":[{"at":0,"block":"title","set":{"opacity":0,"offset_y":0.06}},
          {"at":0.6,"block":"title","set":{"opacity":1,"offset_y":0},"ease":"out"}],
 "media":null}}
```

- `duration_sec` is in 0.001..3600.
- `visible_block_ids` lists, without repeats, the leaf blocks of the scope that are
  present in this state.
- `advance` is `auto` or `wait` (section 2.6). Any other value means `wait`.
- `track` is a list of at most 256 keyframes. Each has `at` (0..`duration_sec`),
  `block` (a leaf block of the scope), `set` (a non-empty map from property to
  number) and the optional `ease` (default `linear`).
- `media` is `null` or an audio or video binding (section 2.5).

**Animatable properties** (a closed list):

| Property | Range | Identity | Meaning |
|---|---|---|---|
| `opacity` | 0..1 | 1 | Multiplies the alpha of the block's primitives. |
| `offset_x` | -2..2 | 0 | Horizontal shift, as a fraction of the canvas width. |
| `offset_y` | -2..2 | 0 | Vertical shift, as a fraction of the canvas height. |
| `scale` | 0.05..8 | 1 | Scale of each primitive about its own center. |
| `emphasis` | 0..1 | 0 | Extra scale of `1 + 0.04 × emphasis`, multiplied with `scale`. |

**Evaluating a track** at local time `t`, for each block and property:

1. Take the keyframes for that block that set the property, ordered by `at`
   (keyframes with the same `at` keep their order in the file).
2. At or before the first keyframe, use its value. At or after the last, use its
   value.
3. Between two neighbouring keyframes, interpolate using the **later** keyframe's
   `ease`, where `u` is the fraction of the time from the earlier keyframe to the
   later one: `linear` = `u`; `in` = `u²`; `out` = `1 − (1 − u)²`;
   `in-out` = `u²(3 − 2u)`; `hold` = 0 until the later keyframe is reached. The
   first keyframe's `ease` has no effect.
4. A property that no keyframe sets stays at its identity, so a state without a track
   shows the display list unchanged.

For a primitive with rectangle `[x, y, w, h]` on a canvas of width `W` and height `H`,
let `s = scale × (1 + 0.04 × emphasis)`. It is drawn at
`[x + offset_x × W + (1 − s) × w / 2, y + offset_y × H + (1 − s) × h / 2, s × w, s × h]`
with its alpha multiplied by `opacity`. Motion is applied after layout, so it never
changes line breaks, and `(state, t)` determines exactly one picture.

### 2.5 Assets and media

```json
{"type":"asset","asset_id":"clip_1","mime":"video/mp4","encoding":"base64","data":"…"}
```

- `encoding` is `base64` and `data` is padded standard base64.
- `mime` is `video/mp4`, `video/webm`, `audio/mpeg`, `audio/mp4` or `audio/wav` for
  state media, and `image/png`, `image/jpeg` or `image/webp` for image primitives.

A state's `media` is:

```json
{"kind":"video","asset_id":"clip_1","at":0.5,"source_in_sec":0,"duration_sec":3,"fit":"contain"}
```

- `kind` is `video` or `audio`, and the asset's `mime` MUST begin with `<kind>/`.
- `duration_sec` is greater than 0 and at most 3600. `at` (default 0) is where the
  media starts within the state. `at + duration_sec` MUST NOT exceed the state's
  `duration_sec`.
- `source_in_sec` (default 0) is where playback starts within the media.
- `fit` is `contain` (default) or `cover`.
- `external`, if present, MUST be false: the asset MUST be in the file.

While the state's local time `t` is in `[at, at + duration_sec)`, the media plays at
`source_in_sec + (t − at)`. Players keep it in step with the state's clock when the
viewer pauses, seeks, switches states or replays.

### 2.6 Edges and playback

```json
{"type":"edge","edge":{"id":"e1-pick_a","from":"hub_s1","to":"a_s1",
 "trigger":{"kind":"event","primitive":"pointer.click","target":"block:pick_a"},
 "priority":0,"when":[],
 "actions":[{"primitive":"variable.set","args":{"name":"seen_a","value":true}}],
 "transition":{"kind":"cut","duration_sec":0},"loop":false}}
```

- `from` and `to` are states in the file. Several edges may lead into one state, and
  an edge may lead back to its own state, so branches can merge and loop.
- `trigger` is `{"kind": "auto"}` or `{"kind": "event", "primitive": …, "target": …}`
  with `primitive` `pointer.click` or `keyboard.keydown` (double-clicks are handled
  by Skills). `target` is at most 160 characters and defaults to `canvas`.
- `priority` is an integer in -10000..10000 (default 0).
- `when` is at most 16 conditions, all of which must hold. A condition is
  `{"primitive": "variable.exists" | "variable.equals", "args": {"name": …, "value": …},
  "not": false}`; `not: true` negates it.
- `actions` is at most 32 actions, run in order before moving:
  `{"primitive": "variable.set", "args": {"name": …, "value": …}}` (the name is an id)
  or `{"skill": "<id>", "args": {…}}`, which calls a Skill in the file with `args`
  as its parameters.
- `transition` MUST be exactly `{"kind": "cut", "duration_sec": 0}`.

**Targets.** A pointer event's target is `block:<id>` for the topmost block under the
pointer, by `z_index`, that is visible with opacity above 0.01; otherwise `canvas`.
Key events have the target `canvas` and carry the key name as `key`. A target
pattern matches an event target when it is equal to it, when it is `*`, or when it
ends in `:*` and the event target starts with the part before the `*`.

**Graph and linear files.** A file is a *graph file* if it has at least one edge, or
a Skill that navigates (`navigation.*`) or calls another Skill. Otherwise it is
*linear*, and its states play in `state_order`.

A player starts at `entry`, with no variables and an empty history, and plays the
state's span on a local clock.

- **Candidate edges.** The current state's outgoing edges are considered in order of
  `priority` (higher first), then `id`. An edge is a candidate only if its `when`
  conditions hold. Writers SHOULD give edges that can apply at the same moment
  different priorities rather than rely on id order.
- **Input.** For an event, the first candidate event edge whose primitive and target
  match is followed. If none matches but some event edge of the current state would
  match apart from its conditions, the event does nothing. Otherwise every Skill
  binding that matches the event runs (section 2.7).
- **Following an edge** runs its actions, records the current state and local time
  in the history, and moves to `to` at local time 0.
- **End of a span.** In a graph file, the first candidate `auto` edge is followed; if
  there is none, the player holds the last picture and waits for input. In a linear
  file, the player moves to the next state in `state_order` if `advance` is `auto`
  and holds otherwise; after the last state, playback is finished.
- An input that matches no edge or binding leaves the file's state unchanged. In a
  graph file, players MUST NOT turn it into an implicit move to another state.
  Players MAY offer their own controls, such as play, pause, seek and back.
- A player MAY stop with an error if more than 64 automatic moves happen without any
  time passing.

### 2.7 Skills and capabilities

A Skill is a small interaction program carried in the file.

```json
{"type":"skill","skill":{"id":"double-click-back","name":"double-click-back","kind":"skill",
 "version":"1.0.0","title":"Go back","capabilities":["pointer.double_click","navigation.back"],
 "runtime":{"profile":"lumeri.interaction/v1","steps":[],
   "bindings":[{"id":"double-click-back","on":{"primitive":"pointer.double_click","target":"*"},
                "when":[],"do":[{"primitive":"navigation.back","args":{}}]}]}}}
```

- `kind` is `skill`. `name` (default: the id), `version` (default `1.0.0`) and
  `title` (at most 160 characters) are descriptive.
- `runtime.profile` is `lumeri.interaction/v1`.
- `runtime.bindings` is at most 32 bindings with unique ids. A binding has `on`
  (`primitive`, one of the three input events, and `target`, a target pattern that
  defaults to `*`), `when` (at most 16 conditions) and `do` (1 to 32 steps).
- `runtime.steps` (at most 32) is what runs when the Skill is called from an edge or
  from another Skill. A Skill has at least one binding or one step.
- A step is `{"primitive": …, "args": {…}}` or `{"skill": "<id>", "args": {…}}`. A
  condition is `{"primitive": …, "args": {…}, "not": false}`.
- Inside `args`, a string that begins with `$` is a reference, read when the step
  runs: `$event.<path>` (for example `$event.key`), `$params.<path>` (the caller's
  `args`), `$variables.<name>` or `$context.state_id`.

The capability set is closed:

| Capability | Use | Arguments |
|---|---|---|
| `pointer.click`, `pointer.double_click`, `keyboard.keydown` | binding `on` | |
| `variable.exists` | condition | `name` |
| `variable.equals` | condition | `name`, `value` (compared as JSON) |
| `state.is` | condition | `state_id` |
| `variable.set` | step | `name`, `value` |
| `navigation.goto` | step | `target`: a state id, a scope id (its first state in the file) or a `$` reference |
| `navigation.back` | step | none; returns to the previous state in the history, at the time it was left |
| `playback.play`, `playback.pause` | step | none |

- A Skill MUST declare in `capabilities` every capability it uses, and MUST NOT use
  any other. Input events are only valid in `on`, conditions only in `when`, and
  everything else only in steps.
- A `navigation.goto` target that is not a `$` reference MUST name an existing state
  or scope.
- Skill calls are at most 8 deep and MUST NOT recurse.
- When an event matches several bindings, every one whose conditions hold runs.
- A file cannot reach the network, the host's files, project import or export, or any
  host session.
- Readers SHOULD accept the older spelling `pointer.double-click` for
  `pointer.double_click`.

### 2.8 End

```json
{"type":"end","scope_count":1,"state_count":1,"skill_count":0,"edge_count":0,
 "record_count":3,"content_sha256":"…"}
```

The counts MUST equal the manifest's and the number of records of each type.

## 3. Canvas profile: `quanta.canvas/v4`

### 3.1 Records and order

```text
manifest
package?           present exactly when manifest.has_package is true
snapshot
frame 0            the starting canvas
op 1, frame 1
op 2, frame 2
…
op N, frame N
end
```

The order is fixed. Frame 0 comes right after the snapshot, so a viewer can show
the starting canvas while the rest of the file is still being read.

### 3.2 Manifest

| Field | Value |
|---|---|
| `format`, `version`, `media_type` | as in the table at the top |
| `protocol` | `quanta.canvas/v4` |
| `title` | text |
| `document_id`, `composition_id` | identity of the canvas document and its root composition |
| `canvas` | `{width, height, fps}` |
| `operation_count` | `N`, an integer in 1..256 |
| `frame_count` | `N + 1` |
| `has_package` | boolean |
| `base_hash`, `final_hash` | semantic hashes (section 3.6) of frame 0 and frame N |
| `player_contract` | `quanta-v4-player/0.1`, or `quanta-v4-player/0.2` when `playback` is present |
| `playback` | optional; closed `{schema: "quanta.canvas-playback/v1", fps, autoplay}` |

With `playback`, `fps` MUST equal `canvas.fps` (greater than 0, at most 120),
`autoplay` is a boolean, and every operation MUST carry a `beat`. Each frame is then
one tick of a clock running at `fps`.

### 3.3 Snapshot

```json
{"type":"snapshot","snapshot":{"format":"quanta-v4-player-snapshot","version":"0.1",
 "quanta_version":"4","document_id":"…","composition_id":"root","metadata":{},
 "document":{"version":…,"id":…,"title":…,"canvas":{…},"assets":[…],"root":{…},"selection":…}}}
```

`document` is a static [LumenFrame](../../lumenframe/) document: its layers MUST NOT
carry keyframes, expressions or time remapping. Its semantic hash MUST equal
`manifest.base_hash`.

### 3.4 Frames

```json
{"type":"frame","index":0,"mime":"image/png","width":640,"height":360,
 "data":"<base64 PNG>","png_sha256":"…","semantic_hash":"…"}
```

`data` is a base64 PNG of at most 16 MiB once decoded, and `png_sha256` is the
SHA-256 of the decoded bytes. Frame `i` is the picture of the canvas after operation
`i`; frame 0 is the snapshot.

### 3.5 Operations

```json
{"type":"op","index":4,"summary":"Opacity · card · 82%","semantic_hash":"…",
 "operation":{"op":"effect.opacity","operation_id":"op-d4ae093ddc31553f8bfa7283","space":"canvas",
   "target":{"target_type":"lumenframe.layer","document_id":"…","composition_id":"root","layer_id":"card"},
   "parameters":{"value":{"type":"scalar","value":0.82,"unit":"fraction","min":0,"max":1,
                          "precision":6,"mode":"absolute"}},
   "preconditions":[]}}
```

- `operation` is the edit itself: `op`, `operation_id`, `target` (`document_id`,
  `composition_id`, `layer_id`, `target_type` `lumenframe.layer`, optional
  `object_id`), `parameters`, `preconditions` (a list) and `space` (`canvas`,
  `composition.frame`, `composition.normalized` or `object.local`).
- `semantic_hash` is the hash after the operation and MUST equal frame `i`'s.
- `summary` is a line of text for the viewer.
- `beat` groups operations: if any operation has one, all do. Beats are integers
  that start at 1 and each equal the previous one or the previous one plus 1. With
  `playback`, the clock stops after the last operation of each beat until the viewer
  continues or seeks.

Registered operations in this revision:

| `op` | `parameters` |
|---|---|
| `transform.basic` | `transform`: a `transform2d` value (position `x`, `y`; `scale_x`, `scale_y`; `rotation` in `degrees` or `radians`; anchor) |
| `transform.quad` | `quad`: a `quad2d` value with the four corners `top_left`, `top_right`, `bottom_right`, `bottom_left` |
| `effect.opacity` | `value`: scalar, unit `fraction`, 0..1 |
| `effect.blur` | `value`: scalar, unit `pixel`, 0..200 |
| `effect.sharpness` | `value`: scalar, unit `unitless`, 0..10000 |
| `effect.corner_radius` | `value`: scalar, unit `fraction` of the short side, 0..0.5 |

A **scalar** is closed: `type` (`scalar`), `value` (a finite number), `unit`, and the
optional `min`, `max`, `step` (greater than 0), `precision` (0..12) and `mode`
(`absolute`, default, or `relative`). `value` MUST lie within `min`..`max`.

The `transform2d` and `quad2d` values, and how every operation changes a layer, are
those of the LumenFrame implementation in this repository; the samples show
complete instances of both. Structure and integrity checking (section 3.8) does not
depend on them.

### 3.6 Semantic hash

The semantic hash fingerprints what the canvas looks like, not where its files are or
what is selected. It is the lowercase hexadecimal SHA-256 of the canonical JSON of:

```text
{ "document_id", "composition_id",
  "canvas": the fields width, height, fps, background and work_area that are present,
  "assets": the assets, sorted by id,
  "root":   the root layer }
```

- Canonical JSON: keys sorted, separators `,` and `:` without spaces, non-ASCII
  characters written as UTF-8 rather than escaped, no `NaN` or `Infinity`.
- These keys are removed from every object: `selection`, `selected`,
  `selected_layer_id`, `active_selection`, `viewport`, `viewport_state`, `zoom`,
  `pan`, `pointer`, `pointer_id`, `widget_id`, `temp_id`, `temporary_id`,
  `created_at`, `updated_at`, `mtime`, `mtime_ns`. From assets, `path`,
  `source_path`, `file_path`, `resolved_path`, `filesystem_path` and `uri` are
  removed as well.
- Each layer becomes exactly these fields, with the value in parentheses used when
  the layer leaves a field out: `id`, `type`, `start` (0), `duration` (0),
  `source_in` (0), `source_out` (0), `speed` (1), `lane` (0), `transform`,
  `opacity` (1), `blend_mode` (`normal`), `visible` (true), `mask` (null),
  `clip_to_below` (false), `asset_id` (null), `props` ({}), `time_remap` (null),
  `effects` and `children` (in order, each a layer). `transform` starts from `x` 0,
  `y` 0, `scale_x` 1, `scale_y` 1, `rotation` 0, `anchor_x` 0.5, `anchor_y` 0.5 and
  takes the layer's own transform fields over them. Each effect becomes `type`,
  `enabled` (true) and `params`.

### 3.7 Package

The optional `package` record carries data-only declarations: which effects and
interactions the file uses, and on-canvas `controls` (for example buttons and
sliders drawn as layers) that a live player can bind to registered operations. It
never contains executable code. A player without control support still replays the
file exactly, because every change a control makes is itself a recorded operation.

### 3.8 End and checking

```json
{"type":"end","operation_count":10,"frame_count":11,"record_count":24,"content_sha256":"…"}
```

There are two levels of checking:

1. **Structure and integrity** (any viewer): record order and counts,
   `content_sha256`, every `png_sha256`, each operation's hash equal to its frame's,
   frame 0 and frame N equal to `base_hash` and `final_hash`, the snapshot's hash
   equal to `base_hash`, and the beat and playback rules. A viewer that passes this
   can show the frames as recorded.
2. **Full replay** (needs LumenFrame): apply every operation to the snapshot,
   recompute every semantic hash, render every step again and require the pixels to
   equal the embedded frame. A file whose bytes and hashes were rewritten
   consistently still fails here.

## 4. Earlier versions and extensions

Stream versions 1 to 3 came before version 4 and are not described here. A version 4
reader MUST reject them unless it implements them separately.

Lumeri wrote Quanta files with the extension `.quanta` from 2026-10-08 to 2026-10-31.
Their content is the same, and readers MAY accept that extension. New files use
`.qua`.

## 5. Samples

The files in [`examples/`](examples/) pass Lumeri's checker: the presentation sample
against every rule in section 2, and the canvas samples at both levels of
section 3.8.

| File | Profile | What it shows |
|---|---|---|
| `quanta-branches.qua` | presentation | A hub with three branches; variables that unlock the last scene once all three are seen; double-click to go back. Chinese text. |
| `canvas-basics.qua` | canvas | Ten edits to one canvas (move, scale, rotate, opacity, four-corner warp, corner radius, blur, sharpness), with on-canvas buttons and sliders. |
| `canvas-presentation.qua` | canvas | Eight of the same edits as a plain step-through, without controls. |
| `canvas-flow.qua` | canvas | The same canvas as a 240-step animation at 30 fps that plays on open, in two beats: it pauses after the first until the viewer continues. |

## 6. Implementations

Lumeri's reader, players and checker will be published in this repository under the
repository's MIT License. The LumenFrame renderer used by the canvas profile is
already here, in [`lumenframe/`](../../lumenframe/). If this document and Lumeri's
players disagree, please open an issue.
