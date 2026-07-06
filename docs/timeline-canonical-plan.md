# Timeline Canonical Model, Lumenframe Bridge & Export Honesty Plan（时间线正典模型 + 桥接契约 + 导出诚实规则）

Status: 已定稿，待实施（Phase 1 未开始）
制定日期: 2026-07-06
适用范围: `/Volumes/Extreme SSD/gemia`（后端 + web v3）与 `~/Code/lumeri-cli`（TUI 客户端）
Citations pinned to: commit `37a289b` (all `file:line` references below were verified against
HEAD at authoring time; the concurrent in-flight shotlist work adds lines to
`lumerai/patches.py` / `gemia/project_model.py` **below/above** the cited regions but does not
move the cited code semantically — re-verify with `git show 37a289b:<file>` if lines drift).

Companion docs: `docs/protocol-parity-plan.md` (parity rules referenced in §7),
`docs/timeline-v1/01-op-vocabulary.md` (op vocabulary this plan builds on).

---

## 0. TL;DR — the four decisions this doc locks in（决策一览）

| # | Decision | One-line verdict |
|---|----------|------------------|
| D1 | Canonical model | The **lumerai flat timeline stays canonical** for editing + export. lumenframe comps become **first-class timeline citizens as clips** whose asset is a lazily-rendered comp reference (`comp_ref`). No big-bang migration to lumenframe-as-master. |
| D2 | Bridge contract | A comp enters the timeline via a new verb `lumen_comp_to_timeline`: `export_range` renders a window → content-addressed cache file → one atomic patch (`upsert_asset` + `insert_clip`). Staleness = **sha256 of `lumenframe.json` bytes**; comp clips are **live references**, re-rendered when the hash changes. Undo asymmetry across the seam is documented, not hidden. |
| D3 | Export honesty | Every field writable through `set_clip_effects` / `add_transition` is assigned exactly one class: **RENDERED / WARN-AT-WRITE / PREVIEW-ONLY**. Nothing is ever silently dropped again: warnings at the tool layer + `dropped_fields` in the export manifest. |
| D4 | Transition rendering | First tranche implements `fade` + `dissolve` in `project_export.py` pass 1 under a hard invariant: **base-video duration must not change**. Naive `xfade`-join (which shrinks output and desyncs pass-3 audio) is explicitly forbidden; the exact segment math is specified in §5. |

中文小结：lumerai 时间线继续做正典；lumenframe 合成通过"渲染成媒资的引用"进入时间线，而不是反向迁移。导出必须对每个字段"诚实"——要么渲染、要么写入时警告、要么明文标注仅预览。转场首批实现 fade + dissolve，并用精确的分段数学守住时长与音画同步不变量。

---

## 1. Problem — two data models, three renderers, one export path（问题：双模型三渲染器）

Two independent, both-live editing models exist today:

**(a) lumerai flat timeline** — the model behind the web/CLI timeline UI and export.

- Persisted by `ProjectStore`: `<root>/<project_id>/state.json` + append-only
  `patches/NNNN.json` + `meta.json` (gemia/project_store.py:5-12).
- Every mutation is one patch in the log (`apply_patches`, project_store.py:108-164);
  **undo is replay-from-seed** (`undo_to_seq`, project_store.py:172-234), surfaced as the
  `timeline_undo` verb and `/timeline/op {"op":"undo"}` (v3_routes.py:518-541).
- Op vocabulary: 14 ops (patches.py:827-842 at HEAD), including
  `add_transition` → writes `clip["transition_after"] = {kind, duration_sec}`
  (patches.py:574-614) and `set_clip_effects` with whitelist
  `_EFFECT_KEYS = {rotation, mirrored, muted, speed, blur_radius, opacity, x, y, scale,
  gain_db, fade_in, fade_out, blend_mode}` (patches.py:24-27).
- User direct edit flows through `POST /sessions/{id}/timeline/op`
  (`_USER_EDIT_OPS = {move, trim, split, delete, set_time, set_effects, add_transition}`,
  v3_routes.py:405; handler v3_routes.py:471-515) into the **same** patch log as model verbs.
  The web frontend currently only emits `move`/`trim`/`set_time` from drag interactions
  (static/v3/v3.js:1183-1300); `set_effects`/`add_transition` are reachable by the model's
  verbs (gemia/tools/timeline.py:280-302) and by any future UI.

**(b) lumenframe layer tree** — the After-Effects-class model.

