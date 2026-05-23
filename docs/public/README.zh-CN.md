# Lumeri Public Workflow Kit

> Lumeri 是一个 AI 原生的媒体创作工作台。Gemia 是它的历史/内部工程名；对外表达统一使用 Lumeri。

这个 public workflow kit 的目标不是公开 Lumeri 的全部内部实现，而是把**可安全展示、可复用、可讨论**的那一层整理出来：

- Lumeri 是什么
- Lumeri 能做什么
- Lumeri 不能被误解成什么
- 哪些内容可以公开
- 哪些内容必须留在私有层
- 如何用假素材做安全 demo
- 用户可以复制哪些工作流 prompt
- 顶层 execution skill 如何约束每次执行

## Lumeri 是什么

Lumeri 是一个面向视频、图片、音频的 AI 媒体工作台。用户把素材导入媒体库和时间轴，用自然语言描述目标，Lumeri 通过 planner 把意图转成结构化执行计划，再调用本地媒体 primitives 做真实处理和导出。

当前重点能力包括：

- Google 登录后的本地账号隔离
- 账号级媒体库
- 视频、图片、音频导入
- 图片默认按 3 秒视频片段进入时间轴
- 媒体池、时间轴、会话历史和执行日志
- 自然语言规划与多步执行
- OpenRouter planner 接入
- LumeriLink to Blender 后端空间效果
- 可观察的模型输入 TXT（私有调试用途）

## Lumeri 不是什么

为了避免误解，公开表达里要说清楚：

- Lumeri 不是只靠一句话就能稳定生成任何成片的“魔法按钮”。
- Lumeri 不是 OpenRouter、Google、Blender、ffmpeg 或任何第三方服务的再分发包。
- Lumeri public kit 不是完整私有工作流、真实素材库、客户案例库或 benchmark 包。
- Lumeri Desktop 当前仍以本地 sidecar 和本地媒体处理为核心，不应被包装成已经完整上线的云端 SaaS。
- Gemia 只是历史/内部工程名，不应作为新的对外品牌主名。

## 这个 public kit 包含什么

- 公开版中文 README
- public/private split
- dependency boundary
- 假素材 public demo brief
- copyable prompts
- 顶层 `lumeri-execution` skill

## 这个 public kit 不包含什么

- API key、token、OAuth secret、refresh token
- 真实 Google 账号信息
- 真实模型输入 TXT
- 真实用户素材、真实客户案例、真实客户文案
- 私有 prompt 链和 planner 调优细节
- 私有 benchmark、质量评测集、第三方素材归档
- 内部自动化日志、桥接队列、agent 运行记录
- OpenRouter、Google、Blender 或其他第三方产品的安装包再分发

## 推荐公开叙述

可以这样说：

> Lumeri 是一个 AI 原生媒体工作台原型：它把媒体库、时间轴、自然语言规划、本地 primitives 和导出流程串成一条可检查的创作链路。这个 public workflow kit 公开的是安全的使用框架、边界说明和假素材 demo，不包含私有素材、密钥、真实客户数据或内部 prompt 链。

不建议这样说：

- “公开仓库已经包含完整 Lumeri 内部版。”
- “装上这个 kit 就能复刻全部演示效果。”
- “这里包含真实模型输入、真实案例或生产级素材库。”
- “这里是官方 OpenRouter/Google/Blender 集成包。”

## 默认工作流

1. 先确认当前项目、媒体库和时间轴状态。
2. 说明本轮改什么、不改什么。
3. 选择阶段：`Reference Cut`、`Timeline Build`、`Manual Lock` 或 `Render Review`。
4. 读取素材和时间线，不凭空假设真实素材内容。
5. 让 planner 只调用当前阶段需要的能力。
6. 执行后复核输出、媒体库、时间轴和合规边界。
7. 报告输出路径、验证方式和剩余依赖。

## 公开演示原则

公开 demo 必须使用假项目、假素材名和假文案。允许展示流程，不允许带出真实账号、真实客户、真实素材或私有模型输入。

推荐 demo 主题：

- “咖啡店新品短视频”
- “校园活动回顾”
- “虚构 App 宣传片”
- “假旅行 vlog”

每个 demo 都应明确标注：

- 素材是占位素材
- 文案是虚构文案
- 输出只是流程示例
- 不代表真实客户结果

## 文件入口

- [公开/私有拆分](./LUMERI_PUBLIC_PRIVATE_SPLIT.md)
- [依赖边界](./DEPENDENCY_BOUNDARY.md)
- [public demo brief](../../demo/01_public_demo_brief.md)
- [copyable prompts](../../demo/02_copyable_prompts.md)
- [Lumeri execution skill](../../gemia/ai/skills/lumeri-execution/SKILL.md)
