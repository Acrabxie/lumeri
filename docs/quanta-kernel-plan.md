# Lumeri Quanta：离散视频内核方案（v1）

- status: adopted（2026-07-13；用户拍板产品本质 =「离散视频：有状态树、交互树、编辑树的视频」，弃用 PPT/Deck 命名；本方案经 3 路独立设计 + 7 份三视角对抗验证综合而成）
- supersedes vocabulary of: `docs/deck-interactive-video-plan.md` v2.1（主仓，架构决策仍有效；本文档只重定义词汇与内核，物化链决策原样继承）
- 基线: feat/deck-interactive @ a45e7ac（Phase 1a+1b 完成态）；回归红线快照 = 3 failed（test_memory_log / test_v3_contract / test_v3_generate_image，均既有）/ 2473 passed / 88 skipped

## 0. 命名：Lumeri Quanta

**产品线名：Lumeri Quanta。中文品类词：离散视频。**

quanta 是普朗克为「光的能量只能一份一份存在」造的词——光的离散性的第一词源，离散不是隐喻而是词义本身。四字核心逐条对应：

1. **离散视频** = 光量子本身；「连续视频是退化情形」有严格物理对应——对应原理（量子数大、能级间隔趋密时量子系统退化为连续经典系统），线性路径 + 自动推进 + 零 hold 正是经典极限。
2. **状态树** = 能级图（Grotrian diagram）：离散状态的树，每个 quantum 一个状态。
3. **交互树** = 受激跃迁：状态间跳变只在触发时发生。
4. **编辑树** = 按 quantum 寻址做单态手术，refine 单个 quantum 不重物化全份。

两位对抗评委独立裁决 Quanta 全场最高（8/10），并经仓内 grep 取证：quanta/quantum 是唯一零命名空间污染的候选（spectra 的 line 撞排版引擎 line_breaks、pulse 命中 9 个特效文件、facet 撞 metadata_facets、prism 色散连续 + 棱镜门）。已知风险与对策：中文语境「量子」有营销腔——**对外中文文案一律用「离散视频」，不用「量子」自称**；Quanta Computer 在先（不同类别，仅搜索占位）。

### 0.1 词汇映射表（rename 的完整契约）

| 旧 | 新 | 备注 |
|---|---|---|
| Lumeri PPT / Deck | Lumeri Quanta | 产品线名 |
| `gemia/deck/` | `gemia/quanta/` | python 包 |
| `project_state.deck` | `project_state.quanta` | IR 键 |
| slide / build（节点） | quantum（统一名词） | kind 由结构派生：group / content / state |
| `gemia/tools/deck.py` | `gemia/tools/quanta.py` | set/update/get 三件套 |
| `draft_deck` | `draft_quanta` | 工具 |
| `set_deck` | `set_quanta` | 工具 + patch op |
| `update_slide` | `update_quantum` | 工具（v1 升格为节点寻址编辑面，§4） |
| `get_deck` | `get_quanta` | 工具 |
| `assemble_deck` | `assemble_quanta` | 工具 |
| `refine_slide` | `refine_quantum` | 工具 |
| `deck_frames.py` | `quanta_frames.py` | 帧资产注册 |
| `normalize_deck` | `normalize_quanta` | project_model |
| patch op `set_deck`/`update_slide` | `set_quanta`/`patch_quantum`/`insert_quantum`/`remove_quantum`/`move_quantum` | patches.py（§4） |
| `state_scope: "deck"` | `state_scope: "quanta"` | patch 事件 |
| `static/v3/deck.{html,js}` | `static/v3/quanta.{html,js}` | pager；URL 契约形状不变 |
| `tests/test_deck_*` 等 | `tests/test_quanta_*` 等 | 62+ 个测试全量迁移，零覆盖丢失 |

分支名 feat/deck-interactive、worktree 目录名**不改**（历史指针价值 > 一致性；QUEUE 有记录）。工具总数不变（7 个 rename，零新增平铺工具——尊重 execution-intelligence-fix 的「暂停新增平铺工具」纪律）。

## 1. 一句话内核

> **一棵有序状态树；三棵树是它的三张脸：结构（状态树）、观看（交互跃迁）、修改（按节点寻址的编辑 op + patch-log）。连续视频 = 线性叶链 + 全自动推进 + 零 hold 的退化情形，零特判。**

## 2. IR v2：状态树