- Full layer tree with 11 layer types, 14 blend modes, keyframes, shape/pixel masks,
  alpha/luma track mattes, adjustment layers, time-remap (lumenframe/model.py:31-109;
  compile capabilities documented at lumenframe/compile.py:25-33; sizes: model 648 +
  ops 2452 + compile 1827 lines).
- Persisted to **one file per project**: `<project_dir>/lumenframe.json`
  (gemia/tools/layer.py:64-86), written atomically (layer.py:151-191) with
  **no history and no undo** — by design "fully orthogonal to the main project state and
  timeline patch history" (layer.py:194-218, esp. the docstring at :204-207).
- Has its own render path: `compile_to_layer_stack` → per-frame CPU numpy compositing →
  `render_range` / `export_range` (lumenframe/render_range.py:127-291), exposed as the
  `lumen_render_range` verb which already registers exported MP4s as session assets
  (gemia/tools/lumen_render_range.py:120-168).

**Renderers (three and a half):**

1. `gemia/project_export.py` (966 lines) — THE export. Three-pass ffmpeg: segment-concat
   base video (pass 1, :200-347), one `-filter_complex` overlay pass reading **only
   `effects.x/y/scale/opacity`** for image/lottie (:414-437) and `x/y` for text (:453-460)
   (pass 2), audio submix with `gain_db`/`fade_in`/`fade_out`/`muted`/ducking (pass 3,
   :529-751). It consumes **only** the lumerai timeline (`store.load(project_id)`, :78).
   It never opens `lumenframe.json`, never reads `transition_after`, never applies
   `blend_mode`/`rotation`/`speed`/`mirrored`/`blur_radius` (verified by exhaustive read;
   see the field table in §4).
2. `lumenframe/compile.py` → `LayerStack.render_frames`/`render_to_video` — a second,
   feature-rich but CPU-bound render path (no GPU on this machine).
3. `gemia/video/compositing_graph.py` + `gemia/video/layers.py` — a third path that DOES
   understand `effects.blend_mode` on timeline clips (`_spec_effect`,
   compositing_graph.py:895-910) — but `project_export.py` never imports or calls it.
   `patches.py` even validates `blend_mode` against this renderer's `BLEND_MODES`
   (patches.py:640-649) — a value that is validated against a renderer the export never runs.
4. (half) `gemia/project_render.py` — the low-res timeline preview: video-track-only concat
   that drops overlays **and all effects** (project_render.py:33-38). So today **no**
   renderer on the timeline path draws `blend_mode`, `rotation`, `speed`, `mirrored`,
   `blur_radius`, or `transition_after`. "Preview-only" would currently be a lie for all of
   them.

The open QUEUE item "eventually bridge the current `/timeline/op` clip UI to lumenframe
where appropriate" (shared QUEUE.md, shared-2026-06-27 lumen-time entry, `next_action`)
is what D1/D2 answer.

中文小结：两套模型都活着——lumerai 平铺时间线有补丁日志和撤销，是 UI 与导出的事实模型；lumenframe 图层树能力强（关键帧/遮罩/表达式）但无历史无撤销。导出只读 lumerai 时间线，而 `set_clip_effects` 能写入的一半字段和全部 `transition_after` 在导出时被静默丢弃；`blend_mode` 甚至按一个导出根本不会调用的渲染器做写入校验。

---

## 2. D1 — Canonical-model verdict（正典模型裁决）

### Decision

**The lumerai timeline remains the single canonical model for the editing and export
surface.** `lumenframe` compositions become first-class citizens **of the timeline** —
each comp is referenced by an ordinary video clip whose asset carries a `comp_ref`
(§3), lazily materialized by `lumenframe.render_range.export_range` on demand and
refreshed at export. `/timeline/op` keeps writing only the lumerai model. There is no
migration of the timeline onto lumenframe.

### Why (grounded in what exists)

1. **Undo/audit live only on the timeline side.** ProjectStore's append-only patch log +
   replay-from-seed undo (project_store.py:108-234) is load-bearing for both the UI
   (`/timeline/op` undo, v3_routes.py:518-541) and the agent (`timeline_undo`,
   gemia/tools/timeline.py:330-342). lumenframe has deliberately no history
   (layer.py:204-207). Making lumenframe master means rebuilding undo/audit from scratch.
2. **Export economics.** Export is 3-pass ffmpeg with stream-copy concat
   (project_export.py:332-347) — cheap on a no-GPU machine under a $5/600s budget guard.
   lumenframe's renderer composites every frame in numpy on CPU
   (compile.py → LayerStack), fine for short comp windows, prohibitive as THE export path
   for full-length 1080p timelines.
