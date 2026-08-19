# 实施计划：时空语法层（Spatial-Temporal Grammar Layer）

- **状态**: Ready for Review
- **创建日期**: 2026-08-16
- **最后更新**: 2026-08-16（并入原型实证结论，路径改为 alpha-first，Phase 0 降级为并行）
- **来源规格**: `specs/20260816-spatial-temporal-grammar/spec.md`
- **Foundation 版本**: 1.0.0（`directive/foundation.md`，FOUNDATION_TYPE = `default`）
- **任务 ID**: `shared-2026-08-16-lumeri-spatial-temporal-grammar-v1`
- **本计划覆盖**: Phase 1 细化到可开工；Phase 1.5 / 2 / 3 给方向与关键决策点

> **⚠ 先读 §13。** 原型实证的结论优先于本文档前文的推测，冲突处已就地标注。
> 三条影响最大的：**① 实现路径改为 alpha-first**（原设想的全类型尺寸解算基本不需要）；
> **② Phase 0 渲染补齐从前置降级为并行 Phase 0′**（它不再阻塞主线）；
> **③ §1.1 ② 的像素边界已重写**——原文的"让步不扩散"守卫测试方向作废，
> 照原文实施会写出一条正好禁止正确做法的测试。
>
> **给 Acrab 的阅读提示**：澄清已全部有答案，见第 10 节「已确认决策」。
> 以下**三处**是第一轮就与拍板预期不一致的部分：
> - **§2.2**：决策 A 改不动 `compile.py` 一个文件就完事——非等比缩放和 anchor 的实现在**渲染后端** `gemia/video/layers.py` 里，那是所有产品线共用的合成器。工作量比"顺手补齐"大。
> - **§2.11**：我把全机器 91 份存量文档扫了一遍——**没有任何一份写过 anchor / 非等比缩放 / 旋转 / 关键帧**。你担心的"存量作品画面移位"风险，**实际发生数为 0**。这改变了回归门的设计（见 §8.2）。
> - **§2.12**：anchor 要做对，**依赖**决策 B 的图层实际尺寸。两条决策是耦合的，不能先做 A 再做 B。

---

## 1. 目标与边界

把「模型只能靠抽帧看画面」换成「模型不看像素也能精确定位、描述、编辑」。

| 层 | 名称 | 一句话 |
|---|---|---|
| A | 态势描述 Stage Report | 把 `x=-320, y=180, scale=0.8` 翻译成"标题在左下三分位，占画面 22%" |
| B | 网格词表 Grid Vocabulary | 语言 ↔ 数值的双向桥。"往左一点" = 减一个档位 |
| C | 元素组 Element Groups | 创作意图关系（Phase 3，本计划只给方向） |

### 1.1 边界条款（**已按决策 A、D 重写**）

规格原文写的是「零渲染改动」和「不看像素」。这两条**都已按 Acrab 的拍板放宽**，但放宽是**有界的**，边界如下：

**① 渲染改动：从「零改动」改为「一次有界的正确性补齐」**

- ✅ **允许**：让渲染器真正支持 `anchor_x`/`anchor_y` 与非等比 `scale_y`，并把 `transform.scale_y` 纳入关键帧映射。
- ❌ **仍然禁止**：任何**其他**渲染行为变更——不改效果链、不改混合模式、不改遮罩/track matte、不改时间重映射、不改色彩管线、不改编码。
- 🔒 **约束**：这次补齐必须做到「**只有**显式设过 `anchor_x`/`anchor_y`/`scale_y` 的图层发生变化，其余逐像素一致」，由 §8.2 的回归门强制。
- 📌 **2026-08-16 修订**：这项补齐**已从 Phase 1 的前置降级为并行的 Phase 0′**（见 §7）。
  它原本的理由之一是消解描述分裂，而 alpha-first 已经消掉了分裂，所以它现在是
  纯粹的能力问题，不再卡住主线。决策 A 本身不变。

**② 像素使用：从「零像素」改为「测量基于单层 alpha」**

> **2026-08-16 原型实证后重写。** 本条原文是「仅遮挡判定一项让步」，并要求 §8.4 写一条
> 守卫测试盯住"除遮挡外无其他项依赖光栅化"。原型证明那个边界划错了地方：
> 用 alpha 量包围盒同时解决了尺寸解算、真实覆盖遮挡与"描述真相还是意图"三个问题
> （见 §13）。**按原文实施会写出一条正好禁止正确做法的测试。**

- ✅ **允许**：态势测量整体基于**单层 solo 渲染的 alpha 通道**——包围盒、面积占比、
  区域跨度、遮挡，全部由同一套覆盖数据得出（同源是硬规则，见 §7 E4）。
- ❌ **仍然禁止**：跑效果链、做 blend、走完整合成路径；也不得因为"反正已经渲染了"
  顺手做 alpha 之外的像素分析（色彩、对比度、显著性等一律不在本层）。
- 🔒 **约束**：让步的边界从"哪些字段"变成"**渲染的深度**"——只取单层 alpha，不合成。
  §8.4 的守卫测试要按这个新边界重写，原方向作废。
- 📌 **诚实记账**：本层**不再声称是零像素路径**。代价是每图层一次渲染，成本未优化。

**②′ alpha 的两个盲区必须补（非可选）**

alpha 让描述必然正确，同时让它对两类事情静默失明，两者都无法从像素恢复：

- **被丢弃的意图**——渲染器读 `scale_x`、不读 `scale_y`/`anchor`。实测 `scale_y=1.5`
  与 `scale_y=0.5` 渲染结果完全相同，alpha 看不出用户设过它。这正是 Foundation
  原则 IV 禁止的静默丢弃。
- **裁剪**——alpha 只存在于画布内，出画图层量到的是残余，且平移换算失效。

必须由文档侧检查补上（§7 的 X 组）。**没有这一层，描述会在这两种情况下骗人。**

**③ 其余硬边界不变**

- 零 provider 花费——本层不发起任何模型调用
- 派生层是**只读派生物**，不是第二份状态；**禁止旁路缓存**（NFR-2）
- 所有编辑必须落成 `lumen_patch` 的 op 回写文档，禁止旁路写入（FR-4）
- web v3 与 CLI（`~/Code/lumeri-cli`）协议对等（Foundation 原则 VI）

---

## 2. 技术上下文：代码调研纪要

调研范围：`lumenframe/` 的 `model.py`、`compile.py`、`ops.py`、`preview.py`、`seek.py`、`resolve.py`、`compose/framing.py`、`craft/determinism.py`；`gemia/` 的 `video/layers.py`、`tools/layer.py`、`lumen_seek.py`、`inspect_timeline.py`、`extract_frame.py`、`safe_areas.py`、`transform_geometry.py`、`mcp/toolset.py`；`gemia/docs/point-library-charter.md` 与相关测试；以及全机器 91 份存量文档。

### 2.1 已有一个"不看像素的状态查询" —— `lumenframe/seek.py::state_at`

`state_at(doc, seconds)` 已经存在，自我定位就是"不渲染任何像素地回答'此刻时间轴在做什么'"。它已经复刻了编译器的时间数学（`timebase` 量化、`is_active` 门控、`time_remap` 源帧映射），并按编译器真实的合成顺序（`_lane_ordered_children`）排序，且明确声明 **ADD-ONLY**。

**结论**：态势描述不另起炉灶，作为 `seek.py` 同族的新模块，沿用同一套纪律。

**它的两个洞**（Phase 1 要补）：

1. **报的是静态 transform，不是 t 时刻的真实 transform**。`seek.py:252` 只做 `{**DEFAULT_TRANSFORM, **(layer.get("transform") or {})}`，没求值关键帧——而同一文件 `:179` 对 `time_remap` 的关键帧**是**求值的。**时间维求值了，空间维漏了。** 已确认属实，Acrab 已开成独立任务卡。
   → **本计划的承接方式**：Phase 1 的 t 时刻属性求值内核（P1-B1）落地后，`state_at` **复用它**，不各写一套插值。这是那张任务卡的实现依赖。
2. **只遍历顶层图层**，不进嵌套 composition。按决策（澄清 14），Phase 1 同样只做顶层，嵌套留 Phase 1.5——两者一起收敛。

### 2.2 【决策 A 的真实范围】补齐 anchor / scale_y 改的不止 `compile.py`

决策 A 说"让 `compile.py` 真正支持"。调研结论：**`compile.py` 只是转发方，真正的实现在渲染后端**。

现状链路：

- `lumenframe/compile.py` `_populate_stack`：`scale = float(transform.get("scale_x", 1.0))` —— 取**单个标量**，`scale_y` 丢弃；`anchor_x`/`anchor_y` 全文件从不读取
- `gemia/video/layers.py:711` `Layer` 数据类：`scale: float` —— 后端模型本身就是**标量缩放**
- `gemia/video/layers.py:769` `frame_content`：`_transform_frame(frame, scale=scale, rotation_deg=rotation_deg)`
- `gemia/video/layers.py:527` `_transform_frame`：`cv2.resize(rgba, (dst_w, dst_h))` 用**同一个** `scale` 乘宽高；旋转用 `cv2.getRotationMatrix2D(center, ...)`，`center` 是**内容中心**
- `lumenframe/compile.py` `_KEYFRAME_PROP_MAP`：`transform.scale_x` / `transform.scale` / `scale` 全部映射到后端的 `"scale"`；**没有** `transform.scale_y` 这一项

**所以决策 A 实际要动 4 个地方**：

