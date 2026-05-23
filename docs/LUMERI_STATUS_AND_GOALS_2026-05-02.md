# Lumeri 当前进展与目标

> 日期：2026-05-02  
> 正式产品名：Lumeri  
> 历史工程名：Gemia  
> 主仓库：`/Volumes/Extreme SSD/gemia`  
> Android 仓库：`/Users/xiehaibo/Code/gemia-android`  
> 说明：本文件只记录项目状态、架构、目标和非敏感路径；不记录任何 API key、token、secret 或账号私密内容。

---

## 1. 一句话定位

**Lumeri 是一个 AI 原生的媒体创作工作台：用户导入视频、图片、音频，用自然语言描述目标，系统把素材理解、剪辑规划、底层媒体处理和导出串成一条可执行工作流。**

它不是传统意义上的“套壳剪辑软件”，也不是单纯的聊天机器人。它的核心是：

1. 用户输入创作意图。
2. AI 规划器把意图转换成结构化执行计划。
3. `PlanEngine` 调用本地 primitives 对真实媒体文件执行处理。
4. 前端以时间轴、媒体池、会话历史和执行日志呈现整个过程。
5. 未来再把本地端侧能力、云端 AI/渲染、协作与发布串起来。

当前阶段的 Lumeri 已经从“AI 调用一堆技能”推进到“账号隔离 + 媒体库 + 时间轴 + 多步执行 + OpenRouter 规划 + Blender 空间效果 + Android 本地优先架构”的产品雏形。

---

## 2. 命名状态

从现在开始，产品对外名称可以统一改为 **Lumeri**。

保留 **Gemia** 的地方：

- 仓库路径和 Python 包名目前仍是 `gemia`。
- 部分日志、脚本、历史文档、LaunchAgent 仍带 `gemia`。
- 备份目录仍是 `GemiaBackups`。
- 旧文件 `GEMIA_PROJECT_RECORD.md` 是 2026-04-17 的历史记录，已经明显过时，不能再当当前架构说明使用。

建议的命名迁移策略：

1. 用户可见 UI、文档、状态说明、应用名统一用 **Lumeri**。
2. 工程内部短期保留 `gemia` 包名，避免一次性重命名破坏大量导入路径。
3. 新增文档和新 UI 文案优先使用 Lumeri。
4. 等桌面端和 Android 都进入更稳定版本后，再评估包名、服务名、备份名的系统性迁移。

---

## 3. 当前产品线

### 3.1 Lumeri Desktop

位置：`/Volumes/Extreme SSD/gemia`

当前桌面端是主要实验场和功能最完整的版本。它通过本地 7788 sidecar 提供后端能力，前端在 `http://127.0.0.1:7788/` 运行。

已具备：

- Google 登录账号体系。
- 本地账号隔离。
- 账号级媒体素材库。
- 视频、图片、音频导入。
- 图片按默认 3 秒视频片段处理。
- 时间轴 V1/A1 展示。
- 媒体池、会话历史、聊天输入框。
- 深色界面。
- Lumeri logo/品牌入口。
- 多步执行事件流。
- 时间点/时间段参考。
- Quick Actions 工具栏。
- OpenRouter 规划器。
- LumeriLink to Blender 后端桥。
- 每次真实 RUN 的完整模型输入 TXT 记录。

### 3.2 Lumeri Android

位置：`/Users/xiehaibo/Code/gemia-android`

当前版本：

- `applicationId = com.xiehaibo.lumeri`
- `versionName = 0.3.4`
- `versionCode = 7`

Android 线的方向已经从“手机遥控电脑”切换为 **端侧优先 APK**：

- 手机本地 Google 登录。
- 本地 Room 数据库。
- 本地媒体导入。
- 本地项目、媒体库、时间轴。
- 本地规则 planner。
- Media3 Transformer 导出 MP4。
- WorkManager 管理导入、缩略图、导出等后台任务。
- 顶部视频预览、快捷功能、视频轴、会话历史、底部输入框。
- 左滑历史项目 + 素材池。
- 右滑账号/设置占位。
- 参考附件和素材池分离。
- 最多 3 条视频轨，第 4 条开始 packed 到第 3 条。
- 未来预留 APK 内置 1B 小模型，用于错误翻译、轻量理解、离线辅助。

Android 线目前是可继续推进 APK MVP 的基础，但它的功能完整度还低于 Desktop。

### 3.3 Agent Control

位置：`/Users/xiehaibo/Code/agent-control-apk`

当前版本：

- `applicationId = com.xiehaibo.agentcontrol`
- `versionName = 0.3.26`
- `versionCode = 27`

