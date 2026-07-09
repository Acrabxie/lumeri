# Lumeri Video / Deck 协同结构

- status: v1（2026-07-08，随 deck Slice 1 落地）
- 姊妹文档: `docs/deck-interactive-video-plan.md`（deck 架构 spec，本文引用其编号）
- 一句话: **一台引擎，两条产品线**——video 与 deck 不是两个产品各自烟囱，而是同一个五层栈上的两个域；未来 Audio/Image 按同一模式接入。

## 1. 五层协同结构

```
L4 投影      video: .mp4 / 各档位导出        deck: presentation mode / .pptx / .html / .mp4
              └────────────┬────────────────────────────┬─────────────┘
L3 物化      ————— 同一条 clip timeline（V*/OV*/A* 轨 + project_export + undo + timeline_op 事件）—————
              assemble_shotlist ↗                        ↖ assemble_deck（Phase 1b）
L2 域 IR     shotlist（project_state.shotlist）    deck（project_state.deck）
              draft_shotlist / update_shot          draft_deck / update_slide（同构，见 §2）
L1 创作地基  检索双工具(search_media/search_frames) · generate_image/video · 字体解析(gemia/video/fonts)
              文字测量/断行原语(Day4 落地) · theme token(design-manuals: DESIGN.md + DECK.md) · narrate/subtitle
L0 平台      session/agent loop/plan mode · apply_ops patch-log(单 op 路径+undo) · AssetRegistry/媒体库
              v3 契约(SSE/路由) · budget_guard · skills · 账户/登录 · web v3 壳 + CLI 瘦客户端
```

协同的核心判据：**新能力先问落在哪一层。** 落 L0/L1 = 两条产品线同时受益（优先做成共享件）；落 L2/L4 = 域专属（进各自模块）。L3 是本架构的关键汇合点——deck 物化到与 video 同一条 timeline（spec §0 决策），所以导出、undo、事件、色彩管理（DAY3 的 Rec.709/H.265 档位）对 deck 自动生效，反向地 deck 逼出来的地基件（文字测量、token 分层）也回馈 video。

## 2. 域 IR 同构：shotlist ↔ deck

两个 IR 是刻意同构的（spec §2.2 迁移表）：shot 与 slide 都是「一屏叙事单元」，narration↔notes、on_screen_text↔text blocks、素材引用↔image blocks。同构不是美学，是转换通道的地基：

### 2.1 shotlist → deck（已实现，Slice 1：`draft_deck(from_shotlist=true)`）

宣传片分镜一键转 pitch deck 骨架：每 shot 一页 + 自动封面页；narration 变讲稿、on_screen_text 变标题块、已生成素材直接挂 image block、mood 众数上移为 theme.mood、duration 变 build 驻留。**dogfood 闭环**：《One Lumen》/宣传片的分镜与素材直接喂 Lumeri 产品介绍 deck，一份创作两种交付物。

### 2.2 deck → video（Phase 1b：`export_deck(mp4)`）

deck 沿 default_path 压平为影片（builds 按 dwell_sec 驻留），notes 可选经既有 `narrate` 合成旁白进 A1——deck 一键变带旁白的产品短片。video 是 deck 的退化情形，这条通道本质是免费的（L3 汇合的直接红利）。

### 2.3 素材库共享（零成本，已生效）

同一账户的 AssetRegistry/媒体库/FTS 标注对两个域透明：video 里 generate_image 的素材、search_media 的标注命中，deck 直接引用 asset_id；反之 deck 生成的示意图也进同一个库。

## 3. 模块边界规则（防两域互相拖累）

1. **deck 域专属代码进 `gemia/tools/deck*.py` 与（Phase 1b 起）`gemia/deck/`**（layout 引擎、光栅器、pptx 导出器）。
2. **video 主链（timeline/export/render/agent loop）不 import 任何 deck 模块**——deck 对 video 是纯增量，回归红线以此为界（见 §5）。
3. **deck v1 不 import lumenframe**（spec §0 物化决策；lumenframe 运动创作层 Phase 3 再接，接法=bake 成资产回 timeline，仍不产生 deck→lumenframe 的运行时耦合）。
4. **跨域共享原语放 L1**，不放任何一个域内：文字测量/断行 → 独立模块（gemia/text/ 或 fonts 侧，Day4 落点定）；字体解析已在 `gemia/video/fonts.py`（历史路径，deck 直接 import，不复制）。
5. **token 分层**：品牌层（色板/mood 词汇/logo）在 DESIGN.md，两域共享；域扩展层各自一个文件（video 用 DESIGN.md 本体的字幕/调色组，deck 用 DECK.md 的版式组）。禁止 deck 代码硬编码任何色值/字号——一律 token 引用（spec §3.4）。

## 4. 与 fiveday 循环（video 能力线）的交接协议

- **Day4（文字/标题动画）→ deck**：spec §7 五条约束已入共享 QUEUE。Day4 产出的文字测量/断行原语与具名动画预设目录是 L1 共享件，deck 布局引擎（Phase 1a 后半）直接消费，**不自行再造**。
- **合并顺序**：`feat/fiveday-promo` 先合主线，`feat/deck-interactive` 随后 rebase 再合（本分支 off fiveday-promo 尖端 6dcb3bd，QUEUE 已记后置合并债 +1）。
- **冲突面预判**：两分支共同触碰 `_schema.py`/`__init__.py`/`plan_mode.py`/`budget_guard.py`/`system_v3.md`（都是注册型追加，冲突为行级、机械可解）；`project_model.py`/`lumerai/patches.py` 目前仅 deck 侧改。deck 侧凡改共享注册文件，一律**追加式、不重排既有条目**，把合并面压到最小。

## 5. 回归红线（协同的安全带）

- 分支点 6dcb3bd 全量基线快照：**3 failed / 2380 passed / 88 skipped**（test_memory_log / test_v3_contract::cli_vendored / test_v3_generate_image，均既有 env/跨仓类）。此后任何新增失败即回归，不做临场豁免。
- 必绿套件：`test_v3_tool_protocol`（DISPATCHER 不变量）、`test_plan_mode`（完备性 fail-closed）、shotlist/draft_shotlist 套件（同构先例不许被 deck 改动波及）、`test_deck_ir`（金标准）。
- 测试纪律：`TMPDIR=/tmp` + 主仓 `.venv/bin/python -m pytest`（cwd 在 worktree 遮蔽 editable）；一切测试会话根走 APFS /tmp，绝不触真实 `~/.gemia`。

## 6. 面向未来的接入模式（Audio/Image 复用同一模板）

一个新域接入 Lumeri = ①一份域 IR（project_state.<domain>，normalize+透传+patch op 三件套）②draft_<domain>/update_* 工具四件套 + 注册五处接线 ③一个 assemble_*（物化到 clip timeline 或注册资产）④域投影导出。deck 是这个模板的第一次完整走样（video 是原生长出来的），Audio/Image 照抄本文件与 deck Slice 1 的 diff 即可起步。
