# Lumeri Deck：交互视频架构方案（v2.1）

- status: reviewed draft（2026-07-08；v2 初稿经四视角对抗评审——代码现实/pptx 可行性/产品质量/工程纪律，原始 41 条发现（跨视角去重 27 条，含 5 P0）全部整合，另经一轮发布前核对修正整合期矛盾）
- author: Claude Code（架构方向与 Acrab 对话定案：**PPT = 结构化可交互的视频**）
- supersedes: 2026-06-21 的 deck-domain-skeleton「独立域插件」设计（处置见 §10.3）
- 参考基线代码: worktree `GemiaTemp/worktrees/fiveday-promo`（分支 `feat/fiveday-promo`）；正式开工基线见 §10.1

## 0. 一句话架构：概念与物化分离

> **概念层：deck = 幻灯片序列 + hold 点（等待交互才继续）+ 交互图（触发边）。video = 它的退化情形（线性路径、自动推进、零 hold）。**

历史背书：Flash（timeline + `stop()` + `gotoAndPlay`）、Prezi（画布 + 相机路径 + 点击驻留）、Rive（timeline + 状态机）、PowerPoint 自身的动画模型（点击触发的时间线片段）。

**物化层（评审后的关键决策，v1 已定）：deck 物化到 clip timeline，不物化到 lumenframe composition。** 理由：

1. `assemble_shotlist` 真先例物化的就是 clip timeline（timeline_* verbs，每步进 patch log）；
2. 唯一 broadcast 级成片链是 timeline→`project_export`（One Lumen iter04-06 端到端验证：V1 图 + OV1 字 + A1 音合成、cover 满幅 c888bf9、CJK drawtext fontfile e8e3ba9）；
3. lumenframe 文档（`lumenframe.json`）刻意存在于 apply_ops patch-log **之外**（`gemia/tools/layer.py` 明言 orthogonal、`_save_lumendoc` 直写文件、无 undo、无 on_patch 事件），`project_export` 全文件零处引用 lumenframe，浏览器侧零 lumenframe 渲染能力，lumenframe 唯一导出通路 `render_range` 是 mp4v 预览级编码且无音频。走 composition 路线 = 四条缝全要新桥，Phase 1 必翻车。

「slide = lumenframe composition」**不是被否决，是被排期**：作为 Phase 3 的运动创作层（slide comp 做 Keynote 级 keyframe 动效 → bake 成视频资产回填 timeline），届时需一并解决 broadcast 编码桥、音频 mux、CJK resolver、与 patch-log 的一致性模型四项桥接成本（见 §8 Phase 3）。

为什么放弃 6/21 的独立域设计：独立 IR/verb 面/渲染路径 = 二套 undo、二套契约、二套导出。本方案 deck 复用同一条 `apply_ops` patch-log、同一个工具协议、同一条被验证的导出链；新增面收敛为：交互图 IR、布局引擎（placed blocks）、slide 光栅器、pptx 投影、播放器 presentation mode。

## 1. 设计原则与目标用户

1. **Motion-native，pptx 是投影不是母体。** Lumeri 播放器是第一放映媒介；`.pptx` 是兼容性投影。卖点 = 「演示文稿本身就是影片」。
2. **语义优先于像素。** 内容真相在 deck IR（blocks）与 placed blocks；像素（PNG/视频段）永远是投影产物。**禁止把文字烧进 generate_image 的画面**——这是 pptx 可编辑导出的生死约束。
3. **单 op 路径。** deck IR 的一切变更走 `ctx.project.apply_ops`；物化产物（PNG/视频段/timeline 排布）定义为**可由 IR 确定性重建的缓存**，一致性模型见 §3.1。
4. **契约先行。** 新 event kind / 新 `/sessions*` 路由先进契约再实现，双端同 commit（§4.3）。
5. **确定性菜谱。** 模型的自由度在「选模板/写 blocks/挑预设」，不在像素摆放（One Lumen 监工教训的直接应用）。

**目标用户（v1 诚实边界）**：solo builder / 自家 dogfood 的高光 pitch——要一份「放出来像影片」的演示，接受对话式 + 表单式修改文案。**不服务**（v1）：多人协作编辑、以 PowerPoint 为主编辑器的往返工作流。`.pptx` 导出是**单向逃生舱**：在 PowerPoint 里改过的文件不能回流 Lumeri，此单向门写进导出 manifest 与文档，不藏着。临场改字的需求由 Phase 2 的 blocks 级表单直编面板承接（§5.3），不逼用户出走 pptx。

