# Outline/Script-Driven Editing v1 — 大纲 IR → Rough Cut

状态: 已定稿，待实施（本文档是唯一规格；实施代理不得自行改设计）
制定日期: 2026-07-06
适用范围: `/Volumes/Extreme SSD/gemia`（后端 + prompts + lumenframe 模板）与 `~/Code/lumeri-cli`（仅 parity 验证，v1 无 UI 改动）
姊妹文档: `docs/protocol-parity-plan.md`（协议同步规则）、`docs/timeline-canonical-plan.md`（lumenframe↔timeline 桥接，另案）、`docs/semantic-search-media-plan.md`（语义检索，另案，本文只预留接口）

Every claim below is grounded in code read at authoring time; citations are
`file:line`. Where the tree was dirty, content was cross-checked against the
in-flight files themselves (`gemia/plan_mode.py` is a legitimate in-flight
feature and is referenced as such).

---

## 0. Decisions locked (quick index)

| # | Decision | Where detailed |
|---|----------|----------------|
| D1 | Outline IR v1: `beats[]` + doc header, tagged-union `duration_policy`, reserved `matched_evidence` | §2 |
| D2 | Narration rate: **zh 4.0 chars/sec, en 15 chars/sec**, +0.6 s pad, clamp [2.0, 20.0] s | §2.3 |
| D3 | Storage: one `outline.json` per project dir, atomic write, NOT in patch log, NOT undoable | §3.1 |
| D4 | Two thin verbs only: `outline_write` (full-document replace) + `outline_get` | §3.2 |
| D5 | Plan mode: `outline_get` **allowed**, `outline_write` **blocked** | §4 |
| D6 | Assembly canvas: **lumerai project timeline document only** (no lumenframe bridging in v1) | §5 |
| D7 | Checkpoint cadence: `render_preview` after every **N=4** placed beats + once after the last beat | §6.2 |
| D8 | Titles in v1 = `timeline_insert_clip` text clips; `template_hint` recorded for the lumenframe path | §5, §7 |
| D9 | New lumenframe templates: `chapter_title`, `outro` (pure `(**params) -> list[op]`) | §7 |
| D10 | Architecture: thin persistence verbs + prompt choreography; NO host-side `assemble_from_outline` | §8 |
| D11 | Narration **audio is out of v1 scope** — TTS backends are simulation stubs | §1.2 |

---

## 1. Problem and scope

Lumeri can already execute fine-grained edits: the ~90-verb surface in
`gemia/tools/_schema.py` (`TOOL_NAMES` derived at `_schema.py:1577`) includes
`timeline_insert_clip` supporting **both media assets and text title clips**
(`_schema.py:1224-1261`, dispatcher `gemia/tools/timeline.py:77-207` — text
branch at 91-112), plus delete/move/trim/split/set_clip_time/add_transition/
set_clip_effects/add_track/undo, `render_preview`, `inspect_timeline`, and
`project_export` (registered in `gemia/tools/__init__.py:141-158`).

What is missing is a **persistent, structured plan of the video** — a 大纲
(outline) the model drafts once, the user approves, and the agent then executes
beat-by-beat into a rough cut, updating per-beat status as it goes. Today that
plan lives only in transient turn text; a session restart, a long session, or a
mid-assembly correction loses it.

v1 delivers: (a) an outline IR schema, (b) two persistence verbs, (c) a
system-prompt choreography section that turns an approved outline into a rough
cut on the project timeline, (d) two new lumenframe templates so
`template_hint` has real referents.

### 1.2 Explicitly out of scope for v1

- **Narration audio (TTS).** Verified: `gemia/audio/ai_speech.py` is a
  simulation stub — `_simulate_lyria_api_call` prints "Simulating Lyria API
  call", sleeps 0.5 s, and writes literal dummy bytes
  (`b"This is a dummy audio file simulating speech generation."`).
  `gemia/video/speech_generator.py` hard-raises on real generation:
  `if dry_run is False: raise ValueError("dry_run=False is not supported. This
  implementation only generates deterministic dry-run speech.")`. Therefore
  **v1 produces no narration audio**. `narration_text` in the IR exists to (1)
  drive duration estimation and (2) feed title/caption text; it is stored so a
  future TTS integration can consume it without an IR change.
- **Semantic media matching.** v1 matching uses keyword `search_library`
  (its tokenizer is already CJK-aware: `gemia/tools/search_library.py:31-33`
  splits on `[^a-z0-9一-鿿]+`) plus `get_media_annotations`
  (`_schema.py:541-548`). The IR reserves `matched_evidence` (§2.4) for the
  semantic-search plan — see `docs/semantic-search-media-plan.md` (authored
  concurrently; referenced by path only, per coordination rules).
- **lumenframe bridging.** See D6/§5 and `docs/timeline-canonical-plan.md`.
- **Frontend outline UI.** v1 is UI-free; new verbs render through each
  frontend's generic tool fallback (§9.2).

> 小结：v1 只做「大纲 IR + 两个持久化动词 + 系统提示编排 + 两个新模板」。
> 旁白配音（TTS 是仿真桩）、语义检索、lumenframe 桥接、前端大纲 UI 全部不做。

---

## 2. Outline IR schema v1

### 2.1 Full example (normative)

```json
{
  "version": 1,
  "title": "三分钟产品发布回顾",
  "target_duration_sec": 180,
  "canvas": { "width": 1920, "height": 1080, "fps": 30 },
  "updated_at": "2026-07-06T09:30:00+00:00",
  "beats": [
    {
      "id": "b01",
      "narration_text": "今年发布会的三个关键词：更快、更稳、更便宜。",
      "visual_query": "keynote stage wide shot opening",
      "duration_policy": { "kind": "narration_estimated" },
      "template_hint": "intro",
      "status": "draft",
      "chosen_clip": null,
      "matched_evidence": null,
      "notes": ""
    },
    {
      "id": "b02",
      "narration_text": "",
      "visual_query": "product close-up demo hands",
      "duration_policy": { "kind": "fixed_sec", "seconds": 6.0 },
      "template_hint": null,
      "status": "matched",
      "chosen_clip": { "asset_id": "v_003", "in_sec": 12.4, "out_sec": 18.4 },
      "matched_evidence": null,
      "notes": "annotation marker 'demo close-up' covers 12.0-19.5"
    }
  ]
}
```