它不是 Lumeri 主应用，但对整个开发体系很重要：它让手机可以控制 Codex、Claude Code、Antigravity、Gemini CLI 等代理。

已具备：

- 手机与桌面桥配对。
- Cloudflare relay。
- 加密消息。
- 会话历史。
- 多 agent 对话列表。
- 子 agent。
- 团队群聊。
- Codex 模型和 reasoning 控制。
- 真实 Codex/Claude/Antigravity/Gemini CLI 路由。

它是“用手机调度开发和自动化”的旁路工具，未来可以成为 Lumeri 开发、运维、任务监控的一部分。

---

## 4. Desktop 当前后端架构

### 4.1 服务入口

本地服务运行在：

`http://127.0.0.1:7788/`

关键文件：

- `server.py`
- `gemia_server_entry.py`
- `~/.gemia/server/bin/run-7788.sh`
- `~/Library/LaunchAgents/com.gemia.sidecar.plist`

当前 live probe 状态：

- `/config`：planner provider 是 `openrouter`。
- `/model-profile`：planner 解析为 `google/gemini-3.1-pro-preview`。
- `/blender-link/status`：检测到本机 Blender。

当前 Blender 状态：

- `available = true`
- `blender_path = /opt/homebrew/bin/blender`
- `version = Blender 5.0.1`

### 4.2 AI 规划器

核心文件：

- `gemia/ai/gemini_adapter.py`
- `gemia/ai/ai_client.py`
- `gemia/ai/prompt_slimming.py`
- `gemia/ai/cache.py`
- `gemia/ai/sub_agents.py`

历史上类名叫 `GeminiAdapter`，现在为了兼容保留类名，但它已经变成通用 planner adapter：

- 优先走 OpenRouter。
- 当前 provider 是 `openrouter`。
- 当前模型是 `google/gemini-3.1-pro-preview`。
- 官方 Gemini API key 已经从当前桌面配置移除。
- OpenRouter 通过 `OPENROUTER_API_KEY` / `openrouter_api_key` 读取本地私密配置。
- Authorization/API key 不进入共享记忆、不进入公开文档。

当前规划器行为：

1. 前端发起 RUN。
2. 后端组装项目状态、用户请求、时间参考、素材上下文。
3. prompt slimming 选择相关 primitive catalog，而不是把完整 registry 全塞进去。
4. OpenRouter 返回 JSON plan。
5. `PlanEngine` 执行 plan。
6. 执行事件回写任务流和 UI。

### 4.3 模型输入 TXT

已经启用：

`~/Desktop/Lumeri Gemini Inputs/`

每次真实 RUN 都会生成：

- 一个带时间戳的 `.txt` 文件。
- 一个 `latest.txt` 指向最近一次输入。

文件内容包括：

- provider
- model
- tag
- endpoint
- headers
- request_meta
- request_body
- system prompt
- user payload

安全边界：

- Authorization 会显示为 `<redacted>`。
- API key 不会写入 TXT。
- 这是为了让用户完整看见“Lumeri 到底把什么发给模型”，同时不泄露密钥。

当前还做了一个重要修正：

- 当输入 TXT 记录开启时，planner cache 会绕过。
- 这样每次 RUN 都会产生一个真实模型请求和可检查 TXT，而不是静默复用缓存。

---

## 5. 媒体库与账号体系

### 5.1 账号体系

已完成：

- Google 登录。
- 本地账号记录。
- 账号间 memory 隔离。
- 账号间会话历史隔离。
- 账号级媒体库路径隔离。

当前设计：

`~/.gemia/accounts/<account>/`

账号下保存：

- 媒体库 SQLite。
- 原始媒体文件。
- 缓存、缩略图、波形、预览。
- 项目/历史会话相关状态。

### 5.2 媒体库

核心文件：

- `gemia/media_library.py`
- `gemia/project_model.py`
- `gemia/video/timeline_assets.py`

已完成的设计：

- 媒体资产从 timeline clip 中独立出来。
- 视频、图片、音频先成为 asset。
- timeline clip 只引用 `asset_id`。
- 素材库软删除。
- 上传兼容旧 `/upload-media`。
- 图片导入到时间轴默认按 3 秒视频处理。

后端接口包括：

- `GET /media-library/list`
- `GET /media-library/<asset_id>`
- `POST /media-library/import`
- `POST /media-library/<asset_id>/add-to-project`
- `DELETE /media-library/<asset_id>`

资产记录包含：

- asset id
- 类型
- 文件名
- MIME
- 原始路径
- 指纹
- 时长
- 尺寸
- fps
- 音频信息
- 预览路径
- 状态
- 错误信息
- 创建/更新时间

