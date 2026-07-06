# Semantic search_media v1 — Gemini-Vision Annotation Indexing + FTS Retrieval

状态: 已定稿，待实施
制定日期: 2026-07-06
适用范围: `/Volumes/Extreme SSD/gemia`（后端 + web v3）与 `~/Code/lumeri-cli`（TUI 客户端）
前置阅读: `docs/protocol-parity-plan.md`（协议变更三件套同 commit 规则，本文遵循其规则 1–5）

Every claim below is grounded in code read at HEAD (`3b28add feat(v3): media-library
annotations` for the annotation layer) plus read-only `.venv/bin/python` checks run on
2026-07-06. Where a decision is made, the decision, the alternatives, and the rationale
are stated explicitly. This doc is written to be executed by a weaker coding agent
without room for interpretation.

---

## 0. Problem / current state（现状与问题）

What exists today:

- **Per-account media library**: `gemia/media_library.py` — SQLite at
  `<account>/media/library.sqlite3` (`library_path`, media_library.py:39-40), assets keyed
  by content fingerprint (`asset_id = f"asset_{fingerprint[:24]}"`, media_library.py:67),
  8 timeline thumbnails per asset (media_library.py:95), 512-sample waveform peaks
  (media_library.py:96). `media_annotations` lives **in the same sqlite file** — its
  `_connect` also opens `library_path(account_id)` (media_annotations.py:269). This means
  one SQL query can join annotations ↔ assets. v1 relies on this.
- **Annotation storage**: `gemia/media_annotations.py` — table `media_annotations`
  (schema media_annotations.py:282-309): scope ∈ {asset, time_range, frame}
  (media_annotations.py:14), `start_sec`/`end_sec`, `label` ≤200ch
  (media_annotations.py:363), `note` ≤5000ch (media_annotations.py:364), `tags_json`,
  `category` ≤80ch, `confidence` clamped 0..1 (media_annotations.py:353-355), `source` ∈
  {gemini, user, import, system} (media_annotations.py:15), `language`, `metadata_json`.
  `upsert_annotations(replace_source=...)` (media_annotations.py:98-109) is already built
  for idempotent re-indexing: delete-by-source then insert.
- **The "annotator" is blind**: `annotate_asset_heuristic`
  (media_annotations.py:180-245) writes evenly-spaced "Segment N / 片段 N" markers
  without ever looking at pixels, yet stamps them `source: "gemini"`
  (media_annotations.py:207, :229) at confidence 0.55/0.45 (media_annotations.py:206,
  :228). Only `metadata.strategy: "local_heuristic"` (media_annotations.py:209, :231)
  admits the truth. The `annotate_media` tool dispatch (tools/media_annotations.py:54)
  calls exactly this.
- **Search is lexical, asset-level, and N+1**: `search_library`
  (tools/search_library.py) scores by token-substring containment
  (tools/search_library.py:31-39) and returns asset-level hits only — no time ranges.
  Its library path goes through `list_assets(q=...)` (media_library.py:176-198), which
  calls `search_annotation_text` **once per asset** (media_library.py:180-183 →
  media_annotations.py:248-263): the N+1 substring path.
- **An unwired frame-stat indexer exists**: `gemia/video/intellisearch.py` +
  `intellisearch_features.py`. Only `gemia/__main__.py:21` (CLI subcommands) and a
  registry string reference it — nothing in the agent loop uses it. Its reusable part is
  the cv2 frame-diff/motion probe (`_probe_video`, intellisearch_features.py:32-90;
  consecutive-frame gray abs-diff at :53-56; even-spread `_sample_indexes` at :169-175).
- **Budget entry is dishonest-in-waiting**: `budget_guard.py:43` prices
  `annotate_media` at `$0.00 / 8s`. Fine while the annotator is a local heuristic; a lie
  the moment it calls Gemini vision.
- **Model I/O plumbing that already works**: the agent loop attaches images to the
  next user message as base64 `image_url` data URLs (`_thumbnail_user_content`,
  agent_loop_v3.py:206-222) over the OpenAI-compat chat-completions endpoint
  (gemini_client.py:422 for `gemini`, :409-411 for `vertex`). There is **no Files API**;
  base64 inline is the only image channel, and it is proven.

**中文小结**：素材库和标注表都已就位（同一个 sqlite 文件），`upsert_annotations` 天生支持
幂等重建；但现在的"标注"是不看画面的占位符还冒充 `source='gemini'`，检索是逐资产 N+1 子串
匹配、只到资产级。本文要做的：真·Gemini 视觉标注（诚实署名）+ FTS5 中英检索（到时间段级）+
预算诚实 + 双端同批交付。

---

## 1. Decisions at a glance（决策总览）

| # | Decision | One-line rationale |
|---|----------|--------------------|
| D1 | New annotation sources `gemini_vision` and `heuristic` added to `_VALID_SOURCE`; heuristic stops writing `source="gemini"` | `_source()` silently coerces unknown sources to `"user"` (media_annotations.py:424-426) — `replace_source="gemini_vision"` today would **delete the user's own annotations** (media_annotations.py:170-177). Must extend the enum first. |
| D2 | Vision output is **bilingual at index time** (en + zh fields per segment) | FTS matches literal text; no embeddings, no query-time translation call allowed (§4, §5). |
| D3 | Scene-change-biased sampling: coarse cv2 diff pass → k = clamp(⌈duration/150⌉, 6, 12) frames, detailed 2× (12–24), 512px JPEG, base64 inline | Reuses the proven intellisearch diff signal; bounded request size; arithmetic in §3. |
| D4 | Retrieval = SQLite **FTS5, external-content table, `unicode61` tokenizer + CJK char-bigram ingest normalization** (NOT trigram) | Trigram is available in the shipped sqlite 3.50.4 but **fails 2-char CJK queries** (verified: `MATCH '日落'` → 0 rows). Bigram normalization passes all verified cases. §4.1. |
| D5 | New tool `search_media` (time-range hits, session-registered); `search_library` kept unchanged; `list_assets(q=)` N+1 replaced by one FTS call | Additive protocol change; asset-level preflight semantics of `search_library` preserved. |
| D6 | No embeddings in v1 (ADR §5) | No GPU, per-query cost, tiny per-account corpora; orchestrator re-ranks FTS top-k for free. |
| D7 | Vision runs only when resolved provider ∈ {`gemini`, `vertex`}; otherwise honest heuristic fallback (`source="heuristic"`, `metadata.degraded_reason`) | Both providers are Gemini API surfaces with the verified base64 image path; claude/openrouter/openai are not budgeted or verified for this. |
| D8 | Lazy indexing, model-driven; **no auto-spend**: import never annotates, `search_media` never annotates — it reports `unindexed_count` and the model decides | Matches budget-guard philosophy: "The model holds the wheel; the host only reports" (budget_guard.py:5-9). |
| D9 | Sync `annotate_media` vision ≤ 4 assets/call; larger batches via `JobRegistry` async job `kind="annotate"`; budget entry `$0.12 / 45s`; loop honors `result["actual_usd"]` at commit | Keeps the static gate honest; per-batch真实花费在 commit 时入账 (§3.3, §7.3). |
| D10 | Audio/ASR is **day-2**, out of scope | whisper/faster_whisper NOT installed (verified); librosa IS installed — energy/beat only, no transcription (§8.2). |