### 2.2 Field rules (validator MUST enforce exactly these)

Document level — required keys, no others (unknown top-level key ⇒ error):

| Key | Type | Rule |
|-----|------|------|
| `version` | int | must equal `1` in v1; bump only on breaking change |
| `title` | str | non-empty, ≤ 200 chars |
| `target_duration_sec` | number \| null | > 0 when present; advisory, not enforced against the sum |
| `canvas` | object | `{width:int, height:int, fps:number}`; defaults `1920/1080/30` — the same defaults `set_timeline_format` uses on OTIO import (`gemia/tools/timeline.py:495-499`) |
| `updated_at` | str | **ignored on input, host-stamped** (UTC ISO-8601) on every `outline_write`, mirroring `ProjectStore.create`'s timestamping (`gemia/project_store.py:79`) |
| `beats` | array | 1..40 items |

Beat level — required keys exactly `{id, narration_text, visual_query,
duration_policy, template_hint, status, chosen_clip, matched_evidence, notes}`;
any other key ⇒ error (catches typos like `narration_txt` loudly — same
fail-closed spirit as `plan_mode.py:21-23`):

| Key | Type | Rule |
|-----|------|------|
| `id` | str | non-empty, unique within `beats`; recommended format `b01`, `b02`, … (zero-padded, stable across rewrites — never renumber existing beats) |
| `narration_text` | str | may be empty; ≤ 2000 chars |
| `visual_query` | str | non-empty free text; v1 feeds it to `search_library` keyword matching |
| `duration_policy` | object | tagged union, §2.3 |
| `template_hint` | str \| null | when non-null MUST be a key of `lumenframe.templates.TEMPLATES` (`lumenframe/templates/__init__.py:33-36`); validate via `template_names()` so newly registered templates work without touching the validator |
| `status` | str | one of `draft` \| `matched` \| `placed` \| `refined` (validator checks membership only, not transition order — the model may legitimately re-draft) |
| `chosen_clip` | object \| null | when non-null: `{asset_id: non-empty str, in_sec: number ≥ 0, out_sec: number > in_sec}`. For image/text-backed beats use `in_sec=0, out_sec=<on-screen duration>` |
| `matched_evidence` | object \| null | **reserved, opaque** — §2.4 |
| `notes` | str | free text, ≤ 2000 chars |

Serialized document hard cap: 256 KB (reject larger — an outline is a plan,
not a media store).

### 2.3 `duration_policy` — tagged union and the narration rate decision

```
{"kind": "fixed_sec", "seconds": <number > 0>}
{"kind": "narration_estimated"}
{"kind": "source_length"}
```

- `fixed_sec` — place exactly `seconds` on the timeline (`source_out =
  source_in + seconds` for media; `duration` arg for image/text, cf. the 3 s
  default `_TEXT_DEFAULT_DURATION`, `gemia/tools/timeline.py:29`).
- `narration_estimated` — deterministic host-computable estimate:

  ```
  cjk    = count of codepoints in U+4E00..U+9FFF        (same CJK range search_library.py:32 already uses)
  other  = count of remaining non-whitespace codepoints
  est_sec = clamp(cjk / 4.0 + other / 15.0 + 0.6, 2.0, 20.0)
  ```

  **Rates and why:** Mandarin narration/broadcast reads at roughly 200–300
  characters/min (news at the high end; documentary/promo voice-over
  200–260). We lock **4.0 chars/sec (= 240/min)** — the middle of the
  narration band. English narration sits near 150 wpm; at an average word
  length of ~4.7 letters + 1 space ≈ 5.7 chars/word that is ~855 chars/min ≈
  14.3 chars/sec; we round to **15 chars/sec**. Rounding *up* (slightly tight
  beats) is deliberate: extending a placed clip later
  (`timeline_set_clip_time` / `timeline_trim_clip`) is cheaper than cutting,
  and the +0.6 s breath pad compensates. Summing per-script (cjk + other)
  handles mixed-language text with **no language-detection branch** — a
  sonnet-class implementer cannot get it wrong. Alternative considered:
  per-beat language tag + single rate — rejected (adds a field the model must
  fill correctly; the mixed-sum formula is strictly more robust).
- `source_length` — keep the chosen clip's natural range
  (`out_sec - in_sec` from `chosen_clip`, typically taken from an annotation
  marker or `probe_media` duration). If the resulting total wildly exceeds
  `target_duration_sec`, `outline_write` returns a **warning**, never an error
  (the outline is advisory; the timeline is the source of truth).

The estimate function lives host-side (`gemia/outline_ir.py`, §10 D1) so
tests pin the arithmetic; the same numbers are quoted in the prompt section
(§6) so the model and host agree.

### 2.4 Forward-compat rule: `matched_evidence` (reserved NOW)

`matched_evidence` is part of the v1 schema as an **opaque `object | null`**.
The v1 validator accepts any JSON object there and never inspects it; v1
writers (the choreography) always pass it through unchanged and set it to
`null` on newly drafted beats. When semantic search lands
(`docs/semantic-search-media-plan.md` owns the final shape), it will populate
something like `{method, query, candidates: [{asset_id, score,
annotation_ids}], embedding_model, retrieved_at}` **without a version bump**,
because the key already exists and is contractually opaque. Rationale:
reserving the key now means the semantic-search plan needs zero IR migration
and zero `outline_write` changes; the alternative (add the key later) forces
every stored outline through a version check for a purely additive field.

> 小结：IR = 文档头（title/target_duration_sec/canvas/version/updated_at）+
> beats[]（id/narration_text/visual_query/duration_policy/template_hint/
> status/chosen_clip/matched_evidence/notes）。时长估算锁定 zh 4.0 字/秒、
> en 15 字符/秒、+0.6s、夹在 [2,20]s；`matched_evidence` 现在就预留，语义检索
> 落地时零迁移。