| # | 文件 | 改什么 | 性质 |
|---|---|---|---|
| 1 | `gemia/video/layers.py` | `Layer.scale: float` → 支持 x/y 双轴；新增 anchor 字段 | **改共用渲染后端** |
| 2 | `gemia/video/layers.py` | `_transform_frame` 支持非等比 resize，旋转中心从"内容中心"改为"anchor 点" | **改共用渲染后端** |
| 3 | `lumenframe/compile.py` | 转发 `scale_y` / `anchor_*`；`_centred_position` 改为 anchor 感知 | 转发层 |
| 4 | `lumenframe/compile.py` | `_KEYFRAME_PROP_MAP` 加 `transform.scale_y`；`frame_content` 关键帧分支同步 | 转发层 |

⚠️ **这必须说清楚**：`gemia/video/layers.py` 是 **所有产品线共用的 RGBA 合成器**（timeline 渲染、导出、预览都走它），不是 lumenframe 私有的。改它的风险面比改 `compile.py` 大一个量级。这不是"顺手"的活，风险面比改 `compile.py` 大一个量级。**2026-08-16 更新**：它已独立成
并行的 **Phase 0′**（§7），不再阻塞主线——alpha-first 让描述层不必等它（§13.2）。

**限制 #3（transform 关键帧不重算旋转包围盒）是否一并做——我的建议：做，而且它会自动消失。**

理由：现在的 `compile.py::_centred_position` 是在**编译期**预先算一个"抵消缩放/旋转后包围盒增长"的静态位置补偿；而 `layers.py::position_value` 在**运行期**独立读关键帧位置——所以缩放一被打上关键帧，那份静态补偿就失效了。这个补偿本身**就是"没有 anchor 模型"的变通做法**。一旦按 anchor 正确实现（图层绕自己的 anchor 点缩放/旋转），补偿就不再需要，限制 #3 随之消失。反过来说，如果只补 anchor 而留着限制 #3，会出现"静态路径 anchor 正确、关键帧路径 anchor 不正确"的新分裂——比现状更糟。**所以建议纳入，且它是纳入 anchor 改造的自然结果，不是额外工作量。**

同时，`compile.py:32-34` 的 "Known limitations" docstring 必须在同一次改动里更新（留着过期的限制声明本身就是不诚实）。

### 2.3 【决策 B】图层"多大"文档里没有，各类型情况不同

各图层类型的可见范围来源：

| 图层类型 | 尺寸从哪来 | 纯文档可派生？ |
|---|---|---|
| `shape` | `props` 的 `rect: [x0,y0,x1,y1]` 或 `cx/cy/rx/ry`（归一化），由 `_shape_rect_px` 解析 | ✅ 可以 |
| `solid` / `gradient` | 定义上满画布 | ✅ 可以 |
| `text` | 渲染时用 PIL `textbbox` 现算，字体由 `_resolve_font` 走回退链解析 | ❌ 取决于本机字体 |
| `image` / `video` | `gemia/video/layers.py::_fit_to_canvas` 名字有误导性——它是**粘贴+裁切**不是缩放适配。素材按**原生像素尺寸**居中贴上，比画布大就裁掉。范围 = min(原生尺寸, 画布) | ❌ 原生尺寸要读文件 |

决策 B 已拍板：**允许读本机字体度量与素材尺寸**。新的确定性契约见 §10 决策 B 与 §8.3。

### 2.4 网格词表已经有一份 —— `lumenframe/compose/framing.py`（决策 C：复用）

`compose/framing.py` 已有成型词表：`GRIDS` 注册表（`thirds` 三分、`golden` 黄金 0.382/0.618、`center` 中心十字）、`GridSpec`（参考线 + 锚点格）、安全边距常数 `_SAFE = 0.04`。其 docstring 明说做成词表就是为了让"overlay 和锚点数学永不悄悄分叉"。

坐标系差异：compose 的网格活在"**素材裁切内 0..1**"空间（服务智能重构图），态势层需要"**画布放置**"空间。决策 C 已定：**复用，加一层坐标系适配；12 列栅格不做**。

### 2.5 表达式也能驱动位置，诚实的派生必须求值它

`compile.py::_eval_property_value` 走 `gemia/expressions.py` 的 `SafeEvaluator`，允许把表达式绑到 `transform.x` 等属性（优先级：**表达式 > 关键帧 > 静态值**）。t 时刻的诚实位置必须复刻这条三级优先级。`SafeEvaluator` 是确定性的。

### 2.6 FR-6 点名的两个工具不是文档的抽帧通道（决策：改为 `lumen_seek`）

- `gemia/tools/inspect_timeline.py` 渲染的是**项目 timeline**（另一棵状态树）
- `gemia/tools/extract_frame.py` 是对**视频素材**用 ffmpeg 抽帧
- lumenframe 文档真正的通道是 **`gemia/tools/lumen_seek.py`**、`lumen_render_range`、`layer.py::dispatch_render`

### 2.7 lumenframe 文档与 timeline 是两棵正交状态树（Phase 2 只做文档内）

`gemia/tools/layer.py` docstring 明说 "Lumenframe is **orthogonal** to timeline — no mutation of timeline fields"。文档在 `project["lumenframe"]`，shotlist 在 `project_state`。唯一的桥 `lumen_comp_to_timeline.py` 是**单向**的。且 `detect_beats` 接受**音频素材 asset_id**、返回**素材内秒数**，映射到文档时间需知道音频落位。

### 2.8 新工具上线有一道机器门，必须一次配齐 7 处

`tests/test_tool_catalog_contract.py` 是 build==install 元检查：`TOOL_NAMES` 的每个动词必须在**同一次改动**里配齐真实 dispatcher、TOOL_PACK 成员资格、恰好一个 plan-mode 分类、显式成本行。

| # | 文件 | 加什么 |
|---|---|---|
| 1 | `gemia/tools/_schema.py` | `TOOL_SCHEMAS` 条目 + 面向模型的描述 |
| 2 | `gemia/tools/__init__.py` | `DISPATCHER` 映射 |
| 3 | `gemia/tool_router.py` | 至少一个 TOOL_PACK |
| 4 | `gemia/plan_mode.py` | `PLAN_ALLOWED_TOOLS`（只读） |
| 5 | `gemia/budget_guard.py` | `_TOOL_COSTS` 行 `{"usd": 0.00, …}` |
| 6 | `gemia/turn_ledger.py` | 账本登记 |
| 7 | `gemia/mcp/toolset.py` | 若暴露 MCP：`_PHASE1_1TO1` + `MCP_READ_ONLY` |

另有 `tests/test_charter_integrity.py` 的硬编码创作动词参考集 `{grade, vector_motion, kinetic_type, camera, compose, edit_grammar, rhythm_edit}`。按决策（澄清 13）本层是 Layer 1 描述基座、**不进**该集合。

### 2.9 现成可用的零件

- `lumenframe/craft/determinism.py::stable_digest` —— blake2 over canonical JSON 的进程无关摘要，用于 NFR-1
- `gemia/tools/safe_areas.py` —— 五套平台安全区预设含 `avoid_zones`。⚠️ **坐标系**：它用**左上原点像素**，`model.DEFAULT_TRANSFORM` 用**画布中心原点像素**（"0,0 是正中，对齐 CapCut"）。换算层是经典 bug 温床。
- `lumenframe/preview.py::preview_frames` —— 一次编译多帧稀疏渲染，回归门与验收通道可直接复用

### 2.10 i18n 基建目前不存在

`gemia/`、`static/v3/`、`~/Code/lumeri-cli/` 均无 locales 目录、无翻译表、无取词函数。按决策（澄清 12），本特性只输出**语言中立机器标记**，翻译推到展示层，不承担 i18n 基建。

### 2.11 【新增·关键】存量文档实测：决策 A 的现实风险敞口为 0

回归门要跑什么素材，我实测了，不是假设。

**方法**：扫描全机器（`~` 下 9 层深度）所有 `lumenframe.json`，递归遍历图层树，统计非默认 `anchor_x`/`anchor_y`、`scale_x != scale_y`、`rotation != 0`、关键帧轨道。

**结果**：

| 指标 | 数值 |
|---|---|
| 找到的文档数 | **91** |
| 其中非空文档（根合成之外还有图层） | **11** |
| 图层总数 | **107** |
| **非默认 anchor 的图层** | **0** |
| **`scale_x != scale_y` 的图层** | **0** |
| **`rotation != 0` 的图层** | **0** |
| **任何关键帧轨道** | **0** |
| 图层类型分布 | `composition` 91（根）、`html` 11、`gradient` 2、`shape` 2、`adjustment` 1 |

文档分布在 `~/.lumeri/v3/projects/`、`~/.lumeri/v3/accounts/<account>/projects/`、`~/Code/lumeri-logo-motion-8s/`（logo 动效作品）、`~/Code/lumeri-reality-production-debug/`。

**补充**：我另外查了生成式库（`lumenframe/templates`、`elements`、`vector`、`kinetic`、`camera`）——**它们全都不写 `anchor_x`/`anchor_y`/`scale_y`**。也就是说 anchor 只可能来自模型显式调 `set_transform`，而实测没有一次这样的调用留下痕迹。

**这个发现的后果（重要）**：

原本设想的"对存量文档逐帧渲染对比"这道门，**在当前语料上会毫无悬念地全绿——因为没有任何一份文档触及被改的代码路径**。它证明的是"没有误伤"，**不是**"anchor 路径正确"。

所以回归门必须**拆成两道**（详见 §8.2）：一道跑真实语料证明**零误伤**，另一道跑合成夹具证明**新行为正确**。只做前者是自欺。

同时这也让**回退策略变得很轻**（§8.2 第 4 点）：没有存量数据依赖旧行为，回退就是回退代码，无需数据迁移。

### 2.12 【新增·关键】anchor 与"图层实际尺寸"是耦合的 —— 决策 A 依赖决策 B