```jsonc
project_state.quanta = {
  "version": 2,
  "theme": { "tokens": {…}, "mood": "calm-tech", "aspect": "16:9" },   // 原样
  "root": {
    "id": "root",
    "children": [
      // ── group quantum（章节；无 blocks，纯结构，可嵌套 group/content）──
      { "id": "sec1", "title": "第一章", "hidden": false, "children": [
        // ── content quantum（内容域 = 今日 slide；声明 blocks，几何按此求解一次）──
        { "id": "s1", "layout": "title", "title": "One Lumen",
          "blocks": [ …v1 blocks 原样（text/stat/image/shape/group）… ],
          "notes": "讲稿…", "mood_override": null,
          "transition": { "kind": "cut" },
          "links": [ { "trigger": "hotspot:blk_cta", "target": "quantum:s9" } ],
          "children": [
            // ── state quantum（离散状态叶 = 今日 build）──
            { "id": "s1_b1", "visible_block_ids": ["blk_title"], "dwell_sec": 1.2, "advance": "wait" },
            { "id": "s1_b2", "visible_block_ids": ["blk_title","blk_cta"], "dwell_sec": 2.0, "advance": "wait" }
          ] }
      ] }
    ]
  }
}
```

- **kind 由结构派生，不入库**：声明 `blocks` → content；content 之下 → state；其上 → group。`get_quanta` 文本视图可标注派生 kind 供模型阅读。
- **content quantum 的 children 必须全为 state**（嵌套 content → `E_BAD_ARG`）——这是 layout 几何「按 content scope 求解一次、state 只做可见性过滤」不变量的技术根据，也排掉 scene/state 混排的 build-run 校验地雷。
- 无 children 的 content quantum：normalize 回填单个全可见隐式 state（v1 行为原样）。
- **`default_path` 删除**：默认路径 = 树的 DFS 叶序（有序树，children 列表序即序）。「路径必须全覆盖」的整类校验被结构消灭；重排 = `move_quantum`。
- 新字段：
  - `hidden`（任意节点，默认 false）：子树移出默认路径与 mp4 压平，仅显式跳边可达——附录/备份页的标准表达，交互树因此能分支到「线外」。
  - `advance`（state 叶，`"wait"|"auto"`，默认 wait）：presentation 语义——wait = hold 至交互，auto = dwell_sec 后自动推进。自动播放/压平路径忽略此字段（一律按 dwell 推进）。
- `builds`/`dwell_sec` 等 v1 字段语义原样迁入 state；时间一律秒制。

### 2.1 扁平糖（永久保留的 authoring 形态）

`set_quanta`/`draft_quanta` 接受 v1 扁平 shape `{slides:[…], default_path?}`，normalize 确定性升格为树（default_path 若在则决定顺序，随后丢弃；builds → state children）。**lift 幂等**：`lift(lift(x)) == lift(x)`，已带前缀的 state id 不二次加前缀（金标准测试钉死回环 id 漂移）。`draft_quanta` 继续产扁平形，靠 lift 入库——模型的心智负担不因树而增加。

### 2.2 normalize / validate 语义

沿用「结构容忍、引用严格」双轨：

- **容忍（normalize 回填）**：缺 id 按路径回填（回填后仍需文档级唯一）、缺 state 回填隐式全可见、缺 dwell 回填默认、v1 扁平升格。
- **严格拒绝（`TimelinePatchError E_BAD_ARG`）**：
  1. links.target 悬空 quantum id；
  2. `hotspot:<blk>` 的 blk 不存在于挂载节点的 content scope（links **只允许挂在 content quantum 或 state 叶上**，挂 group → E_BAD_ARG；content 级 links 作用于其全部 state 子叶——即 v1「slide 级 links 对全部 builds 生效」原语义。group 级级联与跨 scope hotspot 属 Phase 2，需 scope 限定的 block 寻址方案）；
  3. `dwell_sec <= 0`；
  4. `visible_block_ids ⊄` scope blocks；
  5. content 之下嵌套 content；
  6. id 重复；
  7. 全树可见叶数为 0 而文档非空。
- **修 fixture 债**：现 tests/test_deck_ir.py 的 `hotspot:blk_cta`/`blk_url` 引用了不存在的块（自动回填成 blk_1/blk_2）——迁移时给 fixture 补真实块 id，否则规则 2 落地即打红 ~21 个复用测试。

## 3. 遍历内核（gemia/quanta/traverse.py，纯函数，新增）

JSON 进 JSON 出、无 I/O、无外部状态，镜像 layout.py 的确定性纪律。**自动播放、mp4 压平、未来播放器、get_quanta 文本视图四个消费者共用这一份语义，无第二实现**（顺带消灭 materialize.py 与 deck_frames.py 里重复的两份 _ordered_slides）。