---

## 3. Storage and verbs

### 3.1 Storage: `<project_dir>/outline.json`

One outline per **project**. Sessions bind 1:1 to a project via
`ProjectHandle.open` (`gemia/project_store.py:287-308` — non-conforming ids are
hashed to a stable `p_<sha1[:12]>`), and every tool reaches it as
`ctx.project` (`gemia/tools/_context.py:165`). The outline path is:

```python
outline_path = project.store.project_dir(project.project_id) / "outline.json"
```

using `ProjectStore.project_dir` (`gemia/project_store.py:40-42`), which
already validates the id. Writes MUST use the store's atomic
tmp-then-`replace` pattern (`ProjectStore._write_json`,
`gemia/project_store.py:257-262`) — copy that exact pattern, do not import the
private method.

**Locked decisions with rationale:**

- **NOT inside `state.json`, NOT in the patch log, NOT undoable via
  `timeline_undo`.** The patch log is the canonical *editing* history — the
  timeline design contract is "every verb compiles to exactly ONE
  TimelinePatch … undoable via `timeline_undo`"
  (`gemia/tools/timeline.py:5-9`). The outline is *agent working memory about
  intent*, not editing state. If outline writes shared the patch seq, a user
  hitting undo after a rough cut would silently rewind plan bookkeeping
  interleaved with clip edits — wrong coupling. Alternative considered
  (outline as a `state.json` extension + new patch ops): rejected for exactly
  that undo/audit pollution, plus it would force `lumerai/patches.py`
  vocabulary changes for a non-editing concern.
- **Full-document replace, last-write-wins.** At ≤ 40 beats / ≤ 256 KB there
  is no payoff for per-beat patch verbs; one `outline_write` per checkpoint
  (§6.2) keeps call volume trivial. A per-beat `outline_update_beat` verb was
  considered and rejected: it doubles the schema surface, needs its own
  plan-mode/budget/test entries (`tests/test_plan_mode.py:41-49` exact
  coverage makes every extra verb a real cost), and saves nothing measurable.
  Concurrent-writer safety is owned by the project-state single-writer
  serialization work already queued (QUEUE task「项目状态单写者串行化」); v1
  relies on the loop owning "exactly one handle per session"
  (`gemia/project_store.py:268-270`).
- **Survives session restarts** for free, because the project dir does.

### 3.2 The two verbs

Dispatchers live in a new `gemia/tools/outline.py`; pure
validation/estimation in a new `gemia/outline_ir.py` (no IO, unit-testable).
Pattern-copy the `_project(ctx)` guard from `gemia/tools/timeline.py:32-37`
(raise `ValueError` when `ctx.project is None`). Dispatchers must NOT swallow
errors (`gemia/tools/_context.py:8-10`); raise `ValueError` with an
`E_OUTLINE_SCHEMA: …` message prefix so the model can read the cause from the
`tool_exec_error` event.

**`outline_write(outline)`** — validate per §2.2; stamp `updated_at`; write
atomically; return a compact digest, NOT the echoed document (token economy —
same principle as timeline verbs returning `compact_text()` instead of full
state, `gemia/tools/timeline.py:44-51`):

```json
{
  "applied": true,
  "path": "…/outline.json",
  "beats": 12,
  "status_counts": {"draft": 3, "matched": 2, "placed": 6, "refined": 1},
  "estimated_total_sec": 172.4,
  "warnings": []
}
```

`estimated_total_sec` sums per-beat policy durations (`fixed_sec` seconds,
`narration_estimated` formula §2.3, `source_length` from `chosen_clip` when
present, else the 2.0 s clamp floor with a warning). Exceeding
`target_duration_sec` appends a warning string; it never fails the call.

**`outline_get()`** — read + return `{"exists": true, "outline": {…}}`, or
`{"exists": false, "outline": null}` when the file is absent (non-throwing on
absence, like `search_library`'s empty-library contract,
`gemia/tools/search_library.py:3-6`).

### 3.3 Exact `_schema.py` entries (paste into `TOOL_SCHEMAS` before the closing `]`, `_schema.py:1574`)

```python
    _tool(
        "outline_write",
        "Persist the session's video outline (大纲) — the structured plan of beats that "
        "drives a rough cut. Full-document replace: pass the complete outline object "
        "{version:1, title, target_duration_sec, canvas{width,height,fps}, beats:[...]}. "
        "Each beat: {id 'b01'-style unique, narration_text, visual_query, duration_policy "
        "({kind:'fixed_sec',seconds}|{kind:'narration_estimated'}|{kind:'source_length'}), "
        "template_hint (a lumenframe template name or null), status "
        "(draft|matched|placed|refined), chosen_clip ({asset_id,in_sec,out_sec}|null), "
        "matched_evidence (reserved, pass through unchanged), notes}. Returns a compact "
        "digest with per-status counts and the estimated total duration. Unknown keys are "
        "rejected; updated_at is host-stamped.",
        {
            "outline": {
                "type": "object",
                "description": "The complete outline document to persist (replaces the previous one).",
            },
        },
        ["outline"],
    ),
    _tool(
        "outline_get",
        "Read the session's persisted video outline (大纲). Returns {exists, outline}; "
        "exists=false with outline=null when none has been written yet. Use before "
        "resuming or refining a rough cut so beat statuses and chosen clips are current.",
        {},
        [],
    ),
```

### 3.4 Registration + budget entries

- `gemia/tools/__init__.py`: `from gemia.tools import outline as _outline` and
  in `_REAL` (`__init__.py:91-182`):

  ```python
      "outline_write": _outline.dispatch_write,
      "outline_get":   _outline.dispatch_get,
  ```

  (Anything present in `TOOL_NAMES` but missing from `_REAL` becomes a raising
  stub — `__init__.py:82-88, 185-187` — so forgetting this line fails loudly at
  first call, but the tests in §10 catch it before that.)