3. **Blast radius.** The timeline model is wired into: web v3 drag-edit
   (v3.js:1183-1300), CLI parity, OTIO round-trip (lumerai/otio_adapter.py:212, 343),
   ~10 `tests/test_timeline_*` suites, and the protocol-parity contract. Swapping the
   master model violates the "protocol changes land web+CLI+tests same commit" rule at a
   scale no single session can honor.
4. **The models are good at different things.** Flat tracks/clips with ripple/overlap
   invariants (patches.py:130-177) match NLE-style cutting; the layer tree matches
   motion-graphics compositing. Nesting the latter as a *clip source* of the former is
   exactly the After Effects precomp / CapCut compound-clip pattern.

### Alternative considered and rejected

**lumenframe-as-master, clip UI as a projection.** Pros: one model, keyframes/masks
everywhere, no bridge. Cons: (a) must reimplement patch log + undo + OTIO + the whole
`/timeline/op` contract against a tree model; (b) export becomes per-frame CPU
compositing (point 2) or requires a new lumenframe→ffmpeg compiler (months, not phases);
(c) every existing timeline test and both frontends churn simultaneously. Rejected as a
big-bang with no incremental safe state.

### Revisit triggers（何时重议）

Re-open this verdict if ANY of these becomes true:

- The `_EFFECT_KEYS` whitelist (patches.py:24-27) keeps accreting lumenframe-shaped
  features (per-clip keyframes, masks) — i.e. the flat model starts reimplementing the
  tree model field by field.
- A GPU/hardware-accelerated compositor lands, making full-frame compositing export
  competitive with ffmpeg concat.
- Product decides multi-video-track *compositing* (not just latest-order-wins slicing,
  see `_timeline_segments` latest-order rule, project_export.py:817-848) is a core
  timeline feature — at that point the base pass needs a real layer compositor anyway.

中文小结：时间线继续当正典——撤销/审计、导出成本、双端契约的爆炸半径都在这一侧；lumenframe 以"预合成"身份作为剪辑素材进时间线。若未来平铺模型开始逐字段复刻图层树能力、或有了 GPU 合成器、或多视频轨真合成成为核心需求，再重议。

---

## 3. D2 — Bridge contract: `comp_ref`（桥接契约）

### 3.1 Representation on the timeline

A comp appears on the timeline as a **normal clip** with `media_kind: "video"` on a
video track — so every existing invariant (track-kind check patches.py:232-251, duration
== source range patches.py:149-163, overlap check :170-177), the web renderer, and export
pass 1 treat it with zero special cases.

Its **asset** is a normal video asset whose `source_path` points at the rendered cache
file, plus bridge provenance **inside `metadata`** — chosen because
`_normalize_asset` is a fixed-key whitelist that would drop any new top-level key, but
passes `metadata` through as an opaque dict (`"metadata": metadata`,
project_model.py:262-279 at HEAD). No model change required.

```jsonc
// asset (normalized shape unchanged, one new metadata member)
{
  "id": "cmp_ab12cd34",            // allocate via ctx.registry.allocate_id("video")
  "media_kind": "video",
  "source_path": "<renders>/comp_<hash12>_<in_ms>_<out_ms>.mp4",
  "duration": 2.5,                  // == t_out - t_in
  "metadata": {
    "comp_ref": {
      "doc_id":  "<lumenframe doc id>",        // doc.get("id")
      "t_in":    1.0,                           // half-open [t_in, t_out) seconds,
      "t_out":   3.5,                           //   same convention as export_range
      "step":    1,
      "doc_hash": "sha256:<64 hex>",            // sha256 of lumenframe.json BYTES at render time
      "rendered_at": "2026-07-06T…Z"
    }
  }
}
```

Constraints a weaker agent must not violate:

- `t_in < t_out` and the window MUST be non-empty after clamping — `export_range` raises
  `ValueError` on an empty range (lumenframe/render_range.py:283-287); surface that as
  the existing `E_EMPTY_RANGE` error (see gemia/tools/lumen_render_range.py:128-134).
- Clip fields: `source_in = 0.0`, `source_out = t_out - t_in`, `duration = t_out - t_in`
  — this satisfies the video-clip duration invariant (patches.py:149-163). Trimming the
  comp clip afterwards with `trim_clip` is legal and needs NO re-render (it trims within
  the rendered file).