---

## 6. 时间轴与交互

Desktop 当前已经从“上传后堆在页面里”演化为更像剪辑工作台的形态。

已完成：

- 左侧上部媒体素材池。
- 左侧下部会话历史。
- 中央视频预览。
- 快捷工具栏。
- V1 视频轨。
- A1 音频波形轨。
- 时间刻度。
- 播放头。
- 选中 clip。
- 删除 clip。
- 媒体池 hover 才显示添加/删除按钮。
- 时间轴 hover 才显示删除按钮。
- V1 左上播放/暂停按钮。
- 点击时间轴上部 ruler 可以定位播放头。
- 拖动播放头有吸附。
- 拖动空白区域可框选时间范围。
- 点击输入框可把时间范围提交为给 AI 的时间参考。
- 点击空白时间轴可清除选择。
- 时间点可显示为虚线框，点击可确认。
- 时间参考会作为 Gemini/OpenRouter 规划的重要约束。

当前仍可继续加强：

- 多轨 UI。
- clip 修剪手柄。
- 真实切割/裁剪交互。
- 音频轨更专业化。
- 缩略图和波形性能。
- 选区、吸附、播放头与预览同步的边界情况。

---

## 7. 多步执行机制

已从“直接返回一个结果”改成更接近 Codex/Claude Code 的执行体验。

已完成：

- think/read/plan/execute/report 的方向已经确立。
- 后端任务会记录多步事件。
- UI 展示运行步骤，而不是单个黑盒状态。
- primitive 执行会显示当前步骤、底层函数和输出。
- clarification / ask 不再当作红色 error。
- 当模型需要补充信息时，应该进入 `needs_input` / ask 交互。

已经修过的问题：

- `transition_dissolve() got multiple values for argument 'input_a'`
- 空 HTML layer 触发 `layers[n] needs source or inline html`
- 模型 ask 被 UI 当成 error
- 执行日志信息过少

目标体验：

像 Codex 一样，Lumeri 每次执行应该能让用户看见：

1. 它读了哪些素材。
2. 它理解了什么。
3. 它计划做哪几步。
4. 它正在执行哪一步。
5. 哪一步产出了什么。
6. 最后汇报结果、输出文件和可继续操作项。

---

## 8. Primitive 与媒体能力

当前底层已经很厚，包含大量 picture / audio / video primitives。

已出现的重要模块包括：

- `gemia/picture/*`
- `gemia/audio/*`
- `gemia/video/frames.py`
- `gemia/video/timeline.py`
- `gemia/video/layers.py`
- `gemia/video/layer_flow.py`
- `gemia/video/html_graphics.py`
- `gemia/video/lottie_renderer.py`
- `gemia/video/motion.py`
- `gemia/video/motion_graphics.py`
- `gemia/video/motion_deblur.py`
- `gemia/video/ultrasharpen.py`
- `gemia/video/cinefocus.py`
- `gemia/video/face_age.py`
- `gemia/video/face_reshaper.py`
- `gemia/video/blemish.py`
- `gemia/video/slate_id.py`
- `gemia/video/delivery_scene.py`
- `gemia/video/dialogue_matcher.py`
- `gemia/video/music_editor.py`
- `gemia/video/speech_generator.py`
- `gemia/video/animated_subtitles.py`
- `gemia/video/blender_link.py`

当前能力方向：

- 基础剪辑
- 调色
- 变速
- 字幕
- 转场
- 图像风格化
- 视频逐帧处理
- 图层合成
- HTML/Lottie 图形
- Motion / MG 动画
- 人像处理
- 锐化/去模糊
- Slate ID / 元数据
- 音乐与语音相关能力
- Blender 空间效果

需要注意：

- 不是所有 primitive 都已经有完美 UI。
- 不是所有 primitive 都经过真实媒体高强度验收。
- 当前大量能力是“底层可执行”，产品层还需要筛选、组织和包装。

---

## 9. LumeriLink to Blender

核心文件：

- `gemia/video/blender_link.py`

后端接口：

- `GET /blender-link/status`
- `GET /blender-link/capabilities`
- `POST /blender-link/execute`
- `POST /blender-spatial`

当前状态：

- 本机检测到 Blender 5.0.1。
- 路径：`/opt/homebrew/bin/blender`
- 支持后端空间场景生成。
- 前端可以保持不变，由后端 planner/primitive 走 Blender。

当前能力：

- `spatial_scene`
- `parallax_orbit`
- `depth_grid`
- `neon_hologram`

实现策略：