## 2. Deck IR

### 2.1 Schema（存于 project_state，经 apply_ops 变更；时间一律秒制，与全库 seconds-canonical 一致）

```jsonc
project_state.deck = {
  "version": 1,
  "theme": {
    "tokens": { "...": "…" },        // 引用 design-manuals 的 deck 扩展层（§3.6，开工前置产物）
    "mood": "calm-tech",             // deck 级统一基调（评审采纳：mood 上移，防逐页拼贴感）
    "aspect": "16:9"
  },
  "slides": [
    {
      "id": "s1",
      "layout": "title",             // 布局模板名（§3.5）
      "title": "One Lumen",
      "blocks": [                     // 语义内容块 = 内容真相
        { "kind": "text",  "role": "title", "text": "…", "style_token": "type.display" },
        { "kind": "text",  "role": "body",  "bullets": ["…", "…"] },
        { "kind": "stat",  "value": "97", "label": "工具数" },        // v1 即有：pitch 数据页的 80%
        { "kind": "image", "asset_id": "img_003", "role": "hero" },
        { "kind": "shape", "shape": "rect", "role": "accent", "fill_token": "color.accent" },
        { "kind": "group", "children": [ /* 同构子块，如 3 张 feature 卡 */ ] }   // v1 即有：卡组/栅格
      ],
      "notes": "讲稿……",             // speaker notes；shotlist.narration 的直系后代
      "mood_override": null,          // 默认 null；仅调制 build 预设强度，不改 accent
      "builds": [                     // 有序 build-state 列表（状态序列由列表顺序定义）
        { "id": "b1", "dwell_sec": 1.2 }   // dwell_sec = 该状态在自动播放/mp4 压平路径下的默认驻留秒数（>0，否则 E_BAD_ARG）；
      ],                              // presentation mode 忽略 dwell，驻留至交互推进
      "links": [                      // 交互图出边；缺省只有隐式 advance
        { "trigger": "advance", "target": "next" },
        { "trigger": "hotspot:blk_cta", "target": "slide:s9" },
        { "trigger": "hotspot:blk_url", "target": "url:https://…" }
      ],
      "transition": { "kind": "cut" } // v1 默认 cut；可选单侧 fade。见 §3.4 转场约束
    }
  ],
  "default_path": ["s1", "s2", "…"]   // 必须覆盖全部 slides；.mp4 压平与自动播放沿此路径
}
```

- **stat / group 是 v1 一等块**（评审采纳）：固定四槽装不下 pitch deck 的三卡 feature 栏、大数字行、团队网格；事后补 = IR 升版 + 布局引擎槽位模型重做 + pptx 导出器改写。槽位模型从第一天就按「槽可容纳单块或同构 group」设计。
- **builds 属播放器语义**：物化与导出如何消化见 §3.3、§6。
- **交互图 v1 = advance + hotspot 树 + 少量跳边**；`timer`/条件分支/状态机明确 P3+。

### 2.2 与 shotlist IR 的同构迁移

| shotlist（已落地） | deck | 说明 |
|---|---|---|
| shot | slide | 同为「一屏叙事单元」 |
| `narration` | `notes` | 旁白 → 讲稿 |
| `on_screen_text` | `blocks[kind=text]` | 单字符串升格为结构化块 |
| `mood`（镜头级） | `theme.mood`（deck 级） | **有意偏离**：deck 观众前后翻页，逐页变基调呈拼贴感 |
| `search_query`/`source` | `blocks[kind=image]` + 检索填充 | 复用 `search_media`/`search_frames` |
| `duration` | builds 的 `dwell_sec`（仅自动播放/压平路径消费） | 演示态整页时长 =「直到交互」，事件驱动 |
| `set_shotlist`/`update_shot` | `set_deck`/`update_slide` | patch op 镜像 |
| `draft_shotlist` | `draft_deck` | 结构模板 promo/story → pitch/report/teach |
| `refine_shot` | `refine_slide` | 单页手术不重物化全份 |
| `assemble_shotlist`（物化到 **clip timeline**） | `assemble_deck`（同样物化到 clip timeline） | 先例与 v1 决策一致（§0） |