- v1 scope: the whole project doc (`<project_dir>/lumenframe.json`,
  layer.py:64-86) is the comp. **No sub-composition (`layer_id`) references, no multiple
  docs per project** — add a `layer_id` member to `comp_ref` later if needed; readers
  MUST ignore unknown `comp_ref` members (forward compatibility).

### 3.2 The verb: `lumen_comp_to_timeline`

New tool verb (registered in `gemia/tools/__init__.py` + `_schema.py`, same pattern as
`lumen_render_range`). Args: `t_in`, `t_out`, optional `track_id`, `at_time`/`at_index`,
`ripple`. Behavior, in order:

1. Read the doc via `layer._lumendoc(ctx)` (never a private copy — it resolves the
   persisted file with the `_DOC_CACHE` fallback).
2. Compute `doc_hash` = sha256 of the current `lumenframe.json` **bytes** (when the doc
   only exists in `_DOC_CACHE`, hash the canonical
   `json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()` instead and mark
   `"doc_hash_source": "memory"` — memory docs are session-scoped, so this is best-effort).
3. Cache path: `store.renders_dir(project_id) / f"comp_{hash12}_{in_ms}_{out_ms}.mp4"`
   (content-addressed: hash prefix + window in integer milliseconds). If the file already
   exists and is non-empty → **skip rendering** (idempotent re-invocation).
   Otherwise call `export_range(doc, t_in, t_out, cache_path)`
   (lumenframe/render_range.py:229-291). Note `render_to_video` already transcodes `.mp4`
   output to browser-playable H.264 when ffmpeg is present (gemia/video/layers.py:576-591),
   so the cache file is valid both for export pass 1 (ffmpeg input) and for web playback.
4. Apply **one** `apply_ops` patch containing `upsert_asset` (asset above) +
   `insert_clip` (extended form with `track_id`/`at`/`ripple`,
   provenance `{"verb": "lumen_comp_to_timeline", "session_id": …}`) — mirroring
   `timeline_insert_clip`'s single-patch pattern (gemia/tools/timeline.py:181-191).
   One patch = the whole bridge insertion is **atomic and undoable as one step**.

### 3.3 Staleness & refresh（缓存失效）

**Comp clips are LIVE references** (After Effects precomp semantics): editing the comp is
expected to update the timeline clip's content. `doc_hash` is a **cache key, not a pin**.

- Freshness check = compare sha256 of current `lumenframe.json` bytes against
  `comp_ref.doc_hash`. An `mtime` fast-path MAY short-circuit the hash (unchanged mtime ⇒
  assume fresh) but mtime alone is NOT authoritative: `_save_lumendoc` writes via
  temp-file + `os.replace` (layer.py:151-191), which bumps mtime even for byte-identical
  saves. Hash decides.
- **Refresh points** (only these two; no background watcher):
  1. `export_project` gains a **pass 0**: for every enabled clip whose asset has
     `metadata.comp_ref`, run the freshness check; if stale, re-render the same
     `[t_in, t_out)` window to the NEW content-addressed path and apply an
     `upsert_asset` patch updating `source_path` + `doc_hash` + `rendered_at`.
     Because the refresh is a patch, it is itself undoable and the web picks it up on the
     next `/timeline` poll. Old cache files are left in place (append-only, GC later).
  2. Re-invoking `lumen_comp_to_timeline` for the same window (agent-driven refresh).
- **Shrink guard**: `export_range` clamps to the compiled doc's `total_frames`
  (render_range.py:276-287). If the comp got shorter and the clamped render no longer
  covers `t_out`, pass 0 MUST fail the export with a typed
  `ProjectExportError("comp_shrunk", …)` naming the clip — NEVER silently change
  `clip.duration` (that would ripple into the overlap/duration invariants and pass-3
  audio positions). The fix is a user/agent decision (`trim_clip` or re-window).

Rejected alternative: **frozen snapshots** (pin the clip to `doc_hash` forever, never
re-render). Rejected because a user who edits a comp and re-exports would get stale
pixels with no signal — worse than the cost of a re-render. Revisit if multi-user
editing or multiple comps-per-project make live refresh races real.

### 3.4 Undo across the seam — the honest asymmetry（撤销语义的不对称）

| Action | Undoable? | Mechanism |
|--------|-----------|-----------|
| Insert comp clip (`lumen_comp_to_timeline`) | ✅ yes, one step | patch log (`upsert_asset`+`insert_clip` in one patch) |
| Move/trim/split/delete the comp clip | ✅ yes | normal timeline ops |
| Pass-0 refresh (`upsert_asset` re-point) | ✅ yes | it is a patch; undo re-points `source_path` to the OLD cache file, which still exists (append-only cache) |
| **Editing the comp itself** (any `lumen_*` LayerPatch) | ❌ **no** | `lumenframe.json` is overwritten atomically with no history (layer.py:194-218) |

