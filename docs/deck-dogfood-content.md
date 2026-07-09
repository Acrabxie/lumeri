# Dogfood 首作：「Lumeri 产品介绍 pitch deck」逐页内容清单

- status: draft（2026-07-08，写作时 `docs/video-deck-synergy.md` 尚不存在，协同结构落地后按需对齐）
- 用途：① 从内容反推布局模板需求，验证首发 4 模板（`title` / `content` / `stat` / `full-bleed`）够不够（deck-interactive-video-plan §3.5）；② 开工后 `draft_deck` / `refine_slide` 的真实验收素材。
- 上游依据：`docs/deck-interactive-video-plan.md` §1（目标用户/单向门）、§2.1（blocks kind：text/stat/image/shape/group）、§3.5（首发 4 模板+槽位思想）、§8（dogfood：数据页只用 stat 块，曲线图表 Phase 3 前不承诺）。
- 文案纪律（产品原则直译）：**图案优先于文字**——每页一个念头，body ≤3 个要点、每要点 ≤12 字；stat 页数据表达只用 stat 块（大数字+标签），全份 deck 零曲线零图表；一切图像素材**不含任何烧入文字**（§7 生死约束）。
- builds 语义：`dwell_sec` 仅供自动播放 / `.mp4` 压平消费；presentation mode 驻留至交互推进（§2.1）。
- 数字口径总注：**所有 stat 数值以发布时实测为准**；本文给的是口径与当前基线，不是承诺值。

---

## s1 封面 · Lumeri

- **页目标**：3 秒内立住品牌与一句话主张，观众还没坐稳就知道这是谁。
- **layout**：`title`
- **blocks 草案**：
  - `image` / role `hero`：满幅背景，《One Lumen》冰蓝辉光球帧（深空底）。
  - `text` / role `title`：`Lumeri`（style_token: type.display）
  - `text` / role `subtitle`：`让创作只剩下想法`
  - `shape` / role `accent`：双光带细线（fill_token: color.accent，呼应 logo #5FC6DE）。
- **notes**：开场不抢话，让画面先呼吸两秒；一句话点题：Lumeri 是一个创作 agent 家族，今天讲两件事——Video 和 Deck。
- **builds**：2 步。b1 = 背景+accent 光带（dwell 2.0）；b2 = +标题与副标（dwell 3.0）。
- **素材需求**：**复用**《One Lumen》辉光球高光帧（挑暗部占比大、右侧留白够放字的一帧）。

## s2 问题 · 想法很快，工具很慢

- **页目标**：一句话钉住痛点——工具复杂度吞掉创作冲动，不展开、不列举。
- **layout**：`full-bleed`
- **blocks 草案**：
  - `image` / role `background`：星系漩涡暗帧，整体压暗。
  - `text` / role `title`：`想法很快，工具很慢`
- **notes**：讲时间线地狱、素材管理、导出参数这些劝退点，但页面上一个字都不给——留白就是态度。
- **builds**：2 步。b1 = 纯背景（dwell 1.5）；b2 = +主张句（dwell 3.0）。
- **素材需求**：**复用**《One Lumen》星系漩涡帧（选暗、静的一帧；若对比度不足，generate_image 方向：「冰蓝深空、稀疏星点、缓慢漩涡感、大面积暗部、无文字无符号」）。

## s3 定位 · 用一句话，下达一部片

- **页目标**：给出答案——Lumeri Video 的定位：Codex / Claude Code for video creation。
- **layout**：`content`
- **blocks 草案**：
  - `text` / role `title`：`用一句话，下达一部片`
  - `text` / role `body`：bullets = [`自然语言，下达创作`, `Agent 执行全链`, `导出即成片`]
  - `text` / role `caption`：`Codex / Claude Code — for video creation`
- **notes**：类比一句话讲透：Codex 把编程变成对话，Lumeri Video 把视频创作变成对话；你说念头，agent 干活。
- **builds**：4 步。b1 = 标题（1.2）；b2/b3/b4 = 逐条亮 bullet（各 1.2，末态 2.5）。
- **素材需求**：无图，靠版式与 accent shape；模板自带留白即可。

## s4 工作流 · Agent 替你走完全程