这一条在拍板时没被讨论，但它决定 Phase 1 的**做事顺序**。

所有 content resolver 返回的都是**画布尺寸**的 RGBA（§2.3）。一个文字图层，是把文字块**居中贴**在一张画布大小的透明图上（`resolve.py` 里 `x_pos = (ctx.width - block_width) // 2`）。

于是问题来了：**`anchor_x = 0.0` 是谁的左边？**

- 若解释为"**画布尺寸内容帧**的左边" —— 实现极简单，但**语义无用**：所有图层的 anchor 都是画布的角，跟图层自身没关系，`anchor` 参数等于没意义。
- 若解释为"**图层实际内容（ink extent）**的左边" —— 这才是用户和 After Effects 意义上的 anchor，但它**需要知道图层的实际尺寸**——正是决策 B 要解决的东西。

**结论**：**anchor 要做出有意义的行为，必须先有决策 B 的尺寸解算能力。** 因此 Phase 1 的顺序不能是"先 A0 补渲染器、再 B 做几何"，而必须是：**先做尺寸解算内核（原 B 的一部分）→ 再补渲染器 A0 → 再做其余几何**。§7 的任务分解已按此重排。

这也意味着 A0 的验收标准里要显式写明 anchor 的语义选型（建议选"ink extent"，因为选另一个等于没做）。

---

## 3. Foundation 合规检查

| 原则 | 要求 | 本计划如何满足 | 风险 |
|---|---|---|---|
| **I. Primitive-First** | 纯原语 + 完整 docstring（docstring 是模型访问该函数的唯一接口） | 派生函数放 `lumenframe/grammar/`，纯函数无副作用；`lumen_stage` 的 schema 写全字段语义、坐标系、不可信情形 | 低 |
| **II. Model Plans, Host Executes** | 主机只提供事实，不代替模型做创作决策 | 态势报告只陈述事实（"重叠 8%"），**不**给建议；档位增量仅在模型明确要求时解析 | 低 |
| **IV. Output Honesty** | 描述必须反映真实渲染结果；存了没渲染的字段必须显式暴露 | **决策 A 让这条从"绕开"变成"修好"**：anchor/scale_y 不再是"存了不渲染"，而是真的渲染。`scale_y` 关键帧被静默丢弃的问题一并修复。transform 之外若仍有未渲染字段，`unrendered_fields` 机制保留 | 中→低（决策 A 后大幅降低） |
| **VI. 单一真相源 / 协议对等 / drift 门** | 一处声明，测试守护每个消费者 | 网格复用 `compose.framing.GRIDS`（决策 C）；安全区数据下沉后单一来源；web v3 与 CLI 同步；新增 drift 测试 | 中 |
| **III / V** | Skills 可复现 / provider 纪律 | 本层不产生 skill、不调 provider | 无 |

**新增合规注意**：alpha-first 让整个测量都用像素（不止遮挡）。这不违反任何 Foundation
原则（原则 IV 要的是"诚实"，不是"不许用像素"），但有两条必须做到，否则成为新的不诚实：

1. **工具 docstring 必须说明**遮挡与占比来自降采样覆盖掩码，是估计值而非精确值；
2. **盲区必须主动报出**（§7 X 组）——alpha 看不见"被丢弃的字段"与"裁剪"，
   而这两者恰恰是原则 IV 明令禁止静默处理的情形。第 2 条比第 1 条重要得多。

---

## 4. 架构决策（ADR）

### ADR-1：派生层落在 `lumenframe/grammar/`，与 `seek.py` 同族，ADD-ONLY

新建包 `lumenframe/grammar/`，作为 `model` / `timebase` / `compile` 公开面的纯消费者，**不被** `compile` / `model` / `ops` 反向引用。

**权衡**：塞进 `seek.py`（已 271 行、职责清晰）会让它膨胀；做成 `craft/` 下的点库违反 ADR-6；放 `gemia/` 工具层则 CLI 与未来 surface 无法共享。

### ADR-2（**已按决策 A 重写**）：补齐渲染器，派生层按数学正确算

**原方案**（描述"渲染器真相"+ `unrendered_fields` 暴露差异）**已作废**。

**新决策**：先把渲染器的 anchor / 非等比 scale 补正确（Phase 1 A0），派生层随后**直接按数学正确**计算包围盒。原 §2.2 的"描述真相 vs 描述意图"分裂设计消解。

**收益**：
- FR-1 不再需要分裂设计，派生逻辑简单一半
- `scale_y` 关键帧静默丢弃的诚实性缺陷顺带修复
- `unrendered_fields` 在 transform 这一块不再需要（其他字段若仍有未渲染项则保留该机制）

**代价**（诚实标注）：
- 触及共用渲染后端 `gemia/video/layers.py`（§2.2），风险面扩大
- 依赖决策 B 的尺寸解算才能让 anchor 有意义（§2.12）
- 需要一道两段式回归门（§8.2）

### ADR-3（**按决策 C 定稿**）：单一网格真相源，复用 `compose.framing`

网格制式 = **三分位 + 安全区**，12 列栅格不做。复用 `lumenframe/compose/framing.py` 的 `GRIDS` / `GridSpec`，在其上加坐标系适配层（裁切内 0..1 → 画布放置）。

若后续发现两个用途的网格必须分化，则把公共部分上提到 `lumenframe/grammar/vocabulary.py`，由 compose 反向消费，并加 drift 测试锁住数值一致。**不允许**并存两套数值。

### ADR-4（**按决策 C 定稿**）：安全区数据下沉，纯搬迁行为不变

**搬迁范围**：`gemia/tools/safe_areas.py` 里的 `_PRESETS`（五套：`generic_vertical` / `tiktok` / `reels` / `shorts` / `square_feed`，含 `title_margin`、`subtitle_margin`、`avoid_zones`）与 `_ALIASES` 别名表，整体搬到 `lumenframe/grammar/safe_zones.py`。

**谁来引用**（搬迁后）：
- `gemia/tools/safe_areas.py` —— 改为 `from lumenframe.grammar.safe_zones import PRESETS, ALIASES`，其 `dispatch` 逻辑（缩放、别名归一、box 计算）**保持原样不动**
- `lumenframe/grammar/stage.py` —— 消费同一份数据做安全区状态判定

**不搬**：`_scale_preset` / `_box_from_margin` 等纯计算函数留在原处（它们服务工具层的输出形状），避免把工具层的输出契约也拖进核心。

**验收**：搬迁前后 `safe_areas` 工具对同一组输入的输出**字节一致**（§8.4）。

### ADR-5：编辑闭环 = 派生层只产出 op **提案**，写入一律走 `apply_layer_patch`

`lumenframe/grammar/language.py` 把"往左一点"解析成 **LayerPatch ops 列表**（纯数据），**不**自己写文档。工具层再把 ops 交给已有的 `dispatch_patch`（`gemia/tools/layer.py`）执行。

**理由**：FR-4 禁止旁路写入；`ops.py::apply_layer_patch` 已保证原子性 + 校验 + 稳定错误码，复用即继承这些性质。

### ADR-6（**按决策定稿**）：本层是 Layer 1 描述基座，不是 Layer 2 点库

不按点库宪章立项，不进 `test_charter_integrity.py` 的创作动词参考集。

**理由**：宪章 §0.1 里点库（Layer 2）的定义是"把专业手艺硬编码成不可逃逸的结构"。本层**品味中立**——陈述"标题在左下三分位、重叠 8%"这个事实，不主张"应该在哪"。它关闭的不是创作域，而是**可寻址性缺口**（对应宪章 §10 失败模式 2 "TOOL-HONESTY / ADDRESSABILITY"）。

**仍需遵守**：宪章 §6.3 的安装元检查对**所有**动词生效，7 处配齐一处不能少（§2.8）。

### ADR-7（**新增·按决策 D**）：遮挡走轻量 alpha 光栅化

**决策**：遮挡比例与压盖关系用真实 alpha 覆盖计算，但**只光栅化 alpha**、在**低分辨率**下算、**不跑效果链、不做 blend、不走完整合成路径**。

**分辨率建议：画布长边缩到 256 px（约 1/7.5，1920×1080 → 256×144）。**

理由：遮挡比例的用途是回答"标题压住人脸了吗、压了多少"，模型据此决定挪不挪，**1% 的精度绰绰有余**。256×144 = 36,864 个采样点，1% 面积 ≈ 369 点，统计上足够稳。再低（如 128 长边）时细长元素（字幕条、分割线）会掉进采样缝隙里，风险不划算。选 2 的幂次的邻近值也便于整数倍下采样、避免重采样抖动。

**关键优化（让成本真的低）**：不是所有图层都需要真光栅化——

| 内容类型 | alpha 怎么来 | 成本 |
|---|---|---|
| `solid` / `gradient` | 满画布不透明，解析可知 | ~0 |
| `image` / `video` | 不透明矩形，范围 = min(原生尺寸, 画布)，解析可知（原生尺寸走决策 B 的元数据读取，**不解码像素**） | ~0（只读元数据） |
| `shape` | `props` 的归一化几何，低分辨率 PIL 绘制 | 极低 |
| `text` | 字体度量得块尺寸后低分辨率绘制 | 低 |
| 带 `mask` / track matte 的任意图层 | 复用 `compile.py::_rasterise_shape_mask` 的逻辑在低分辨率下重放 | 低 |

**要害**：`image`/`video` **不解码帧**，只读元数据取尺寸——这是成本估算成立的关键，否则解一帧 4K 就把优势吃光了。

**代价量级估算**（vs `lumen_render_range`）：