Consequences to state verbatim in the verb's schema description so the model knows:
*"Undoing timeline steps never restores composition content. If you undo past a refresh,
the clip points at the older rendered file (stale but playable); the comp document itself
stays at its latest state."* Comp-level history is a separate future feature (out of
scope here; belongs to lumenframe, not the bridge).

中文小结：合成以"普通视频 clip + `metadata.comp_ref` 资产"进时间线，插入是一个原子补丁、可整体撤销。失效判定以 `lumenframe.json` 字节 sha256 为准（mtime 只做快路径），缓存文件按内容寻址、只增不删；导出前 pass 0 统一刷新，comp 变短必须报 `comp_shrunk` 而不是偷改时长。诚实声明：时间线可撤销、comp 编辑不可撤销，这个不对称写进工具描述。

---

## 4. D3 — Export honesty rule（导出诚实规则）

**Rule: every field writable via `set_clip_effects` or `add_transition` MUST carry
exactly one classification, kept in a machine-readable table, and enforced at two
points: write time (tool-layer warnings) and export time (manifest `dropped_fields`).**
Silent dropping is a bug from Phase 1 onward.

### 4.1 Verified field-by-field state (HEAD `37a289b`) and assignment

Cross-read of `_EFFECT_KEYS` (patches.py:24-27) + `add_transition` (patches.py:574-614)
vs. every read in `project_export.py`:

| Field | Export today (verified) | Class (assigned) | Plan |
|---|---|---|---|
| `muted` | ✅ read at project_export.py:563-565 (skips embedded audio) | **RENDERED** | — |
| `gain_db`, `fade_in`, `fade_out` | ✅ read :594-596, applied :619-626 | **RENDERED** | — |
| `x`, `y`, `scale`, `opacity` on **image/lottie overlay** clips | ✅ read :414-418 (+ alpha path :431-437) | **RENDERED** | — |
| `x`, `y` on **text** clips | ✅ fallback read :453-460 (`text_config.position` wins) | **RENDERED** | `scale`/`opacity` on text: WARN-AT-WRITE |
| `x`, `y`, `scale`, `opacity` on **video-track** clips | ❌ pass 1 reads no effects at all (:280-307 builds only the canvas `_video_filter` :854-859) | **WARN-AT-WRITE** | Phase 3 candidate: PIP via overlaying video in pass 2 |
| `rotation` (0/90/180/270, patches.py:651-654) | ❌ never read | **WARN-AT-WRITE → RENDERED (Phase 3)** | per-segment `transpose` (90/270) / `hflip,vflip` (180) in `_render_video_segment`'s `-vf` |
| `mirrored` | ❌ never read | **WARN-AT-WRITE → RENDERED (Phase 3)** | per-segment `hflip` |
| `blur_radius` | ❌ never read | **WARN-AT-WRITE → RENDERED (Phase 3)** | per-segment `gblur=sigma=<r>` |
| `speed` | ❌ never read; trim math explicitly reserves it ("speed stays reserved", patches.py:446) | **WARN-AT-WRITE (indefinitely)** | Implementing speed changes the `duration == source_out - source_in` contract itself (needs `duration = range/speed` + `setpts`/`atempo` + patch-vocabulary change). Requires its own spec; do NOT bolt on. |
| `blend_mode` | ❌ never read by export; validated against `gemia.video.layers.BLEND_MODES` (patches.py:640-649); only `compositing_graph` can render it (compositing_graph.py:895-910) and export never calls it | **WARN-AT-WRITE** | Phase 3: overlay-clip blend via ffmpeg `blend=all_mode=` in pass 2. Video-track blend needs true multi-layer base compositing → revisit trigger in §2. |
| `transition_after` `= dissolve/fade` | ❌ written (patches.py:613), survives split (:521-522), OTIO round-trips (otio_adapter.py:212, 343), **no renderer reads it** | **RENDERED (Phase 1, §5)** | — |
| `transition_after` `= wipe` | ❌ same dead path | **WARN-AT-WRITE** | Phase 3: same window mechanism as dissolve, `xfade=transition=wipeleft` etc. |
| `transition_after` `= cut` | n/a (clears the field, patches.py:582-585) | RENDERED (trivially) | — |