- `gemia/budget_guard.py` `_TOOL_COSTS` (style-match the timeline block at
  `budget_guard.py:63-79` — document mutations are near-free):

  ```python
      "outline_write":            {"usd": 0.00, "eta_sec": 0.3},
      "outline_get":              {"usd": 0.00, "eta_sec": 0.2},
  ```

> 小结：`<project_dir>/outline.json`，原子写、整篇替换、不进 patch log、不可
> undo。两个瘦动词：`outline_write` 返回摘要不回显全文，`outline_get` 缺文件
> 返回 exists=false 不抛错。schema/dispatcher/budget 三处登记缺一不可。

---

## 4. Plan-mode classification: `outline_write` is BLOCKED

`gemia/plan_mode.py` (in-flight, referenced as a legitimate feature) is
fail-closed: "a tool name in neither set … is treated as blocked" and
`tests/test_plan_mode.py` asserts `PLAN_ALLOWED_TOOLS | PLAN_BLOCKED_TOOLS`
**exactly covers and stays disjoint over `TOOL_NAMES`**
(`plan_mode.py:21-23`; `tests/test_plan_mode.py:41-49`). Therefore the same
commit that adds the two verbs to `_schema.py` MUST classify both, or the
suite reds. Locked classification:

```python
PLAN_ALLOWED_TOOLS  += {"outline_get"}     # pure read; joins get_timeline/probe_media etc. (plan_mode.py:28-46)
PLAN_BLOCKED_TOOLS  += {"outline_write"}   # durable file write (plan_mode.py:50-74)
```

**Why block `outline_write`, even though "writing an outline IS the planning
artifact":**

1. **The classification contract is side-effect-based, not intent-based.**
   The allow/block split "was derived by reading every dispatcher
   implementation, not from tool names" (`plan_mode.py:10-11`), and the
   module blocks *every* durable write — including `remember` and `log_note`,
   which are just as "planning-adjacent" as an outline
   (`plan_mode.py:16-17, 56, 67`). Admitting one write because its *content*
   is plan-like creates a category exception a weaker agent will extend
   ("`write_file` of plan.md is also a planning artifact…"). Fail-closed
   survives only if the rule stays mechanical.
2. **Plan mode currently guarantees the project dir is byte-identical after
   planning.** That invariant is worth keeping for user trust, and it keeps
   plan mode trivially compatible with the queued single-writer serialization
   work — a plan-mode session can never contend for project-dir writes.
