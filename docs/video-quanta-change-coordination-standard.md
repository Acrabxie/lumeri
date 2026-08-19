# Lumeri Video / Lumeri Quanta 协同开发标准 v1.0

- 状态：已由 Acrab 批准（2026-08-05）
- 日期：2026-08-05
- 适用范围：Lumeri Video 原生 App、Lumeri Quanta 原生 App、Lumeri 家族共享引擎，以及它们使用的 Skill、Tool、契约和共享资源
- 本文件解决：改动应该落在哪里、何时提取为共享能力、怎样避免双份实现漂移、怎样证明两条产品线都没有被破坏
- 不决定：Quanta 的界面设计、Bundle ID 的具体取值（由机器可读产品标识契约管控）、最低系统版本、签名、发布、商业策略和具体功能优先级

## 1. 标准批准时的真实基线

| 区域 | 当前权威路径 | 当前角色 |
| --- | --- | --- |
| 家族共享引擎 | `/Volumes/Extreme SSD/lumeri` | Product Core、Project/Session 语义、Agent loop、共享资产能力、Skill/Tool 注册、Quanta IR/遍历/物化、Web/CLI 契约的当前权威源 |
| Lumeri Video 原生 App | `/Users/xiehaibo/Lumeri Video` | 已有真实功能的 Apple 平台产品壳；Video 的原生交互、平台适配和产品专属代码权威源 |
| Lumeri Quanta Mac 开发界面 | `/Volumes/Extreme SSD/lumeri/static/v3/quanta`，入口 `http://127.0.0.1:7788/quanta` | 与 Video 共用现有 `:7788` Web-first 开发流程的 creator-visible 权威入口；Mac-first 阶段先在既有 Quanta 标签页迭代和验收 |
| Lumeri Quanta 原生 App | `/Users/xiehaibo/Lumeri Quanta` | 后续平台壳；当前不是 Quanta UI 开发入口，不因能够构建就进入 DMG、签名或发布阶段 |
| Quanta 既有内核 | `/Volumes/Extreme SSD/lumeri/gemia/quanta` 与相关 tools/tests | 已存在的离散视频语义权威源；原生 Quanta 不得另造第二套状态树、遍历、patch 或物化语义 |

跨 Android 与 Apple 平台的产品标识统一遵循 `com.acrab.lumeri.<product>`；当前正式型号为 `video` 与 `quanta`。机器可读权威源为 `config/product-identities.json`，各平台工程只消费和校验该约定。

`/Users/xiehaibo/Code/lumeri-quanta-demo` 只算演示资产，任何 worktree 或 build 产物只算阶段性实现；除非另有明确批准，它们都不是权威源。

现有 `docs/video-quanta-synergy.md` 继续说明两条产品线如何共享五层引擎；本文件是跨目录、跨产品的变更与复用制度。两者冲突时，先停止实施并由 Acrab 拍板，不静默选择其中一份。

## 2. 总原则

1. **一个语义只有一个权威源。** 两个 App 可以有两个适配层，但不能长期维护两份同义核心代码、Tool schema 或 Skill 正文。
2. **共享不等于从 Video 复制到 Quanta。** 可复用能力要先抽出稳定契约，再由两边消费；复制文件只能用于临时比较，不得成为正式同步机制。
3. **Quanta 复用 Lumeri，不复刻 Video。** 它继承 Lumeri 的身份、真实性、账号、Project/Session、资产与 Agent 机制；它保留自己的离散状态树、交互树、编辑树和观看方式。
4. **共享层不认识产品界面。** 共享代码可以知道能力、状态、证据和错误；不能 import Video 或 Quanta 的 View、路由、Bundle、签名或商店配置。
5. **域适配显式存在。** 如果两边需要同一底层能力但输入/输出语义不同，保留两个薄适配器，底层服务共享；不强行把两个产品压成一个万能接口。
6. **契约先于实现，证据后于实现。** 先定 schema、版本、错误和兼容策略，再写实现；实现后必须分别证明共享层、Video 和 Quanta。
7. **Project/Session 上下文不隐式互通。** 资产库可以共享，但素材必须通过 `asset_id` 或明确 handoff 进入另一个 Project/Session；对话、历史、当前选择和制作上下文不得自动污染另一边。
8. **激活与源码完成分开。** 源码、测试、App build、安装/启动、真实交互、线上服务与发布是不同证据层，任何一层通过都不能替代后一层。

## 3. 改动归属判定

每个改动开始前必须先归到以下一类，只允许一个首要归属。

### A. Lumeri 家族共享改动

满足以下条件才进入共享层：

- Video 和 Quanta 必须遵守同一语义；
- 规则不依赖某个产品的界面或领域名词；
- 失败会让两个产品对身份、Project 真相、执行结果或完成状态产生分歧；
- 可以通过稳定接口被不同产品消费。

