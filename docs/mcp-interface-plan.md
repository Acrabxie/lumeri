# Lumeri MCP Interface Plan（MCP 接口设计规范，双向）

Status: 设计定稿，待实施（Phase 1 未开始）
Date: 2026-07-06
Scope: `/Volumes/Extreme SSD/gemia`（backend）; `~/Code/lumeri-cli` is functionally
unaffected until Phase 3 (see §7).
Style/prior art: this doc follows `docs/protocol-parity-plan.md` conventions —
decisions are numbered, every decision states the rejected alternative, and
acceptance is red/green testable.

> Line numbers cited below are from the 2026-07-06 working tree at commit
> `0142052` (+ concurrent uncommitted edits to `plan_mode.py`, `tools/__init__.py`,
> `tools/_schema.py`). Cite by **symbol first, line second**; if a line has
> drifted, the symbol is authoritative.

---

## 0. Locked decisions at a glance

| # | Decision |
|---|----------|
| D1 | Direction A ships **two transports**: stdio subcommand `python -m gemia mcp-serve` (Phase 1) and **streamable HTTP on a separate localhost port 7789**, hosted as a background thread inside the existing `server.py` process (Phase 2). NOT a `/mcp` route on the stdlib HTTP server. |
| D2 | Auth is **explicitly deferred** per the project's security-last decision: localhost bind + spec-mandated `Origin` validation only, until the final security pass. |
| D3 | Curated surface of **25 tools** (Phase 1 ships 18), names **1:1 with internal verb names**; 5 MCP-native lifecycle/import tools; ~70 internal verbs deliberately excluded with per-family rationale. |
| D4 | **Explicit `session_id`**: `create_session` returns it; every other tool takes it as a required parameter. No per-connection implicit session. |
| D5 | `ToolContext.emit_progress` maps to MCP `notifications/progress` when the client sent a `progressToken`. |
| D6 | MCP resources under `lumeri://session/{id}/...`; **binary media is returned as file-path descriptors, never base64**; JSON-ish resources (timeline, annotations) are returned inline. |
| D7 | MCP calls dispatch through the **same `DISPATCHER`** behind a new single choke point `SessionRunner.run_verb()`, which applies `is_plan_safe()` **then** `BudgetGuard.check()` in the same order as the agent loop, and mirrors `tool_exec_*` SSE events with an additive `origin: "mcp"` field. |
| D8 | SDK: official `mcp` Python SDK (new optional dependency extra `[mcp]`, `mcp>=1.12,<2`), **low-level `mcp.server.Server` API**, tool schemas mechanically transformed from `TOOL_SCHEMAS` — no hand-retyped schemas, no FastMCP decorators. Protocol revision pinned to **2025-06-18** (minimum accepted: 2025-03-26). |
| D9 | Direction B: client config `~/.gemia/mcp.json`, tool names `mcp__<server>__<tool>`, schemas ingested at session creation, dispatch adapter behind instance-level indirection in `AgentLoopV3` (today's module-level `TOOL_SCHEMAS`/`DISPATCHER` uses become `self._tool_schemas` / `self._dispatch()`). |
| D10 | Plan mode: **external MCP tools are always plan-blocked**. `is_plan_safe()` is already fail-closed (verified, §3.2); the exact-coverage test stays scoped to static `TOOL_NAMES`; one new test asserts dynamic `mcp__*` names are blocked. |
| D11 | Budget: unknown `mcp__*` tools get a conservative default **$0.02 / 10 s**; per-server and per-tool overrides live in `mcp.json`; implemented as a small extension in `BudgetGuard.estimate()`. |
| D12 | Phase 1 = stdio server + read/timeline toolset + in-process MCP client test harness. Phase 2 = render/export verbs + resources + progress + HTTP transport. Phase 3 = consuming direction. |
| D13 | Contract impact: **zero new SSE kinds, no `PROTOCOL_VERSION` bump** (additive `origin` field only). CLI parity untouched until Phase 3's cosmetic tool-label mapping. |

**中文小结**：Lumeri 同时做 MCP 服务端（差异化能力：任何 MCP 客户端都能驱动剪辑）
和 MCP 客户端（吃外部工具）。服务端先行：stdio 子命令 + 7789 独立端口的
streamable HTTP（挂在 7788 同进程内）；工具面裁剪到 25 个、与内部 verb 同名；
显式 session_id；预算闸与 plan mode 闸复用同一套检查；鉴权按"安全最后做"推迟。
消费端：`~/.gemia/mcp.json` 配置、`mcp__` 命名空间、外部工具一律被 plan mode
封禁、未知成本按 $0.02/10s 保守计。协议契约零影响。

---

## 1. Architecture facts this design builds on

Facts an implementer must not re-derive wrongly:

1. **`server.py` is a synchronous stdlib server** — `ThreadingHTTPServer` +
   `BaseHTTPRequestHandler` (`server.py:44`, `server.py:629`, `main()` at
   `server.py:1603-1612`), hand-routed. There is no ASGI stack in the process.
2. **One session = one thread + one private asyncio loop.**
   `SessionRunner` (`gemia/session_manager.py:52`) owns an `AgentLoopV3`; all
   work hops onto the session loop via `asyncio.run_coroutine_threadsafe`
   (e.g. `add_external_asset` at `session_manager.py:164-171`,
   `run_project_edit` at `session_manager.py:~198`). `get_manager()`
   (`session_manager.py` tail) is a **process-local singleton** — sessions are
   not visible across processes.
3. **The dispatch table is the single execution surface.**
   `DISPATCHER: dict[str, Dispatcher]` (`gemia/tools/__init__.py:197-199`),
   ~95 verbs, schemas in OpenAI function shape in `TOOL_SCHEMAS`
   (`gemia/tools/_schema.py:29-46`), names in `TOOL_NAMES`
   (`_schema.py:1718`). Dispatcher signature:
   `async def dispatch(args: dict, ctx: ToolContext) -> dict`.
4. **Gates live in the agent loop, not in the dispatcher.** Order is
   plan gate (`agent_loop_v3.py:1206`, "checked BEFORE the budget gate")
   → budget gate (`agent_loop_v3.py:1261`) → dispatch
   (`agent_loop_v3.py:1341`) → `budget.commit(actual_seconds=elapsed)`
   (`agent_loop_v3.py:1344,1388`). Any new front door that bypasses the loop
   bypasses the gates unless it re-applies them — that is the central risk
   §5 addresses.
5. **Progress** is a per-call callback: the loop swaps
   `self._tool_ctx.emit_progress` before each dispatch
   (`agent_loop_v3.py:1337`, factory at `1489-1508`), and dispatchers emit
   `ProgressUpdate{percent,message,eta_sec}` (`gemia/tools/_context.py:147-152`).
6. **Assets are session-scoped ids, never paths, on the model side**
   (`_schema.py:20-22`); the host resolves ids via `AssetRegistry`
   (`_context.py:71-144`). Files live under the session `output_dir`.
7. **Events**: everything the agent emits goes through
   `SessionRunner._emit_event` → durable transcript + SSE ring
   (`session_manager.py:133-162`). The protocol surface is frozen in
   `gemia/v3_contract.py` (19 kinds); additive **fields** are non-breaking and
   do not bump `PROTOCOL_VERSION` (`v3_contract.py:20-22`).
8. **No MCP dependency exists yet**: `pyproject.toml` dependencies
   (checked 2026-07-06) and `uv.lock` contain no `mcp` package.

**中文小结**：server.py 是同步 stdlib HTTP；每个会话一个线程一个私有 asyncio
loop；DISPATCHER 是唯一执行面；plan/budget 闸在 agent loop 里而不在 dispatcher
里（新前门必须自己接闸）；进度是每次调用注入的回调；资产走 session 级
asset_id；SSE 契约冻结在 v3_contract.py，加字段不加 kind 不用升版本；仓库目前
没有任何 mcp 依赖。

---

## 2. Direction A — Lumeri as an MCP server

### 2.1 D1: Transports — stdio subcommand + streamable HTTP on 127.0.0.1:7789

**Decision.** Ship both:

- **stdio**: new subcommand `python -m gemia mcp-serve` registered in
  `gemia/__main__.py` next to the existing `server` subparser
  (`gemia/__main__.py:153-155`). Flags:
  `--transport {stdio,http}` (default `stdio`), `--http-port` (default 7789).
  stdio is the Phase 1 deliverable: it is what
  `claude mcp add lumeri -- python -m gemia mcp-serve` and Codex's
  `mcp_servers` config consume.
- **streamable HTTP**: the MCP SDK's streamable-HTTP ASGI app served by
  `uvicorn` on `127.0.0.1:7789`, launched as a **daemon thread inside the
  existing `server.py` process** when `LUMERI_MCP_HTTP=1` (or
  `python -m gemia server --mcp-http`). Same process ⇒ same
  `get_manager()` singleton ⇒ MCP-driven edits stream live into the web UI
  over the existing SSE channel. This is the demo that sells Direction A:
  Claude Code edits over MCP while a human watches the timeline move in the
  browser.

**Why not a `/mcp` route on `server.py` (rejected alternative 1).**
Streamable HTTP is not "a POST endpoint": the spec requires a single MCP
endpoint supporting POST (client→server messages, optionally answered by an
SSE stream), GET (server→client stream), DELETE (session teardown),
`Mcp-Session-Id` handling, and resumability semantics. Reimplementing that on
`BaseHTTPRequestHandler` (`server.py:629`) means hand-maintaining a second
protocol state machine that the official SDK already owns — maximal drift
risk for zero benefit. The stdlib server also blocks one OS thread per open
stream with no backpressure story.

**Why not a fully separate `mcp-serve --transport http` process (rejected
alternative 2).** A separate process has a separate `get_manager()`
(architecture fact §1.2), so MCP sessions would be invisible to the web UI —
killing the flagship "watch the agent edit" property. Standalone HTTP mode
remains *possible* via the same entrypoint (useful for tests), but the
supported deployment is in-process with 7788.

**stdio process-model caveat (state it honestly in the README/docstring):**
`mcp-serve` over stdio runs in **its own process** with its own
`SessionManager`. Its sessions persist to disk through the normal
`ProjectStore`/sessions roots but are **not** live-visible in a separately
running 7788 web UI. Clients that want live web visibility should use the
HTTP transport (Phase 2). Do not attempt to have the stdio process "attach"
to 7788 in Phase 1 — that would require proxying every verb over private
HTTP endpoints that do not exist yet (non-goal for now, revisit only if
users ask for it).

### 2.2 D2: Auth — explicitly deferred (security-last)

Per the standing project decision that security hardening lands as one
unified pass at feature-complete (see shared memory "Lumeri 安全最后做"),
MCP transports ship **without authentication** until that pass:

- HTTP binds `127.0.0.1` only — never `0.0.0.0` (note `server.py`'s own
  default host is `0.0.0.0` via `_configured_server_host`, `server.py:70`;
  the MCP port must NOT inherit that default).
- The one thing we do NOT defer: **`Origin` header validation** on the HTTP
  transport, because the MCP spec explicitly requires it for localhost
  servers to prevent DNS-rebinding, and it is a two-line check.
- Everything else — bearer tokens, OAuth resource-server metadata, scoping
  run_shell/file tools back in — is on the security-pass backlog. This doc
  records the deferral so the security pass has a checklist anchor.

**中文小结**：传输层双通道：stdio 子命令（Phase 1，给 Claude Code/Codex 直接
add）+ 7789 端口 streamable HTTP（Phase 2，挂在 7788 同进程线程里，才能让
MCP 驱动的剪辑实时出现在网页时间线上）。不在 stdlib server 上手搓 /mcp 路由
（协议状态机太重、必漂移）；不做独立 HTTP 进程（跨进程看不到会话）。鉴权按
"安全最后做"整体推迟，只保留 localhost 绑定 + 规范强制的 Origin 校验。

### 2.3 D3: Curated tool surface (25 tools, not ~95)

Exposing all ~95 verbs would (a) blow client context windows, (b) expose
`run_shell`/file-write before the security pass, (c) surface Lumeri-internal
plumbing (memory, skills, ask-bridge) that makes no sense across an agent
boundary. The MCP surface is a **frozen curated set** defined in a new
`gemia/mcp/toolset.py` as `MCP_TOOLSET: frozenset[str]` plus
`MCP_NATIVE_TOOLS` (tools that exist only at the MCP layer).

**Naming rule (decision):** MCP tool names are **byte-identical to internal
verb names** (`probe_media`, `timeline_insert_clip`, …). Rationale: the plan
gate and budget gate look up by exact name (`plan_mode.py:86`,
`budget_guard.py:135-139`) — identical names mean zero mapping tables to
drift, and greppability across the boundary. Rejected alternative: service
prefix `lumeri_*` per generic MCP best practice — unnecessary here because
MCP clients already namespace (Claude Code renders `mcp__lumeri__probe_media`),
and a prefix would force a strip/translate layer in front of both gates.

| MCP tool | Internal verb | Group | Phase | Plan-mode class |
|---|---|---|---|---|
| `create_session` | — (MCP-native → `SessionManager.create_session`, `session_manager.py:294`) | lifecycle | 1 | allowed (native table) |
| `list_sessions` | — (native → `SessionManager.list_sessions`) | lifecycle | 1 | allowed |
| `get_session` | — (native → assets + `plan_mode` + `BudgetGuard.snapshot()` `budget_guard.py:177-184` + `PROTOCOL_VERSION`) | lifecycle | 1 | allowed |
| `close_session` | — (native → `SessionManager.close_session`) | lifecycle | 1 | allowed |
| `import_media` | — (native → `SessionRunner.add_external_asset`, `session_manager.py:164`; takes an absolute local file **path**) | media | 1 | **blocked** (registers a session asset — same rationale as `copy_in`, `plan_mode.py:13-15`) |
| `probe_media` | `probe_media` | media | 1 | allowed |
| `analyze_media` | `analyze_media` | media | 1 | allowed |
| `search_library` | `search_library` | media | 1 | allowed |
| `get_timeline` | `get_timeline` | timeline | 1 | allowed |
| `timeline_insert_clip` | 1:1 | timeline | 1 | blocked |
| `timeline_move_clip` | 1:1 | timeline | 1 | blocked |
| `timeline_trim_clip` | 1:1 | timeline | 1 | blocked |
| `timeline_split_clip` | 1:1 | timeline | 1 | blocked |
| `timeline_delete_clip` | 1:1 | timeline | 1 | blocked |
| `timeline_add_transition` | 1:1 | timeline | 1 | blocked |
| `timeline_undo` | 1:1 | timeline | 1 | blocked |
| `get_lumenframe` | `get_lumenframe` | lumen | 1 | allowed |
| `get_media_annotations` | `get_media_annotations` | annotations | 1 | allowed |
| `lumen_add_layer` | 1:1 | lumen | 2 | blocked |
| `lumen_patch` | 1:1 (the power verb — covers transform/opacity/keys) | lumen | 2 | blocked |
| `lumen_delete_layer` | 1:1 | lumen | 2 | blocked |
| `render_preview` | 1:1 | render | 2 | blocked |
| `project_export` | 1:1 | render | 2 | blocked |
| `extract_frame` | 1:1 | render | 2 | blocked |
| `write_media_annotation` | 1:1 | annotations | 2 | blocked |

Phase 1 = the 18 rows marked Phase 1 (the "read + timeline" set). The
plan-mode class column is **not** a new classification: for every 1:1 verb it
is exactly what `PLAN_ALLOWED_TOOLS`/`PLAN_BLOCKED_TOOLS` already say
(`gemia/plan_mode.py:28-75`); only the 5 native tools need the new
`MCP_NATIVE_PLAN_SAFE` frozenset in `gemia/mcp/toolset.py`.

**Schema derivation rule (mechanical, no hand-typing):** for each 1:1 verb,
MCP `inputSchema` = the verb's `TOOL_SCHEMAS` entry's
`function.parameters` (`_schema.py:29-46`) with one injected property:

```json
"session_id": {"type": "string", "description": "Session id from create_session."}
```

prepended to `required`. The MCP layer strips `session_id` before handing
`args` to the dispatcher. A drift test asserts this equality (§6, Phase 1
gate case 3). Tool
`annotations`: `readOnlyHint: true` exactly for the plan-allowed rows;
`destructiveHint: true` for `timeline_delete_clip`, `lumen_delete_layer`,
`close_session`; `openWorldHint: false` everywhere (local-first).
`outputSchema`/`structuredContent`: return the dispatcher's result dict as
`structuredContent` plus a compact text rendering; do **not** declare
`outputSchema` in Phase 1 — result shapes vary per verb and a wrong frozen
schema is worse than none (revisit when result shapes get their own contract).

**Exclusions and why (document verbatim in `toolset.py`):**

- `elicit` / ask-bridge: it is a model→human question routed over SSE
  `ask_question` (`gemia/tools/_ask_bridge.py`); across MCP the caller *is*
  an agent and there is no Lumeri-owned human channel. MCP's own elicitation
  (spec 2025-06-18) is an optional client capability with patchy support —
  revisit after Phase 3; until then the tool cannot cross the boundary.
- `run_shell`, `build`, `check_job`, `wait_for_job`, `file_write`,
  `file_copy`, `file_move`, `file_delete`, `write_file`, `move_file`,
  `organize_files`: arbitrary code execution / filesystem mutation offered to
  an unauthenticated localhost caller — excluded until the security pass
  (same deferral as D2, explicitly linked).
- `remember`, `log_note`, `save_skill`, `recall_skills`: Lumeri-private
  memory and skill store. External agents have their own memory; letting
  them write ours pollutes cross-session state invisibly.
- `generate_image`, `generate_video`, `generate_audio`, `narrate`: real
  provider spend (`budget_guard.py:21-24`) triggered by an unauthenticated
  caller. The $5 session cap would still hold, but pre-auth we keep spend
  behind Lumeri's own loop only. Reconsider at the security pass together
  with auth-scoped budgets.
- `web_search`, `web_open`, `fetch`: every MCP-capable client already has
  better web tools; pure surface noise.
- Remaining one-shot ffmpeg verbs (`edit_video`, `composite`, `color_grade`,
  `adjust_media`, `arrange_timeline`, `mix_audio`, `edit_audio`,
  `edit_image`, `transform_geometry`, `smart_reframe`, `subtitle`,
  `paint_*`, shotlist/lottie/OTIO verbs…): not excluded on principle —
  excluded to keep the surface learnable. The timeline + lumen document
  verbs are the canonical editing path (`docs/timeline-canonical-plan.md`);
  the one-shot verbs can be added individually later without any design
  change (the adapter is table-driven).

**中文小结**：只暴露 25 个工具（Phase 1 先 18 个），按 生命周期 / 媒体 /
时间线 / lumen 图层 / 渲染导出 / 标注 分组；名字与内部 verb 逐字节相同（闸门
按名字查表，零映射零漂移；拒绝 lumeri_ 前缀，客户端本来就会加 mcp__lumeri__
命名空间）。schema 从 TOOL_SCHEMAS 机械变换生成（注入 session_id），禁止手抄。
排除清单及理由写死在 toolset.py：elicit 过不了边界、run_shell/文件写等安全
пass 后再说、memory 工具不给外部代理写、付费生成先不放行、web 工具冗余。

### 2.4 D4: Session mapping — explicit `session_id` everywhere

**Decision:** `create_session` returns
`{"session_id", "output_dir", "protocol_version", "budget": snapshot}`;
**every** other tool takes required `session_id`. Errors:
unknown id → tool error `isError: true` with
`"unknown session: <id>"` (mirrors `v3_routes.py:_json_error` 404 text);
`SessionLimitError` (`session_manager.py:39`) → actionable error naming the
limit env var.

**Rejected alternative — per-connection implicit session** (server creates a
session on `initialize` and binds all calls to it): superficially simpler
(no id threading) but wrong on three axes:
1. Streamable HTTP in stateless deployments has no stable connection
   identity to hang the session on; stdio would behave differently from
   HTTP — two semantics, one API.
2. One MCP client (one Claude Code session) could not drive two projects
   side by side, which is a real orchestration pattern.
3. It diverges from the HTTP API (`POST /sessions` returns an explicit id,
   `v3_routes.py:146-166`), so docs/tests/mental models would fork.

Session lifetime: MCP sessions are ordinary `SessionRunner`s — the idle
sweeper and session limits apply unchanged. `close_session` is polite, not
required.

**Long-running verbs → MCP progress notifications.**
`SessionRunner.run_verb()` (§2.6) accepts an `emit_progress` callable. The
MCP tool handler passes one that forwards to
`ctx.session.send_progress_notification(progressToken, progress, total=100.0,
message=...)` — only when the incoming request carried
`_meta.progressToken` (SDK exposes this on the request context; if absent,
the callback is a no-op lambda, same as `session_manager.py`'s default).
Field mapping from `ProgressUpdate` (`_context.py:147-152`):
`percent → progress` (with `total=100.0`; if `percent is None`, send a
message-only notification without `total`), `message → message`,
`eta_sec` → appended to message as `" (~Ns left)"` (MCP has no eta field).
Threading: the dispatcher runs on the **session loop**, the MCP server on
its own anyio loop — the callback must marshal via
`asyncio.run_coroutine_threadsafe(send(...), mcp_loop)` and swallow delivery
failures (progress is best-effort, exactly like the SSE path).
Cancellation: MCP `notifications/cancelled` is acknowledged but Phase 1 does
**not** interrupt an in-flight ffmpeg dispatch (no kill plumbing in
dispatchers today); document as a known limitation instead of pretending.

**中文小结**：显式 session_id（create_session 发号、所有工具带参）而不是
"一连接一会话"：HTTP 无稳定连接身份、单客户端要能同时开多项目、且与现有
HTTP API 同构。长任务用 MCP progress 通知：ProgressUpdate.percent→progress
（total=100），跨 loop 用 run_coroutine_threadsafe 投递，尽力而为；取消在
Phase 1 只应答不真中断（dispatcher 没有 kill 管道，如实写明）。

### 2.5 D6: Assets across the boundary — resources + paths, not base64

**Resource URI scheme:**

- `lumeri://session/{session_id}/timeline` — the same JSON payload as
  `GET /sessions/{id}/timeline` (`v3_routes.py:_timeline_payload_dict`,
  `v3_routes.py:302-388`). `mimeType: application/json`, returned inline.
- `lumeri://session/{session_id}/asset/{asset_id}` — a **descriptor**, not
  the bytes: `{asset_id, kind, path (absolute), size_bytes, summary,
  created_at, lineage, mime}` from the `AssetRecord`
  (`_context.py:50-56`). `mimeType: application/json`.
- `lumeri://session/{session_id}/annotations/{asset_id}` — inline JSON
  (Phase 2, alongside `write_media_annotation`).

`resources/list` enumerates the timeline + asset descriptors of every live
session (bounded by the session limits); the two URI shapes are also
declared as resource templates so clients can construct URIs from a
`session_id` + `asset_id` they got from tool results. Asset registration
already flows through one place (`AssetRegistry.register_output` /
`add_external`), so `notifications/resources/list_changed` hooks there —
Phase 2, and only for sessions with a live subscriber.

**Why paths, not base64 (justification, keep in the code docstring):**
MCP binary resource contents are base64-in-JSON. The upload cap alone is
500 MiB (`v3_routes.py:45`); one such read is ~670 MB of JSON materialized
in **both** processes, and most MCP clients then inject resource contents
into model context — useless and harmful for video. Lumeri is local-first:
pre-auth the server and client are on the same machine by construction
(D2), so an absolute path is a strictly better handle — the client's own
tools (ffprobe, Read, upload) operate on it directly, and `tool` results
stay a few hundred bytes. When remote access arrives with the security
pass, the answer is ranged HTTP on 7788 (`_serve_file_with_range`,
`v3_routes.py:633` — already exists), not fatter MCP payloads. Small
text-like resources (timeline JSON, annotations) ARE returned inline
because that is what resource reads are good at.

**中文小结**：资源用 `lumeri://session/{id}/...` 方案：timeline 与标注是小
JSON，内联返回；媒体资产返回"描述符 + 本机绝对路径"，绝不 base64——500MB
视频转 base64 是双进程各背 ~670MB 的 JSON，还会被客户端灌进模型上下文。
local-first 前提下路径就是最好的句柄；远程场景等安全 pass 时走 7788 已有的
Range 下载，不加肥 MCP。

### 2.6 D7: Budget + plan mode — one choke point, same gates

**The problem:** gates live inline in `AgentLoopV3._run_turn`
(plan gate `agent_loop_v3.py:1206`, budget gate `:1261`), entangled with
turn accounting (`plan_gates_this_turn`, nudges, doom-loop). A second front
door must reuse the **primitives**, not that turn machinery.

**Decision — add `SessionRunner.run_verb()`** (in
`gemia/session_manager.py`, next to `run_project_edit`,
`session_manager.py:~198`), the single MCP execution path:

```python
def run_verb(self, tool_name: str, args: dict, *,
             emit_progress: ProgressCallback | None = None,
             timeout: float | None = None) -> dict:
    """Dispatch ONE internal verb on the session loop with the same gates
    as the agent loop. Returns the tool-result dict; raises VerbGateError
    (plan/budget) or the dispatcher's exception unchanged."""
```

Semantics, in order (each step cites its agent-loop twin):

1. **Membership**: `tool_name` must be in `MCP_TOOLSET` ∩ `DISPATCHER` —
   `run_verb` is not a general RPC hatch; excluded verbs stay excluded even
   if someone calls `run_verb` directly.
2. **Turn-collision guard**: if `self.turn_in_progress`
   (`session_manager.py:105-108`) and the verb is plan-BLOCKED-class
   (i.e. mutating), fail fast with `E_BUSY`
   ("agent turn active; retry when the turn completes"). Read verbs may
   interleave. Rationale: an external mutation landing between two agent
   tool calls silently invalidates the model's context mid-turn. This
   mirrors the 409 on double `submit_turn` (`v3_routes.py:177-179`).
   Rejected alternative — rely on loop serialization alone: serializes
   writes but not *semantics*.
3. **Plan gate first** (same order as `agent_loop_v3.py:1204-1205`):
   if `self.plan_mode and not is_plan_safe(tool_name)` → structured error
   `{blocked_by_plan_mode: true, error_code: "E_PLAN_MODE", message:
   plan_gate_message(tool_name)}` — byte-compatible with
   `agent_loop_v3.py:1217-1224`. Native tools consult
   `MCP_NATIVE_PLAN_SAFE` instead (§2.3).
4. **Budget gate**: `decision = self.agent.budget.check(tool_name)`
   (**the same `BudgetGuard` instance** as the loop — MCP spend and model
   spend share the one $5/600 s pot, `agent_loop_v3.py:283`); not ok →
   `{needs_approval: true, error_code: "E_BUDGET", reason, alternatives,
   estimated_cost_usd, estimated_eta_sec}` mirroring
   `agent_loop_v3.py:1274-1282`. MCP has no approval channel, so
   `needs_approval` is terminal for the caller: raise as tool error with
   the reason text; the calling agent decides what to ask its own human.
5. **Dispatch on the session loop** via `run_coroutine_threadsafe`, with a
   **shallow copy** of the tool context:
   `ctx = dataclasses.replace(self.agent._tool_ctx, emit_progress=cb)`.
   Copying matters: the agent loop mutates the shared ctx's
   `emit_progress` per call (`agent_loop_v3.py:1337`); an interleaved read
   verb reusing the shared object could cross progress streams. Registry /
   jobs / project stay shared references (that is the point).
6. **Commit actuals**: `self.agent.budget.commit(tool_name,
   actual_seconds=elapsed)` on success AND failure, exactly like
   `agent_loop_v3.py:1344,1388`.
7. **SSE mirror**: emit `tool_exec_start` / `tool_exec_result` /
   `tool_exec_error` (and `tool_exec_progress` from the cb) through
   `self._emit_event` with one **additive** field `"origin": "mcp"` and a
   synthetic `call_id` (`mcp-<uuid8>`). Existing kinds only ⇒ no contract
   change (§7); both frontends already render these kinds, and the durable
   transcript (`session_manager.py:133-162`) picks them up for free.
8. **Timeout**: `future.result(timeout or max(60.0, eta * 6))` where `eta`
   comes from `budget.estimate(tool_name)`; `FuturesTimeoutError` →
   `E_BUSY` message copied from the `run_project_edit` handler
   (`v3_routes.py:513-519`) — the verb may still land; the caller should
   re-read state.

Error surfacing at the MCP layer: dispatcher exceptions that are
`GemiaError` keep their structured payload
(`to_payload()`, `agent_loop_v3.py:1350-1353`) inside the tool error text +
`structuredContent`; anything else becomes `E_UNCAUGHT` with the type name,
same as the loop. Never a protocol-level JSON-RPC error for tool-domain
failures — clients treat those as transport faults.

**中文小结**：新增唯一咽喉 `SessionRunner.run_verb()`：先查白名单，再查
"agent 回合进行中禁外部变更"（读可以插队，写返回 E_BUSY），然后按 agent loop
同序过闸——先 plan 后 budget，用**同一个** BudgetGuard 实例（MCP 花销和模型
花销同池 $5/600s）；dispatch 时浅拷贝 ToolContext 换 emit_progress 防进度
串流；成功失败都 commit 实际耗时；并把 tool_exec_* 事件带 origin:"mcp" 镜像
进 SSE 和持久 transcript——网页端免费看到 MCP 驱动的每一步。

---

## 3. Direction B — Lumeri consuming external MCP servers

### 3.1 D9: Config, namespacing, ingestion, dispatch adapter

**Config file: `~/.gemia/mcp.json`** (local/private, same directory
convention as `config.json`; never copied into shared memory or logs):

```json
{
  "mcpServers": {
    "blender": {
      "command": "/Users/me/Code/blender-mcp/start.sh",
      "args": [],
      "env": {},
      "enabled": true,
      "budget": {
        "default_usd": 0.02,
        "default_eta_sec": 10,
        "overrides": {
          "render_scene": {"usd": 0.0, "eta_sec": 120}
        }
      }
    }
  }
}
```

Shape deliberately mirrors Claude Code's `.mcp.json` (`command`/`args`/`env`)
so users copy entries verbatim; `url` is reserved for HTTP servers but
**Phase 3 implements stdio only** (scope control; HTTP client adds auth
questions we deferred). The `budget` block is the Lumeri extension (§3.3).

**Namespacing:** ingested tools are named `mcp__<server>__<tool>`.
Collision-proof by construction: internal `TOOL_NAMES` never contain a
double underscore (verifiable over `_schema.py:1718`; add the assertion to
the Phase 3 test). Matches the convention this machine's agents already
read daily, and survives Gemini's function-name charset (`[a-zA-Z0-9_.-]`,
≤64 chars — enforce at ingestion: names exceeding limits are dropped with a
logged warning, not truncated, because truncation re-introduces collisions).

**Ingestion point:** session creation. A new `gemia/mcp/client_hub.py`
(`McpClientHub`) is constructed inside `SessionRunner._create_agent`
(`session_manager.py:124-131`) when `mcp.json` exists: spawn each enabled
stdio server, `initialize`, `tools/list`, convert each MCP `inputSchema`
into the OpenAI function shape via `_tool(...)`'s structure
(`_schema.py:29-46` — inverse of Direction A's transform), and hand
`AgentLoopV3` two things: `extra_tool_schemas: list[dict]` and
`extra_dispatch: dict[str, Dispatcher]` whose coroutines call
`session.call_tool()` on the hub and adapt results
(text/structuredContent → tool-result dict; MCP `isError` → `ToolError`
with `recovery=transient_retry` for connection-class failures, `fix_args`
for validation-class). Snapshot-per-session: `tools/list_changed` is **not**
tracked in Phase 3 (documented limitation; the next session picks changes
up). Server processes are owned by the hub and terminated in
`SessionRunner.close`.

**Agent-loop seam (the only loop edit Direction B needs):** today the loop
uses module globals — `tools=TOOL_SCHEMAS` at `agent_loop_v3.py:1046` and
`DISPATCHER[tc.name]` at `:1341`. Both become instance-level:
`self._tool_schemas = TOOL_SCHEMAS + extra_tool_schemas` and
`self._dispatch(name)` = `DISPATCHER.get(name) or extra_dispatch.get(name)`
(miss → existing unknown-tool error path). No behavior change when
`extra_*` is empty — a pure seam, safe to land ahead of Phase 3.

### 3.2 D10: The plan-mode wrinkle

Verified against the source: `is_plan_safe` is
`return tool_name in PLAN_ALLOWED_TOOLS` (`gemia/plan_mode.py:83-86`) — any
name outside the static frozensets returns `False`. **Already fail-closed**;
dynamic `mcp__*` names are blocked with zero code change at the gate
(`agent_loop_v3.py:1206` applies it uniformly).

Locked policy: **external MCP tools are always plan-blocked.** We cannot
statically audit third-party side effects, and the alternative — trusting
the server's `readOnlyHint` annotation — is rejected because annotations
are unverified metadata from code we didn't write; plan mode's whole design
derives classification "by reading every dispatcher implementation, not
from tool names" (`plan_mode.py:10-11`). A read-only external tool being
blocked during planning is a tolerable annoyance; a "read-only" tool that
mutates is a broken safety promise.

Test impact:
- `tests/test_plan_mode.py::test_every_registered_tool_is_classified`
  (`tests/test_plan_mode.py:40-49`) asserts
  `PLAN_ALLOWED_TOOLS | PLAN_BLOCKED_TOOLS == set(TOOL_NAMES)` — it stays
  **scoped to static `TOOL_NAMES`** and is untouched: dynamic names are not
  in `TOOL_NAMES`, so exact coverage still holds.
- Add `test_external_mcp_tools_are_plan_blocked`: assert
  `is_plan_safe("mcp__anyserver__anytool") is False` and that a plan-mode
  session gates a dispatched `mcp__*` call with `E_PLAN_MODE` (reuse the
  fixture pattern of `tests/test_plan_mode.py:126`).
- Document the policy line in `plan_mode.py`'s module docstring (one
  sentence, next to the fail-closed paragraph at `plan_mode.py:21-23`).

### 3.3 D11: Budget for unknown external tools

Today `BudgetGuard.estimate` returns `(0.0, 5.0)` for any unknown name
(`budget_guard.py:135-139`) — external tools would be **free**, which
undercounts real time and lets a chatty turn dodge the time cap. Extension:

```python
def __init__(self, *, max_usd=5.0, max_seconds=600.0,
             extra_costs: dict[str, dict[str, float]] | None = None): ...

def estimate(self, tool_name):
    entry = _TOOL_COSTS.get(tool_name) or (self._extra_costs or {}).get(tool_name)
    if entry is not None:
        return float(entry["usd"]), float(entry["eta_sec"])
    if tool_name.startswith("mcp__"):
        return 0.02, 10.0          # conservative default for unknown external tools
    return 0.0, 5.0                # unchanged legacy fallback
```

`extra_costs` is assembled at ingestion from each server's `budget` block:
`mcp__<server>__<tool>` → override entry if present, else the server's
`default_usd`/`default_eta_sec`, else the hardcoded $0.02/10 s. Rationale
for a nonzero default: unknown external calls are network/process hops with
real latency and possibly real money behind them; $0.02/10 s means ~250
un-priced external calls exhaust a $5 session — a guardrail, not a wall.
`commit` already prefers actuals for seconds (`agent_loop_v3.py:1344`), so
eta misestimates self-correct. `_TOOL_COSTS` itself is untouched — static
verbs never consult `extra_costs` (static table wins on lookup order).

**中文小结**：消费方向：`~/.gemia/mcp.json`（结构照抄 Claude Code 便于复制，
外加 Lumeri 专属 budget 段），工具名 `mcp__服务名__工具名`（内部名无双下划线，
天然防撞）；会话创建时 spawn+tools/list、schema 反向换成 OpenAI 形状，agent
loop 只开一个缝：TOOL_SCHEMAS/DISPATCHER 的两处使用改成实例级间接。plan mode
不用改闸——is_plan_safe 已 fail-closed（plan_mode.py:83-86 实读验证），锁死
"外部 MCP 工具一律被 plan 挡"（readOnlyHint 是未经验证的他人元数据，不信）；
覆盖测试仍只管静态 TOOL_NAMES，另加一条动态名被挡的测试。预算：未知外部工具
默认 $0.02/10s，可按服务器/按工具覆写，estimate 三段查找，静态表优先。

---

## 4. D8: SDK choice and protocol version

**Decision: official `mcp` Python SDK** (`modelcontextprotocol/python-sdk`),
added as an optional extra so the core install stays light (same precedent
as `[interop]`, `pyproject.toml:40-47`):

```toml
[project.optional-dependencies]
mcp = ["mcp>=1.12,<2"]
```

Checked 2026-07-06: no `mcp` dependency exists in `pyproject.toml` or
`uv.lock`, so this is a fresh, unconflicted add. Python floor is fine
(repo requires ≥3.12, SDK requires ≥3.10).

- **Why not hand-rolled JSON-RPC:** the surface is not one method — it is
  the initialize/capability lifecycle, version negotiation, tools,
  resources + templates + subscriptions, progress, cancellation, and the
  entire streamable-HTTP session/resumability state machine. Hand-rolling
  recreates exactly the "human-discipline protocol parity" failure mode
  this project just spent Phase 1 of the parity plan eliminating — except
  against an external spec we don't control.
- **Why the low-level `mcp.server.Server` API, not FastMCP decorators:**
  FastMCP generates schemas from Python signatures — but our schemas
  already exist as data (`TOOL_SCHEMAS`). Re-typing 25 signatures is 25
  drift opportunities; the low-level API accepts explicit
  `types.Tool(name, description, inputSchema, annotations)` built by the
  §2.3 transform, and a test can assert byte-equality with `_schema.py`.
  FastMCP remains fine for throwaway servers; not for a mirrored surface.
- **Protocol revision pin: `2025-06-18`.** Everything this design needs is
  ≤ that revision: streamable HTTP (since 2025-03-26), structured tool
  output & resource links (2025-06-18). Version negotiation is handled by
  the SDK; we accept a client's `2025-03-26` (we lose nothing we rely on)
  and reject `2024-11-05` (predates streamable HTTP; stdio-only clients
  that old can't consume our HTTP mode anyway and the SDK handles the
  refusal). Later revisions (e.g. the 2025-11 line) negotiate down to
  2025-06-18 — we do not depend on any post-06-18 feature, deliberately.

**中文小结**：用官方 `mcp` Python SDK（可选依赖 extra `[mcp]`，`mcp>=1.12,<2`；
已核实仓库 pyproject/uv.lock 现无 mcp 依赖）。不手搓 JSON-RPC——那等于对着外部
规范重演本项目刚消灭的"人肉协议对齐"。用底层 Server API 而非 FastMCP 装饰器：
schema 已是现成数据，机械变换 + 相等性测试即可，重新手写签名就是 25 个漂移点。
协议版本钉 2025-06-18（所需特性全部 ≤ 该版），接受 2025-03-26，拒绝 2024-11-05。

---

## 5. New code layout

```
gemia/mcp/__init__.py        # empty / exports
gemia/mcp/toolset.py         # MCP_TOOLSET, MCP_NATIVE_TOOLS, MCP_NATIVE_PLAN_SAFE,
                             # exclusion rationale docstring, schema transform
gemia/mcp/server.py          # build_server() -> mcp.server.Server; stdio + http runners
gemia/mcp/client_hub.py      # Phase 3: McpClientHub (config load, spawn, ingest, adapt)
tests/test_mcp_server.py     # Phase 1 gate (in-process harness)
tests/test_mcp_resources.py  # Phase 2 gate
tests/test_mcp_client_hub.py # Phase 3 gate
```

Touched existing files (each a small seam, all additive):
`gemia/__main__.py` (subcommand), `gemia/session_manager.py`
(`run_verb`), `server.py` (optional HTTP thread, Phase 2),
`gemia/budget_guard.py` (`extra_costs` + `mcp__` default, Phase 3),
`gemia/agent_loop_v3.py` (instance-level schemas/dispatch seam, Phase 3),
`gemia/plan_mode.py` (docstring sentence, Phase 3), `pyproject.toml`
(`[mcp]` extra, Phase 1).

---

## 6. Phased delivery and test gates

**Phase 1 — stdio server, read+timeline toolset (one codex session).**
Deliverables: `gemia/mcp/toolset.py` + `gemia/mcp/server.py` (stdio only),
`SessionRunner.run_verb`, `__main__` subcommand, `[mcp]` extra,
`tests/test_mcp_server.py`.
Test gate — **in-process MCP client harness**, no subprocess, no network,
never touches the live sidecar on 7788: use the SDK's in-memory paired
streams (`mcp.shared.memory.create_connected_server_and_client_session`)
to drive the real `Server` object. Required cases:
1. initialize handshake succeeds; negotiated `protocolVersion` ∈
   {2025-06-18, 2025-03-26}.
2. `tools/list` == the Phase 1 frozen set **exactly** (drift test against
   `MCP_TOOLSET` — a newly added internal verb must NOT leak into MCP
   without a deliberate toolset edit; mirror of the plan-mode
   exact-coverage philosophy).
3. Schema parity: every 1:1 tool's `inputSchema` equals the §2.3 transform
   of its `_schema.py` entry.
4. Functional path: `create_session` → `import_media(fixture.mp4)` →
   `timeline_insert_clip` → `get_timeline` shows the clip (fixtures reuse
   the timeline test assets).
5. Plan gate: `runner.set_plan_mode(True)`; `timeline_insert_clip` over MCP
   → `isError` with `E_PLAN_MODE`; `get_timeline` still succeeds.
6. Budget gate: construct the runner's guard with a tiny cap;
   verb → `E_BUDGET` payload carries `reason` + `alternatives`.
7. Exclusion lock: `run_shell`, `generate_video`, `remember` absent from
   `tools/list`; `run_verb("run_shell", ...)` refuses (§2.6 step 1).
8. Turn-collision: with `turn_in_progress` forced true, mutating verb →
   `E_BUSY`; read verb succeeds.
9. SSE mirror: transcript/ring contains `tool_exec_start/result` with
   `origin: "mcp"` for an MCP-driven call.
Acceptance: `python3 -m pytest tests/test_mcp_server.py` green locally
(local pytest is the accepted signal for this project), plus a manual
smoke: `claude mcp add lumeri -- python -m gemia mcp-serve` and drive one
insert from Claude Code.

**Phase 2 — render/export + resources + progress + HTTP.**
Adds the 7 Phase 2 tools, `lumeri://` resources + templates +
`list_changed`, progress-notification forwarding, and the in-process
HTTP transport thread (port 7789, `Origin` check, `LUMERI_MCP_HTTP=1`).
Test gate: `tests/test_mcp_resources.py` (resource list/read shapes; asset
descriptor contains an existing absolute path; timeline resource equals
the HTTP payload dict), progress test (fake dispatcher emits
`ProgressUpdate`s → client receives ordered `notifications/progress`), and
an ASGI-level HTTP test using the SDK client against the mounted app on an
ephemeral port — still never 7788.

**Phase 3 — consuming direction.**
`client_hub.py`, `mcp.json` loading, agent-loop seam, budget extension,
plan-block test, and the two cosmetic frontend label mappings for
`mcp__*` names (web `static/v3/v3.js` toolLabel + CLI `src/format.js`,
same commit per the parity plan's label rule — display-only, drift warns
not blocks). Test gate: `tests/test_mcp_client_hub.py` spins a toy MCP
server **in-process** (SDK memory streams again) exposing one echo tool;
asserts namespacing, schema conversion shape, dispatch round-trip,
`is_plan_safe` blocking, and `estimate("mcp__toy__echo") == (0.02, 10.0)`
without config / override values with config.

**中文小结**：三阶段交付。Phase 1（一个 codex session 可完成）：stdio 服务器 +
18 个读/时间线工具 + run_verb + 内存内 MCP 客户端测试挂具（九条用例：握手、
工具面精确相等防漏、schema 相等、功能链路、plan 闸、预算闸、排除锁、回合冲突、
SSE 镜像），全程不碰 7788 活服务。Phase 2：渲染导出 + 资源 + 进度 + 7789 HTTP。
Phase 3：消费方向全套 + 双端 mcp__ 工具名美化（仅展示层，同 commit）。

---

## 7. Contract and parity impact — stated explicitly

- **No new SSE event kinds.** MCP-driven dispatches reuse
  `tool_exec_start` / `tool_exec_progress` / `tool_exec_result` /
  `tool_exec_error` with one additive field `origin: "mcp"`. Per
  `v3_contract.py:20-22`, additive content is non-breaking and does **not**
  bump `PROTOCOL_VERSION`; `EVENT_KINDS` is untouched, so
  `tests/test_v3_contract.py` and lumeri-cli `test/contract.mjs` stay green
  with zero edits. MCP is a **parallel front door**, not a protocol change.
- **No new error codes in the frozen set**: `E_PLAN_MODE`, `E_BUDGET`,
  `E_BUSY` already exist in `ERROR_CODES` (`v3_contract.py:77-93`).
- **CLI parity rule** ("protocol changes land web + CLI + tests same
  commit") is therefore **not triggered** by Phases 1–2. Phase 3 touches
  both frontends only for the optional tool-label niceties, which the
  parity plan already classifies as warn-not-block.
- The MCP toolset itself is a **new, separate contract surface** with its
  own single source of truth (`gemia/mcp/toolset.py`) and its own exact
  drift test (Phase 1 gate case 2) — same mechanism-over-memory philosophy,
  different frozen set.

**中文小结**：对现有 SSE 契约零影响：不加 kind、不升 PROTOCOL_VERSION，只在
tool_exec_* 事件上加 origin:"mcp" 附加字段；错误码全在既有冻结集内；双端契约
测试无需改动，Phase 1-2 不触发"同 commit 双端同步"规则。MCP 工具面自身则是一
个新的独立契约面，在 toolset.py 冻结并配精确漂移测试。

---

## 8. Non-goals and open questions

Non-goals (this cycle): remote/multi-user access; auth (security pass);
MCP prompts capability (nothing to offer yet); exposing `elicit`;
mid-flight cancellation of ffmpeg dispatches; stdio-process attach to a
running 7788; HTTP MCP client (Direction B is stdio-only).

Open questions for the implementer to surface (not blockers):
1. Whether `get_session` should also return the last N transcript events —
   useful for an MCP agent resuming context; cheap now that the durable
   transcript exists (commit `0142052`). Lean yes, cap N=50.
2. Whether Phase 2 should add `undo` depth >1 semantics to MCP
   `timeline_undo` args (internal verb already supports steps) — follow the
   internal schema, yes by construction.
3. Per-server `enabled:false` semantics on a *running* session (currently:
   next session only). Acceptable.
