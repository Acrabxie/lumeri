# Session & Project Durability Plan（会话与项目持久性）

Status: Phase 1 (durable transcript) SHIPPED 2026-07-06. Phases 2-4 are
decision-complete but need one explicit user choice each before execution.

问题（gnarl-scan P3）：session ≡ project ≡ workdir，默认根在 `/tmp/lumeri-v3`
（session_manager.py:33）——重启即蒸发；2 小时闲置清扫连 workdir 一起删
（`cleanup_idle` + `_remove_workdir`）；对话与事件只活在内存和 200 条的 SSE
replay 环形缓冲里；budget 是终身 $5/600s，无重置，长会话慢慢变砖。

## Phase 1 — Durable event transcript ✅ SHIPPED

- Every agent-emitted event appends to `<sessions_root>/<sid>/transcript.jsonl`
  (`SessionRunner._emit_event`, before SSE fan-out; write failure disables
  itself and never blocks the loop). Per-connection synthetic frames
  (`protocol_hello`, `replay_gap`) are transport-level and correctly excluded.
- `GET /sessions/{id}/transcript` (NDJSON, `?since_seq=N`) serves it **even
  after the session closed or the server restarted** — this is the resync
  source that the 200-event ring buffer never was.
- Line shape: `{"seq": n, "ts": epoch, "event": {…}}`. Size policy: none yet —
  a chatty session's text deltas are the bulk; see Phase 4.

Frontend consumption is deliberately Phase 2 (additive endpoint; no protocol
kinds changed, so the parity rule's same-commit clause is satisfied by
backend tests + this doc note).

## Phase 2 — Client resync from transcript（待实施，可委派）

- On `replay_gap`, both frontends currently abandon the turn and resync
  state. Upgrade: fetch `?since_seq=` from the transcript and REPLAY missed
  events through the normal dispatch (web `dispatch()`, CLI `handleEvent`)
  before resuming the live stream. Needs a seq↔SSE-id bridge: recommend the
  transcript seq becomes the authoritative cursor and clients persist it
  alongside lastEventId.
- Parity: web + CLI + tests in one commit (protocol semantics change).

## Phase 3 — Project root decoupled from session lifetime（需用户拍板 ①②）

今天的现实：project 目录（patch log、state.json、lumenframe.json、outline）
住在 per-session workdir 里，会话清扫 = 项目删除。

- **决定①（存储根）**：默认输出根从 `/tmp/lumeri-v3` 迁到哪？
  推荐：`LUMERI_V3_OUTPUT_ROOT` 默认改为 `~/.gemia/v3`（系统盘小而稳），
  媒体产物大文件继续偏好外置 SSD（`/Volumes/Extreme SSD/GemiaTemp/v3-media`，
  挂载缺席时回落本地）。备选：全部进 SSD（快但拔盘=服务降级）。
- **决定②（清扫语义）**：闲置清扫改为"关 runner、保留 project 目录 +
  transcript，只删 transient workdir 子目录（临时下载、代理帧）"。
  重新打开 = 新 session 绑定既有 project（`ProjectHandle.open` 已支持按
  id 打开——见 project_store.py）。反对意见：磁盘无限增长 → 配套一个
  `lumeri gc` 手动清理命令而不是自动删用户作品。
- 实施量：session_manager `_remove_workdir` 拆分 transient/durable 路径 +
  `POST /sessions` 接受 `project_id` 复用 + 双端"重新打开项目"入口。
  codex 一个会话量，但要先拍板①②。

## Phase 4 — Budget lifecycle（需用户拍板 ③）

- 现状：BudgetGuard $5/600s 每会话终身，`budget_gate` 后只能弃会话。
- **决定③**：推荐 `POST /sessions/{id}/budget` `{add_usd, add_seconds}`
  （本地单用户下就是"用户点一下继续"；将来接计费时这条路由天然变成
  扣费点）。两端 budget_gate 横幅加"追加预算"按钮。协议影响：新路由 +
  横幅交互，走 parity 同 commit 规则；无新 SSE kind（budget_gate 已存在）。
- Transcript 体积也归这里：超过 50MB 的 transcript 在会话关闭时 gzip 归档
  （`transcript.jsonl.gz`），路由透明解压——实现时进同一个 commit。

## 排序建议

Phase 2（resync）和 Phase 4（budget top-up）都是 codex 级、各一个会话量；
Phase 3 是真正把 Lumeri 从"演示"变"日用工具"的那一步，拍板①②后也是
codex 可执行的量。三者互不阻塞。