- **页目标**：四步讲清 agent 干了什么，一眼看懂「它替你干活」。
- **layout**：`content`（body 槽装同构 group×4） **[潜在缺口→见汇总 G1]**
- **blocks 草案**：
  - `text` / role `title`：`Agent 替你走完全程`
  - `group` / role `body`：children = 4 个同构 `text` 块：[`检索素材`, `剪辑装配`, `预览诊断`, `导出成片`]（理想形态横排步骤卡，模板配 accent shape 连接感）。
- **notes**：对应真实工具链讲半分钟：search_media 检索、timeline 装配、预览抽帧诊断、project_export 出 broadcast 级成片——每一步都是已上线的 verb，不是路线图。
- **builds**：5 步。b1 = 标题（1.0）；b2–b5 = 逐卡出现（各 1.0，末态 2.5）。
- **素材需求**：无图；四步图标 v1 不做（图标质量拉不齐反而破坏克制），用 shape accent 代位。

## s5 成片实证 ·《One Lumen》

- **页目标**：用真实成片画面证明输出质量——show, don't tell。
- **layout**：`full-bleed`
- **blocks 草案**：
  - `image` / role `background`：《One Lumen》最美一帧满幅（冰晶分形或等离子光带高光帧）。
  - `text` / role `caption`：`《One Lumen》 · Lumeri Video 出品`（角标小字）
- **notes**：这一帧从检索到导出全程 agent 完成；端到端链路（画面+字幕+音轨合成导出）已真机验证。演示时可停留久一点。
- **builds**：1 步。b1 = 整页（dwell 4.0）。
- **素材需求**：**复用**《One Lumen》成片剧照；若现场有条件，notes 里备一手：hotspot 跳转播放 mp4 片段（Phase 2 后再启用，v1 不承诺）。

## s6 Deck 主张 · 演示文稿，本身就是影片

- **页目标**：抛出 Deck 的核心命题，全场只此一句（本页兼任章节转折页）。
- **layout**：`full-bleed`
- **blocks 草案**：
  - `image` / role `background`：冰晶分形暗帧。
  - `text` / role `title`：`演示文稿，本身就是影片`
- **notes**：一句带过血统：Flash 的 stop()、Keynote 的动画时间线，本质都是「影片+驻留点」；Lumeri Deck 把这个本质做成母体。
- **builds**：2 步。b1 = 背景（1.2）；b2 = +主张句（3.5）。
- **素材需求**：**复用**《One Lumen》冰晶分形帧（压暗处理，保证字面对比度）。

## s7 结构揭示 · deck = 幻灯片 + hold + 交互图

- **页目标**：用一行公式讲清概念层，并当场用「hold」演示 hold。
- **layout**：`content`
- **blocks 草案**：
  - `text` / role `title`：`deck = 幻灯片 + hold + 交互图`
  - `text` / role `body`：bullets = [`hold：等交互再继续`, `交互图：点击即分支`, `video = 线性退化情形`]
- **notes**：讲稿设计成自证：讲到「hold」时就停在半 build 状态不动，说「现在这一页正在 hold 等我」；最后点出 video 只是零 hold 的特例。
- **builds**：4 步。b1 = 标题公式（1.5）；b2/b3/b4 = 逐条 bullet（各 1.2，末态 3.0）。
- **素材需求**：无图。刻意不用示意图（交互图图示若要好看需自由摆放，v1 布局引擎不做，文字公式反而更克制）。

## s8 数据页 · 不是概念，是运行中的系统

- **页目标**：四个大数字压实工程底座可信度。**数据表达只用 stat 块，零曲线零图表**（§8 红线）。
- **layout**：`stat`
- **blocks 草案**：
  - `text` / role `title`：`不是概念，是运行中的系统`
  - `group` / role `stats`：children = 4 个 `stat` 块：
    - `{ value: "97+", label: "Agent 工具" }` — 口径：`gemia/tools/_schema.py` 注册工具数；当前基线 97，deck 七件套上线后 100+；**以发布时实测为准**。
    - `{ value: "4", label: "首发模板 · 做深" }` — 口径：Phase 1a 布局模板数（title/content/stat/full-bleed）。
    - `{ value: "2", label: "导出通路 mp4 · pptx" }` — 口径：v1 实际可用导出格式数（HTML player 属 P3，不计入、不口头承诺）。
    - `{ value: "<5s", label: "改一页，回到演示" }` — 口径：refine_slide 单页重物化延迟预算（方案 §5.1）；**以发布时实测为准，未达标则本条撤下**。