1. `leaf_walk(doc) -> tuple[Leaf, ...]`：DFS 先序收集全部 state 叶（跳过 hidden 子树）。Leaf 携带 (state_id, scope_id, scope_index, state_index, dwell_sec, advance, ancestor_path)。scope_index/state_index 与现 slide_index/build_index 一一保序——pager URL 契约 `frame=<i>:<j>:<asset>` 零变更。
2. `leaf_walk_full(doc)`：同上但含 hidden——跳边目标解析与 hidden 内推进用。
3. `resolve_leaf(doc, state_id) -> RenderSpec`：内容域解析，两层视图——**render 视图**（layout/title/blocks/visible/dwell/mood_override，进 leaf_hash）与 **full 视图**（+notes/links/transition，不进 render hash——改讲稿不得误杀渲染缓存）。`RenderSpec.as_slide_view()` 合成 layout_slide 今日消费的 legacy dict `{id: scope_id, layout, title, blocks, builds:[…]}` —— **layout.py（1366 行，槽位/autofit/CJK 测量）与 raster.py 零 diff**。
4. `step(doc, cursor, event) -> StepResult{to, effect, transition}`：事件解释器，Event = advance | back | hotspot(block_id) | goto(quantum_id)。
   - **advance 语义唯一钉死**：结构性，= 可见叶序下一叶；显式 advance 出边仅允许挂在 state 叶（覆盖自身），**永不继承**；尾叶 → END。
   - hidden 绕行：跳边进入 hidden 子树后，advance 在该子树叶序内推进，出其尾叶回到该子树之后的下一可见叶（PowerPoint 隐藏页 detour 语义，确定性、可测试）。
   - back = 可见叶序上一叶（跨 scope 后退落在前 scope 末态叶——spec §5.2「回退显示已 build 完成末态」惯例免费满足）。
   - hotspot：从 cursor 叶就近向其 content scope 找匹配边；无匹配 → no-op。target 文法：`next` | `quantum:<id>`（group/content 取其子树首叶，state 直达）| `url:…`（cursor 不变 + effect 外抛）。跳边允许成环——一事件一步，无发散。
5. `run(doc, events) -> tuple[state_id, ...]`：fold(step)。「树 + 交互边 + 事件流 → 状态序列」的题面纯函数。
6. `flatten(doc) -> tuple[(state_id, dwell_sec, transition_in), ...]`：默认遍历 + dwell + 跨 scope 转场标记，供 assemble/mp4；**压平永不追 links**，非隐式出边在 assemble 结果 degradations 报 `{kind:"interaction_flattened", quantum_ids:[…]}`（与既有 fade_to_cut 降级报告同构）——压平不能等交互是媒介事实，显式承认而非静默丢弃。

**退化情形自证**：root 下 N 个无 group 包裹、各单 state、全 auto 的 content quanta ⇒ leaf_walk 线性链、压平输出与 v1 同 dwell 逐字节相同、播放器行为 = 放视频。`draft_quanta(from_shotlist=true)` 的产物就是这个退化形。反向：任何连续视频可无损表达为退化树。

## 4. 编辑树：patch ops 与工具面

全部经既有 `ctx.project.apply_ops` append-only patch-log（版本化/可审计/`timeline_undo` 免费）。patches.py `_OP_HANDLERS` 注册 5 op：

| op | 语义 |
|---|---|
| `set_quanta {quanta}` | 整树替换（normalize 含 lift + validate） |
| `patch_quantum {quantum_id, fields}` | 字段合并进任意节点；`id`/`children` 不可经 fields 改（结构变更必须走结构 op）；content 节点 fields.builds 接受 v1 风格列表升格为 state 子叶（既有 update_slide 测试语义借此存活） |
| `insert_quantum {parent_id, index?, quantum}` | 整子树插入（normalize 子树、回填 id）；parent 必须 group/root 或 content（插 state）；越界 E_BAD_ARG |
| `remove_quantum {quantum_id}` | 整子树摘除；root 拒绝；**悬空入边严格拒绝，且错误消息枚举全部入边位置（节点 id + link 下标）**，「同批先 patch 改边再 remove」菜谱写进 schema 描述 |
| `move_quantum {quantum_id, parent_id, index}` | 重排/重挂；成环（目标 parent 在被移子树内）与 kind 约束校验 |

**批内原子性**：apply_patches 本就整批 all-or-nothing。引用完整性校验**后置到批末**（单次全文档 validate），使 remove + 改边在同批内不依赖 op 顺序；若 patches.py 架构不支持后置，则退而求其次：逐 op 校验 + schema 明文「改边 op 必须先于 remove」+ 枚举式报错（实现时二选一，金标准测试钉住所选语义）。

**工具面（7 个，零新增）**：`draft_quanta` / `set_quanta` / `get_quanta` / `assemble_quanta` / `refine_quantum` 语义原样 rename；**`update_quantum` 升格为编辑树的统一入口**：