**PREVIEW-ONLY is an intentionally empty class today.** `project_render.py` renders no
effects (§1 item 4), so classifying anything preview-only would be dishonest. The class
exists so a future web-canvas preview can claim fields without touching this rule.

### 4.2 Mechanism (what Phase 1 builds)

1. **One table, one module**: `lumerai/export_support.py` — pure data + two functions:
   `effects_warnings(media_kind: str, effects: dict) -> list[str]` and
   `transition_warnings(kind: str) -> list[str]`. Warning strings are stable and typed,
   e.g. `W_NOT_EXPORTED:blend_mode:video-track blend_mode is not rendered by export yet`.
   The table in §4.1 is the normative content; the module is its executable form.
2. **Write-time wiring (warn, never reject)**: append a `warnings: [...]` list to
   - the tool results of `dispatch_effects` / `dispatch_transition`
     (gemia/tools/timeline.py:280-302), and
   - the 200 response of `/timeline/op` for `set_effects` / `add_transition`
     (v3_routes.py:471-515).
   Rejecting was considered and dropped: `_EFFECT_KEYS` values already round-trip
   through OTIO import (otio_adapter.py:212) and existing projects/tests store them;
   a write-time hard error would corrupt import paths and break undo replay of old
   patch logs. Warn-only preserves compatibility while killing the silence.
3. **Export-time manifest honesty**: `export_project` collects, per enabled clip, every
   field the table says is not rendered and writes
   `"dropped_fields": [{"clip_id": …, "field": …, "reason": "not_rendered"}]` into the
   manifest (extend the dict at project_export.py:176-190). Transitions degraded at
   render time (§5) are recorded here too with `reason: "no_handle" | "not_adjacent" |
   "kind_not_supported"`.
4. **Parity**: warnings are protocol surface. Web shows them (toast/banner) and CLI
   prints them **in the same commit**, with tests on both ends — per
   `docs/protocol-parity-plan.md` rule 1 (三件套同 commit).

> Coordination note: the shared QUEUE bug ③ "transition 死链路显露（双端同批）" IS
> items 1-2 of this mechanism plus §5. Whoever picks up bug ③ implements Phase 1 of this
> doc — do not build a parallel warning scheme.

中文小结：字段三分类（已渲染 / 写入时警告 / 仅预览），表格即规范、模块即实现。选择"警告而非拒绝"是为了不破坏 OTIO 导入与旧补丁重放；导出清单新增 `dropped_fields` 把每一次静默丢弃变成可见记录。警告是协议面，web+CLI 同 commit 落地。

---

## 5. D4 — Transition rendering: the exact constraint（转场渲染精确约束）

### 5.1 Why this is hazardous（先说清坑在哪）

Pass 1 renders **one file per timeline segment** and concatenates with `-c copy`
(project_export.py:224-277, `_concat_segments` :332-347). Pass 3 then positions every
audio source on the timeline by `adelay = clip.start * 1000` (:628-630) and pads video
to `timeline.duration` with black (:109-124, :220-223). Therefore:

> **INVARIANT T1 — the base video's total duration MUST equal `timeline_duration`
> exactly (segment durations must sum unchanged).**

The "obvious" ffmpeg approach — feeding adjacent clips A and B into
`xfade=duration=d:offset=dA-d` — outputs `dA + dB - d`: **the video shrinks by `d` per
dissolve while pass-3 audio stays positioned on the original timeline.** Every clip after
the first dissolve plays its audio `d` late; the black-pad math also breaks.
**A joint-xfade concat pipeline is FORBIDDEN.** Transitions must be rendered as
duration-preserving segment surgery, below.

Second trap: `_render_video_segment` floors every segment to 0.1 s
(`-t max(duration, 0.1)`, :296; also `trim_dur` floor at :257). Any scheme that produces
segments shorter than 0.1 s silently *lengthens* them and violates T1. The math below
avoids ever creating a segment < 0.1 s.

### 5.2 Semantics per kind (tranche 1: `fade`, `dissolve`)

Setup: `add_transition` guarantees at write time that A and B are same-track and
butt-joined (`|A.end - B.start| <= EPSILON`, patches.py:604-608) and
`d <= min(A.duration, B.duration)` (:609-612). Let `cut = B.start`.

**`fade` (fade-through-black) — zero-hazard, no extra media:**

- A's segment gets `,fade=t=out:st={segdur - d/2}:d={d/2}` appended to its existing
  `-vf` chain; B's segment gets `,fade=t=in:st=0:d={d/2}`.