典型范围：身份与完成契约、Project/Session 基础语义、资产身份与显式 handoff、账号/会话协议、事件和错误词汇、Provider profile 契约、通用文字/字体/媒体原语、Skill/Tool 注册与版本规则。

### B. 平台共享改动

Apple 平台上两个 App 都需要、但不属于产品语义的代码，进入共享 Swift Package；包名、路径和仓库形式在下一阶段由 Acrab 批准。在批准前不得用“双份复制”代替它。

典型范围：安全存储接口、HTTP/流式客户端、账号 token 生命周期、文件选择与资产 handoff、通用诊断、无产品文案的基础 UI primitive。

### C. Video 专属改动

只服务连续时间线、clip/layer 编辑、逐帧预览、Video 工作区交互或 Video 发布配置的改动，留在 Lumeri Video。

### D. Quanta 专属改动

只服务 quantum 状态树、交互跃迁、presentation/hold、按 quantum 寻址编辑、Quanta 工作区或 Quanta 发布配置的改动，留在 Lumeri Quanta 或共享引擎的 Quanta 域模块。

### E. 仍不确定

无法明确归类时，不先建 `common`、`utils` 或复制文件。写一张边界提案，列出两个候选归属、用户可见后果和推荐方案，等 Acrab 拍板。

## 4. 从单产品提升为共享能力的门槛

一个已在 Video 或 Quanta 中出现的实现，只有同时满足下列条件才能提升为共享件：

1. 第二个真实消费者已经存在，或它本来就是第 3 节 A 类的家族不变量；
2. 输入、输出、错误、取消、重试、成本和副作用都能写成产品无关契约；
3. 产品文案、页面状态、Bundle、签名、环境路径和领域状态已被隔离到适配层；
4. 有共享层单测/契约测试和两个消费者各自的集成验收；
5. 有版本号、变更说明、兼容策略和回滚方法；
6. 原产品内的旧实现被删除或变成薄适配器，不留下第二份可编辑副本。

若只是“代码长得像”，但行为、失败方式或验收标准不同，不提升为共享能力。

## 5. Tool 复用标准

Tool 是一次原子、可观察的运行时动作。共享 Tool 必须具备：

- 唯一稳定 id 和版本化 schema；
- 明确的输入、输出、错误码、取消语义、幂等性和副作用；
- 明确的作用域：`family`、`video` 或 `quanta`；
- 明确的安全等级：只读、可逆写、不可逆/付费/外部状态；
- 真实结果与接受验收分离，失败或 fallback 不得伪装成功；
- 不返回某个 App 的 View 状态或本机私有路径作为跨产品契约。

归属示例：

- `search_media`、资产注册、通用导出底层可以是家族共享能力；
- `draft_shotlist`、Video timeline 编辑是 Video 域 Tool；
- `draft_quanta`、`update_quantum`、`assemble_quanta` 是 Quanta 域 Tool；
- 两个域 Tool 可以调用同一个底层服务，但不能因为底层相同就合并掉领域语义。

同一个 Tool id 在 Video 与 Quanta 中不得代表不同动作。若语义不同，使用两个域适配器；若只是展示不同，由客户端把同一结果投影成不同界面。

## 6. Standard Skill 与 Creator Workflow 复用标准

本标准使用三层定义：

- **Tool**：执行一个原子动作；
- **Standard Skill**：定义一项专业能力如何被正确完成，包含输入、输出、步骤、质量门、失败策略和安全边界；
- **Creator Workflow**：把多个 Standard Skill 组合成可恢复的创作阶段和最终证据。

每个可复用 Skill 必须：

1. 声明 `family`、`video` 或 `quanta` 作用域；
2. 通过 capability/Tool id 引用能力，不直接写某个 App 的文件路径或页面选择器；
3. 把产品差异放进 adapter 或显式分支，禁止靠“如果失败就试另一个产品工具”碰运气；
4. 声明产物、质量门、人工确认点、付费/外部操作和失败退出；
5. 带 semver 版本；破坏性修改必须升 major，并提供迁移说明；
6. 有至少一个真实 fixture 和消费者验收，不以“Agent 成功调用了 Tool”作为完成证明。

Family Skill 只包含真正跨域的专业方法，例如素材检索与引用、字体解析、文字排版、通用生成资产验收和导出证据。Video/Quanta 的叙事结构、编辑动作和观看验收留在各自 Skill。

Skill 的 `manifest`、`definition`、`instructions` 只有一个可编辑源。若 App 需要离线内嵌，使用构建步骤生成只读快照，并在快照中记录源版本与内容哈希；禁止在 App bundle 内直接修第二份正文。

## 7. 版本与兼容制度

共享契约、Tool、Skill 和共享包都使用明确版本，不跟随隐含的 `latest`。

- patch：内部修复，不改变已有输入输出语义；
- minor：向后兼容地新增字段或能力；
- major：删除、改名、改变默认值、错误语义、副作用或状态模型。