---

## 2. Vision annotation contract（视觉标注契约）

### 2.1 Source-enum fix first (blocking prerequisite)

`media_annotations.py:15` becomes:

```python
_VALID_SOURCE = {"gemini", "user", "import", "system", "gemini_vision", "heuristic"}
```

Semantics, locked:

- `gemini_vision` — written **only** by the vision indexing pass (§2.3). Replaced
  wholesale on re-index via `upsert_annotations(..., replace_source="gemini_vision")`.
- `heuristic` — written **only** by `annotate_asset_heuristic`. Change
  media_annotations.py:207 and :229 from `"source": "gemini"` to `"source": "heuristic"`,
  and the `replace_source="gemini"` at media_annotations.py:238 to
  `replace_source="heuristic"`. Keep `metadata.strategy: "local_heuristic"` — it was
  always honest; the source field now matches it.
- `gemini` — reserved for annotations the **orchestrator model** writes explicitly via
  `write_media_annotation` (tools/media_annotations.py:106 hardcodes it; unchanged).
- `user` / `import` / `system` — unchanged.

Why this ordering matters (the landmine, spelled out): `delete_source_annotations`
routes its argument through `_source()` (media_annotations.py:171 → :424-426), which
maps any unknown value to `"user"`. Calling
`upsert_annotations(replace_source="gemini_vision")` **before** extending the enum would
silently execute `DELETE ... WHERE source = 'user'`. The enum extension must land in the
same commit as (or before) any caller that passes the new source strings, and the test
suite must pin this (§8.1, T3).

**Legacy-row migration** (one-time, per asset, at vision-index time): rows written by
the old heuristic carry `source='gemini'` AND `metadata.strategy='local_heuristic'`.
Before upserting vision annotations for an asset, the indexer runs a targeted delete:

```sql
DELETE FROM media_annotations
WHERE asset_id = :asset_id AND source = 'gemini'
  AND json_extract(metadata_json, '$.strategy') = 'local_heuristic'
```

This never touches genuine orchestrator-written `source='gemini'` rows (those carry no
`strategy` key, see tools/media_annotations.py:95-108). No global migration script — the
cleanup rides along lazily with re-indexing.

### 2.2 Exact JSON the Gemini pass must emit (per asset)

The vision call returns **strict JSON, no markdown fence**, matching:

```json
{
  "asset": {
    "caption_en": "string, one factual sentence, <=160 chars",
    "caption_zh": "string, 简体中文一句话, <=80 字",
    "subjects": ["lowercase english noun lemmas, e.g. woman, dog, car"],
    "actions": ["lowercase english verb lemmas, e.g. running, cooking"],
    "tags_zh": ["2-6 个简体中文关键词, e.g. 日落, 海边, 航拍"],
    "setting": "short english phrase, e.g. beach at dusk / indoor kitchen",
    "quality_flags": ["subset of the fixed vocabulary below"],
    "confidence": 0.85
  },
  "segments": [
    {
      "start_sec": 0.0,
      "end_sec": 14.5,
      "caption_en": "string, <=160 chars",
      "caption_zh": "string, <=80 字",
      "subjects": ["..."],
      "actions": ["..."],
      "tags_zh": ["..."],
      "on_screen_text": "verbatim visible text in its original language, else empty string",
      "quality_flags": ["..."],
      "confidence": 0.8
    }
  ]
}
```

Fixed `quality_flags` vocabulary (closed set; anything else is dropped by the parser):
`["blurry","shaky","overexposed","underexposed","low_light","noisy","banding","letterboxed","duplicate","slate","black_frame"]`.

Parser rules (deterministic, implement exactly):

1. Strip a leading/trailing ``` or ```json fence if present, then `json.loads`.
2. On parse failure: retry the request **once** with the extra line
   `"Previous reply was not valid JSON. Reply with ONLY the JSON object."` appended to
   the text part. On second failure: mark the asset failed in `media_index_state`
   (§7.1, `status='error'`), write nothing to `media_annotations`, continue the batch.