### 2.3 Ops 与校验语义

镜像 `gemia/tools/shotlist.py` 三件套：`dispatch_set` / `dispatch_update_slide` / `dispatch_get`，全部经 `project.apply_ops`。

**校验语义显式偏离 shotlist 先例**（评审采纳）：shotlist 的 `normalize_shotlist` 是「never raises、坏条目静默丢弃」；deck IR 有 shotlist 没有的**引用完整性**约束——`links.target` 悬空 slide id、`default_path` 未覆盖全部 slides、builds `dwell_sec ≤ 0` → `TimelinePatchError E_BAD_ARG`（结构容忍缺省回填，引用完整性严格拒绝），列金标准测试。

### 2.4 持久化接线（评审抓出的静默丢数据雷，必须与 ops 同 commit）

- `gemia/project_model.py`：`empty_project()` 加 `deck` 键 + `normalize_deck()` + `_normalize_canonical_project` 透传——否则 **`ProjectStore.load()` 每读一次就把 `project_state.deck` 无声剥掉**（normalize 从 empty_project 重建、只拷贝已知键）。
- `lumerai/patches.py`：`_OP_HANDLERS` 注册 `set_deck`/`update_slide`，否则 `E_OP_UNKNOWN`。
- **金标准测试（专杀静默剥离）**：`set_deck → store.load() → deck 逐字段幸存`。

### 2.5 placed blocks：唯一的投影源头（评审 P0 采纳）

布局引擎的输出不是「lumenframe 层树」而是一个显式中间 IR——**placed blocks**：

```jsonc
{ "block_ref": "…", "rect_px": [x, y, w, h],          // 画布像素系（左上原点），槽位解析结果
  "style": { "family": "…", "path": "…", "weight": 500, "size_px": 48, "color": "#…" },  // token 已展开
  "autofit": { "final_size_px": 44, "line_breaks": ["…", "…"] } }                          // 阶梯终态
```

**slide 光栅器（§3.2）与 pptx 导出器（§6.2）的几何与文字样式都只消费 placed blocks**（notes/links/slide 顺序/builds 等非几何字段取自 IR）。几何/缩字/断行只有一份实现；px→EMU/pt 是纯线性映射（§6.2）。若 pptx 导出器消费 raw blocks，就得再实现一遍槽位几何——初稿声称要杜绝的「分叉」会在几何层原样回归。

## 3. 物化与渲染

### 3.1 一致性模型：IR 是唯一 source of truth

`assemble_deck` 定义为**确定性、幂等的重物化**：同一 IR + theme + 模板版本 → byte-stable 的 placed blocks 与同一 timeline 排布；按 slide id 删除重建，禁止对物化产物做绕过 IR 的手工手术（system prompt + plan 分类双处立规）。`refine_slide` = 改 IR + 自动重物化该页。undo 回滚 IR 后，**重 assemble 必须收敛**（验收测试）。物化产物（PNG/视频段/timeline clip）= 可重建缓存，不承载真相。

### 3.2 slide 光栅器（新组件，Phase 1）

placed blocks → 每个 build-state 一张 PNG（画布原生分辨率，16:9 = 1920×1080 起，支持 2x 供高分屏）。实现要点：

- Pillow 合成，**字体一律经 `gemia.video.fonts.resolve_font_path`**（CJK 感知：pingfang/noto cjk/source han 候选链）。评审证实 lumenframe 自带 `_SYSTEM_FONT_CANDIDATES` 无一 CJK 字体、且 e8e3ba9 修复不在该路径上——deck 不走那条栈，此雷绕开，但**「中文 slide 首帧含 CJK 字形」列金标准测试**。
- 文字测量与断行用与布局引擎同一套原语（§3.5），保证「布局判断的断行 = 渲出来的断行」。
- 纯函数：placed blocks 进、PNG 出，无外部状态，金标准可断言。

### 3.3 builds / hold 的物化形态