- 有 Blender 时，生成本地 Blender scene，渲染 PNG sequence，再用 ffmpeg 合成视频。
- 可保留原视频音频。
- 无 Blender 时，OpenCV 生成确定性空间预览 fallback。
- 输出 `.blenderlink.json` 记录 renderer、参数、路径等元数据。

目标：

把 LumeriLink 发展成 Lumeri 的空间视频/3D 效果桥：

- 2D 视频空间化。
- 产品展示空间场景。
- 标题/字幕/动态图形进入 3D 空间。
- 后续可能接 USD、glTF、Geometry Nodes、相机路径、真实 3D asset。

---

## 10. Prompt Slimming 与模型输入预算

近期已经做过重要优化：

- 不再把完整 registry 全塞进 system prompt。
- 根据请求类别选择相关 primitive catalog。
- 去掉项目状态里的缩略图、波形、预览等重 payload。
- clarifications 合并进有效请求。
- 视频 summary context 有才注入。
- 规则说明压缩成路由表。
- 加入 token budget regression 测试。

目标：

- 常见规划请求控制在约 8K 输入 token 内。
- 复杂项目仍能带足够上下文。
- 模型看到的是“必要上下文”，不是一整座仓库。
- 让 Gemini/OpenRouter 输出更稳定、更便宜、更快。

---

## 11. 当前验证状态

近期验证过：

- Desktop 7788 sidecar 可启动。
- `/config` 显示 OpenRouter planner healthy。
- `/model-profile` planner 为 `google/gemini-3.1-pro-preview`。
- `/blender-link/status` 显示 Blender 可用。
- 模型输入 TXT 实际生成到桌面。
- OpenRouter live probe 返回成功。
- 聚焦测试通过：
  - prompt / planner / input txt 相关测试。
  - Blender link 相关测试。
  - engine video routing 相关测试。
  - media library / project model / account 相关测试在先前迭代通过。
- Android 0.3.4 基线通过：
  - `./gradlew testDebugUnitTest assembleDebug`

最近重要备份：

- Desktop input txt / sidecar env / cache bypass：
  `/Volumes/Extreme SSD/GemiaBackups/versions/20260502-093610-e08aef1-dirty-desktop-input-txt-sidecar-env-cache-bypass`
- OpenRouter planner switch：
  `/Volumes/Extreme SSD/GemiaBackups/versions/20260501-155649-e08aef1-dirty-openrouter-planner-switch`
- LumeriLink Blender spatial：
  `/Volumes/Extreme SSD/GemiaBackups/versions/20260430-070933-e08aef1-dirty-lumerilink-blender-spatial`
- Lumeri Android 0.3.4：
  `/Volumes/Extreme SSD/GemiaBackups/android-versions/20260429-173449-lumeri-android-0.3.4`

---

## 12. 当前风险与技术债

### 12.1 命名不统一

用户可见名称要改为 Lumeri，但代码仍大量使用 Gemia。

风险：

- 文档混乱。
- UI 文案不统一。
- 未来上架、品牌、包名、服务名之间需要清理。

建议：

- 用户可见层立即统一 Lumeri。
- 代码包名暂缓。
- 新文档统一 Lumeri。
- 旧工程名用“历史名/内部名”解释。

### 12.2 仓库状态较脏

当前工作树有大量未跟踪/修改文件，这是连续快速迭代的结果。

风险：

- 不易判断哪些是用户改动，哪些是 agent 改动。
- 备份依赖当前 dirty snapshot。
- Git 提交前需要分批梳理。

建议：

- 继续依赖外置盘版本备份作为安全网。
- 重要功能稳定后，分主题整理提交。
- 不做大规模 reset 或 checkout。

### 12.3 Desktop 与 Android 产品线分叉

Desktop 功能更多，Android 架构更适合最终用户路径。

风险：

- 两条线目标不一致。
- 后端能力无法直接带到 Android。
- 用户容易期待 Android 立刻拥有 Desktop 的全部功能。

建议：

- Desktop 做能力探索和本地工作站。
- Android 做“素材到成片”的最小闭环。
- 云端 API 第二阶段再对齐两端。

### 12.4 AI 模型输入仍需持续可观察

虽然已经有 TXT，但还需要更结构化。

建议：

- UI 增加“查看本次模型输入”按钮。
- 每个 task 关联 input txt 路径。
- 将模型输出 JSON、执行 plan、最终任务日志串联展示。

### 12.5 Primitive 很多，但产品组织还不够

底层能力很厚，但需要产品化。

建议：

- 不要让用户看到“几百个技能”。
- 让 AI planner 调用 primitives。
- UI 只暴露少数高频动作：导入、生成、导出、删除、时间参考、空间效果。

---

## 13. 近期目标