3. Clamp every segment: `start_sec = max(0, start_sec)`,
   `end_sec = min(duration, end_sec)`; drop segments where `end_sec - start_sec < 0.25`.
   (`_normalize_payload` re-clamps anyway, media_annotations.py:334-344 — the dispatcher
   pre-drop exists so degenerate segments don't become zero-length markers.)
4. Sort segments by `start_sec`. Cap at 32 segments/asset (excess dropped from the end).
5. Unknown JSON keys are ignored; missing optional arrays default to `[]`.

### 2.3 Mapping into `upsert_annotations`

One call per asset:

```python
upsert_annotations(account_id, asset_id, rows, replace_source="gemini_vision")
```

Row construction (field → storage; storage limits per media_annotations.py:356-371):

| JSON | annotation row |
|---|---|
| `asset` object | one row: `scope="asset"`, `label=caption_en[:200]`, `note=f"{caption_en}\n{caption_zh}\nsetting: {setting}"`, `tags=subjects+actions+tags_zh+quality_flags`, `category="summary"`, `confidence`, `source="gemini_vision"`, `language="multi"` |
| each `segments[i]` | one row: `scope="time_range"`, `start_sec`, `end_sec`, `label=caption_en[:200]`, `note=caption_zh + ("\nOSD: "+on_screen_text if on_screen_text else "")`, `tags=subjects+actions+tags_zh+quality_flags`, `category="segment"`, `confidence`, `source="gemini_vision"`, `language="multi"` |

`metadata` (every row, honesty contract):

```json
{
  "strategy": "gemini_vision_v1",
  "prompt_version": 1,
  "model": "<GeminiClientV3.model as resolved>",
  "provider": "<gemini|vertex>",
  "frames_used": 12,
  "frame_timestamps": [0.5, 12.0, ...],
  "fingerprint": "<asset sha256>"
}
```

`metadata.strategy` MUST equal `"gemini_vision_v1"` only when pixels were actually sent
to the model. The degraded path (§6) writes `"local_heuristic"` + `degraded_reason`.
This is the fix for the current dishonesty: strategy, source, and confidence now all
tell the same story.

### 2.4 Bilingual policy — decision D2

**Decision**: emit both English and Simplified-Chinese text at index time
(`caption_en`+`caption_zh`+`tags_zh`; subjects/actions stay canonical English).

Alternatives considered:

- *(a) Single auto-detected language* — rejected: FTS matches literal strings. A zh
  query (`日落`) against an en-only caption ("sunset") returns nothing, and vice versa.
  The user base is CJK-first; stock footage names/captions skew English.
- *(b) Query-time translation* — rejected: adds a paid model call per query, violating
  the locked retrieval rule "orchestrator re-ranks FTS top-k itself, no extra paid call"
  (§4.4), and adds latency to a tool that must stay `$0.00 / 0.5s`-class.
- *(c) Embeddings for cross-lingual recall* — rejected in the ADR (§5).

Cost of D2: roughly +40% output tokens per asset ≈ +$0.01 (see §3.3) — one-time per
asset, versus per-query for alternative (b). ja/ko are NOT emitted in v1 (revisit
trigger in §5).

### 2.5 Exact prompt skeleton

One user message: a single `text` part followed by k `image_url` parts (base64 JPEG data
URLs), sent through `GeminiClientV3.stream_turn` with `tools=None`,
`temperature=0.1` (explicit override; see gemini_client.py:496-501 — `None` would fall
back to the orchestration temperature), text deltas accumulated to a string. Image parts
use exactly the shape at agent_loop_v3.py:216-220.

```text
You are a video librarian indexing one asset for later text search.
Asset: name="{name}", kind={media_kind}, duration={duration:.1f}s, {width}x{height}.
You will see {k} frames sampled from it, in chronological order.
Frame timestamps in seconds: {[t_1, ..., t_k]}.

Return STRICT JSON only — no markdown fence, no commentary — matching this schema:
{the JSON schema block from §2.2, verbatim}

Rules:
- Group visually continuous frames into segments; a segment's start_sec/end_sec must lie
  within [0, {duration:.1f}]. Anchor boundaries at frame timestamps and extend each
  segment halfway toward its neighbors.
- caption_en: one factual sentence, <=160 chars. caption_zh: 对应的简体中文一句话。
- subjects/actions: lowercase English lemmas. tags_zh: 2-6 个简体中文检索关键词。
- on_screen_text: transcribe visible text verbatim (keep its original language); "" if none.
- quality_flags: only from ["blurry","shaky","overexposed","underexposed","low_light",
  "noisy","banding","letterboxed","duplicate","slate","black_frame"].
- confidence: 0..1, your confidence in the segment description as a whole.
- Do not invent content you cannot see in the frames.
```

`prompt_version = 1` is a module constant next to the skeleton; bump it on any wording
change (drives re-index staleness, §7.2). For images (`media_kind == "image"`): k=1,
`duration` omitted from rules, `segments` must be `[]` (asset row only).

**中文小结**：先把 source 枚举补上（否则 `replace_source="gemini_vision"` 会被静默改写成
删用户标注——这是现行代码里最危险的一颗雷）；启发式改署 `heuristic`，视觉署
`gemini_vision`，`write_media_annotation` 继续用 `gemini`。视觉输出中英双语落库（一次付费、
终身可检索），JSON 契约、解析规则、prompt 骨架全部钉死。

---

## 3. Sampling policy + budget arithmetic（采样与预算算术）

### 3.1 Scene-change-biased frame selection

New module `gemia/media_index.py` (indexing pipeline lives here; retrieval in
`gemia/media_search.py`, §4). Selection algorithm, locked:

1. **Coarse diff pass** (cv2, in-memory, no disk writes) — reuse the exact technique of
   `intellisearch_features._probe_video` (intellisearch_features.py:32-90): sample
   `min(48, frame_count)` evenly spread frame indexes (`_sample_indexes` logic,
   intellisearch_features.py:169-175), decode, convert to grayscale, and record the
   consecutive abs-diff means (the motion signal at intellisearch_features.py:53-56).
   Downscale each frame to 128px width before diffing (the original diffs full-res;
   128px is enough for scene-change ranking and ~25× cheaper).
2. **Pick k anchors**: always include `t = min(0.5, duration/10)` and
   `t = duration - min(0.5, duration/10)` (skip-black-slate guard). Fill the remaining
   `k-2` with the highest-diff sample boundaries, greedily enforcing minimum separation
   `duration / (2k)`; if peaks run out, fill evenly.
3. **k defaults**: `k = clamp(ceil(duration_sec / 150), 6, 12)` for `mode="quick"`
   (existing schema enum, tools/_schema.py:498ff); `mode="detailed"` uses
   `k = clamp(2 * ceil(duration_sec / 150), 12, 24)`. Images: k=1. Audio: no vision pass
   (§8.2).
4. **Extraction**: per anchor `t`, ffmpeg (consistent with `analyze_media._make_thumbnail`,
   analyze_media.py:89-129, but JPEG and no pad — padding wastes image tokens):
   `ffmpeg -y -loglevel error -ss {t:.3f} -i <src> -frames:v 1 -vf "scale='min(512,iw)':-2" -q:v 4 <dst>.jpg`
   written to `asset_cache_root(account_id, asset_id)/vision/f"{prompt_version}_{int(t*1000):08d}.jpg"`
   (cache root per media_library.py:47-52) — cached across retries/re-runs of the same
   prompt_version.
5. **Unreadable-by-cv2 fallback**: if the coarse pass can't open the file (the
   `readable: False` case, intellisearch_features.py:36-38), skip step 1-2 and place k
   anchors evenly. ffmpeg extraction failures on ≥ half the anchors → asset
   `status='error'` in index state.
6. **Request packing**: ≤ 16 frames per model request. `detailed` with k>16 splits into
   two requests (frames 1..⌈k/2⌉, rest), each carrying its own timestamp list and the
   note `"This is part {i}/2 covering {t_a:.1f}s–{t_b:.1f}s"`; segment lists are
   concatenated, then §2.2 rule 3-4 applied to the union.