- **notes**：每个数字一句话讲口径，尤其 97+ 要点明是「已注册可调用的工具」，不是功能点数——诚实本身是卖点。
- **builds**：5 步。b1 = 标题（1.0）；b2–b5 = 四个大数字逐个亮起（各 0.8，末态 3.0）。
- **素材需求**：无图；大数字即视觉主体（stat 模板的存在理由）。

## s9 家族页 · 一个 Lumeri，一族创作

- **页目标**：亮出母品牌版图——Deck 不是孤品，是 Lumeri 家族一员。
- **layout**：`content`（body 槽装同构 group×5） **[模板缺口→见汇总 G1]**
- **blocks 草案**：
  - `text` / role `title`：`一个 Lumeri，一族创作`
  - `group` / role `body`：children = 5 个同构 `text` 块：[`Video · 影片`, `Deck · 演示`, `Image · 图像`, `Audio · 声音`, `CAD · 图纸`]（产品线名英文，注释中文；理想形态五卡网格）。
- **notes**：一句定关系：Lumeri 是母品牌，Video 已出首作，Deck 是今天的主角，其余产品线共享同一套 agent 底座与美学管线。
- **builds**：3 步。b1 = 标题（1.2）；b2 = Video+Deck 先亮（今天的两位主角，1.5）；b3 = 其余三条补全（2.5）。
- **素材需求**：v1 无图。P3 grid-cards 落地后可为每卡配 generate_image 抽象符号，提示词方向：「冰蓝深空底、单一发光几何符号（球体/波形/晶格/线框），风格与《One Lumen》同源，**画面绝不出现任何文字或字母**」。

## s10 元卖点 · 这份演示，就是它自己做的

- **页目标**：揭底 dogfood，并现场 refine_slide 改一页给观众看——产品演示即产品证明。
- **layout**：`content`
- **blocks 草案**：
  - `text` / role `title`：`这份演示，就是它自己做的`
  - `text` / role `body`：bullets = [`每页出自 draft_deck`, `现在，当场改一页`]
  - `shape` / role `accent`：光点强调（呼应 logo 光点）。
- **notes**：演示脚本：请观众现场提一个改法（换个词/改个数），切到对话窗跑 `refine_slide`，回到演示页面刷新——用 <5s 的往返闭环把「agent 生成 + 对话精修」演成一个动作。
- **builds**：3 步。b1 = 标题（1.5）；b2 = bullet 1（1.2）；b3 = bullet 2（3.0，hold 在此进入现场演示）。
- **素材需求**：无图。links 备一条 `hotspot:blk_cta → slide:s8`（改完数据页后一键跳回数据页看结果，交互图跳边的真实用例；v1 若 hotspot 排量吃紧则删，靠键盘跳页）。

## s11 诚实边界 · pptx 是投影，不是母体

- **页目标**：主动交代单向门（§1），把诚实做成卖点而非小字免责。
- **layout**：`content`
- **blocks 草案**：
  - `text` / role `title`：`pptx 是投影，不是母体`
  - `text` / role `body`：bullets = [`母体：Lumeri 播放器`, `pptx：兼容性投影`, `导出单向，不回流`]
- **notes**：两句讲透：发给别人放映没问题，manifest 会逐页声明降级项；在 PowerPoint 里改过的文件不能回流 Lumeri，临场改字请用 Lumeri 自己的直编面板（Phase 2）。
- **builds**：4 步。b1 = 标题（1.2）；b2/b3/b4 = 逐条 bullet（各 1.2，末态 2.5）。
- **素材需求**：无图。

## s12 路线 · 三步，不画大饼

- **页目标**：给出可信的三阶段路线，边界口径与方案 §8 严格一致。
- **layout**：`content`
- **blocks 草案**：
  - `text` / role `title`：`三步，不画大饼`
  - `text` / role `body`：bullets = [`P1 生成 + 四模板`, `P2 播放器 + 直编`, `P3 图表 + 动效`]