- **静态驻留 = PNG**：播放器 hold 时显示该 build-state 的 PNG 原生分辨率原图——文字锐利，不吃 H.264 压缩（评审产品 P0：投屏文字发糊恰是 Keynote 用矢量渲染的强项，静态态用 PNG 正面回应）。
- **动效 = 预渲染微段**：build 入场/页间动效由服务端渲染为短视频段资产（Phase 2 起；编码在段边界强制 keyframe）。v1 动效收敛为「淡入/上移淡入」两个预设，Keynote 级 keyframe 动效走 Phase 3 lumenframe 路径。
- assemble_deck 产物：① 每页每 build-state PNG（注册进 AssetRegistry）② 页间/build 微段（Phase 2）③ timeline 排布（供 mp4 压平，§6.1）。

### 3.4 交互图与转场约束

- 交互图由播放器解释执行；default_path 必须全覆盖（§2.3 校验）。跳边允许成环。
- **转场 v1 = cut + 单侧 fade**——质量纪律（DESIGN.md `transition.default=cut` 的克制同样适用于 deck）与 Phase 1 排量的双重决定；timeline 侧既有 clip 转场能力经真机验证后逐类放开，真交叉溶解列 Phase 3。（初稿曾以 lumenframe `cross_dissolve` 的重叠约束立论；v1 已不走该路径，论据随物化决策一并更新。）

### 3.5 布局引擎（本方案唯一的真新引擎活，质量命脉）

- **从内容反推模板**（评审采纳）：开工前先写 dogfood pitch deck 的逐页内容清单，由它决定首发模板。预计首发 **4 个做深**：`title` / `content` / `stat`（大数字行）/ `full-bleed`；`split`/`quote` 随 Phase 2 补，`grid-cards`/`section-divider` 列 Phase 3（排期以 §8 为准）。宁少而深，符合「设计正确优先」。
- 每个模板 = 具名槽位（可容纳单块或同构 group）+ safe-area 内网格几何 + autofit 规则，输出 placed blocks。
- **autofit 两档制**（评审采纳，反连续缩字）：字号只在 type scale 的两个离散档间降；正文优先换行不缩字；仍溢出 → **不再硬塞，把 overflow 作为结构化信号回给模型触发 `refine_slide` 改短文案**——把「less is more」做成机制而非祈使句。
- **CJK 断行最小集**：避头尾标点 + 中英混排不拆英文单词；全套禁则 P3。文字测量原语做成可复用模块（Day4 共用，§7）。
- 现状诚实：引擎今天零自动断行（文字只按显式 `\n` 切行），测量/断行/缩字全部从零造——这正是 Phase 1a 要做深而不是做多的原因（§8）。

### 3.6 主题 token：前置产物 = design-manuals 的 deck 扩展层

评审证实 `DESIGN.md` 顶层组是 colors/typography/frame/grading/depth（+时间层），**没有 spacing 组**，且静态半部是纯视频语义（typography 只有 3 档字幕样式、colors 无 surface/中性灰/浅色体系、frame 是 TV 安全框）。直接引用 = Phase 1 中途 token schema 返工。

**开工前置（零代码，spec 期间即可做）**：在 `~/Code/lumeri-design-manuals/` 新增 deck 静态扩展层（DESIGN.md 兄弟文件）：间距阶（4/8pt 制）、栏格网格、阅读式 type scale（display/h1/h2/body/caption + 比例）、neutral/surface 语义色板、浅色主题、**双语字体栈**——字体 token 值为 `{family, path, weight}` 三元组（渲染端用 path，pptx 端用 family；`gemia/video/fonts.py` 的 FontRecord 本就双持有）。

## 4. Agent 工具面

### 4.1 新工具（7 个）

| 工具 | 语义 | plan_mode | budget |
|---|---|---|---|
| `draft_deck` | 一句话主题→整份 deck IR（pitch/report/teach 模板，中英自适应） | MUTATING | $0 |
| `set_deck` | 整份 IR 落 patch（§2.3 校验语义） | MUTATING | $0 |
| `update_slide` | 单页局部 patch | MUTATING | $0 |
| `get_deck` | 文本视图（模型读） | PLAN_ALLOWED | $0 |
| `refine_slide` | 改文案/换素材/调 build，自动重物化该页 | MUTATING | $0 |
| `assemble_deck` | IR→placed blocks→光栅→timeline 排布（§3） | MUTATING | $0 |
| `export_deck` | `format=pptx\|mp4\|html`（§6） | MUTATING | $0（本地导出） |