Why not wire `intellisearch.py` itself: its record shape is review-report/stock-catalog
flavored (intellisearch.py:133-176) and it writes JSON files, not sqlite. We reuse its
*signal* (frame diff), not its store. It stays untouched.

### 3.2 Request-size math (base64, OpenAI-compat, no Files API)

- 512px-long-edge JPEG at `-q:v 4`: 30–80 KB typical (measured range for natural video
  frames; use 80 KB for worst-case planning).
- base64 inflation ×4/3: ≤ ~110 KB per frame part.
- Default 12 frames: ≤ 1.3 MB body. Detailed 24 frames (2 requests × 12): ≤ 1.3 MB each.
  Hard cap: if a composed body would exceed **8 MB**, re-encode offending frames at
  `-q:v 7` once, then drop frames from the middle of the list until under cap. (Inline
  request limits on the generativelanguage OpenAI-compat surface are ~20 MB; 8 MB leaves
  wide margin and keeps latency sane. The same base64 mechanism is already exercised by
  `_thumbnail_user_content`, agent_loop_v3.py:206-222.)

### 3.3 Token & dollar arithmetic — the 30-minute-clip worked example

Gemini image accounting: a frame ≤ 384px in both dimensions bills 258 tokens; larger
frames bill 258 per 768×768 tile. A 512×288 frame is 1 tile ⇒ **258 tokens; budget the
2-tile worst case (516) for 512×512-ish portrait crops**. Pricing assumption for the
math below (conservative pro-tier ceiling; **re-verify against the doc-07 pricing
snapshot referenced at budget_guard.py:19-21 before landing, and adjust the constant**):
input ≤ $2.50/1M tokens, output ≤ $15/1M tokens.

Per asset, `quick` mode, 30-min clip (1800s ⇒ k = clamp(⌈1800/150⌉,6,12) = 12):

| item | tokens | cost |
|---|---|---|
| 12 frames × 258 (×2 worst case) | 3,096 – 6,192 | $0.008 – $0.015 |
| prompt text (§2.5 + schema) | ~700 | ~$0.002 |
| JSON output (≤32 segments, bilingual) | ~1,500 | ~$0.023 |
| **total / asset (quick)** | ~5.3k – 8.4k | **≈ $0.03** |
| **total / asset (detailed, 24 frames)** | ~10k – 16k | **≈ $0.06 – 0.07** |

Session-cap consequences under `$5 / 600s` (budget_guard.py:128):