3. **Nothing is lost.** Plan mode's contracted deliverable is the plan *as
   text* presented for approval (`plan_mode.py:117-125`: "presenting the plan
   IS the successful outcome of the turn"). The choreography (§6) has the
   model draft the outline as fenced JSON inside the plan text; the client
   flips plan mode off on approval (`plan_mode.py:6-8`), and the model's
   **first execution-mode action is `outline_write` persisting the approved
   outline verbatim**. One extra 0.3 s call, zero design compromise.

**Alternative considered and rejected:** allow `outline_write` in plan mode
because it registers no assets, spends no money, and is session-scoped. Points
1–2 above outweigh the one-call saving; additionally the plan-gate message
(`plan_mode.py:88-97`) already teaches the model to present plans as text, so
an allowed write verb would create two competing "where does my plan go"
affordances during planning.

> 小结：`outline_get` 进白名单，`outline_write` 进黑名单。plan mode 的分类是
> 「按副作用、机械判定」，连 remember/log_note 都拦；大纲在规划期以计划文本
> 形式呈现，批准后第一步再落盘，只多一次 0.3 秒调用。

---

## 5. Assembly canvas: the lumerai project timeline document ONLY

The rough cut targets the session's persistent **project timeline** — the
document behind `get_timeline`/`timeline_*`/`render_preview`/`project_export`/
`timeline_undo` (`gemia/tools/__init__.py:141-158`), reached via `ctx.project`
(`ProjectHandle`, `gemia/project_store.py:265-333`). It is chosen because it
is the only canvas where **preview, export, undo, patch audit, and SSE
`timeline_op` events already exist end-to-end** (`project_store.py:313-333`
emits per-patch `on_patch`; `render_preview`/`project_export` dispatchers at
`timeline.py:347-419`).

The lumenframe layer document is NOT a v1 assembly target. Templates
(`lumenframe/templates/`) expand to lumenframe `add_layer`/`set_transform`/
`animate_text` ops (`lumenframe/templates/__init__.py:1-8`), which the
timeline patch vocabulary does not accept — bridging the two documents is a
separate plan (`docs/timeline-canonical-plan.md`, referenced by path). The
practical consequence a weaker agent must not get wrong:

- **In v1 choreography, beat titles are text clips** via
  `timeline_insert_clip {text: {content, font_size, color, align}}` on an
  overlay track (auto-created `OV1`, `timeline.py:151-158`; schema
  `_schema.py:1232-1242`). Do NOT attempt `apply_template` against the
  project timeline — there is no such timeline verb.
- `template_hint` is still recorded per beat (validated against
  `TEMPLATES`, §2.2) so the lumenframe path can honor it the day bridging
  lands, and so §7's new templates have a consumer contract now.

> 小结：v1 只组装到 lumerai 项目时间线（预览/导出/undo 全在那）。lumenframe
> 模板不能直接打到时间线上——v1 用 text clip 近似标题，`template_hint` 先记
> 账，桥接见 timeline-canonical-plan.md。

---

## 6. Choreography — ready-to-paste `system_v3.md` section

### 6.1 Insertion point

Insert the block below into `gemia/prompts/system_v3.md` immediately BEFORE
the `---` separator that precedes `## Creative coding paths` (currently line
200; "Things to know about the environment" ends at line 198). It becomes a
sibling H2, matching the existing section grammar (imperative, bulleted,
bold-leads — cf. `system_v3.md:70-165`).

### 6.2 Why N=4 for the checkpoint cadence (budget arithmetic)

The budget guard counts **estimated tool-execution seconds, not wall-clock**
(`budget_guard.py:141-147`), against caps `$5 / 600 s`
(`budget_guard.py:128`). Relevant ETAs: `inspect_timeline` 12 s
(`budget_guard.py:76`), `render_preview` 20 s (`:78`), `project_export` 60 s
(`:79`), `analyze_media` 4 s + $0.01 (`:24`); the per-beat verbs are 0.2–0.5 s
each (`:64-75`).

Worked example, 12-beat rough cut:

| Item | Arithmetic | Seconds |
|---|---|---|
| Per-beat loop ×12 | (search 0.5 + annotations 0.2 + probe 0.2 + insert 0.5 + title insert 0.5 + transition 0.2) ≈ 2.1 | ~25 |
| `outline_write` ×4 (post-approval + 3 checkpoint flushes) | 4 × 0.3 | ~1 |
| Checkpoints at beats 4, 8, 12 (N=4) | 3 × (render_preview 20 + analyze_media 4) | 72 |
| Refinement allowance | ~10 targeted ops + 1 inspect_timeline + 1 render_preview | ~35 |
| Final `project_export` | | 60 |
| **Total** | | **~193 of 600 s**, ~$0.05 of $5 |

- **N=2** doubles checkpoint spend to 144 s and crowds out refinement head-room
  on 20+ beat outlines.
- **N=6+** saves one checkpoint but lets a bad match go undetected for up to
  5 further beats; the targeted fixes then cost more than the checkpoint
  saved (and violate "Iterate from observation", `system_v3.md:71-74`).
- **N=4** keeps checkpoint spend ≈ 12 % of the time budget with worst-case
  discovery latency of 3 beats. **Locked: N=4, plus always one checkpoint
  after the final beat.** Mid-assembly spot checks of a single cut boundary
  should prefer `inspect_timeline` (12 s, returns frames the model can see,
  `_schema.py:1365-1382`) over `render_preview`+`analyze_media` (24 s).

### 6.3 The section text (paste verbatim)

```markdown
## Rough cut from an outline

When the user hands you a script/大纲, or asks you to assemble existing
footage into a first cut, work outline-first:

- **Draft the outline before touching the timeline.** Build an outline
  document — beats with `id` ("b01", "b02", …), `narration_text`,
  `visual_query`, a `duration_policy`, an optional `template_hint`, `status:
  "draft"`, `chosen_clip: null`, `matched_evidence: null`, `notes` — plus
  `title`, `target_duration_sec`, `canvas`, `version: 1`. Duration policies:
  `{kind:"fixed_sec",seconds:N}` for exact lengths;
  `{kind:"narration_estimated"}` when narration text should set the pace
  (estimate ≈ Chinese chars ÷ 4 + other chars ÷ 15 + 0.6 s, kept within
  2–20 s); `{kind:"source_length"}` to keep a chosen clip's natural length.
- **In plan mode, the outline IS your plan.** Present it as fenced JSON in
  your plan text; `outline_write` is blocked while planning. The moment the
  user approves and plan mode turns off, persist the approved outline
  verbatim with `outline_write` before any timeline edit. Outside plan mode,
  show the outline and `elicit` approval first when it has more than 5 beats
  or implies paid generation; otherwise proceed and say so.
- **Assemble beat by beat, in order.** For each beat:
  1. Find footage: `search_library(visual_query)`; read
     `get_media_annotations` on the best candidate to pick an exact range
     (markers/cut candidates). Record it in the beat's `chosen_clip`
     (`{asset_id, in_sec, out_sec}`) and set `status: "matched"`.
  2. Verify physics with `probe_media` (exact duration/fps) and clamp the
     range before cutting.
  3. Place it: `timeline_insert_clip` appending to V1 with
     `source_in`/`source_out` per the beat's duration policy; correct with
     `timeline_trim_clip`/`timeline_set_clip_time` only if needed. Note the
     returned `clip_id` in the beat's `notes` ("clip_id=clip_ab12cd34") and
     set `status: "placed"`.
  4. Transition: default is a plain cut; add `timeline_add_transition`
     (e.g. dissolve 0.5) only where the outline's notes ask for one.
  5. Title: when the beat has a `template_hint` or opening/section text,
     insert a text clip (`timeline_insert_clip` with `text:{content,...}`)
     on the overlay track at the clip's start, duration ≤ min(3s, clip
     length). Keep `template_hint` recorded — richer template rendering
     arrives with the layer-document bridge later.
- **Checkpoint every 4 placed beats, and after the last beat.** Call
  `render_preview`, look at it with `analyze_media` (or `inspect_timeline`
  on one boundary frame for a quick spot check), then flush beat statuses
  with ONE `outline_write`. Fix problems with targeted verbs on the
  offending beat's clip — `timeline_trim_clip`, `timeline_set_clip_time`,
  `timeline_move_clip`, or `timeline_undo` for the last bad step.
- **Refine per beat — never rebuild.** Locate the clip via the beat's
  recorded clip_id (fall back to `get_timeline`), change only what the
  feedback names, set that beat's `status: "refined"`. Deleting everything
  and re-inserting is a failure of the turn; reordering is
  `timeline_move_clip` with `ripple`, not re-assembly.
- **No narration audio yet.** Speech synthesis is not available in this
  build: `narration_text` drives beat timing and on-screen text only. Say so
  if the user asks for voice-over, and keep the text in the outline so it
  can be voiced later.
- **Resume from disk.** On a fresh turn, `outline_get` tells you exactly
  which beats are placed/refined; continue from the first non-`refined`
  beat instead of re-planning.
```

> 小结：编排 = 计划期起草（plan mode 内以文本呈现，批准后第一步落盘）→
> 逐 beat：search_library+annotations → probe → insert/trim → transition →
> text-clip 标题 → 每 4 个 beat 一次 render_preview 检查点 + 一次
> outline_write 刷状态 → 针对单 beat 精修，禁止推倒重来。N=4 的预算账见 §6.2。

---

## 7. New lumenframe templates: `chapter_title`, `outro`

Pure functions `(**params) -> list[op]`, exactly the `intro.py` pattern
(`lumenframe/templates/intro.py:13-24`): keyword-only params after the leading
text arg, `start`/`duration` in seconds, `prefix` for id-collision freedom,
`animate` flag, ops restricted to the already-validated vocabulary
`add_layer(solid|text|shape)` + `set_transform` + `animate_text` presets
`pop`/`rise` (the only presets templates use today — `intro.py:67-73`,
`lower_third.py:78-84`).

### 7.1 `lumenframe/templates/chapter_title.py`

```python
"""``chapter_title`` template — a section divider card with an optional kicker.

Expands to a full-canvas ``solid`` background, an optional small kicker line
("CHAPTER 3") above centre, and a centred ``text`` title that pops in. Same
structure as ``intro`` but tuned for mid-video section breaks (shorter default
duration, kicker slot for the chapter index).
"""
from __future__ import annotations

from typing import Any


def chapter_title(
    title: str = "Chapter",
    *,
    index: int | None = None,
    subtitle: str | None = None,
    start: float = 0.0,
    duration: float = 2.5,
    background: str = "#0b0d10",
    title_color: str = "#ffffff",
    kicker_color: str = "#a8b2bb",
    font_size: int = 84,
    prefix: str = "chapter",
    animate: bool = True,
) -> list[dict[str, Any]]:
    """Build the op list for a chapter/section divider card.

    Args:
        title: the section headline.
        index: optional chapter number; when set, a small "CHAPTER <n>" kicker
            line renders above the title.
        subtitle: optional second line under the title.
        start / duration: timeline placement (seconds).
        background: full-canvas solid fill (hex).
        title_color / kicker_color: text colours (hex).
        font_size: title point size (kicker renders at ~1/3 of it).
        prefix: id prefix so two chapter cards never collide.
        animate: when True the title pops in (animate_text pop).
    """
    bg_id = f"{prefix}_bg"
    kicker_id = f"{prefix}_kicker"
    title_id = f"{prefix}_title"

    ops: list[dict[str, Any]] = [
        {
            "op": "add_layer",
            "type": "solid",
            "id": bg_id,
            "name": "Chapter Background",
            "at_time": float(start),
            "duration": float(duration),
            "color": background,
        },
    ]
    if index is not None:
        ops.append({
            "op": "add_layer",
            "type": "text",
            "id": kicker_id,
            "name": "Chapter Kicker",
            "at_time": float(start),
            "duration": float(duration),
            "text": f"CHAPTER {int(index)}",
            "color": kicker_color,
            "font_size": max(int(font_size) // 3, 24),
            "align": "center",
        })
        ops.append({"op": "set_transform", "layer_id": kicker_id, "y": -140.0})
    ops.append({
        "op": "add_layer",
        "type": "text",
        "id": title_id,
        "name": "Chapter Title",
        "at_time": float(start),
        "duration": float(duration),
        "text": title if not subtitle else f"{title}\n{subtitle}",
        "color": title_color,
        "font_size": int(font_size),
        "align": "center",
    })
    if animate:
        ops.append({
            "op": "animate_text",
            "layer_id": title_id,
            "preset": "pop",
            "duration": min(0.6, float(duration)),
        })
    return ops
```

### 7.2 `lumenframe/templates/outro.py`

```python
"""``outro`` template — a closing card with an optional call-to-action line.

Expands to a full-canvas ``solid`` background and a centred ``text`` block
(title plus optional CTA subtitle) that rises in. The end-of-video sibling of
``intro``; longer default duration so end-screen text stays readable.
"""
from __future__ import annotations

from typing import Any


def outro(
    title: str = "Thanks for watching",
    *,
    subtitle: str | None = None,
    start: float = 0.0,
    duration: float = 4.0,
    background: str = "#0b0d10",
    title_color: str = "#ffffff",
    font_size: int = 72,
    prefix: str = "outro",
    animate: bool = True,
) -> list[dict[str, Any]]:
    """Build the op list for a closing/outro card.

    Args:
        title: the closing headline.
        subtitle: optional call-to-action line under the title
            (e.g. "Subscribe for more").
        start / duration: timeline placement (seconds).
        background: full-canvas solid fill (hex).
        title_color: text colour (hex).
        font_size: title point size.
        prefix: id prefix so two outros never collide.
        animate: when True the text rises + fades in (animate_text rise).
    """
    bg_id = f"{prefix}_bg"
    title_id = f"{prefix}_title"

    ops: list[dict[str, Any]] = [
        {
            "op": "add_layer",
            "type": "solid",
            "id": bg_id,
            "name": "Outro Background",
            "at_time": float(start),
            "duration": float(duration),
            "color": background,
        },
        {
            "op": "add_layer",
            "type": "text",
            "id": title_id,
            "name": "Outro Title",
            "at_time": float(start),
            "duration": float(duration),
            "text": title if not subtitle else f"{title}\n{subtitle}",
            "color": title_color,
            "font_size": int(font_size),
            "align": "center",
        },
    ]
    if animate:
        ops.append({
            "op": "animate_text",
            "layer_id": title_id,
            "preset": "rise",
            "duration": min(0.6, float(duration)),
        })
    return ops
```

### 7.3 Registration (`lumenframe/templates/__init__.py`)

Extend the existing import block and registry (`__init__.py:26-36`) and
`__all__` (`:54`):

```python
from lumenframe.templates.chapter_title import chapter_title
from lumenframe.templates.outro import outro

TEMPLATES: dict[str, Template] = {
    "lower_third": lower_third,
    "intro": intro,
    "chapter_title": chapter_title,
    "outro": outro,
}
```

`apply_template` and the catalogue pick both up automatically
(`__init__.py:10-12`). Note: any test asserting the registry lists exactly two
templates (cf. `tests/test_lumenframe_presets.py:285`
`test_registry_lists_both_templates`) must be updated in the same commit.

> 小结：新增 `chapter_title`（可带 "CHAPTER n" kicker，pop 入场）与 `outro`
> （收尾卡 + CTA 副标题，rise 入场），严格复刻 intro.py 的纯函数模式，只用
> 既有 op 词汇；注册进 TEMPLATES 后 apply_template 自动可用。

---

## 8. ADR: thin persistence verbs + prompt choreography, NOT a host-side `assemble_from_outline`

**Decision.** v1 ships exactly two thin verbs (§3.2) and a system-prompt
section (§6). There is deliberately **no** host-side macro verb that takes an
outline and performs search→insert→trim→transition server-side.

**Alternatives considered.**

1. **Monolithic `assemble_from_outline(outline)` host verb.** Rejected:
   - It inverts the architecture's single stated control principle: "There is
     no capability gate, no stability gate, no approval stub. **The model
     holds the wheel; the host only reports real money and real time**"
     (`gemia/budget_guard.py:5-9`). A host assembler makes creative decisions
     (which candidate clip, where to cut) inside host code the model can
     neither observe nor veto mid-flight.
   - It breaks per-step auditability. The timeline design contract is one
     verb = one auditable, undoable patch surfaced as a `timeline_op` SSE
     event, explicitly "No big apply-patch(json) black box"
     (`gemia/tools/timeline.py:5-9`); a macro verb is exactly that black box,
     and `timeline_undo`'s step granularity (`timeline.py:330-341`) becomes
     meaningless ("undo one step" = undo the whole cut?).
   - Failure recovery degrades from adapt-per-beat (the loop's whole design:
     "take an action, see what it actually produced, decide the next move",
     `gemia/prompts/system_v3.md:4-6`; error `recovery` protocol at
     `:81-93`) to abort-or-retry-the-batch.
   - It would hard-code v1's keyword matching into host API surface that the
     semantic-search plan (`docs/semantic-search-media-plan.md`) would then
     have to break.
2. **No verbs at all — outline lives in turn text / `remember` memory.**
   Rejected: turn text does not survive session resumption with structure; the
   memory store is cross-session user facts, not per-project working state
   (`system_v3.md:222-229`); nothing machine-checkable for the offline test
   gate (§10); no stable artifact a future outline UI can render.
3. **Outline embedded in the project `state.json` via new patch ops.**
   Rejected in §3.1 (undo/audit pollution, patch-vocabulary creep).

**Consequences.** The model pays a few extra prompt tokens per beat for
narration/tool round-trips — bounded and measured in §6.2 (~193 s of the 600 s
cap for 12 beats). In exchange every insert/trim stays individually visible in
the UI event stream, undoable, and correctable the moment a checkpoint reveals
a bad match.

> 小结：拒绝宿主端一键组装宏动词——它违背 budget_guard 的「model holds the
> wheel」原则、砸掉逐 patch 审计/undo、把匹配逻辑焊死在宿主 API 里。瘦动词 +
> 提示词编排：每步可见、可撤销、可中途纠偏，预算账证明成本可控。

---

## 9. Delegation plan (codex-class implementers) and parity checklist

### 9.1 Work packages — each is one commit with its test gate green

**D1 — IR + verbs + wiring (backend only).**
Files: new `gemia/outline_ir.py` (validator §2.2 + `estimate_beat_seconds`
§2.3 + `OUTLINE_FILENAME = "outline.json"`); new `gemia/tools/outline.py`
(`dispatch_write`/`dispatch_get` per §3.2, `_project` guard copied from
`timeline.py:32-37`, atomic write copied from `project_store.py:257-262`);
edits to `gemia/tools/_schema.py` (§3.3), `gemia/tools/__init__.py` (§3.4),
`gemia/budget_guard.py` (§3.4), `gemia/plan_mode.py` (§4 — both frozensets
AND the tool list inside `PLAN_MODE_PROMPT`, `plan_mode.py:110-114`, which
gains `outline_get`).
Test gate:
- new `tests/test_outline_ir.py`: happy path; unknown top-level/beat key
  rejected; duplicate beat id rejected; bad `template_hint` rejected; status
  enum enforced; estimation pinned — assert
  `estimate_beat_seconds("今年发布会的三个关键词：更快、更稳、更便宜。")`
  equals `clamp(20/4.0 + 0/15.0 + 0.6, 2, 20) = 5.6` (20 CJK chars; CJK
  punctuation `：、。` is outside U+4E00–U+9FFF and non-whitespace, so it
  counts at the en rate — the test must pin whichever total the formula
  yields, computed once by hand); clamp floor (1 char → 2.0) and ceiling
  (very long text → 20.0).
- new `tests/test_outline_verbs.py`: write→get roundtrip on a real
  `AgentLoopV3` project via the `_loop` harness pattern
  (`tests/test_timeline_direct_edit.py:50-58`); `outline_get` on empty project
  returns `exists=false`; `updated_at` host-stamped; digest counts correct;
  file written atomically under `store.project_dir(...)`.
- `pytest tests/test_plan_mode.py` stays green — this is the exact-coverage
  tripwire (`test_plan_mode.py:41-49`); if it reds, classification was
  forgotten.

**D2 — templates.**
Files: new `lumenframe/templates/chapter_title.py`, `outro.py` (§7.1–7.2);
edit `lumenframe/templates/__init__.py` (§7.3).
Test gate: extend `tests/test_lumenframe_presets.py` following its existing
patterns (`:208-285`): each new template yields the expected layer types,
renders without error, can be applied twice without id clash (distinct
`prefix`), registry lists all four; update the registry-count assertion.

**D3 — prompt section.**
File: `gemia/prompts/system_v3.md` — paste §6.3 verbatim at the §6.1 anchor.
Test gate: no automated prompt test exists today; the gate is (a) the pasted
block is byte-identical to §6.3, (b) `pytest tests/` full run stays green
(the prompt is loaded/templated at runtime; a malformed edit shows up in
loop tests).

**D4 — offline assembly fixture (the end-to-end gate, no Gemini, no network).**
New `tests/test_outline_assembly.py`: build a `ToolContext` +
`ProjectHandle.open(tmp_path, ...)`; synthesize 3 tiny media files (ffmpeg
`-f lavfi -i testsrc=duration=8:size=320x180:rate=30` style, or reuse an
existing fixture generator if one exists in `tests/`), register via
`ctx.registry.add_external`; author a 4-beat outline (one
`narration_estimated`, one `fixed_sec` 6.0, one `source_length` with
`chosen_clip`, one title-only beat with `template_hint: "outro"`); then drive
the §6.3 choreography **as direct dispatcher calls in the test** (the test
plays the model's role) and assert: `outline_write` digest; V1 clip order ==
beat order; per-clip durations equal the policy computations (6.0 exactly;
the pinned estimate from D1; `out-in` for source_length); overlay text clip
placed at its beat's clip start; one transition where requested; patch log
labels contain `timeline_insert_clip` (via `get_timeline`'s `history` arg,
`timeline.py:69-71`); final `outline_get` shows all beats `placed`.
This is the fixture the parent asked for: mock assets → outline → assembled
timeline asserting clip order/durations.

Suggested order D1 → D2 → (D3, D4 in parallel). D3/D4 depend on D1; D4's
outro beat depends on D2 only for `template_hint` validation.

### 9.2 Parity checklist (per `docs/protocol-parity-plan.md` rules)

- **No new SSE event kinds.** `outline_*` results ride the existing
  `tool_exec_*` events, so the Phase-1 contract (`EVENT_KINDS`) is untouched.
  This is a tool-surface change, not a protocol change — but parity rule 1
  (三件套同 commit) still applies in spirit: both test suites run in the D1
  commit.
- **Web generic fallback — verified present.** `static/v3/v3.js`
  `renderToolCall` renders any tool generically: raw `tc.tool_name` in the
  card head (`v3.js:269`), JSON-pretty args (`:254-256`), summary/error/
  progress blocks (`:257-277`). Unknown verbs need zero web changes.
- **CLI generic fallback — verified present.** `src/App.js` handles
  `model_tool_call_start` generically (`ev.tool_name || "tool"`,
  `App.js:151-166`; likewise `tool_exec_start/progress/result/error`,
  `:178-209`), and `src/components/ToolCall.js:96` renders
  `toolLabel(call.tool_name)` where `toolLabel` "falls back to the raw tool
  name for anything unmapped — so unknown/future verbs always show something
  sensible" (`src/format.js:64-67, 88-91`). Unknown verbs need zero CLI
  changes.
- **Optional polish, non-blocking:** add `outline_write`/`outline_get`
  entries to the CLI `TOOL_LABELS` map (`format.js:68-86`) and any web
  equivalent later; parity plan Phase 2 explicitly treats label maps as
  warn-only drift.
- **Acceptance is local, both ends** (parity plan 规则 2): backend
  `python3 -m pytest tests/test_outline_*.py tests/test_plan_mode.py
  tests/test_lumenframe_presets.py` and CLI `cd ~/Code/lumeri-cli && npm test`
  both green in the D1 commit's verification note.

> 小结：四个工作包各带测试闸门（IR 校验、verbs 往返、模板、离线组装端到端），
> D1 的 plan-mode 精确覆盖测试是防漏登记的绊线。v1 无新事件 kind、双端通用
> fallback 均已核实存在（v3.js:269 / ToolCall.js:96+format.js:88-91），前端零改动。

---

## 10. Acceptance criteria (v1 done means)

1. `outline_write`/`outline_get` in `TOOL_NAMES`, dispatched, budgeted,
   plan-classified; `pytest tests/test_plan_mode.py` green (exact coverage).
2. `tests/test_outline_ir.py` + `tests/test_outline_verbs.py` +
   `tests/test_outline_assembly.py` green offline (no network, no Gemini).
3. `chapter_title` + `outro` registered; `tests/test_lumenframe_presets.py`
   green including updated registry assertions.
4. `system_v3.md` contains the §6.3 section verbatim at the §6.1 anchor.
5. CLI `npm test` green, zero CLI code changes required (fallback verified).
6. A manual smoke (post-merge, live session): draft outline in plan mode →
   approve → agent persists outline, assembles ≥ 4 beats with a checkpoint at
   beat 4, `outline_get` shows statuses advancing — within the $5/600 s caps.

## 11. Reference index (all citations)

- `gemia/tools/_schema.py:29-46` (`_tool` helper), `:1224-1261`
  (`timeline_insert_clip` media+text), `:1313-1322` (transition kinds),
  `:1365-1403` (inspect/preview/export), `:1542-1573` (`elicit`), `:1577`
  (`TOOL_NAMES`).
- `gemia/tools/__init__.py:82-88, 91-187` (dispatch table, stub-on-missing).
- `gemia/tools/timeline.py:5-9, 29, 32-37, 44-51, 69-71, 77-207, 330-341,
  347-419, 495-499`.
- `gemia/tools/_context.py:8-10, 157-169` (`ToolContext`, `ctx.project`).
- `gemia/project_store.py:40-42, 79, 257-262, 265-333` (`ProjectStore`,
  atomic write, `ProjectHandle`).
- `gemia/plan_mode.py:3-23, 28-74, 88-97, 103-126` (fail-closed contract,
  sets, gate message, prompt block); `tests/test_plan_mode.py:41-49`.
- `gemia/budget_guard.py:5-9, 18-104, 128, 141-147` (philosophy, ETAs, caps,
  execution-seconds accounting).
- `gemia/prompts/system_v3.md:4-6, 70-165, 198-202, 222-229`.
- `lumenframe/templates/__init__.py:1-12, 26-36, 54`; `intro.py:13-74`;
  `lower_third.py:15-85`; `tests/test_lumenframe_presets.py:208-285`.
- `gemia/audio/ai_speech.py` (`_simulate_lyria_api_call`, dummy bytes);
  `gemia/video/speech_generator.py` (raises on `dry_run=False`).
- `gemia/tools/search_library.py:3-6, 31-33`.
- `gemia/agent_loop_v3.py:1026-1063` (plan gate), `:1081` (budget check).
- Web: `static/v3/v3.js:250-279` (generic tool card, raw `tool_name` at 269).
- CLI: `src/App.js:151-209`, `src/components/ToolCall.js:96`,
  `src/format.js:64-91`.
- `tests/test_timeline_direct_edit.py:50-58` (loop harness pattern).
- `docs/protocol-parity-plan.md` (同 commit 规则、本地双端测试验收)。