素材复用既有 `generate_image`/`search_media`/`search_frames`，不新增。

### 4.2 接线清单（逐项标注是否有 fail-closed 保护，评审修正）

| 接线点 | fail-closed？ |
|---|---|
| `gemia/tools/_schema.py` 注册 + `__init__.py` DISPATCHER | 是（DISPATCHER==TOOL_NAMES 不变量测试） |
| `gemia/plan_mode.py` 分类 | 是（完备性测试红） |
| `gemia/project_model.py` normalize 透传（§2.4） | **否——静默丢数据**，靠金标准测试钉死 |
| `lumerai/patches.py` op handlers（§2.4） | 是（E_OP_UNKNOWN 显式抛） |
| `gemia/budget_guard.py` | **否**（未知工具默认放行，无完备性测试）——仍要登记 |
| `system_v3.md` 词汇表 + deck 工作流引导 | 否（行为面，靠端到端验收） |
| deck 质量基准 skill（挂 design-system 手册） | 否（路由冒烟验证） |

### 4.3 协议与可观察性（评审修正后的诚实版）

- **Phase 1 零新增 event kind 成立**，但初稿的「被两端观察」言过其实：apply_ops 的 on_patch 只发 `timeline_op`（payload 硬编码 timeline 的 duration/clip_count），deck-only 变更会在 CLI 刷出误导性的「timeline updated · 0 clip(s)」——kind 已知不触发 unknown-banner，但**可见且错误比不可见更糟**。修法：契约只钉 kind 不钉 payload，`apply_ops` 带区分性 label / on_patch payload 加 `state_scope` 字段；CLI 同 commit 加一处分支渲染「deck updated · N slide(s)」，外加 7 个新工具的 `format.js` TOOL_LABELS 条目。**§4.4 的「CLI 零改动」不成立，改为「CLI 一处小改，与后端同 commit」。**
- **Phase 2 的真协议变更**：`GET /sessions/{id}/deck`（IR + placed blocks + 资产引用，播放器与直编面板的数据源）。`/sessions*` 路由形状属协议面（protocol-parity-plan），按契约先行、双端同 commit、双端测试处理，不轻描淡写。

### 4.4 CLI 对等边界（需用户批准的例外，不是作者自授）

- agent 工具面：瘦客户端自动对等 + §4.3 的一处小改。
- 播放器：CLI 不做终端内幻灯片渲染——**此例外是用户级 parity 硬规则的豁免，列入 §10.4 待用户拍板**，批准后写入 `docs/protocol-parity-plan.md`。
- 无论豁免与否，CLI 必须有 `/present`：openExternal 打开 `?present=1` 演示 URL（CLI 已有 doPreview/login 同款 web-handoff 先例，十几行），与 Phase 2 播放器同 commit；外加 `get_deck` 大纲视图与 `export_deck` 触达。

## 5. 播放器（web v3 presentation mode，Phase 2）

### 5.1 播放物（评审 P0 修正：不存在「既有 preview 管线」可复用，交付机制显式定义）

播放器 = **build-state PNG（静态驻留，原生分辨率，文字锐利）+ 预渲染微段视频（动效）** 的序列机 + 交互图解释器。不做浏览器端 lumenframe 实时渲染（服务端 numpy 合成达不到实时；帧流路由是协议变更且不必要）。`refine_slide` 只重物化受影响页并缓存未变页——「编辑→演示」延迟预算：单页 < 5s。

### 5.2 演示交互集（评审补齐，「第一次真实演示不当场尴尬」为准）

- 推进/回退：`click`/`→`/`space`/`PgDn` 推进；`←`/`PgUp` 回退。**回退显示该页已 build 完成的末态，不重播动画**（PowerPoint/Keynote 惯例）。
- **动效播放中点击 = 跳至本段末态**（演讲者赶时间连点不许跳过头）。
- `B` 黑屏（讨论岔开的主持人标配）；数字+`Enter` 跳页（overview 网格 P3）；`Esc` 退出。
- 进入即 `requestFullscreen` + 3s 光标自动隐藏；页码角标；最后一页再推进 → 「演示结束」黑屏而非突然退出。
- hotspot 跳边保留，但排量冲突时让位给以上基本键（真实使用率悬殊）。
- 验收含 4K 外接投屏：正文字号在 100% 缩放下与 Keynote 同屏对比无可见发糊。