消费者升级必须在同一张变更卡中记录：旧版本、目标版本、Video 状态、Quanta 状态、fixture 版本和回滚版本。共享 schema 新增字段时，旧消费者必须能显式忽略或显示“未知能力”，不能静默丢失关键状态。

## 8. 标准变更流程

每项可能影响两条产品线的工作按以下顺序进行：

1. **建一张变更卡**：写清 creator outcome、首要归属、两个产品是否受影响、是否触及共享契约/Tool/Skill、风险、验收和唯一 owner。
2. **盘点权威源**：检查真实路径、当前版本和未提交改动；不得从 build、demo、旧 worktree 或生成副本开始修改。
3. **先改契约**：若跨域，先更新共享 schema/fixture/版本和兼容测试；若不是跨域，明确标记域专属。
4. **实现共享层**：只改一个权威实现；保持 Video/Quanta 依赖方向指向共享层。
5. **接入消费者**：分别通过薄适配器接入 Video 与 Quanta，不把一个产品的 UI/状态对象传给另一个产品。
6. **分别验收**：跑共享测试、Video 集成、Quanta 集成，并完成两个产品各自可见的行为证据。
7. **记录版本与证据**：记录 source hash/version、消费者版本、测试结果、可见截图/产物和未完成层级。
8. **再决定激活**：服务重启、设备安装、签名、发布和远端动作继续使用各自 phase gate，不因共享改动自动获得授权。

并行工作只允许文件和外部状态完全不相交的任务；共享契约、注册表、Xcode project、签名和发布状态同一时刻只能有一个 owner。

## 9. 双端验收矩阵

| 改动类型 | 最低验收 |
| --- | --- |
| 共享纯逻辑 | 单元测试 + 固定 fixture + 版本/兼容测试 |
| 共享 Tool | schema/错误/副作用测试 + Video 调用链 + Quanta 调用链 |
| Family Skill | manifest/definition 校验 + Tool 引用校验 + 两域真实任务各一次 |
| 共享 Swift 代码 | Package tests + Video build/launch + Quanta build/launch |
| 共享 UI primitive/token | 两个 App 的可见截图 + 动态类型/深浅色/目标设备检查 |
| 账号或凭证契约 | 服务端 fail-closed 测试 + 两 App 安全存储/退出/切换验证；绝不在日志或 fixture 放真凭据 |
| Project/资产 handoff | 明确 asset id/导入事件 + 目标 Project 可见；同时证明对话与上下文未串入 |
| 发布相关 | 源码/测试、构建、签名、安装、真实启动、登录、上传、分发逐层单独报告 |

共享改动只有在受影响的每个消费者都通过对应验收后，才可标记为完成。某个消费者尚未接入时，状态必须写成“共享层完成，消费者待接入”，不能写“全系完成”。

## 10. 禁止模式

- 在 Video 与 Quanta 中各放一份 `Shared*.swift` 并人工同步；
- 从 App bundle、build 产物、demo 或旧 worktree 反向复制为源码；
- 为追求复用把 Quanta 的状态树压成 Video timeline，或把 Video timeline 冒充 Quanta 交互树；
- 让共享层 import 产品 View、产品路由、Bundle ID、签名或商店元数据；
- 同一 Tool/Skill id 在两个产品中含义不同；
- 未升级版本就改变默认值、错误码、字段语义或副作用；
- 只跑共享测试，不启动两个消费者；
- 资产库共享时自动合并 Project、Session、对话或当前选择；
- 把 source diff、绿色测试或 build success 直接报告为已上线、已安装或可真实使用。

## 11. 变更卡最小模板

```text
change_id:
creator_outcome:
owner:
authority: family | platform-shared | video | quanta
affected_consumers: [video, quanta]
contract_or_version_change:
tool_or_skill_change:
state_and_asset_boundary:
safety_or_external_actions:
shared_verification:
video_verification:
quanta_verification:
activation_gate:
rollback:
```

## 12. Quanta 下一阶段入口

本标准已经 Acrab 批准；Acrab 随后明确 Quanta 必须像 Video 一样先走 Mac 回环开发流程，不直接进入 DMG。Quanta 的首个实现阶段只做以下基线工作：

1. 固定 Quanta 的 creator-visible Mac 开发入口为既有 `http://127.0.0.1:7788/quanta` 标签页，源码权威源为 `static/v3/quanta/`；不再另起重复的 Quanta Web server；
2. 每次界面更改都执行 source/test → `:7788/quanta` served response/hash → fresh Browser interaction 三层证明，不把源码或单测冒充已在回环入口生效；
3. Quanta 页面消费现有 `gemia/quanta` 的版本化 IR、遍历与物化结果，不在前端重写第二套状态机；
4. 盘点 Video 中第一批平台共享候选，先提契约与测试，再决定共享包或 adapter 的名字和位置；
5. 原生 App 只保留为后续平台壳；DMG、签名、安装、发布和 iOS/Android 均是独立 phase gate，不在本阶段推进。