- One 30-min clip: $0.03, ~10–20s wall time. Trivially fine.
- 100-asset library, quick: ≈ **$3.00** — fits the $5 cap but NOT the 600s time cap if
  done synchronously (100 × ~15s ≈ 1500s). Hence D9: sync path caps at 4 assets/call;
  bulk goes through the async job (§7.3), which returns immediately (the loop commits
  only the dispatcher's elapsed time via `actual_seconds`, agent_loop_v3.py:1164).

Budget-guard changes (all in `gemia/budget_guard.py`):

1. `"annotate_media": {"usd": 0.12, "eta_sec": 45.0}` replaces the `$0.00/8s` entry at
   budget_guard.py:43 — worst case for one **sync** call (4 assets × $0.03). The
   heuristic/degraded path costs $0.00 but the gate prices the worst case; honesty is
   restored at commit time (next item).
2. New: the agent loop passes actuals. At both commit sites
   (agent_loop_v3.py:1164 and :1207) change to
   `self.budget.commit(tc.name, actual_seconds=elapsed, actual_usd=_actual_usd(result))`
   where `_actual_usd(result)` returns `float(result["actual_usd"])` when the tool's
   **dispatcher-produced** result dict contains that key, else `None` (falls back to the
   static estimate, budget_guard.py:173-175). The value is host-computed by our own
   dispatcher, never model-supplied.
3. `annotate_media`'s dispatcher sets `actual_usd`: heuristic/degraded ⇒ `0.0`; sync
   vision ⇒ `0.03 × assets_annotated` (or usage-derived when the response carries a
   `usage` block); async submit ⇒ `0.03 × assets_queued` charged **at submit time**
   (conservative: failures over-charge, never under-charge).
4. New: `"search_media": {"usd": 0.00, "eta_sec": 0.5}` next to `search_library`
   (budget_guard.py:42).

**中文小结**：粗探（cv2 帧差，复用 intellisearch 的信号）选 6–12 帧（detailed 双倍、上限
24），512px JPEG base64 内联（无 Files API，路径已被 analyze_media 缩略图验证）。30 分钟
素材 quick 一次 ≈ $0.03；百素材全量 ≈ $3，必须走异步任务。`annotate_media` 预算条目改为
$0.12/45s，commit 按 dispatcher 报的真实花费入账——修掉现在 $0.00 的假账。

---

## 4. Retrieval design（检索设计）

### 4.1 Tokenizer decision D4 — verified, with transcript

Read-only checks run against the project venv (`.venv/bin/python`, sqlite **3.50.4**):

```
trigram tokenizer:                 available (CREATE ... tokenize='trigram' OK)
contentless (content=''):          available
trigram MATCH '日落' (2-char CJK): 0 rows   ← disqualifying
trigram MATCH 'sun' (en substr):   1 row
unicode61 + bigram normalization:  '日落'→hit, '海边日落'→hit, '海'(prefix 海*)→hit,
                                   'sun*'→hit, '猫咪 跳跃'→hit, '采访'→hit, overlap '边日'→hit
external-content FTS + triggers:   insert/delete/'delete'-command/integrity-check all OK
```

**Decision**: `tokenize='unicode61'` over an **external-content** FTS5 table, with CJK
char-bigram normalization applied in Python at ingest and query time.

Alternatives considered:

- *(a) trigram tokenizer* (the default suggestion): available, gives English substring
  matching — but FTS5 trigram cannot MATCH terms shorter than 3 code points, and
  **2-character words dominate Chinese** (日落/海边/采访/跳跃…). Verified failure above.
  A LIKE-fallback for short queries can't run against a contentless index and would
  reintroduce a second code path. Rejected.
- *(b) trigram + separate LIKE scan for short tokens* — two ranking systems, two code
  paths, mixed-language queries straddle both. Rejected for complexity.
- *(c) ICU / jieba word segmentation* — new dependency; Gemini API is deliberately the
  only external dependency. Rejected.
- Chosen bigram scheme costs English mid-word substring recall (`uns` no longer matches
  `sunset` — prefix `sun*` still does). Accepted regression; noted for reviewers.

### 4.2 Schema: index storage + sync triggers

All DDL goes into `media_annotations._ensure_schema` (media_annotations.py:282-309),
which already runs on every `_connect`; it must stay idempotent and cheap.

```sql
-- migration: add the normalized-text column (guard with PRAGMA table_info check)
ALTER TABLE media_annotations ADD COLUMN search_text TEXT NOT NULL DEFAULT '';

CREATE VIRTUAL TABLE IF NOT EXISTS media_annotations_fts USING fts5(
    search_text,
    content='media_annotations',
    tokenize='unicode61'
);
-- content_rowid defaults to the implicit rowid of media_annotations (TEXT pk keeps rowid).

CREATE TRIGGER IF NOT EXISTS media_annotations_ai AFTER INSERT ON media_annotations BEGIN
  INSERT INTO media_annotations_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
END;
CREATE TRIGGER IF NOT EXISTS media_annotations_ad AFTER DELETE ON media_annotations BEGIN
  INSERT INTO media_annotations_fts(media_annotations_fts, rowid, search_text)
  VALUES('delete', old.rowid, old.search_text);
END;
CREATE TRIGGER IF NOT EXISTS media_annotations_au AFTER UPDATE ON media_annotations BEGIN
  INSERT INTO media_annotations_fts(media_annotations_fts, rowid, search_text)
  VALUES('delete', old.rowid, old.search_text);
  INSERT INTO media_annotations_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
END;
```

Triggers (verified working above, including the `'delete'` command) mean every existing
write path — `create_annotation`, `update_annotation`, `delete_annotation`,
`delete_source_annotations` (media_annotations.py:73-177) — keeps the index in sync with
**zero call-site changes**. The only Python change: `_normalize_payload` additionally
computes `row["search_text"] = fts_normalize(" ".join([label, note, category, *tags]))`,
and the INSERT/UPDATE statements gain the `search_text` column.

Backfill (runs once inside `_ensure_schema`, guarded by
`SELECT count(*) FROM media_annotations WHERE search_text = '' AND label != ''`):
recompute `search_text` for those rows in Python, then
`INSERT INTO media_annotations_fts(media_annotations_fts) VALUES('rebuild')`.

Why external-content over contentless (`content=''`): contentless requires either
`contentless_delete=1` bookkeeping or the same 'delete'-command discipline anyway, and
external-content gives us `'rebuild'` and `'integrity-check'` for free with the
canonical trigger pattern from the SQLite docs. Storage overhead is one duplicated
normalized column; per-account libraries are small.

### 4.3 `fts_normalize` + query builder (reference implementation)

Lives in new module `gemia/media_search.py` (no import of `media_annotations` — that
module imports `fts_normalize` from here; dependency is one-way).

```python
_CJK = re.compile(
    "[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]"
)

def fts_normalize(text: str) -> str:
    """CJK runs -> overlapping char bigrams (len-1 run stays a single char);
    latin/digit runs -> lowercase words; everything else is a separator."""
    out, cjk, word = [], [], []
    def flush_word():
        if word: out.append("".join(word)); word.clear()
    def flush_cjk():
        if len(cjk) == 1: out.append(cjk[0])
        elif cjk: out.extend(cjk[i] + cjk[i+1] for i in range(len(cjk) - 1))
        cjk.clear()
    for ch in str(text or ""):
        if _CJK.match(ch): flush_word(); cjk.append(ch)
        elif ch.isalnum(): flush_cjk(); word.append(ch.lower())
        else: flush_word(); flush_cjk()
    flush_word(); flush_cjk()
    return " ".join(out)

def build_match_expr(query: str) -> str:
    """AND of terms; CJK bigrams verbatim, single CJK char and latin words get prefix *."""
    parts = []
    for tok in fts_normalize(query).split():
        if _CJK.match(tok[0]) and len(tok) >= 2:
            parts.append(tok)            # bigram, exact
        else:
            parts.append(tok + "*")      # single CJK char or latin word: prefix
    return " ".join(parts)               # implicit AND
```

Locked query semantics:

- Terms are joined by implicit **AND** (not phrase). Verified: `'海边日落'` →
  `海边 边日 日落` (AND) hits; phrase would also hit but is brittle when run boundaries
  differ between index and query text. bm25 already ranks tighter co-occurrence higher.
- **OR fallback**: if the AND query returns 0 rows and the expression has ≥ 2 top-level
  terms, retry once with `" OR "` joining; the response sets `"fuzzy": true`. Verified
  above (`'日落 OR 猫咪'` hits).
- **Injection safety**: `fts_normalize` output contains only `[a-z0-9]` and CJK chars —
  FTS5 operators (`" ( ) - ^ *` etc.) cannot survive normalization; the only `*` is
  appended by us. Still bind the expression as a SQL parameter, never f-string it.

### 4.4 The `search_media` tool

New file `gemia/tools/search_media.py`; registered in the dispatch table
(tools/__init__.py:112 block) and schema list (tools/_schema.py, next to
`search_library` at :488). Read-only ⇒ added to `PLAN_ALLOWED_TOOLS`
(plan_mode.py:28-47; note plan_mode.py is a legitimate in-flight feature in this
worktree — coordinate the one-line addition, don't fork the set).

Schema entry:

```python
_tool(
    "search_media",
    "Semantic search over persistent media annotations (vision captions, subjects, "
    "actions, on-screen text, tags — Chinese and English). Returns matching assets "
    "WITH time ranges so timeline/cut tools can act on them directly. Free and fast. "
    "Results are registered as session asset_ids. If unindexed_count > 0, consider "
    "annotate_media (paid) to index the rest.",
    {
        "query": {"type": "string", "description": "Free text, zh or en, e.g. '海边日落 无人机' or 'woman talking to camera'."},
        "kind": {"type": "string", "enum": ["video", "image", "audio", "any"]},
        "limit": {"type": "integer", "description": "Max assets. Default 8, max 20."},
    },
    ["query"],
)
```

Core SQL (one query — this is what kills the N+1; annotations and assets share the db,
§0):

```sql
SELECT a.rowid, a.annotation_id, a.asset_id, a.scope, a.start_sec, a.end_sec,
       a.label, a.category, a.confidence, a.search_text,
       bm25(media_annotations_fts) AS rank
FROM media_annotations_fts f
JOIN media_annotations a ON a.rowid = f.rowid
JOIN media_assets m ON m.asset_id = a.asset_id AND m.deleted_at IS NULL
WHERE media_annotations_fts MATCH :expr
  AND (:kind = '' OR m.media_kind = :kind)
ORDER BY rank
LIMIT 400
```

Post-processing in Python: group rows by `asset_id`; asset score =
`round(-min(rank), 3)` (SQLite bm25 is smaller-is-better; negate so higher-is-better)
with `+0.5` boost if any matching row has `scope='asset'`; take top `limit` assets; per
asset keep ≤ 6 `time_range` rows (best rank first). `matched_terms` per asset = the
original whitespace-split user tokens whose `fts_normalize` form is a substring of any
matched row's `search_text` (host-side check; no FTS auxiliary functions needed).

**Locked result contract** (exact keys; protocol-parity rule 1 applies):

```json
{
  "query": "海边日落",
  "kind": "video",
  "fuzzy": false,
  "result_count": 1,
  "results": [
    {
      "asset_id": "vid_003",
      "library_asset_id": "asset_0123abcdef0123abcdef0123",
      "name": "beach_drone.mp4",
      "kind": "video",
      "duration": 1800.0,
      "score": 3.412,
      "matched_terms": ["海边日落"],
      "asset_labels": ["drone shot of sunset over the sea"],
      "time_ranges": [
        {"start_sec": 12.0, "end_sec": 18.5,
         "label": "drone shot of sunset over the sea",
         "category": "segment", "confidence": 0.82,
         "annotation_id": "ann_0123456789abcdef"}
      ]
    }
  ],
  "unindexed_count": 3,
  "index_hint": "3 asset(s) of this kind have no vision annotations; annotate_media can index them (paid, ~$0.03/asset).",
  "summary": "found 1 asset(s), 1 time range(s) for '海边日落'"
}
```

- `asset_id` is the **session** asset id: each hit is registered into the session
  `AssetRegistry` exactly the way `search_library` does it — extract
  `_session_id_for_library_asset` (tools/search_library.py:93-123) into a shared helper
  `gemia/tools/_library_session.py::ensure_session_asset(ctx, asset)` and import it from
  both tools (do not copy-paste it). This is what lets the model immediately run
  `timeline_insert_clip` / `edit_video` cut/trim against the returned ranges.
- `unindexed_count` = assets of the requested kind with `deleted_at IS NULL` and no
  `media_index_state` row with `status='ok'` (§7.1). The host **never** auto-annotates
  (D8); the hint text lets the model decide to spend.
- Empty result is a normal `result_count: 0` response, never an exception — same
  non-throwing philosophy as `search_library` (tools/search_library.py:1-7).

**Re-ranking rule (locked)**: the host returns FTS top-k in rank order and does **no**
model-based re-ranking; the orchestrator model reads labels/ranges in the tool result
and picks — that is the free re-rank. No second paid call, ever, inside this tool.

### 4.5 Killing the N+1 in `list_assets(q=)` + REST/web/CLI surface

- `media_library.list_assets` (media_library.py:176-198): replace the per-asset
  `search_annotation_text` calls (media_library.py:180-183) with **one** call to a new
  `gemia.media_search.asset_ids_matching(account_id, q)` (same MATCH query as §4.4,
  `SELECT DISTINCT a.asset_id ... LIMIT 1000`), keeping the existing in-Python
  name/mime/kind substring check as a union (asset *names* are not in the FTS index —
  they live in `media_assets`; indexing them is out of scope v1). `search_annotation_text`
  (media_annotations.py:248-263) then has no callers: delete it in the same commit
  (grep first; tests referencing it get updated).
- New REST route in `server.py` (GET family at server.py:892-960):
  `GET /media-library/search?q=&kind=&limit=` → the §4.4 payload **minus** the
  session-specific `asset_id` field (no session context in REST; `library_asset_id`
  stays) and minus `index_hint`. 401 when signed out (same guard as
  `/media-library/list`, server.py:895-897).
- Web v3: add a search input to the library panel (grid element `#media-library-grid`,
  v3.js:32,349-360; current fetch is hardcoded `limit=100` with no q, v3.js:1459).
  Debounced 250 ms; empty input → existing list; non-empty → `/media-library/search`,
  render asset cards with range chips (`12.0–18.5s label`), click-to-copy asset id.
- CLI: new `/search <query>` slash command (register in src/slash.js:5-24 command table),
  `api.js` gains `searchMediaLibrary(baseUrl, {q, kind, limit})` next to
  `listMediaLibrary` (src/api.js:64-72, which already forwards `q` — the new function
  targets the new route), output style mirrors the `/annotations` renderer
  (src/App.js:750-775: one line per range, `start-end  label  [tags]`).

**中文小结**：分词器实测定案——trigram 虽可用但两字中文词（中文主流）MATCH 直接零命中，
改用 unicode61 + 中文双字 bigram 归一化，AND 语义 + 零命中 OR 降级，全部用例已验证。索引用
external-content FTS5 + 触发器，现有全部写路径零改动自动同步。新工具 `search_media` 返回
时间段级命中并注册进会话 AssetRegistry；`list_assets(q=)` 的 N+1 一并铲掉；REST/web/CLI
三面同批。

---

## 5. ADR: FTS5 now, embeddings rejected for v1（ADR：拒绝向量检索）

**Decision**: no embeddings, no vector store, no ANN index in v1.

Rationale:

1. **Scale does not justify it.** Per-account libraries are hundreds of assets, a few
   thousand annotation rows. FTS5 answers in microseconds inside an already-open sqlite
   file. An embedding pipeline adds: a per-asset embedding call at index time, a
   per-QUERY embedding call (paid, violates the free-retrieval rule §4.4), storage, and
   a similarity scan implementation — for a corpus that fits in one FTS page cache.
2. **The semantic gap is closed elsewhere, twice.** (a) Index-time: Gemini vision writes
   rich bilingual captions/subjects/actions/tags — the vocabulary a user would search
   for is *generated into* the index. (b) Query-time: the orchestrator model reads the
   FTS top-k (labels + ranges + matched_terms) in the tool result and re-ranks/filters
   with full task context — strictly better than cosine similarity, and free.
3. **Dependency discipline.** No GPU on this machine; Gemini API is the only model
   dependency (§0). An embeddings endpoint is technically the same vendor but doubles
   the billable surfaces the budget guard must stay honest about.
4. **Failure modes are visible.** FTS misses are explainable (term absent) and fixable
   by enriching annotations; embedding misses are opaque.

**Revisit triggers** (any one → open a v2 spike):

- A single account exceeds ~5,000 vision-annotated assets or search latency > 100 ms.
- Zero-hit rate: log `result_count == 0` queries (host-side counter in the tool
  dispatcher, no content logging beyond the normalized query) — if > 20% of non-fuzzy
  queries zero-hit over a week of real use, literal matching is failing.
- ja/ko or other non-zh CJK becomes a real user population (bigram normalization works,
  but caption languages would need extending — cheaper with embeddings).
- The audio/ASR channel (day-2, §8.2) lands and multiplies text volume ~10×.

**中文小结**：v1 拒绝向量检索：库太小、双语标注已在索引侧补语义、编排模型在结果里免费重排、
且不给预算台账加第二个计费面。触发条件写死，达标再议 v2。

---

## 6. Provider degradation（供应商降级）

The vision pass is **Gemini-only**. Resolution uses the existing client's provider logic
(gemini_client.py:374-378: explicit `LUMERI_V3_PROVIDER` → auto-probe → openrouter).

Locked rules:

1. Vision is available iff resolved provider ∈ {`"gemini"`, `"vertex"`} **and**
   `GeminiClientV3()` constructs without raising (missing key raises,
   gemini_client.py:420-421, :392). Both are Gemini API surfaces sharing the verified
   OpenAI-compat base64 `image_url` path (§0); claude/openrouter/openai are neither
   verified nor budgeted for this and are treated as unavailable.
2. `annotate_media` gains an argument `strategy: "auto" | "vision" | "heuristic"`
   (default `"auto"`; add to the tool schema at tools/_schema.py:498ff).
   - `auto`: vision if available, else heuristic fallback.
   - `vision`: hard-fail with a typed error message naming the resolved provider when
     unavailable (explicit request must not silently downgrade).
   - `heuristic`: always local, always $0.00.
3. The fallback writes **honest** rows: `annotate_asset_heuristic` with
   `source="heuristic"` (§2.1), `metadata.strategy="local_heuristic"`, plus
   `metadata.degraded_reason` ∈ {`"provider_not_gemini"`, `"no_credentials"`,
   `"vision_error"`} (the last one for per-asset §2.2-rule-2 failures when the batch
   continues). `media_index_state.status = "degraded"` (§7.1), so `search_media`'s
   `unindexed_count` still counts these assets as index-worthy.
4. The tool result's `summary` states the strategy actually used
   (`"annotated 3 asset(s) via gemini_vision"` / `"... via local heuristic (degraded: no_credentials)"`)
   so the transcript never implies vision ran when it didn't.

**中文小结**：只有 gemini/vertex 两个 Gemini 面才跑视觉；其余供应商或缺 key 一律降级为
本地启发式，且 source/metadata/summary 三处全部如实署名，降级资产仍计入"未索引"。

---

## 7. Index lifecycle（索引生命周期）

### 7.1 State table

New table in the same `library.sqlite3`, created by `media_annotations._ensure_schema`:

```sql
CREATE TABLE IF NOT EXISTS media_index_state (
    asset_id       TEXT PRIMARY KEY,
    fingerprint    TEXT NOT NULL,
    strategy       TEXT NOT NULL,          -- 'gemini_vision_v1' | 'local_heuristic'
    prompt_version INTEGER NOT NULL DEFAULT 0,
    model          TEXT NOT NULL DEFAULT '',
    frames_used    INTEGER NOT NULL DEFAULT 0,
    annotated_at   TEXT NOT NULL,
    status         TEXT NOT NULL,          -- 'ok' | 'degraded' | 'error'
    error          TEXT
);
```

Written by the indexing pipeline in the same transaction as the `upsert_annotations`
batch. This is the queryable index-state; per-row `metadata_json` (§2.3) is the
per-annotation provenance. (Alternative — deriving state by `json_extract` over
annotations — rejected: O(rows) scans on every `search_media` call for the
`unindexed_count`, and no place to record `error`.)

### 7.2 Staleness / incremental re-index

`needs_index(asset, state_row)` is true iff any of:

- no state row;
- `state_row.fingerprint != asset["fingerprint"]` — note today this is near-impossible
  because `asset_id` is fingerprint-derived (media_library.py:67), so changed content
  becomes a *new* asset; the check is kept because it is one string compare and guards
  any future re-encode-in-place import path;
- `state_row.prompt_version < PROMPT_VERSION` (current constant, §2.5);
- `state_row.status in ("degraded", "error")` and vision is now available (§6.1).

Re-index = the normal pipeline; `replace_source="gemini_vision"` + the §2.1 legacy
delete make it idempotent. User/`gemini`(orchestrator)/`import` annotations are never
touched by re-indexing.

### 7.3 Lazy vs on-import; async bulk — decisions D8/D9

- **On-import: nothing.** `import_media` (media_library.py:55-146) already runs
  ffprobe + 8 thumbnails + waveform synchronously inside the upload request, and runs
  *outside* any agent session — there is no `BudgetGuard` there to account a paid call
  against, and upload latency must not grow. (Alternative — auto-annotate on import —
  rejected: hidden spend violates budget_guard.py:5-9's "host only reports" contract.)
- **Lazy, model-driven**: paid indexing happens only through `annotate_media`
  (`PLAN_BLOCKED_TOOLS` already blocks it in plan mode, plan_mode.py:50ff — stays
  blocked). `search_media` is read-only and reports `unindexed_count` so the model can
  choose to spend.
- **Sync path**: explicit `asset_ids` with ≤ 4 assets and `strategy` resolving to
  vision. More than 4, or `all=true` (tools/media_annotations.py:33-39): the dispatcher
  MUST submit an async job instead.
- **Async bulk** via the existing `JobRegistry` on `ctx.jobs` (_context.py:164;
  JobRecord fields at _jobs.py:33-64): `kind="annotate"` (extend the docstring's kind
  list at _jobs.py:40-43 and :105), `pending_asset_id=""` (no asset is produced),
  `estimated_eta_sec = 15.0 * n_assets`, `summary=f"vision-annotate {n} asset(s)"`.
  Worker = `asyncio.create_task` in the loop's process, processing assets sequentially,
  updating the record via `update_from_poll` with `status="running"` then
  `"done"`/`"failed"` (final_error = first per-asset error summary). Model polls with
  the existing `check_job` / `wait_for_job` verbs. Cost is committed at submit
  (`actual_usd = 0.03 × n`, §3.3 item 3). Affordability guard at submit: the loop
  exposes the guard as `ctx.extra["budget_guard"]` (set where `ToolContext` is built,
  agent_loop_v3.py:280); the dispatcher trims `n` to
  `floor((max_usd - spent_usd - 0.50) / 0.03)` and reports the trim in `summary`.
  **Restart caveat (accepted for v1)**: JobRegistry persists (_jobs.py:235-262) but the
  in-process worker does not survive a sidecar restart; `check_job` on an `annotate` job
  with no live worker returns `failed` with `"worker lost (server restart); re-run
  annotate_media — indexing is idempotent"`. Per-asset state already written stays valid.

**中文小结**：索引状态单独建表（fingerprint + prompt_version 驱动增量重建）；导入不自动
标注、搜索不自动标注——花钱永远是模型的显式决定；≤4 个同步，批量走 JobRegistry 异步任务，
提交时按条数入账，重启丢 worker 按失败上报（幂等可重跑）。

---

## 8. Delivery: parity checklist + v1 exclusions（交付与边界）

### 8.1 Same-commit parity checklist

Per docs/protocol-parity-plan.md rule 1 (三件套同 commit), the v1 landing commit(s) must
contain ALL of:

Backend:
- [ ] `gemia/media_annotations.py`: `_VALID_SOURCE` extension; heuristic source fix
      (:207/:229/:238); `search_text` in `_normalize_payload` + INSERT/UPDATE; schema DDL
      §4.2 + `media_index_state` §7.1; delete `search_annotation_text`.
- [ ] `gemia/media_search.py`: `fts_normalize`, `build_match_expr`,
      `search_media_annotations`, `asset_ids_matching`.
- [ ] `gemia/media_index.py`: sampling (§3.1), prompt/parse (§2.2/2.5), vision pipeline,
      degradation (§6), index-state writes (§7).
- [ ] `gemia/tools/search_media.py` + `gemia/tools/_library_session.py` (shared session
      registration, refactored out of tools/search_library.py:93-123).
- [ ] `gemia/tools/media_annotations.py`: `strategy` arg, sync cap 4, async submit,
      `actual_usd` in results.
- [ ] `gemia/tools/_schema.py`: `search_media` entry; `annotate_media` schema updates.
- [ ] `gemia/tools/__init__.py`: dispatch entry (`"search_media"` at the :112 table).
- [ ] `gemia/budget_guard.py`: entries per §3.3 (fix budget_guard.py:43).
- [ ] `gemia/agent_loop_v3.py`: `actual_usd` pass-through at :1164/:1207;
      `ctx.extra["budget_guard"]` at :280.
- [ ] `gemia/plan_mode.py`: `"search_media"` into `PLAN_ALLOWED_TOOLS` (in-flight file —
      coordinate, one line).
- [ ] `server.py`: `GET /media-library/search` route (§4.5).
- [ ] Web `static/v3/`: library-panel search input + results-with-ranges rendering
      (v3.js:349-360, :1459) + tool-label for `search_media` wherever web labels tools.

CLI (`~/Code/lumeri-cli`), same commit window:
- [ ] `src/slash.js`: `/search` command entry.
- [ ] `src/api.js`: `searchMediaLibrary()`.
- [ ] `src/App.js`: `/search` handler rendering ranges (mirror the /annotations style at
      src/App.js:750-775).
- [ ] `src/format.js`: `toolLabel` for `search_media` (parity-plan Phase 2 item).
- [ ] `scripts/mock-server.mjs`: fixtures for `GET /media-library/search` and updated
      `/media-library/list` (today it has **no** media-library fixtures at all — verified
      by grep; `npm run mock` must exercise `/search`).

Tests (acceptance = local runs per parity-plan rule 2):
- [ ] `tests/test_media_search.py`: T1 normalizer golden cases — MUST include the
      verified set: `日落`→hit, `海`→hit(prefix), `海边日落`→hit, `边日`(overlap)→hit,
      `sun`→hit, `猫咪 跳跃`→hit, AND-miss `日落 猫咪`→0 rows, OR fallback →hit, plus an
      FTS5-operator injection string (`'"del" OR *'`) proving sanitization. T2 trigger
      sync: create/update/delete/`delete_source_annotations` each reflected in MATCH
      results + `integrity-check` passes. T3 **source-enum landmine pin**:
      `upsert_annotations(replace_source="gemini_vision")` must NOT delete `source='user'`
      rows (this test fails on today's code — red/green proof of §2.1).
- [ ] `tests/test_media_annotations_tools.py` (exists): strategy resolution, degraded
      honesty (source/metadata/summary triple), sync cap, `actual_usd` values, legacy
      `'gemini'`+`local_heuristic` cleanup.
- [ ] `tests/test_server_media_routes.py` (exists): `/media-library/search` route incl.
      401 and kind filter.
- [ ] Vision pipeline tests with a **fake client** (patch `GeminiClientV3.stream_turn`
      to yield canned §2.2 JSON): mapping, clamping, fence-stripping, retry-once, error
      path, `media_index_state` writes. No live API calls in tests.
- [ ] CLI `test/search.mjs` in the npm-test chain: `/search` against the mock server
      renders asset + ranges; unknown-route regression guard.
- Verification commands: `python3 -m pytest tests/test_media_search.py tests/test_media_annotations_tools.py tests/test_server_media_routes.py`
  and `cd ~/Code/lumeri-cli && npm test`. QUEUE.md done-marking requires both green.

### 8.2 Explicit v1 EXCLUSIONS（明确不做）

- **Audio/ASR channel — day-2.** `whisper` and `faster_whisper` are NOT installed in
  `.venv` (verified 2026-07-06); do not add them in this change. `librosa` IS installed —
  it may be used day-2 for energy/beat/onset time-range candidates ONLY, never
  transcription. v1: audio assets get no vision pass; they keep heuristic annotations
  and remain findable by name/tags. `on_screen_text` from sampled video frames is the
  only "speech-adjacent" text in v1.
- **No embeddings / vector store** (ADR §5).
- **No auto-indexing** on import or inside `search_media` (D8).
- **No Files API / video upload** — base64 frames only (§0; none exists on this path).
- **No OCR dependency** (tesseract etc.) — on-screen text comes from the vision model.
- **No asset-name FTS** — names stay on the existing substring path (§4.5).
- **No paid re-ranking call** inside retrieval, ever (§4.4).
- **No cross-account search**; the library is account-scoped by construction
  (media_library.py:35-40).
- **No ja/ko caption emission** (§2.4; revisit triggers §5).

**中文小结**：交付按协议对等纪律走：后端/协议/预算/web/CLI/mock/两套测试同一批落地，
验收以本地 pytest + CLI npm test 双绿为准。v1 明确不做：ASR（whisper 未装；librosa 只许
day-2 做能量/节拍）、向量检索、自动索引、Files API、OCR 依赖、跨账户。