### 5.3 blocks 级直编面板（Phase 2，评审采纳）

一页一个表单式改字面板（title/bullets/notes 文本直改 → `update_slide`），不是 WYSIWYG——blocks 本就是语义结构，表单即可承接「开会前 5 分钟改错字」，把用户留在母体内，堵住「被迫以 pptx 为母体」的产品自相矛盾。

## 6. 导出三路与保真契约

### 6.1 `.mp4` 压平

assemble_deck 的 timeline 排布（build-state PNG 作 image clips 按各自 `dwell_sec` 驻留，`--dwell` 参数可全局覆盖；页间 cut/fade；A1 音轨）直接交 `project_export`——**在 clip timeline 物化决策下这条链现在是真免费**（One Lumen 验证的正是 image clips + text overlay + audio 的合成导出）。notes 可选经既有 `narrate` 工具合成旁白作 A1（deck→有声宣传片一键投影）。

### 6.2 `.pptx` 投影（python-pptx，`deck` optional extra）

**依赖**（评审修正）：先例是 pyproject 既有 `interop` extra（otio）+ `pytest.importorskip`（初稿引的「MCP SDK 先例」在本仓查无此物）。新增 `deck = ["python-pptx>=1.0"]`（>=1.0 避 Pillow>=10 兼容破损史）；lxml 是唯一新增二进制依赖（macOS arm64 py3.12 有 wheel）。

**画幅与单位**（评审补，Phase 1 首导就会踩）：python-pptx 默认模板是 **4:3**——必须显式 `slide_width/height = 12192000/6858000 EMU`（16:9, 13.333×7.5in）。换算全局规则：`pt = px × 72 × slide_width_in / canvas_width_px`（1920px 画布 → pt = px/2）；placed blocks 的 rect_px 线性映射为绝对定位 EMU。safe-area 是 Lumeri 侧布局约束，pptx 无对应也不需要。

**bullets**（评审 P1，决定导出器架构）：python-pptx 无 bullet 格式 API（`add_textbox` 出来的段落无项目符号）。**方案：随包一个模板 .pptx**（自制 layout 含 body placeholder），导出器向 placeholder 填内容——顺带修好大纲视图与无障碍标题；多级列表映射 block 层级→`paragraph.level`。placeholder 只供列表语义与大纲视图，**几何仍由 placed blocks 说了算**：导出器按 rect_px 重设该页 placeholder 的位置与尺寸，与 §2.5 单源原则一致。

**CJK 字体**（评审 P1）：`font.name` 只写 `<a:latin>`，中文 run 会回落主题默认东亚字体。**开一个窄豁免**：以 lxml 对含 CJK 的 run 设 `<a:ea typeface>`——这是「拒绝 XML 注入」原则下明确圈定的例外（单元素、无 timing tree 复杂度），白名单仅此一条（`<p:transition>` 列为未来第二候选，v1 不做）。python-pptx 无字体嵌入 API：manifest 声明「本 deck 依赖字体清单，收件方未装则替换」，pptx 侧 family 优先选收件方大概率已装者（PingFang/微软雅黑级）。

**保真契约（导出附 manifest 逐页声明降级项）**：

| 等级 | 内容 |
|---|---|
| 保证可编辑 | 文字 runs（family/字号/颜色；**内容一致保证，行级断行尽力而为**——PowerPoint 用自家度量重排，策略=烘焙 autofit 终态字号+自然换行+槽位 ±1 行容差）、图片、基础形状、slide 顺序、speaker notes、URL 超链接（run/shape 级） |
| 尽力而为 | 表格（结构与文本；边框/主题样式无 API 不保真）、图表白名单（area/bar/column/line/pie/doughnut/radar/scatter/bubble；treemap/waterfall 等 chartex 不可作 → 烘焙；**v1 IR 无 chart 块，本行自 Phase 3 chart 块引入后生效**）、linear 双 stop 渐变底、**视频块经 `add_movie` 真嵌入**（H.264；非常规编码回落 poster+链接）、slide 内跳转（仅 shape 级 `click_action.target_slide` 热区） |
| 明确丢弃（manifest 点名） | keyframe 运动动画、**builds/点击入场**（见下）、slide 转场、letter_spacing、radial/多 stop/文字渐变与双层阴影（烘焙为图）、文字 run 内跳转、字体嵌入 |