```
update_quantum {op: "patch"|"insert"|"remove"|"move", …对应参数}
update_quantum {ops: [ {op:…}, … ]}        // 原子批（如 remove+改边）
```

工具内映射到上述 patch op。plan_mode 全部 MUTATING（get_quanta = PLAN_ALLOWED），budget $0，接线完备性测试（DISPATCHER==TOOL_NAMES、plan 分类完备）保持绿。

## 5. 物化：树感知只发生在「IR→合成视图」一层

- **零 diff 层**：`gemia/quanta/layout.py`、`raster.py`、`project_export`、pager URL 契约。
- **改造层**：
  - `materialize.py::render_quanta_frames`：`_ordered_slides × _build_ids` 双循环 → `for leaf in traverse.leaf_walk(doc)`；RenderedQuantaFrame 字段 → (scope_index, state_index, scope_id, state_id)；manifest_entry 增 `leaf_hash`（render 视图哈希）。「渲染全部完成于内存、任何失败不留半注册文档」的既有保护**原样保留**——不引入任何编辑路径自动最小重渲引擎（Phase 2+）。
  - `quanta_frames.py`：删除重复的 _ordered_slides/_expected_builds，改 import 内核；`_image_asset_ids` 走全树递归。
  - `assemble_quanta`：数据源换 `flatten(doc)`，timeline 排布/幂等重建/非 Quanta clips 旁路原样；hidden 子树不入压平；degradations 增 interaction_flattened。
  - `refine_quantum`：节点寻址；重物化粒度 = 目标节点所属 content scope（今日 per-slide refine 的树版），其余帧/黑底复用原样。
  - `draft_quanta` persist 路径的 slide_count 等读数改走 leaf_walk/树感知计数（「draft 零 diff」承诺不成立，接受一处小 diff）。

## 6. 金标准测试（新增/迁移）

1. 持久化幸存：`set_quanta → store.load() → 全字段逐一幸存`（防 normalize 静默剥离，v1 金标准迁移）。
2. lift 幂等 + 回环稳定：flat→tree→get→再 set 不产生 id 漂移。
3. **undo 收敛（修正版）**：edit → assemble → undo → assemble，比较 **placed blocks 相等 + PNG 内容 sha256 相等 + timeline clip 结构（modulo asset id）**——AssetRegistry id 是 session 级单调计数器，逐字节比 manifest 必假红，故按内容比。
4. 遍历内核单测：叶序、hidden 绕行、step 全事件表、成环跳边、flatten dwell/转场、退化情形逐字节等价。
5. 校验金标准：§2.2 严格拒绝 7 条逐一 E_BAD_ARG；remove 悬空入边报错含枚举位置。
6. CJK 首帧字形（v1 金标准迁移）。
7. state_scope="quanta" patch 事件（迁移 test_v3_timeline_context）。
8. 中文 pitch 13 页 dogfood 真渲回归（Phase 1a 验收链原样跑通）。

## 7. 明确不做（v1 内核）

group 级 links 级联与跨 scope hotspot（需 scope 限定寻址）、timer/条件分支/状态机、内容寻址最小重渲引擎、lumenframe 运动层、pptx 扩展（既有导出起步件原样）、播放器前端、CLI 终端渲染。安全加固按用户决定延后至功能收尾。

## 8. 实施切片（每片提交时全量 suite 必须绿）

- R：rename 全量（§0.1 词汇映射表逐行执行，纯机械，语义零变更）。✅ 2573aaa（36 files，回归=基线）
- K1：遍历内核纯模块 + 单测（未接线）。✅ 09d2e9e（traverse.py + 23 金标准）
- K2：IR v2 切换 + 5 op + 工具 re-plumb + 物化改造 + 测试迁移 + 金标准（原子落地，避免「normalize 已树形而工具仍读扁平」的红灯窗口）。✅ ee63a95（18 files；全量 3 failed=冻结基线 / 2507 passed，+34 金标准）
- C：CLI format.js TOOL_LABELS 追加。✅ 六工具标签已加（未提交，随 CLI 工作树清算）；「timeline updated · N clip(s)」的 state_scope 分支在 App.js:1066（execution-intelligence-fix 独占路径）→ QUEUE 交接。
- V：全量回归对基线快照 + 真机链。✅ 2026-07-13 真机：draft(6 scopes/12 states) → update_quantum 单批 6 op（章节 group + 2 move + hidden 附录 + state dwell patch + hotspot 跃迁边，一个 undo 单元）→ assemble（hidden 正确出流、interaction_flattened 如实上报）→ project_export 20.0s h264 1080p30 → 6 帧抽验全过（渐进 build/CJK/split 图文/group 内 scope/full-bleed）。