- Segment boundaries, durations, and the concat list are **unchanged** → T1 holds by
  construction. No handles needed. This is why `fade` ships first.

**`dissolve` (true crossfade) — needs B-side pre-handle media:**

A real dissolve must show B's content *before* `cut` without shifting B's timeline
placement. The only sync-safe source for those frames is media **before `B.source_in`**
(a handle). One-sided (B-pre-handle) design, chosen over centered/two-sided because it
needs exactly one handle condition and keeps B's segment untouched:

1. Effective duration:
   `d_eff = min(d, B.source_in, A.duration - 0.1, B.duration - 0.1)`
   (the `- 0.1` terms keep every remaining segment above the 0.1 s floor).
   If `d_eff < 2/fps` → **degrade to a hard cut** and record
   `{clip_id: A.id, field: "transition_after", reason: "no_handle"}` in the manifest.
   Never fail the export for a missing handle.
2. Window = `[cut - d_eff, cut)`. Segment surgery:
   - A's segment now ends at `cut - d_eff` (shortened by `d_eff`).
   - Insert a **window segment** of exactly `d_eff` seconds between A's and B's segments.
   - B's segment is untouched (starts at `cut` as before).
   - Sum: `(A - d_eff) + d_eff + B` — T1 holds.
3. Window segment rendering (its own ffmpeg invocation, same quality profile):

   ```
   ffmpeg -y -hide_banner -loglevel error \
     -ss {A.source_in + (cut - d_eff - A.start):.6f} -t {d_eff:.6f} -i {A.asset.source_path} \
     -ss {B.source_in - d_eff:.6f}                    -t {d_eff:.6f} -i {B.asset.source_path} \
     -filter_complex "[0:v]{_video_filter(...)}[va];[1:v]{_video_filter(...)}[vb];\
                      [va][vb]xfade=transition=fade:duration={d_eff:.6f}:offset=0[v]" \
     -map "[v]" -an -c:v libx264 -pix_fmt yuv420p -crf {profile.crf} -preset {profile.preset} \
     -movflags +faststart {work_dir}/NNNN-xfade-{A.id}-{B.id}.mp4
   ```

   Both inputs are exactly `d_eff` long and pre-normalized through the same
   `_video_filter` (scale/pad/fps/format, :854-859) that every other segment uses, so
   `xfade` at `offset=0` outputs exactly `d_eff` seconds in concat-compatible encoding.
   `B.source_in - d_eff >= 0` is guaranteed by step 1.
4. Runtime re-checks (write-time validation can go stale — `move_clip`/`delete_clip` do
   NOT clear `transition_after` today):
   - If A and B are no longer adjacent (`|A.end - B.start| > EPSILON`) or B is gone →
     hard cut + manifest `reason: "not_adjacent"`.
   - Only apply when the segments flanking `cut` actually belong to A and B (multi-track
     latest-order-wins slicing in `_timeline_segments` :817-848 can interpose another
     clip) → otherwise hard cut + `reason: "not_adjacent"`.
   - (Phase 2 cleanup: make `move_clip`/`delete_clip` clear or re-validate
     `transition_after` at write time; until then the export-side re-check is the guard.)

**Out of scope in tranche 1 (explicit):** audio crossfade at the cut (pass 3 untouched —
a dissolve keeps the existing hard audio cut; acceptable and honest, note it in the verb
description), `wipe` (WARN-AT-WRITE), transitions on overlay/audio tracks
(`add_transition` doesn't restrict track kind — export only implements video-track
windows; others go to `dropped_fields`).

中文小结：铁律 T1——基底视频总时长必须等于时间线时长，禁止用"两段连体 xfade"的偷懒实现（它会每个转场吞掉 d 秒并让第三遍的 adelay 音轨永久错位）。`fade` 纯滤镜零风险先上；`dissolve` 采用 B 侧前手柄窗口法：A 段缩短 d_eff、插入恰好 d_eff 的窗口段、B 段不动，无手柄就诚实降级为硬切并写进清单。所有 0.1 秒地板、邻接失效、多轨插队的坑都有明确的运行时守卫。

---

## 6. Migration phases & test gates（迁移阶段与测试门）

Acceptance channel per project convention: local
`.venv/bin/python -m pytest` + CLI `npm test` (NOT GitHub CI; and never `uv run pytest`
on exFAT).

### Phase 1 — Export honesty + fade/dissolve（sized for ONE codex-class session）

Deliverables (all in one commit, parity rule applies):