**builds 的廉价保真**（评审采纳）：`export_deck(pptx, explode_builds=true)`（默认开）——每个 build-state 物化为独立 slide（Keynote/Marp 同款手法），点击翻页即还原逐条出现的放映体验，零动画 API 依赖。

### 6.3 自包含 HTML player（P3）

单文件 HTML + 资产，内嵌 hold/交互图解释器——发出去不依赖收件人装 Lumeri 的最高保真交付物；若 5.2 的 4K 文字验收不达标，DOM/矢量文字渲染路径从这里提前。

## 7. 给 fiveday Day4（文字/标题动画）的约束清单

1. 一切文案走语义层（blocks / text overlay），**永不烧进 generate_image 画面**（system prompt + brief 双处立规）。
2. 字体解析统一经 `gemia.video.fonts.resolve_font_path`；字体 token 一律 `{family, path, weight}` 三元组；CJK fallback 链显式（评审证实 lumenframe resolver 零 CJK 候选，凡新文字路径不得再造无 CJK 的候选表）。
3. **文字测量/断行做成可复用原语**（避头尾最小集 + 不拆英文单词），deck 布局引擎直接吃——这是两个产品线共用的地基件。
4. 标题动画做成具名参数化预设目录，deck builds 按名引用；预设优先落 timeline/overlay 路径（deck v1 物化基底），lumenframe 版 Phase 3。
5. 文字渲染保持纯函数（同输入同输出），金标准可断言。

## 8. 分阶段实施

验收总纪律：pytest 金标准 + **slide 缩略图抽查**（每页每 build-state 渲图，亲自 Read + 像素交叉验证，自检不可信）+ 真机跑通。

**回归红线（评审补，可执行版）**：① 开工日在分支点跑全量 pytest **固化失败快照进 loop brief**（文件名+数量），此后任何新增失败即红，不做临场豁免；② 必绿套件点名：`test_v3_contract`、`test_plan_mode` 完备性、shotlist/assemble/timeline/export 套件、lumenframe deterministic subset、deck 金标准（§2.4 持久化、§3.2 CJK 字形、§3.1 undo 收敛）；③ 凡触碰 CLI/payload 的日子 `npm test` 与 pytest 同为当日验收；④ exFAT/AppleDouble 教训（`.venv/bin/python -m pytest` + APFS /tmp + deterministic subset 优先）原文写进循环 brief。

- **Phase 1a（首窗口前半）**：dogfood 内容清单 → IR + 三 op + 持久化接线（§2.4 金标准）+ `draft_deck` + 布局引擎 **4 模板做深**（含测量/断行原语与 CJK 金标准）+ slide 光栅器 + **静态翻页 pager**（space 翻 slide 首帧 PNG——让 dogfood 从第一阶段就能站着讲）+ §4.3 的 CLI 小改（state_scope 分支 + TOOL_LABELS，与 ops 同 commit）。
- **Phase 1b（首窗口后半）**：`assemble_deck` timeline 排布 + `.mp4` 压平真机验收 + `refine_slide`。**pptx 导出器起步**（模板 .pptx + 文字/图片投影 + manifest），完整契约表验收顺延 **Phase 1.5**（次窗口首日）——评审排量核算：初稿把 ~7-10 日槽塞进 5 日槽，砍法即此。
- **Phase 2（播放器窗口）**：presentation mode（§5 全集：PNG+微段、演示键、语义规则）+ `GET /sessions/{id}/deck`（契约先行双端）+ CLI `/present` + blocks 直编面板 + build 入场预设×2。**硬门验收**：用 Lumeri 播放器把 dogfood deck 对真人完整讲一遍（至少录屏演练），卡点清单逐条闭环；任选 3 页与 Gamma/Keynote 同内容同屏对比留档——「能用+测试绿」不是过门标准。
- **Phase 3**：主题完整化（浅色/token 表切换）、grid-cards/section-divider 等模板扩充、真图表（shape 拼装或白名单 chart）、真交叉溶解、HTML player、**lumenframe 运动创作层**（slide comp 做 Keynote 级动效 → bake 回 timeline；含 broadcast 编码桥/音频/CJK resolver/一致性四项桥接）。
- **Phase 4**：WYSIWYG 直编（归属待定，原 owner Antigravity 永久离线）。此前产品形态 = agent 生成 + 对话/表单精修 + 三路导出，成立且自洽。
- **Dogfood 首作**：「Lumeri 产品介绍 pitch deck」——与宣传片项目互喂素材与美学管线。数据页 v1 用 `stat` 块表达（大数字+标签）；曲线类图表在 Phase 3 chart 块落地前不承诺、不硬凑。