| | 轻量 alpha 遮挡 | 一次真实渲染 |
|---|---|---|
| 每图层数据量 | 256×144×1（alpha，float32）≈ 147 KB | 1920×1080×4（RGBA）≈ 8.3 MB |
| 效果链 | 不跑 | 全跑 |
| blend / 合成 | 不做 | 全做 |
| 视频解码 | **不解码**（只读元数据） | 每帧解码 |
| 编码输出 | 无 | H.264 编码 |

数据量约 **1/56**，且省掉解码与编码这两个真正的大头。量级上**低两个数量级以上**，与"零花费只读查询"的定位相容。

**确定性**：PIL / cv2 的光栅化对同一输入是确定的；低分辨率下采样用固定的整数倍 + 固定插值方式即可。纳入 §8.3 的确定性测试。

**边界守卫**：见 §1.1 ② 的新边界与 §8.4 的渲染深度 / 同源 / 盲区非空三条守卫。
（原"让步不扩散"守卫已作废，理由见 §8.4 表内说明。）

---

## 5. 数据结构草案

### 5.1 Stage Report（态势报告）

```
{
  "doc_id":  "doc_ab12…",
  "time":    2.5,              # 帧对齐后的秒（与 seek.state_at 同语义）
  "frame":   75,
  "canvas":  {"width": 1920, "height": 1080, "fps": 30.0},
  "grid":    {"kind": "thirds", "v": [0.3333, 0.6667], "h": [0.3333, 0.6667]},
  "detail":  "normal",         # brief | normal | full —— NFR-4 分级详略
  "occlusion_method": "alpha_raster@256",   # 诚实标注：遮挡怎么算的
  "layers": [                  # 稳定排序：与 compile 合成顺序一致（bottom → top）
    {
      "id": "text_9f2c…",
      "name": "主标题",
      "type": "text",
      "z": 3,                          # 合成层序，数字大 = 压在上面
      "bbox_px": {                     # 坐标系：左上原点像素，与 safe_areas 一致
        "x": 320, "y": 780, "w": 1280, "h": 160
      },
      "extent_source": "alpha_raster", # alpha_raster | clipped_estimate | empty_at_time | unavailable
      "area_ratio": 0.099,
      "region": "lower_center",        # 语言中立 token，按覆盖重心定位
      "regions_covered": ["lower_left", "lower_center"],  # 实际着墨的格子——
                                       # 必须由覆盖掩码算，不得用包围盒（§7 E4）
      "safe_area": {
        "status": "inside",            # inside | touching_edge | bleeding | offscreen
        "margin_step": 1,
        "violated_zones": []
      },
      "overlaps": [
        {"with": "video_31aa…", "ratio": 0.08, "relation": "covers"}
      ],
      "opacity": 1.0,
      "transform_at_t": {...}          # t 时刻求值后的 transform（表达式/关键帧已解算）
    }
  ],
  "degradations": [],   # 显式降级记录，见 §5.4 —— 绝不静默
  "warnings": [         # alpha 结构性看不见的东西，来自文档侧检查（§7 X 组）
    {"layer_id": "text_9f2c…", "kind": "discarded_intent",
     "field": "transform.scale_y", "set_to": 0.5, "effective": 1.2,
     "detail": "非等比缩放不被支持，渲染时坍缩为 scale_x"},
    {"layer_id": "badge_44b1…", "kind": "suspected_clipping",
     "suspected_clipped_edges": ["right"],
     "consequence": "量到的可能是裁剪残余；若确已出画，尺寸不可信且平移量无法由包围盒位移反推"}
  ]
}
```

**排序与稳定性**：`layers` 按 `compile._lane_ordered_children` 顺序（lane 感知，与渲染一致），保证可 diff。浮点统一保留位数（沿用 `model.TIME_NDIGITS = 6` 的做法），否则 NFR-1 会被浮点尾差打破。

**注意 `unrendered_fields` 已移除**（决策 A 后 transform 不再有未渲染字段）。若将来发现 transform 之外仍有"存了没渲染"的字段，按原则 IV 重新引入该字段。

### 5.2 Grid Vocabulary

```
{
  "grid_kind": "thirds",                       # 决策 C：只做三分位
  "regions": ["upper_left_third", …],          # 9 格
  "margin_steps": [...],                       # 边距档位
  "size_steps":   [...],                       # 尺寸档位
  "nudge_step":   {...}                        # "一点" = 多少
}
```

### 5.3 语言 → op 提案

```
输入: {"layer_id": "text_9f2c…", "phrase": "往左一点", "at_time": 2.5}
输出: {
  "ops": [{"op": "set_transform", "layer_id": "text_9f2c…", "transform": {"x": -416.0}}],
  "reasoning": {"from_x": -320.0, "step_px": 96.0, "steps": 1, "grid_kind": "thirds"},
  "applied": false     # 派生层只提案，执行走 lumen_patch
}
```

`reasoning` 必需——模型和用户都要能看出"一点"被解释成了多少，否则又变回猜数值。

### 5.4 降级记录（决策 B 要求：降级必须显式可见）

```
"degradations": [
  {"layer_id": "text_9f2c…", "kind": "font_missing",
   "detail": "Helvetica Neue 未安装，回退到 DejaVu Sans；文字块尺寸可能与最终渲染不符",
   "extent_source": "font_metrics_fallback"},
  {"layer_id": "video_31aa…", "kind": "asset_missing",
   "detail": "素材文件不可读，无法取原生尺寸",
   "extent_source": "unavailable"}
]
```

**硬规则**：素材缺失 / 字体缺失时，`extent_source` 必须落到 `unavailable` 或 `*_fallback`，且 `degradations` 必须有对应条目。**禁止**静默用默认值（如"当作满画布"）蒙混过去——那正是决策 B 明令禁止的。

---

## 6. 模块 / 文件级改动清单

### 6.1 新增（Phase 1）

| 路径 | 内容 | 约行数 |
|---|---|---|
| `lumenframe/grammar/__init__.py` | 公开面导出 | ~40 |
| `lumenframe/grammar/extent.py` | **alpha 测量内核**：单层 solo 渲染 → 真实包围盒 + 降采样覆盖掩码；上下文相关图层降级记录。**原型已实现，可直接移植** | ~170 |
| `lumenframe/grammar/blindspots.py` | **盲区检查层（非可选）**：被丢弃意图 + 疑似裁剪。**原型已实现，可直接移植** | ~140 |
| `lumenframe/grammar/geometry.py` | t 时刻属性求值：三级优先级（表达式 > 关键帧 > 静态）。**不再负责包围盒**（改由 extent 量得） | ~180 |
| `lumenframe/grammar/vocabulary.py` | 网格 / 区域 / 边距 / 尺寸档位（复用 compose.framing）。**原型已实现** | ~160 |
| ~~`lumenframe/grammar/occlusion.py`~~ | **合并进 extent.py**——遮挡就是覆盖掩码求交，与包围盒同源，独立成文件会诱使两者分叉 | — |
| `lumenframe/grammar/clipped.py` | 出画图层的文档侧几何推算（决策 B 的缩范围版本，见 §7 B0） | ~120 |
| `lumenframe/grammar/safe_zones.py` | 安全区预设数据（从 `gemia/tools/safe_areas.py` 下沉） | ~150 |
| `lumenframe/grammar/stage.py` | `stage_report(doc, seconds, *, detail=)` 主入口。**原型已实现** | ~230 |
| `lumenframe/grammar/language.py` | 短语 → 档位增量 → ops 提案；数值 → 语言反向命名。**原型已实现** | ~200 |
| `gemia/tools/lumen_stage.py` | 工具层 dispatch，只读 | ~120 |

> **移植来源**：标注"原型已实现"的模块在 `~/Code/lumeri-spatial-grammar/spatial_grammar/`
> 有可运行实现与验证脚本。移植时注意原型直接改 dict 应用编辑（`verify.py::apply_delta`），
> 正式实现必须改走 `apply_layer_patch`（ADR-5）。

### 6.2 修改 —— 渲染补齐（决策 A，**这是新增的高风险改动**）

| 路径 | 改什么 | 风险 |
|---|---|---|
| `gemia/video/layers.py` | `Layer` 支持双轴 scale + anchor；`_transform_frame` 非等比 resize + 绕 anchor 旋转 | **高（共用合成器）** |
| `lumenframe/compile.py` | 转发 `scale_y`/`anchor_*`；`_centred_position` 改 anchor 感知；`_KEYFRAME_PROP_MAP` 加 `transform.scale_y`；**更新 32-34 行的 Known limitations docstring** | 中 |

### 6.3 修改 —— 注册与对等（均为"加一行"，不改行为）

| 路径 | 改什么 |
|---|---|
| `lumenframe/__init__.py` | 导出 grammar 公开面 |
| `lumenframe/seek.py` | `state_at` 改为复用 P1-B 的 t 时刻求值内核（承接 Acrab 的独立任务卡） |
| `gemia/tools/_schema.py` | `lumen_stage` schema + 模型可读描述 |
| `gemia/tools/__init__.py` | `DISPATCHER["lumen_stage"]` |
| `gemia/tool_router.py` | 加进 TOOL_PACK（与 `lumen_seek` 同组） |
| `gemia/plan_mode.py` | `PLAN_ALLOWED_TOOLS`（只读） |
| `gemia/budget_guard.py` | `_TOOL_COSTS["lumen_stage"] = {"usd": 0.00, …}` |
| `gemia/turn_ledger.py` | 登记 |
| `gemia/mcp/toolset.py` | 若暴露 MCP：`_PHASE1_1TO1` + `MCP_READ_ONLY` |
| `gemia/tools/layer.py` | `dispatch_patch` 返回值附带精简态势摘要（决策：澄清 3 的第二半） |
| `gemia/tools/safe_areas.py` | 改为消费 `lumenframe/grammar/safe_zones.py`（行为不变） |
| `gemia/tools/lumen_seek.py` | 网格叠加层（Phase 2，FR-6） |
| `~/Code/lumeri-cli/`（独立仓） | 协议对等 |