- **notes**：点明「图表在 P3 才有，所以今天这份 deck 一根曲线都没有」——把红线讲成纪律故事；P2 硬门是用 Lumeri 播放器对真人完整讲一遍。
- **builds**：4 步。b1 = 标题（1.2）；b2/b3/b4 = 逐阶段亮（各 1.2，末态 2.5）。
- **素材需求**：无图。

## s13 结尾 · One Lumen, every story.

- **页目标**：收束回品牌，留一个可点的出口（交互图 URL 边的真实用例）。
- **layout**：`full-bleed`
- **blocks 草案**：
  - `image` / role `background`：等离子光带帧，满幅。
  - `text` / role `title`：`One Lumen, every story.`
  - `text` / role `caption`：产品入口占位（发布时定：站点或联系渠道，勿现在硬编）。
  - links：`hotspot:blk_caption → url:<发布时定>`。
- **notes**：最后一页推进后进「演示结束」黑屏（§5.2），不突然退出；如有 Q&A，用 `B` 黑屏。
- **builds**：2 步。b1 = 背景+光带（1.5）；b2 = +收束句与入口（4.0）。
- **素材需求**：**复用**《One Lumen》等离子光带帧（选流动感最强的一帧）。

---

## 模板缺口汇总

逐条盘点 4 模板（title/content/stat/full-bleed）装不下或有压力的页，给最小补救与取舍建议。

### G1（唯一真实压力点）：content 模板 body 槽的「同构 group 横排」能力 — s4 工作流（4 步卡）、s9 家族（5 卡）

- **性质**：不是缺第 5 个模板，而是 content 模板的深度问题。§2.1 已承诺槽位模型 day-one 支持「单块或同构 group」，但若 Phase 1a 的 content 只实现纵向 bullets 流，这两页的天然形态（横排同构卡，即 grid-cards 的核心视觉）就装不下。
- **最小补救**：在 content 模板内实现 group 槽的两种流向（纵列 / 等宽横排），作为「4 模板做深」的验收项之一；**不新增 grid-cards 模板**。
- **兜底（排量仍不够时）**：s4 改 4 条纵排要点、s9 改 5 行纵列——内容语义完整成立，视觉从「卡片感」降级为「列表感」，可接受；P3 grid-cards 落地后仅改 layout 字段做 refine 升级，顺带验证「IR 不变、模板可替换」这条架构承诺。
- **建议**：**宁改内容适配 4 模板，不提前补模板**。把「group 横排」列入 Phase 1a content 模板验收清单即可，成本远低于新模板（新模板 = 槽位定义+autofit+金标准全套）。

### G2：split（左右对照）——刻意回避，无页受损

- 初拟大纲里「Lumeri vs 传统工具」对比页天然要 split。**处置：删掉对比页**，其论点拆给 s3（定位主张）与 s11（诚实边界）——pitch 里"我是什么"比"他不行"更符合 less is more。split 留 Phase 2，不因本 deck 提前。

### G3：quote（大字引言）——full-bleed 已覆盖

- s6「演示文稿，本身就是影片」是全份 deck 唯一的大字主张页，full-bleed 的 title 槽（满幅底图+display 级大字）完全承载，且比白底 quote 更符合品牌视觉。quote 模板 Phase 2 再说。

### G4：section-divider（章节页）——由 full-bleed 兼职

- 本 deck 只有一个章节转折（Video → Deck，即 s6），full-bleed 兼任即可；13 页的 pitch 不需要专职分隔页。P3 不变。

### 总结论

**首发 4 模板成立，无需 Phase 1b 提前补任何模板。** 13 页中 11 页由 4 模板原生承载；仅 s4/s9 依赖 content 模板的 group 横排能力——这是 §2.1 槽位思想的题中之义，应作为 content「做深」的验收项而非新模板需求，且有纵排兜底不阻塞。整份清单同时覆盖了 4 模板各自的验收面：title（s1）、full-bleed（s2/s5/s6/s13，含满幅图+覆盖文字+角标 caption 三种用法）、content（s3/s7/s10/s11/s12 bullets 流 + s4/s9 group 槽）、stat（s8 大数字行）；外加 builds 逐步显隐（全部页）、hold 演示（s7/s10）、hotspot 跳边（s10 页内跳、s13 URL 边）三类播放器语义的真实素材。
