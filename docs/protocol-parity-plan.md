# Web v3 / CLI 协议对等计划（Protocol Parity Plan）

状态: 已定稿，待实施（Phase 1 未开始）
制定日期: 2026-07-06
适用范围: `/Volumes/Extreme SSD/gemia`（后端 + web v3）与 `~/Code/lumeri-cli`（TUI 客户端）

## 问题

Web v3 与 CLI 的协议一致性目前完全靠人肉纪律维持：

- 后端事件契约散落在 `gemia/agent_loop_v3.py` / `gemia/v3_routes.py` 的 emit 调用里（当前 14 种
  kind：`turn_start`、`model_text_delta`、`model_tool_call_start`、`model_tool_call_ready`、
  `tool_exec_start`、`tool_exec_progress`、`tool_exec_result`、`tool_exec_error`、`timeline_op`、
  `budget_gate`、`completion_check`、`turn_wrapup`、`turn_complete`、`turn_error`，另有
  ask/replay 相关事件）。
- Web 端 `static/v3/v3.js` 用 handlers 对象表逐 kind 处理（约 374 行起），未知 kind 弹 banner。
- CLI 端 `src/App.js` 的 `handleEvent` 用 switch 手工镜像，代码里 6+ 处注释写着
  "mirrors gemia static/v3/v3.js handlers"。
- 没有任何一处机器可读的单一事实源。后端加一个事件 kind，两个前端各自默默渲染
  "unknown event"，只能靠人记得去同步。

## 目标

功能对等由**机制**保证，不靠记忆。后端协议变更时，忘记同步任何一端会导致**测试失败**，
而不是运行时静默降级。

## Phase 1 — 单一事实源 + 漂移测试（首要，1-2 个工作日）

1. 新建 `gemia/v3_contract.py`：纯数据模块，作为协议唯一事实源。内容：
   - `PROTOCOL_VERSION: int`（从 1 开始，语义化递增）
   - `EVENT_KINDS: frozenset[str]`（后端可能 emit 的全部 kind）
   - `ERROR_CODES: frozenset[str]`（typed GemiaError 的稳定 error_code 全集）
   - `ASK_CONTROLS: frozenset[str]`（ask_question 的控件类型：select/multi_select/slider/panel…）
2. 新建 `scripts/export_contract.py`：把上述数据导出为 `static/v3/contract.json`，
   并同步一份到 `~/Code/lumeri-cli/src/contract.json`（同机开发，直接写文件；
   CLI 仓库 vendored 一份保证自包含）。
3. 后端漂移测试 `tests/test_v3_contract.py`：
   - 断言 agent loop / v3_routes 实际 emit 的 kind ⊆ `EVENT_KINDS`
     （grep 源码或 hook emit 函数均可，与现有测试风格一致）；
   - 断言 `static/v3/v3.js` handlers 表的 key 集合 ⊇ `EVENT_KINDS`（正则提取即可）；
   - 断言 `contract.json` 与 `v3_contract.py` 一致（防导出遗忘）。
4. CLI 漂移测试 `test/contract.mjs`（加入 npm test 链）：
   - 断言 `App.js` switch 的 case 集合 ⊇ `src/contract.json` 的 EVENT_KINDS；
   - 断言 ask 控件渲染（`src/ask.js`）覆盖 ASK_CONTROLS 全集。
5. `GET /sessions/{id}` 与 SSE 首事件带 `protocol_version` 字段；CLI 连接时校验，
   不匹配则显示醒目警告（不阻断）。

验收标准：在后端随手加一个假事件 kind 而不同步前端，`pytest tests/test_v3_contract.py`
和 CLI `npm test` 必须至少一个变红。

## Phase 2 — 收编现有约定（Phase 1 落地后 1 周内）

- 两端"未知事件必弹可见 banner"从注释约定升格为契约测试
  （mock server 发未知 kind，断言两端渲染出 banner——CLI 已有类似断言基础设施）。
- 工具 label 映射（CLI `src/format.js` 的 toolLabel 与 web 端对应物）纳入 contract
  的可选段 `TOOL_LABELS`，漂移只警告不阻断（展示文案允许两端有差异）。
- 清理 CLI `src/providers/`（约 600 行无运行路径引用的死代码 + 20 个陪跑测试）：
  接线或删除，二选一，不再挂账。

## Phase 3 — 可选远期

- contract 驱动 codegen（生成两端 handler 骨架）或抽成共享包。
  仅当协议进入高频变更期再做，现在不投入。

## 规则（即日生效，机制未落地前靠此约束）

1. **协议变更三件套同 commit**：凡新增/修改事件 kind、error_code、ask 控件、
   `/sessions*` 请求/响应形状，同一次提交必须包含：contract（Phase 1 前为本文档附录清单）
   + web v3 handler + CLI handler + 两端测试。任何一端缺失，QUEUE.md 里不得标记 done。
2. **验收以本地 CLI 跑测试为准**（这是本项目的主测试通道，GitHub CI 仅为补充）：
   - 后端：`python3 -m pytest tests/test_v3_*.py`（协议相关至少这些）
   - CLI：`cd ~/Code/lumeri-cli && npm test`
   - QUEUE.md 的 verification 字段必须同时给出两端结果。
3. **contract-first**：新事件 kind 先进 contract 再写实现。
4. **未知事件必须可见**：两端禁止静默丢弃未知 kind（现状已满足，违反视为 bug）。
5. **协议同步责任随改动走**：谁改后端协议，谁负责两端同步，不得留 "CLI 稍后补" 的尾巴。