### 6.4 明确不动

- `lumenframe/resolve.py` —— 内容解析不变
- `lumenframe/ops.py` —— Phase 1 不加新 op（复用 `set_transform` 等）
- 文档 schema（`model.py::_SCHEMA_KEYS`）—— Phase 3 才动
- `gemia/video/layers.py` 里 **transform 之外**的一切（效果链、blend、mask、编码）

> ⚠️ **命名冲突提醒**：`gemia/tools/transform_geometry.py` 已存在，是对**素材**做 ffmpeg 裁切/旋转/缩放的工具，与图层变换无关。新模块**不要**叫 `transform_geometry`。

---

## 7. 分阶段任务分解

### Phase 1 —— 空间态势 + 网格词表 + patch 闭环

> **2026-08-16 原型实证后重排**（依据见 §13）。三处结构性变化：
> **① 渲染补齐 A0 移出 Phase 1**，成为可并行的 Phase 0′——它不再是描述层的前置；
> **② B0 尺寸解算大幅缩范围**，从"全类型 extent solver"缩到"仅出画图层的几何推算"，
> 因为 alpha 测量已经覆盖了在画布内的情形；
> **③ 新增 X 组盲区检查层**，这是 alpha-first 方案能安全使用的前提，非可选。

**P1-A alpha 测量内核**（原型已验证，见 §13）

- A-1 单层 solo 渲染 → 从 alpha 通道量真实包围盒（一次编译、按需渲染，复用 `preview_frames` 的稀疏路径）
- A-2 保留降采样覆盖掩码，供遮挡与区域跨度**共用同一套数据**（两者若各算各的，描述会自相矛盾）
- A-3 上下文相关图层（`adjustment` / `null`）solo 渲染无意义 → 标 `unavailable` + `degradations`，不静默给错框
- A-4 成本控制：目前每图层一次全画布渲染，需要评估低分辨率渲染或包围盒预筛的可行性（原型未优化）
- A-5 单元测试：等比缩放 / 旋转 / 组合变换下包围盒与几何期望的偏差 ≤1px

**P1-B0 出画图层的几何推算**（决策 B 的**缩范围**版本）

> 原设想的全类型 extent solver（字体度量 + 素材元数据 + 逐类型实现）**不再需要**——
> alpha 直接给出画布内的真实范围。但 alpha 只存在于画布之内，
> 出画图层量到的是裁剪残余（实测：理论 2316×1302 → 实测 1545×807），
> 且**平移换算在该状态下失效**（施加 x+200，包围盒移动 0px）。所以这一块保留，范围缩小。

- B0-1 出画检测（触及画布边界 → 疑似裁剪，措辞不得断言）
- B0-2 仅对出画图层做文档侧几何推算，补出真实范围
- B0-3 出画图层的编辑换算：不能用包围盒位移反推，需另走文档侧路径
- B0-4 降级路径：推算不出时显式标注，**禁止静默默认值**

**P1-X 盲区检查层**（原型已实现 `blindspots.py`，**非可选**）

> alpha 让描述必然正确，同时让它对两类事情**静默失明**。没有这一层，
> 描述会在这两种情况下骗人——而静默骗人比能力不足严重得多。

- X-1 被丢弃的意图：扫描文档中设了但渲染器不读的字段（`scale_y`≠`scale_x`、`anchor`≠0.5、被丢弃的关键帧轨道），报出"设了什么 / 实际生效什么"
- X-2 疑似裁剪警告（承接 B0-1），声明尺寸不可信且平移量无法反推
- X-3 **drift 测试**：X-1 的字段表必须与 `compile.py` 的 Known limitations 同步；Phase 0′ 落地后相应条目应自动消失，该测试负责让陈旧表转红
- X-4 行为级测试：`scale_y=1.5` 与 `scale_y=0.5` 渲染结果相同时，必须产出警告（实测确认两者当前完全相同）

**P1-B 时刻求值器**

> 注意：包围盒本身**不再需要自己算**（A 组由 alpha 直接量得）。这一组存在的理由
> 变成两个：一是报告里要如实回显"t 时刻求值后的 transform"，二是 `state_at`
> 的既有缺陷需要一个正确的求值器来修。

- B1 t 时刻属性求值器：复刻三级优先级（表达式 > 关键帧 > 静态），复刻 `KeyframeTrack` 的 easing
- B2 `seek.py::state_at` 改为复用 B1（承接独立任务卡，消除"时间维求值、空间维不求值"的不一致）
- B3 单元测试：静态 / 关键帧 / 表达式 三类各一组
- B4 **一致性测试**：B1 求值出的 transform 与 A 组 alpha 量出的位置必须互洽——
  这是发现"求值器与渲染器语义分叉"的唯一机器手段

**P1-C 网格词表**（决策 C）

- C1 复用 `compose.framing.GRIDS` 的三分位；加坐标系适配层（裁切内 0..1 → 画布放置）
- C2 区域 token 表（语言中立）+ 数值 → token 反向命名
- C3 边距档位 / 尺寸档位 / `nudge_step`
- C4 drift 测试：词表与 `compose.framing.GRIDS` 数值一致

**P1-D 安全区**（决策 C）

- D1 预设数据下沉（搬迁范围与引用方见 ADR-4）
- D2 坐标系换算（中心原点 ↔ 左上原点）+ 状态判定
- D3 drift 测试：搬迁前后 `safe_areas` 工具输出**字节一致**

**P1-E 态势报告**

- E1 `stage_report(doc, seconds, *, detail=)` 主入口，排序沿用 `_lane_ordered_children`；顶层图层（嵌套见 Phase 1.5）
- E2 **遮挡：真实 alpha 覆盖**（决策 D / ADR-7）——直接用 A-2 的覆盖掩码求交，按 z 定压盖方向，双向比例（"我被盖了多少"与"我盖了对方多少"是两个问题）
- E3 面积占比、区域锚点、`occlusion_method` 诚实标注
- E4 **区域跨度必须用 A-2 的覆盖掩码，不得用包围盒**——原型初版用 bbox 导致细边框被描述成"占满 9 格"；与 E2 同源是硬规则
- E5 `detail` 三档（NFR-4 token 预算）
- E6 确定性测试（§8.3）
- E7 **误报回归**：空心边框 + 居中标题场景，断言真实覆盖为 0（包围盒法在此误报 100%）

**P1-F 语言桥与编辑闭环**

- F1 短语解析 → 档位增量（中英输入，输出是数值不是文案）
- F2 生成 LayerPatch ops 提案 + `reasoning`
- F3 测试：提案交给 `apply_layer_patch` 必须成功应用；断言模块内**无任何文档写入路径**

**P1-G 工具层与安装**

- G1 `lumen_stage` dispatch（只读）
- G2 schema 描述——docstring 是模型唯一接口（原则 I）：坐标系、字段语义、**遮挡是低分辨率估计**、降级情形
- G3 `dispatch_patch` 附带精简态势摘要（澄清 3 的第二半）
- G4 7 处注册全配（§2.8），`test_tool_catalog_contract.py` 转绿

**P1-H drift 门与验收**

- H1 §8.4 的全套 drift / 守卫测试
- H2 CLI 对等（`~/Code/lumeri-cli`）
- H3 按澄清 4 的降级口径跑基线评测

**Phase 1 完成判据**：模型不拿像素能正确回答"哪个元素在左下 / 有东西出画了吗 / 面积占比多少 / 谁压住谁"；空心图层的遮挡不误报；`stage_report` 连续调用字节一致；**被丢弃字段与疑似裁剪必定产出警告**；`lumen_stage` 通过安装元检查；CLI 对等。

> 注意：原判据里的"两段式回归门全绿"已移交 Phase 0′——它守的是渲染补齐，
> 不是描述层。Phase 1 不再依赖它。

**建议的验收实验**（原型已验证可行，见 §13）：拿一批场景，用一个**只读描述文本**
的确定性修复器把遮挡问题改到位，应用后**重渲染重测**。原型在 18/18 场景一次改到位、
残留 0.0000。这个实验的价值在于它是客观的：判决来自渲染器，不来自驱动编辑的同组数字。

### Phase 0′ —— 渲染补齐（**与 Phase 1 并行，互不阻塞**）

> **为什么从前置降级为并行**：它原本的理由之一是消解「描述残缺现实 vs 描述数学正确」
> 的分裂。alpha-first 已经消掉了这个分裂——不论渲染器支持什么，描述的都是它真正
> 画出来的东西。补渲染器因此成为**纯粹的能力问题**（让用户能用 anchor 与非等比缩放）。
>
> 决策 A 本身不变：补齐 + A0 独立提交 + 两段式回归门。变的只是它不再卡住主线，
> 排期由 Acrab 按能力需求的紧迫度单独决定。

- A0-1 定 anchor 语义：取 "ink extent"（需要图层真实尺寸——现在可由 alpha 测量供给，不必先建 solver）
- A0-2 `gemia/video/layers.py`：`Layer` 支持双轴 scale + anchor 字段
- A0-3 `gemia/video/layers.py`：`_transform_frame` 支持非等比 resize；旋转中心从内容中心改为 anchor 点
- A0-4 `lumenframe/compile.py`：转发 `scale_y` / `anchor_*`；`_centred_position` 改 anchor 感知（限制 #3 在此自然消解，见 §2.2）
- A0-5 `_KEYFRAME_PROP_MAP` 加 `transform.scale_y`；`frame_content` 关键帧分支同步（修复静默丢弃）
- A0-6 **更新 `compile.py:32-34` 的 Known limitations docstring**（过期的限制声明本身是不诚实）
- A0-7 **两段式回归门**（§8.2）—— A0 的出门条件
- A0-8 **落地后同步 Phase 1 的 X-1 字段表**：`scale_y`/`anchor` 警告应自动消失，由 X-3 的 drift 测试守住