### 13.1 第一优先级：Lumeri Desktop 体验闭环

目标：让桌面端成为“能连续剪、能观察 AI、能导出”的稳定工作台。

具体任务：

1. UI 全面改名为 Lumeri。
2. 每个 task 显示本次模型输入 TXT 的入口。
3. 执行日志继续接近 Codex 风格。
4. 时间参考和素材引用在 prompt payload 中更清晰。
5. 时间轴 clip 选择、删除、播放、范围选择继续稳定化。
6. Blender 空间效果作为后端能力继续完善。
7. 输出文件和预览结果更明确地进入媒体库/项目。

### 13.2 第二优先级：Android MVP 可用闭环

目标：一个功能少但真能独立跑的 APK。

最小验收路径：

1. 打开 Lumeri Android。
2. Google 登录。
3. 从 Photo Picker 导入视频/图片/音频。
4. 输入一句话。
5. 本地 planner 生成剪辑计划。
6. Media3 Transformer 导出 MP4。
7. Android share sheet 分享。

继续要做：

- 强化真机素材导入。
- 清理 Google Sign-In 兼容问题。
- 提升错误翻译。
- 接入本地 1B 小模型接口。
- 做更稳的导出失败诊断。

### 13.3 第三优先级：模型与云端规划

当前 Desktop 已走 OpenRouter Gemini3.1。

下一步：

- 明确 OpenRouter 模型 fallback。
- 记录每次模型输入/输出/执行 plan。
- 为 Android 第二阶段设计 Lumeri Cloud API。
- 云端负责：
  - token exchange
  - 项目同步
  - 素材索引同步
  - 云端 planner
  - 云渲染

原则：

- APK 不内置 API secret。
- Desktop 可使用本地私有 key。
- 用户路径上不要要求连接电脑。

### 13.4 第四优先级：底层媒体引擎升级

建议方向：

- OpenTimelineIO：项目/时间轴互操作。
- PyAV：替代部分 cv2/ffmpeg subprocess 媒体读写。
- Media proxy：大素材更流畅。
- Waveform/thumbnail 缓存标准化。
- 渲染 backend 抽象：software / Blender / future GPU。

### 13.5 第五优先级：品牌和分发准备

Lumeri 如果准备更正式走向发布，需要：

- 产品名统一。
- logo 和视觉规范统一。
- Google Play 开发者账号资料准备。
- Android 包名确认。
- 隐私政策。
- 数据安全说明。
- 本地存储与账号隔离说明。
- 不内置云端 secret 的架构说明。

---

## 14. 中期产品目标

Lumeri 的中期形态应该是：

1. 用户不用学剪辑软件。
2. 用户只需要导入素材和描述目标。
3. Lumeri 能读素材、给计划、执行、展示过程。
4. 用户可以在时间轴上精确指出“这里”“这一段”。
5. AI 能把这些时间参考当作强约束。
6. 用户能随时看到模型输入和执行计划。
7. 失败时 Lumeri 用人话解释原因并给下一步。
8. 本地能完成基础成片。
9. 云端能提供更强 planner、生成模型和重渲染。
10. Blender/空间效果让它区别于普通手机剪辑应用。

---

## 15. 下一步建议

如果按“最短路径让 Lumeri 变得更像产品”排序，建议接下来做：

1. **全 UI 改名 Lumeri**  
   先改用户可见文案、窗口标题、logo 文案，不动 Python 包名。

2. **任务详情增加模型输入入口**  
   既然 TXT 已经生成，UI 应该能点开 latest 或本 task 对应文件。

3. **输出结果回流媒体库**  
   每次执行生成的视频，应自动成为媒体库资产，并可添加到时间轴。

4. **Android 真机最小闭环**  
   不追求 Desktop 全功能，只把导入、prompt、本地导出、分享跑通。

5. **错误翻译层**  
   先做规则版，再给未来 1B 小模型留接口。

6. **LumeriLink Blender 模板扩展**  
   把空间效果做成几个稳定模板，而不是让模型随意造复杂 Blender plan。

---

## 16. 当前结论

Lumeri 现在已经不是一个“想法”或“demo”。它已经具备：

- 账号体系。
- 媒体资产库。
- 时间轴。
- AI planner。
- 本地执行引擎。
- 多步执行日志。
- 模型输入透明化。
- Blender 空间桥。
- Android 本地优先架构。
- 外置盘版本备份制度。

下一阶段的关键不是继续证明“能不能做”，而是把这些能力收束成稳定、清晰、可反复使用的产品路径。

当前最重要的产品判断：

**Desktop 继续做能力工作站；Android 做真正独立用户产品；Lumeri 作为统一品牌向前走。**