## 9. 风险与不做清单

| 风险 | 应对 |
|---|---|
| IR 与物化产物分叉 | §3.1 一致性模型 + undo 后重 assemble 收敛测试 |
| pptx 投影被误当全保真 | §6.2 契约 + manifest 逐页降级声明 + §1 单向门声明 |
| 布局引擎质量被排量挤压 | 4 模板做深 + Phase 1a/1b 拆分 + overflow 回模型机制 |
| 文字被烧进生成图 | §7 约束 1 + 缩略图抽查必验语义层存在 |
| timeline_op 误导性噪音 | §4.3 state_scope + CLI 分支同 commit |
| 交互图复杂化 | v1 = advance+hotspot 树；状态机 P3+ |
| 与视频域互相拖累 | 全程加性 + §8 回归红线快照制 |

non-goals（v1）：pptx 导入回流、实时协作、母版级 pptx 主题映射、CLI 终端内幻灯片渲染（待批豁免）、动画/转场 XML 注入（仅 `<a:ea>` 一条窄豁免）、Rive 级状态机。安全加固按用户 2026-07-06 决定延后至功能收尾统一做。

## 10. 开工前置、协调与待拍板

### 10.1 基线与 fallback（评审补：硬前置不能没有出口）

- 主路径：fiveday 收官（约 2026-07-11）+ merge debt 清算后，从主线开 worktree `feat/deck-interactive`。**清算的验收项必须包含 `feat/fiveday-promo` 合入**——deck 依赖的 draft_shotlist/refine_shot/双检索/CJK 修复目前只在该分支。
- **fallback 条款**：清算是尚未排期的用户级决定；若 fiveday 收官后 3 日内未发生，经用户一句确认，从 `feat/fiveday-promo` 尖端开 `feat/deck-interactive`。利：全部依赖在场、与 Day4 约束天然衔接；弊：未合并栈上再叠一层（单 owner、保持可 rebase、QUEUE 记「后置合并债 +1」）。
- spec 期间零代码开工。纪律沿用：身份 Acrabxie；不 push 公开 origin（private 备份照推）；不碰 live :7788/主仓脏工作区/FlClash/:7799。

### 10.2 前置产物（零代码，spec 批准后即可做）

1. design-manuals **deck token 扩展层**（§3.6）。
2. dogfood pitch deck **逐页内容清单**（§3.5 反推模板）。
3. deck 循环 brief（含 §8 回归红线原文）。

### 10.3 旧资产处置（可执行三步，执行者 claude-code，用户批准本 spec 时执行）

1. `git worktree remove` `deck-domain-skeleton`（实测 main 祖先、零独有 commit）与 `deck-frontend-wysiwyg`（仅 scaffold checkpoint 494b894，已在分支上）。
2. 分支打归档标签 `archive/deck-20260621-skeleton` / `archive/deck-20260621-wysiwyg`（先例 archive/timeline-m6m7），分支本体保留。
3. QUEUE 两条目状态改 `superseded`（指向本 spec），**显式解除 antigravity 的 owner 占坑**（其 owner 已按 2026-05-16 决定永久离线，占坑永不释放）。ov-*/tauri 的用户级删除决定保持独立，不与本项捆绑。

### 10.4 待用户拍板清单

1. CLI parity 豁免：终端内不渲染幻灯片（§4.4）——批准后落 `docs/protocol-parity-plan.md`。
2. fallback 基线触发权（§10.1）。
3. 旧 deck 资产归档执行绿灯（§10.3）。
4. WYSIWYG（Phase 4）归属，可延后。