**依赖方向**：Phase 0′ → Phase 1 是**单向可选增强**。Phase 1 不等它；它落地后
Phase 1 的盲区警告自动减少。反向无依赖。

### Phase 1.5 —— 嵌套 composition

- 父级变换链递归；`state_at` 同步支持嵌套（与 Phase 1 的 B2 一起收敛）
- 独立成阶段的理由：嵌套会让 anchor 语义再叠一层（父 anchor × 子 anchor），在 anchor 语义稳定前做等于返工

### Phase 2 —— 时间语义刻度 + 抽帧降级为验收

**已定**：只做**文档内**时间刻度（澄清 11）；网格叠加做在 **`lumen_seek`**（澄清 10）。

**待决策点**：

- **D-P2-1 拍点来源**：`detect_beats` 返回素材内秒数，映射到文档时间需知道音频图层落位。是否要求文档内必须有音频图层才提供拍点刻度？
- **D-P2-2 一致性检查点判据**：结构预期 vs 实际像素的容差是多少？超差是警告还是错误？

**方向性任务**：可见区间 / 共现区间派生 → 关键帧聚类成语义事件 → 拍/镜头/段落刻度 → `lumen_seek` 网格叠加 → 一致性检查点。

### Phase 3 —— 元素组关系

唯一要改文档 schema 的阶段。建议 Phase 1 实测定位准确率提升后再决定是否推进。

**待决策点**：

- **D-P3-1** 关系存哪：图层 `props`（不改 `_SCHEMA_KEYS`，向后兼容）还是文档顶层关系表（好查询，但改 schema + 迁移）？
- **D-P3-2** 与 shotlist IR 的 `belongs_to` 对齐——两棵正交状态树各有一套"属于"，先仲裁谁是真相源
- **D-P3-3** 传播规则本身是**创作语义**，可能真构成一个创作域；若是，Phase 3 要按点库宪章立项（ADR-6 的例外）
- **D-P3-4** 既有 `group_layers`/`merge_layers` 做的是**渲染分组**（建 composition），元素组是**创作分组**，文档里必须可区分

---

## 8. 测试策略

### 8.1 分层

| 层 | 测试 | 目的 |
|---|---|---|
| 纯函数 | `tests/test_lumenframe_grammar.py` | 几何、尺寸解算、词表、语言桥 |
| **渲染回归（决策 A）** | `tests/test_render_anchor_scale_regression.py` | **两段式**，见 8.2 |
| **确定性（NFR-1 新契约）** | `tests/test_grammar_determinism.py` | 见 8.3 |
| **渲染一致性** | `tests/test_grammar_render_agreement.py` | 派生 bbox vs 实渲染 alpha bbox |
| drift / 守卫 | `tests/test_grammar_drift.py` | 见 8.4 |
| 安装契约 | `tests/test_tool_catalog_contract.py`（既有，自动覆盖） | 7 处配齐 |
| 闭环 | 8.1 内 | ops 提案可被 `apply_layer_patch` 应用；无旁路写路径 |
| 协议对等 | 参照 `tests/test_v3_contract.py` | web v3 与 CLI 同步 |

### 8.2 存量回归门（决策 A 的保护，**Acrab 要求的具体设计**）

**背景**（§2.11 实测）：全机器 91 份文档、107 个图层，**没有任何一份**写过 anchor / 非等比缩放 / 旋转 / 关键帧。所以"跑存量文档对比"这一件事**证明不了新行为正确**——它只能证明没误伤。因此拆两段。

#### 第 1 段：零误伤门（跑真实语料）

- **跑什么素材**：`~/.lumeri/v3/projects/`、`~/.lumeri/v3/accounts/*/projects/`、`~/Code/lumeri-logo-motion-8s/`、`~/Code/lumeri-reality-production-debug/` 下全部 **91 份 `lumenframe.json`**。因为这些是用户私有数据，**做法是把它们脱敏快照进 `tests/fixtures/grammar/corpus/`**（只保留文档结构，素材路径替换为夹具素材），而不是让测试去读用户目录——否则测试在别的机器上跑不了，也把私有数据带进了仓库以外的执行环境。
- **怎么比**：对每份文档，在补齐前后各渲染同一组帧（用 `lumenframe/preview.py::preview_frames`，稀疏取帧：首帧、中间帧、末帧，若有关键帧则额外取关键帧时刻）
- **判定阈值**：**逐像素严格相等**（`np.array_equal`）。不设容差——因为按预期这 91 份文档一个像素都不该变。任何差异都说明补齐泄漏到了不该动的路径，必须查清而不是调容差。
- **预期结果**：全绿且零差异。**如果这一段出现任何差异，就是 A0 实现越界了**，直接回退重做。

#### 第 2 段：新行为正确性门（跑合成夹具）

因为语料里没有 anchor / scale_y 的实例，新行为必须**造夹具**来证明。

- **跑什么素材**：一个**合成夹具矩阵**，维度覆盖：
  - anchor：`(0.5,0.5)` 默认 / `(0,0)` 左上 / `(1,1)` 右下 / `(0.5,0)` 上中
  - scale：等比 / `scale_x != scale_y` 两向 / 极端值（0.1、4.0）
  - rotation：0° / 45° / 90° / -30°（与 anchor 组合，验证"绕 anchor 而非绕中心"）
  - 图层类型：`shape`（尺寸解析式可知，最好验证）、`text`、`solid`
  - 关键帧：`scale_y` 打关键帧（验证不再被静默丢弃）
- **判定标准**：**黄金帧比对**（golden frames）。首次实现时人工核验每张夹具渲染结果确实符合 anchor 语义（例如 anchor=(0,0) 时放大，图层应向右下"长出去"而非四周均匀扩张），核验通过后固化为黄金帧，后续逐像素比对。
  - 黄金帧比对用**严格相等**；若因平台 `cv2` 版本差异出现 ±1 灰阶抖动，改用 `np.allclose(atol=1)` 并在测试里注明原因——**不得**无理由放大容差。
- **补充断言**：对 shape 图层（尺寸解析式可知），额外用**解析计算**独立验算包围盒落点，不完全依赖黄金帧——避免"黄金帧把错误行为固化下来"。

#### 第 3 段：回退策略

- **不引入特性开关**。理由：§2.11 实测**没有任何存量数据依赖旧行为**，加开关等于为一个空集合维护两条渲染路径，违反 less is more，且双路径本身会成为新的漂移源。
- **回退 = 回退代码**（`gemia/video/layers.py` + `compile.py` 的该次提交）。**无需数据迁移**——因为没有任何文档的呈现依赖被改的语义。
- **触发回退的条件**（写进 A0 的出门条件）：第 1 段出现任何非零差异，且 24 小时内定位不到原因。
- **补充保险**：A0 建议**独立成一次提交**，不与 grammar 派生层的代码混在一起，这样回退是干净的 revert 而不是手术。

### 8.3 确定性契约（决策 B 改写后的 NFR-1）

**旧契约**（作废）：同一文档 + 同一时刻 → 字节级相同的描述。
**新契约**：**给定同一套字体与素材**，同一文档 + 同一时刻 → 字节级相同的描述。

**可测试形式**（关键：不要写成断言跨机器一致的测试，那必然会 flaky）：

1. **夹具锁定**：测试用字体文件与素材文件**固定进 `tests/fixtures/grammar/`**，测试显式指向夹具而非系统字体。这样确定性测试测的是"给定同一套字体与素材"这个前提下的确定性——**与新契约的措辞完全对应**。
2. **同进程重复**：同一输入连跑 N 次，`stable_digest` 摘要相同。
3. **跨进程重复**：另起进程再跑一次比对（`hash()` 有 per-process salt，`blake2` 没有，所以这条能真正抓住偶然引入的进程态依赖）。
4. **不做**：跨机器 / 跨字体版本的一致性断言。**改为**在 `lumen_stage` 的 docstring 里对模型明说"尺寸依赖本机字体与素材，换环境可能变化"。
5. **降级路径也要测**：字体缺失、素材缺失各一个用例，断言 `extent_source` 正确落到 `*_fallback` / `unavailable` 且 `degradations` 有条目——**断言它不静默**。

### 8.4 drift 门与边界守卫（2026-08-16 按 alpha-first 重写）

| 测试 | 守什么 |
|---|---|
| 网格 drift | grammar 词表数值 == `compose.framing.GRIDS`（ADR-3） |
| 安全区 drift | 数据下沉前后 `safe_areas` 工具输出字节一致（ADR-4） |
| ~~让步不扩散守卫~~ | **作废（2026-08-16）**。原设计断言"关闭光栅化后除 `overlaps` 外所有字段值不变"——alpha-first 下所有测量都来自 alpha，这条测试会阻止正确实现。按 §1.1 ② 的新边界重写为下一行 |
| **渲染深度守卫**（替代） | 断言测量路径只取**单层 alpha**：不跑效果链、不做 blend、不走完整合成，且不读取 alpha 之外的像素通道（色彩/亮度等） |
| **同源守卫** | 断言遮挡与区域跨度使用**同一套覆盖数据**——两者分叉会让描述自相矛盾（原型初版即栽在这里） |
| **盲区非空守卫** | 构造设了 `scale_y`/`anchor` 的文档与出画文档，断言必定产出对应警告；警告缺失即回归 |
| **限制表 drift** | X-1 的被忽略字段表 == `compile.py` 的 Known limitations；Phase 0′ 落地后该表应收缩，陈旧表必须转红 |
| 渲染一致性 | 派生 bbox vs 实渲染 alpha bbox 在容差内一致（alpha-first 下两者同源，此测试降级为回归护栏） |
| 无旁路写入 | `grammar` 包内不存在文档写入路径（FR-4） |
| 无缓存 | `stage_report` 不持有跨调用状态（NFR-2） |