1. `lumerai/export_support.py` — the §4.1 table as data + `effects_warnings` /
   `transition_warnings`.
2. Warning wiring: `gemia/tools/timeline.py` (`dispatch_effects`, `dispatch_transition`)
   + `gemia/v3_routes.py` (`_session_timeline_op`) return `warnings`; web v3 renders a
   banner; CLI prints a warning line.
3. `gemia/project_export.py`: `fade` + `dissolve` per §5.2; manifest gains
   `transitions_rendered` (count) and `dropped_fields`.

Test gates (new `tests/test_export_transitions.py`, `tests/test_export_honesty.py`):

- **T1 duration**: two adjacent clips + `fade`/`dissolve` → `ffprobe` duration of the
  export equals the no-transition export ± 1 frame.
- **Dissolve with handle**: B trimmed with `source_in = 1.0`, `d = 0.5` → duration
  preserved AND a frame extracted at `cut - d_eff/2` differs from both the pure-A and
  pure-B frames at that timestamp (mid-blend pixel assertion).
- **Dissolve without handle**: `B.source_in = 0` → duration preserved, output plays, and
  the manifest carries `reason: "no_handle"`.
- **A/V sync**: a project with an audio clip + one dissolve → audio stream duration and
  `adelay` positioning unchanged vs. the no-transition export.
- **Honesty**: `set_clip_effects {blend_mode, speed, rotation}` → tool result and
  `/timeline/op` response carry the three warnings; export manifest lists them in
  `dropped_fields`.
- **Regression**: `tests/test_timeline_m4_export.py`, `tests/test_timeline_patches.py`,
  `tests/test_timeline_direct_edit.py` stay green; CLI `npm test` green.

### Phase 2 — `comp_ref` bridge

`lumen_comp_to_timeline` verb + export pass 0 refresh (§3) + `move_clip`/`delete_clip`
clearing stale `transition_after`. Gates (`tests/test_comp_ref_bridge.py`): insert →
export consumes the cache file; edit comp via a `lumen_*` op → hash mismatch → pass 0
re-renders and re-points the asset; single-step undo removes clip+asset; shrunken comp →
`ProjectExportError("comp_shrunk")`; idempotent re-invocation reuses the cache.

### Phase 3 — Effects tranche 2 + remaining transitions

`rotation`/`mirrored`/`blur_radius` in pass 1 `-vf`; `wipe` via the §5 window mechanism;
`blend_mode` for overlay clips via pass-2 `blend=`. Each field flips its class in
`export_support.py` **in the same commit** as its renderer support (rule 1 below).

### Phase 4 — Deferred, each needs its own spec

`speed` (duration-contract change), video-track blend compositing / PIP, comp-level
history/undo, sub-comp (`layer_id`) references, cache GC.

中文小结：Phase 1 一个 codex 会话可完成（诚实表 + 警告双端接线 + fade/dissolve + 五道测试门）；Phase 2 桥接；Phase 3 逐字段翻类；speed 与多轨合成等留待独立 spec。

---

## 7. Rules — effective immediately（即日生效的规则）

1. **Classification travels with the field**: adding an effect key or transition kind
   requires, in the same commit, its row in `lumerai/export_support.py` — either
   renderer support or warning wiring. A key that is in `_EFFECT_KEYS` but in no class
   is a red test from Phase 1 on (drift test: `_EFFECT_KEYS ∪ _TRANSITION_KINDS` ⊆
   table keys).
2. **No silent drops**: export must record every unrendered stored field in the manifest
   `dropped_fields`. "It exports without error" is not the bar; "it exports what the
   model says or says what it didn't" is.
3. **T1 is non-negotiable**: any transition/effect implementation in pass 1 must keep
   the segment-duration sum equal to `timeline_duration`. Joint-xfade concat is banned.
4. **The timeline is the only export input**: lumenframe content reaches
   `project_export.py` exclusively as `comp_ref` rendered media. Export never parses
   `lumenframe.json` beyond the pass-0 hash check.
5. **`metadata.comp_ref` is verb-owned**: only `lumen_comp_to_timeline` and pass 0 write
   it. Nothing else edits it in place; readers ignore unknown members.
6. **Warnings are protocol surface**: per `docs/protocol-parity-plan.md`, warning
   payload changes land web + CLI + both test suites in one commit.

中文小结：字段分类随改动走、导出不许沉默、T1 时长不变量不可谈判、lumenframe 只能以渲染产物进导出、`comp_ref` 只有桥接动词可写、警告属于协议面走双端同 commit 规则。
