# Identity Architecture Plan（身份架构：从进程全局到 per-request）

Status: Phase 0 was already true; Phase 1 SHIPPED 2026-07-06. Phases 2-3 are
spec'd below; Phase 2 belongs to the deferred one-shot security pass.

问题：`accounts.current_account_id()`（accounts.py:149）读一个进程全局
`active.json`。所有客户端共享一个身份；任何客户端 `POST /accounts/switch`
会把其它已打开 UI 的 /library、/auth/session、session 快照悄悄换成另一个人
的。整套邮箱码 + Google 登录盖在这个单用户假设上，且每加一条新路由，
改造成本就涨一分。

## Phase 0 — v3 sessions bind at creation ✅（既有事实）

v3 会话在创建时把 `account_id` 固化进 runner/ToolContext（v3_routes
`_create_session` → `create_session(account_id=…)`）。会话内的一切以创建时
身份为准，中途 switch 不影响已开会话。

## Phase 1 — the per-request seam ✅ SHIPPED

- `gemia/identity.py::resolve_account_id(handler)`：解析顺序 =
  ① `X-Lumeri-Account` 请求头 pin（仅当该账户本地存在；**显式 pin 到未知/
  非法账户 → None → 401，绝不静默回落**——"陈旧 pin 冒充全局活跃账户"正是
  要杀死的 bug 类）② 全局 `active.json` 回落（无 pin 的既有客户端行为不变）。
- 迁移完成的调用点：`_require_account`（汇聚点，覆盖其全部调用者）、
  server.py 全部 handler 内联点（session-history / project/current /
  media-library annotations / creative sandbox workspace 等 18 处）、
  v3 `_create_session`。唯一保留全局读的是 `_session_health`
  （健康探针，无请求上下文，语义正确）。
- **信任模型（刻意的，安全 pass 前）**：header ≠ 鉴权。localhost-first，
  且能碰到 socket 的人本来就能 `POST /accounts/switch` 翻全局。Phase 1
  杀死的是**跨客户端身份串扰**，不是伪造。伪造防护是 Phase 2 在同一条缝
  上做的事。

## Phase 2 — signed session tokens（并入推迟的安全 pass，勿提前做）

- 登录成功时（邮箱码 / Google 回调）签发本地会话 token：
  `token = base64(account_id + exp + HMAC(server_secret, …))`，secret 首启
  生成存 `~/.gemia/server_secret`（0600）。
- 客户端（web cookie `lumeri_session`；CLI 保存在 `~/.lumeri/credentials`
  并经 `X-Lumeri-Account` 同一 header 携带 token 而非裸 id）。
- `resolve_account_id` 的 pin 分支从"header 指名账户"升级为"验签 token"，
  **一函数收口，路由零改动**——这就是今天建缝的全部意义。
- 同批必做：`/accounts/switch` 降级为"改本客户端的 token"，全局
  `active.json` 只作单客户端桌面兼容；0.0.0.0 绑定下强制要求 token。
- 按用户决定，这一步**不要在功能期做**——它和 0.0.0.0 鉴权、/config 暴露
  同属收尾安全 pass。

## Phase 3 — client adoption + full multi-tenancy（Phase 2 之后）

- CLI：登录后每个请求自动带 pin（api.js `request()` 一处加 header）；web
  同源默认可不 pin。双端 + 测试同 commit（parity 规则）。
- 媒体库/标注已按 account_id 分库（`account_root/media/library.sqlite3`），
  无需改造；session-history 亦已带 account_id 参数。剩余工作是清点任何
  仍隐式依赖"全局当前账户"的读路径（grep `current_account()` 的使用者）。
- 真多租户（局域网多人）在产品需要前不做；到时的增量 = 会话列表按账户
  过滤 + 每账户会话上限。

## 验收

Phase 1 测试：tests/test_identity_seam.py（pin 覆盖全局 / 未知 pin=None 不
回落 / 恶意 pin 不 500 / 空 pin 回落 / 并发请求隔离）。Phase 2/3 各自带
红绿测试进 commit。