### 8.5 成功标准 2 的基线（按澄清 4 的降级口径）

**必须在成功标准里写清楚这是降级口径，不能读起来像端到端**：

> 成功标准 2（修订）：**"往左一点"这类模糊指令，系统解析出的像素增量落在期望区间**的比例相对基线可测量地提升。
> **本项不测**模型是否真的照做——受 NFR-3（零 provider 花费）约束，评测集不发起模型调用，只测确定性的解析环节。
> 因此本项**不能**被解读为"端到端一次成功率"。端到端表现需要另立带花费的评测，不在本特性范围。

---

## 9. 风险与缓解

| # | 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|---|
| **R-1** | **决策 A 触及共用渲染后端 `gemia/video/layers.py`**（§2.2），波及 timeline 渲染 / 导出 / 预览等所有产品线 | **高** | 中 | §8.2 第 1 段零误伤门（91 份文档逐像素）；A0 独立成一次提交便于干净回退；改动严格限定在 transform，不碰效果链/blend/编码 |
| **R-2** | **anchor 语义选错**（§2.12）——若按"画布尺寸内容帧"解释，等于白做 | 高 | 中 | A0-1 明确取 "ink extent" 语义并写进 docstring 契约；依赖 B0 先落地 |
| **R-3** | 尺寸解算与实际渲染用了**不同的字体回退链**，导致描述与画面不符 | 高 | 中 | B0-2 强制复用 `resolve.py::_resolve_font` 同一条链；§8.4 渲染一致性测试兜底 |
| **R-4** | 遮挡光栅化**成本失控**（若误对 image/video 解码帧） | 中 | 中 | ADR-7 的解析式快路径：image/video **只读元数据不解码**；测试断言遮挡路径不触发视频解码 |
| ~~R-5~~ | ~~"零像素"让步扩散到其他字段~~ | — | — | **作废**：alpha-first 下测量本就全部基于 alpha（§1.1 ②） |
| **R-5′** | 渲染深度失控——从"只取单层 alpha"滑向跑效果链或完整合成 | 中 | 中 | §8.4 渲染深度守卫 |
| **R-13** | 遮挡与区域跨度**分叉**，用了不同的覆盖数据，描述自相矛盾 | 中 | 高 | §8.4 同源守卫；原型初版即栽在这里（§13.5） |
| **R-14** | 盲区检查缺失或失效，描述在"字段被丢弃"与"出画"时**静默骗人** | 中 | **高** | §8.4 盲区非空守卫 + 限制表 drift（X-3/X-4） |
| **R-15** | 每图层一次渲染的成本在图层多时不可接受 | 中 | 中 | A-4 评估低分辨率渲染或包围盒预筛；原型未优化，需实测 |
| **R-6** | 派生层被当缓存用（NFR-2 明令禁止） | 高 | 中 | 架构上不留缓存位；每次从 `layer._lumendoc` 现读现算；§8.4 无缓存测试 |
| **R-7** | 坐标系换算 bug（中心原点 ↔ 左上原点） | 中 | 中 | 换算只在一处发生；正负方向、奇偶像素画布、超出画布四种边界各一测试 |
| **R-8** | 浮点尾差打破确定性 | 中 | 中 | 统一保留位数（沿用 `TIME_NDIGITS` 做法）；跨进程 `stable_digest` 验证 |
| **R-9** | 黄金帧把**错误行为**固化下来（§8.2 第 2 段） | 中 | 中 | shape 图层额外用解析计算独立验算，不单靠黄金帧；首次固化必须人工核验 |
| **R-10** | CLI 对等被漏掉（NFR-5 / 原则 VI） | 中 | 中 | CLI 改动列进 Phase 1 完成判据（P1-H2），不是"之后再说" |
| **R-11** | 报告撑爆上下文（NFR-4） | 中 | 中 | `detail` 三档第一版就做；`brief` 只报被问到的图层；patch 回传只带精简摘要 |
| **R-12** | **既有欠账**：`static/v3/contract.json` 与 `~/Code/lumeri-cli/src/contract.json` 内容已不一致 | 低（对本特性） | 已发生 | **不在本特性范围**，不顺手修。仅记录在此：本特性做 CLI 对等时，需知道对等基线本身有欠账，不要误以为同步机制可靠 |

---

## 10. 已确认决策

> 原「待澄清」章节。Acrab 于 2026-08-16 全部拍板，以下为结论与后果。

### 决策 A（原澄清 6）— 顺手把渲染器补齐 ✅

**结论**：让渲染器真正支持 `anchor_x`/`anchor_y` 与非等比 `scale_y`，并把 `transform.scale_y` 加进 `_KEYFRAME_PROP_MAP`。

**后果**：
- §1.1 的「零渲染改动」边界**已重写**为「一次有界的正确性补齐」，边界与禁止项见 §1.1 ①
- 渲染补齐 **A0** 已于 2026-08-16 独立成并行 **Phase 0′**（§7），不再是 Phase 1 的前置
- **实际范围大于"改 compile.py"**：真正的实现在共用渲染后端 `gemia/video/layers.py`（§2.2），风险面见 R-1
- **依赖决策 B**：anchor 要有意义必须先有图层实际尺寸（§2.12），故 B0 排在 A0 之前
- **正面**：FR-1 不再需要"描述真相 vs 描述意图"的分裂设计，派生层直接按数学正确算；`unrendered_fields` 在 transform 这块移除；`scale_y` 关键帧静默丢弃的诚实性缺陷一并修复
- **限制 #3（关键帧不重算旋转包围盒）建议纳入本次范围**——理由见 §2.2：现有的 `_centred_position` 补偿本就是"没有 anchor 模型"的变通，anchor 做对后它自然消失；只补 anchor 而留着它会产生"静态路径正确、关键帧路径不正确"的新分裂
- `compile.py:32-34` 的 Known limitations docstring 必须同步更新（A0-6）
- **回归门**见 §8.2（两段式 + 回退策略），设计依据是 §2.11 的实测

### 决策 B（原澄清 7）— 允许读本机字体度量与素材尺寸 ✅

**结论**：NFR-1 从「纯函数确定性」改写为「**给定同一套字体与素材，结果确定**」。

**后果**：
- 新增模块 `lumenframe/grammar/extent.py`（P1-B0）
- **新契约的可测试形式**（§8.3）：确定性测试**固定字体与素材夹具**（`tests/fixtures/grammar/`），测"给定同一前提下的确定性"；**不写**跨机器一致性断言（必然 flaky）；改为在 `lumen_stage` docstring 里对模型明说尺寸依赖本机环境
- **降级必须显式可见**：字体缺失 / 素材缺失 → `extent_source` 落到 `*_fallback` / `unavailable`，且 `degradations` 必有条目（§5.4）。**禁止**静默用默认值蒙混，§8.3 第 5 点有专门测试
- 尺寸解算**必须复用** `resolve.py::_resolve_font` 的同一条字体回退链，否则描述与渲染不符（R-3）

### 决策 C（原澄清 1、8）— 三分位 + 安全区，复用既有词表 ✅

**结论**：网格用**三分位 + 安全区**；复用 `lumenframe/compose/framing.py` 与 `gemia/tools/safe_areas.py`；**12 列栅格不做**。安全区数据下沉到 `lumenframe/`（采纳原建议）。

**后果**：ADR-3、ADR-4 定稿。搬迁范围（`_PRESETS` 五套 + `_ALIASES`）与引用方见 ADR-4；纯搬迁、行为不变，由 §8.4 的字节一致 drift 测试守护。

### 决策 D（原澄清 5）— 遮挡算 alpha/mask 真实覆盖 ✅

**结论**：走**轻量 alpha 光栅化**——只光栅化 alpha、低分辨率、不跑效果链、不做 blend、不走完整合成路径。

**后果**：
- **分辨率建议：长边 256 px**（1920×1080 → 256×144）。理由见 ADR-7：1% 精度足够，再低细长元素会掉进采样缝隙
- **代价量级**：数据量约为一次真实渲染的 **1/56**，且省掉视频解码与编码两个大头，量级上低两个数量级以上（对比表见 ADR-7）
- **成本估算成立的关键**：image/video **只读元数据取尺寸、不解码帧**（R-4）
- **确定性**：固定整数倍下采样 + 固定插值，纳入 §8.3
- **「零像素」前提已整体让步**（不止遮挡），已在 §1.1 ② 与 §13.2 诚实标注；报告里 `occlusion_method` 字段显式告诉模型这是降采样估计。换来的是尺寸解算、真实覆盖、描述真相三个问题一并解决
- **让步不得扩散**：§8.4 有专门守卫测试，断言除 `overlaps` 外所有字段在关闭光栅化时仍能算出且值不变

### 其余已定项

| 原澄清 | 结论 | 落点 |
|---|---|---|
| **3 交付形态** | 独立只读工具 `lumen_stage` 主动调用，**加上** patch 应用后自动回传精简摘要。**不做**「每次 `get_lumenframe` 带全量」（会撑爆上下文） | P1-G1、G3；§6.3 `layer.py` |
| **4 基线** | 接受降级：只测"系统把'往左一点'解析成多少像素"的确定性部分，不测模型是否照做（受零花费约束） | §8.5，措辞已写明不得解读为端到端 |
| **10 FR-6 目标** | 网格叠加做在 `lumen_seek`；规格点名 `inspect_timeline` 是错的（另一棵状态树） | Phase 2；§2.6 |
| **11 Phase 2 范围** | 首版只做文档内时间刻度，跨状态树留后 | Phase 2 |
| **12 i18n** | 只输出语言中立机器标记，翻译推到展示层，不承担 i18n 基建 | §2.10；§5.1 `region` 等字段 |
| **13 点库归属** | 不算点库，是 Layer 1 描述基座 | ADR-6 |
| **14 嵌套** | Phase 1 只做顶层，嵌套留 **Phase 1.5** | §7 Phase 1.5 |

### 并入的既有任务卡

`seek.py::state_at` 的空间维不求值缺陷（`:252` 只合并静态 transform，而 `:179` 对 `time_remap` 关键帧是求值的——**时间维求值了、空间维漏了**）已由 Acrab 开成独立任务卡。

**本计划的承接方式**：Phase 1 的 t 时刻属性求值内核（P1-B1）落地后，`state_at` **复用它**（P1-B3），不各写一套插值。这是那张卡的实现依赖，两者应当一起收敛。

---

## 11. 与规格假设不符之处（汇总）

| # | 规格原文 | 实际代码 | 处置 |
|---|---|---|---|
| 1 | §1"当前**不存在**任何面向语言的空间派生量：`safe_area`、遮挡关系、区域锚点、尺寸档位均无实现" | `gemia/tools/safe_areas.py` 有完整平台安全区预设（含避让区）；`lumenframe/compose/framing.py` 有三分/黄金/中心网格词表与锚点格 | **已解决**：决策 C 定为复用（ADR-3、ADR-4） |
| 2 | FR-1"正确处理 `rotation`/`scale`/`anchor`/父级变换链" | `compile.py` 从不读 `anchor_*`；`scale_y` 完全被忽略；`scale_y` 关键帧被静默丢弃 | **已解决**：决策 A 补齐渲染器。但实际范围大于规格设想（§2.2），且与决策 B 耦合（§2.12） |
| 3 | NFR-1"同一文档 + 同一时刻 → 字节级相同的描述" | 文档里没有图层尺寸：文字靠本机字体度量，素材要读文件 | **已解决**：决策 B 改写契约为"给定同一套字体与素材"（§8.3） |
| 4 | §1"唯一通道是抽帧（`inspect_timeline`、`extract_frame`）" | 两者服务的是 timeline 状态树和素材，**不是** lumenframe 文档；文档通道是 `lumen_seek`/`lumen_render_range` | **已解决**：FR-6 目标改为 `lumen_seek` |
| 5 | §1"图层间目前只有两种关系：父子和堆叠顺序" | 还有 **track matte**（`mask.source_layer_id`）与 `clip_to_below` 两种既有渲染关系 | 不影响结论；Phase 3 设计传播规则时必须一并考虑（D-P3-4） |
| 6 | FR-2"接入已有的 `detect_beats`" | 它接受**音频素材 asset_id**、返回**素材内秒数**，映射到文档时间需知道音频落位 | Phase 2 决策点 D-P2-1 |
| 7 | NFR-5"web v3 与 CLI 必须同步" | `static/v3/contract.json` 与 `~/Code/lumeri-cli/src/contract.json` 内容已不一致 | **既有欠账，不在本特性范围**（R-12），仅记录 |
| 8 | （规格未提）决策 A 的"存量作品移位"风险 | 全机器 91 份文档 / 107 图层：**anchor、非等比缩放、旋转、关键帧的使用数均为 0** | **风险敞口实测为 0**；回归门据此重新设计为两段式（§8.2、§2.11） |

---

## 12. 下一步

1. 评审本计划（重点看 §13 原型实证结论、§1.1 ② 重写后的像素边界、§7 重排后的任务分解）
2. 评审通过后走 `/buddy:tasks` 生成任务分解
3. **开工顺序（2026-08-16 重排）**：
   **A alpha 测量内核 → X 盲区检查 → C/D 词表与安全区 → E 态势报告 → B 求值器 → B0 出画推算 → F 语言桥 → G 安装 → H 对等与验收**
   Phase 0′（渲染补齐）与上述主线**并行**，排期独立。

---

## 13. 原型实证结论（2026-08-16）

在动共用合成器之前先做了隔离原型验证核心假设。**本节结论优先于前文的推测**，
前文中与之冲突的部分已就地标注。

- 原型位置：`~/Code/lumeri-spatial-grammar/`（独立目录，主仓零改动）
- 完整报告：该目录下的 `FINDINGS.md`
- 验证脚本：`verify.py`（闭环修复）、`verify_decisions.py`（决策 D + NFR）、
  `verify_hollow.py`（空心图层）、`verify_transform.py`（旋转缩放 + 盲区）

### 13.1 核心假设成立

30 个随机场景、18 个有遮挡问题。一个**只读描述文本、不碰任何像素**的确定性修复器
**18/18 一次改到位**，残留重叠 0.0000。验收用重渲染 + 真实 alpha 重测——
判决来自渲染器，不来自驱动编辑的同组数字。

推理闭合：若一个不会思考的算法读同一份文本就能一次改对，则该文本携带的定位信息
充分；模型读它只会更好。

### 13.2 alpha-first 一次解决三个问题

单独渲染每个图层、从 alpha 量真实包围盒，同时解决：

| 原难题 | alpha 方案下 |
|---|---|
| B0 全类型 extent solver（字体度量 + 素材元数据） | 不需要——alpha 直接给出真实内容范围 |
| 决策 D 真实覆盖遮挡 | 免费得到——覆盖掩码求交即真实覆盖 |
| "描述渲染真相还是文档意图"的分裂 | 不存在——alpha 就是渲染结果 |

**代价**：每图层一次渲染，本层不再声称零像素路径，成本未优化（A-4 待办）。

### 13.3 边界：alpha 只存在于画布内

出画图层量到的是裁剪残余，实测：

```
基准 386×217，放大 6× 后理论 2316×1302
实测 (0, 0, 1545, 807)          → 被画布裁剪
出画状态下施加 x+200，包围盒移动 +0px → 平移换算失效
```

所以 B0 的文档侧几何推算**保留但缩范围**：从"所有图层"缩到"出画图层"。

### 13.4 已验证可推广：平移在任何变换组合下仍是纯平移

修复器把屏幕位移直接当作 transform 位移，这对纯平移显然、对含缩放旋转的链条完全不显然。实测：

| 变换 | 施加 | 包围盒实际移动 |
|---|---|---|
| 纯平移 | (+120, −80) | (+120, −80) ✓ |
| 缩放 1.2 + 平移 | (+120, −80) | (+120, −80) ✓ |
| 旋转 30° + 平移 | (+120, −80) | (+120, −80) ✓ |
| 缩放 0.7 + 旋转 45° + 平移 | (+120, −80) | (+120, −80) ✓ |

包围盒本身也准：等比缩放 1.2×/0.6× 与几何期望差 ≤1px，旋转 90° 宽高正确互换。
**所以 18/18 的结果可推广到旋转缩放场景**（出画状态除外）。

### 13.5 决策 D 被实证必要（初测险些误判）

78 个随机图层对，包围盒误报 **0** 对——看似决策 D 白花钱。但那是场景性质：
随机场景全是填满包围盒的实心形状（椭圆填充率 78%）。补上空心边框场景后：

```
「主标题」有多少被「空心边框」占据？
    包围盒说 100.0%  |  真实 alpha 说 0.0%  → 包围盒误报
```

用包围盒，模型会去移动一个根本没被遮挡的标题——主动破坏本来正确的画面。

**同类 bug 已在原型修复**：区域跨度原本也用 bbox，把细边框描述成"占满 9 格"；
改用真实覆盖后空心边框的 `mid_center` 正确消失。故 §7 E4 定为硬规则。

### 13.6 两个静默盲区（→ X 组的由来）

行为级实测（这是对该缺口的第三次独立验证，前两次为代码级）：

```
scale_y=1.5 → (613, 332, 464, 261)
scale_y=0.5 → (613, 332, 464, 261)   完全相同
anchor 默认 → (613, 332, 464, 261)
anchor 左上 → (613, 332, 464, 261)   完全相同
```

用户设了 `scale_y=0.5`，画面与 `1.5` 一模一样。alpha 结构性看不出这件事——
描述保持正确，用户意图无声消失，正是原则 IV 禁止的静默丢弃。加上 §13.3 的裁剪盲区，
两者构成 X 组的存在理由。

### 13.7 NFR 实测

- **NFR-1**：20/20 场景同进程两次调用字节级一致 ✓。但这是**弱验证**——
  alpha-first 下确定性取决于渲染管线本身（字体、素材、OpenCV 插值），
  跨机器/跨字体一致性**尚未验证**，那才是决策 B 改写契约后的真正考验。
- **NFR-4**：描述平均 727 字符 ≈ 581–830 tokens，同尺寸单帧图约 2764 tokens——
  **约 4–5 倍差距，不是数量级差距**。描述的价值不在省 token 而在**可寻址**：
  图能让模型看出"压住了"，说不出"把哪个 id 的 x 改成多少"。

### 13.8 仍未验证（不要当成已验证）

出画图层的自动修复（能检出、不能修）、真实模型端到端行为（受零 provider 花费约束）、
跨机器/跨字体确定性、真实项目文档与真实素材、时间维 FR-2、嵌套 composition、
渲染成本在图层数多时的表现。
