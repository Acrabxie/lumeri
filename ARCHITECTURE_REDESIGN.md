# Lumerai 架构重设计文档

> 版本：v0.2 | 日期：2026-05-16 | 状态：待审阅
> 代码库路径：`/Volumes/Extreme SSD/gemia`（symlink 自 `/Users/xiehaibo/Code/gemia`）

---

## 一、opencode 核心架构提炼

### 1.1 整体结构

opencode 是一个**提供者无关的模块化 AI 编码代理**，client/server 分离。

```
┌─────────────────────────────────────────────────────────┐
│                     客户端层                             │
│  TUI (ink/react)  │  Web UI (SolidJS)  │  SDK 客户端    │
└────────────────────────┬────────────────────────────────┘
                         │ ACP 协议 (HTTP + SSE)
┌────────────────────────▼────────────────────────────────┐
│                  ACP Agent 层 (agent.ts, 1968 LOC)       │
│  协议处理 / 会话管理 / 工具生命周期 / 权限事件           │
└──────────┬─────────────────────────┬────────────────────┘
           │                         │
┌──────────▼──────────┐   ┌──────────▼──────────────────┐
│    Agent 服务层      │   │      Session 服务层           │
│  build / plan /     │   │  prompt() → LLM call →      │
│  explore / scout    │   │  tool loop → persist        │
└──────────┬──────────┘   └──────────┬──────────────────┘
           │                         │
┌──────────▼─────────────────────────▼──────────────────┐
│                   工具执行层                            │
│  read / write / bash / grep / webfetch / LSP / ...    │
│  每个工具：schema 验证 → handler → metadata 事件流     │
└──────────┬─────────────────────────┬──────────────────┘
           │                         │
┌──────────▼──────────┐   ┌──────────▼──────────────────┐
│    LLM 提供者抽象    │   │       状态持久层              │
│  40+ providers      │   │  SQLite + Snapshot/Patch    │
│  Anthropic/OpenAI/  │   │  Session 三层隔离：           │
│  Google/Local...    │   │  Project → Session → Parts  │
└─────────────────────┘   └─────────────────────────────┘
```

### 1.2 Agent 循环（最核心的机制）

```
用户输入
    ↓
构建上下文（系统 prompt + 对话历史 + 文件引用）
    ↓
调用 LLM（流式输出）
    ↓
┌─────────────────────────────────────────────────┐
│  LLM 决定调用工具？                              │
│  是 → 验证参数 schema → 执行（可并行）→ 结果回传 │
│     → 继续循环                                  │
│  否 → end_turn，输出返回给用户                  │
└─────────────────────────────────────────────────┘
    ↓
消息持久化 (SQLite)
    ↓
SSE 推流（进度、token 消耗）到客户端
```

**核心洞察**：AI 不从"菜单"里选函数，而是**写代码调用工具**，然后看执行结果，再迭代。这是 opencode 与 Gemia 当前架构的本质区别。

### 1.3 值得直接借鉴的设计模式

| 模式 | opencode 做法 | Lumerai 迁移价值 |
|------|--------------|----------------|
| **工具即纯函数** | schema + handler，context 注入 | primitive 可完全套用此模型 |
| **Snapshot + Patch** | 操作前快照，支持任意回滚 | 媒体编辑必须有撤销 |
| **Permission 组合** | allow/deny/ask 可嵌套合并 | 控制 AI 能否覆盖源文件 |
| **元数据事件流** | 工具执行过程流式推送进度 | 渲染进度、编码百分比 |
| **Session 三层隔离** | Project → Session → Messages | 时间轴历史版本管理 |
| **Provider 抽象** | LLM 与逻辑完全解耦 | 渲染引擎同样可抽象 |

---

## 二、现有 Gemia 架构精确盘点

### 2.1 代码规模

```
gemia/ （主包，Python）
├── server.py              4,498 LOC  — 单体 HTTP 服务器（Flask 风格）
├── agent_workflow.py      4,573 LOC  — 意图解析、范围检测、风险门控
├── ai/
│   ├── ai_client.py         463 LOC  — 核心 planner 接口
│   ├── gemini_adapter.py    894 LOC  — Gemini API 封装
│   ├── generative_client.py 505 LOC  — 图像/视频生成 API
│   └── prompt_slimming.py   443 LOC  — Prompt 优化
├── video/
│   ├── effects.py         8,070 LOC  — 200+ 色彩/滤镜/视觉效果
│   ├── layers.py            973 LOC  — 图层合成
│   ├── compositing_graph.py 951 LOC  — 图计算渲染
│   ├── compositing.py       629 LOC  — 合成操作
│   ├── timeline.py          811 LOC  — 时间轴核心
│   └── [79 个其他文件]
├── audio/                  [35 个文件]
├── picture/                [8 个文件，226 个函数]
└── orchestrator.py          547 LOC  — 执行协调层
总计约 25,000 LOC
```

### 2.2 当前架构流程

```
用户输入（自然语言）
    ↓
agent_workflow.py
  → 检测范围（当前时间轴 / 全部素材 / 媒体库）
  → 检测负向约束（"不要用库"、"保留主体"）
  → 风险门控（是否允许 Veo / Blender / 代码生成）
    ↓
ai_client.py → 发送 catalog（808 个函数的文档字符串）给 LLM
    ↓
LLM（Gemini 3.1 Pro via OpenRouter）
  → 返回 Plan JSON（步骤 + 参数 + 依赖关系）
  → 或返回 {"ask": true, "questions": [...]} 请求澄清
    ↓
orchestrator.py → 按顺序执行 Plan 中的每个步骤
    ↓
gemia.video.* / gemia.audio.* / gemia.picture.* 具体执行
    ↓
输出文件 + 时间轴更新 + 任务记录
```

### 2.3 当前架构的真正问题

**不是"有 808 个 primitive"，而是"AI 只能选菜，不能做菜"。**

具体表现：
1. **Plan 是一次性的**：LLM 生成 JSON → 一次性执行，中间不能根据结果调整
2. **错误不可修复**：某步骤失败 → 整个 plan 失败，AI 不知道结果
3. **组合受限**：AI 只能选预定义的 primitive，无法组合出标准库没有的效果
4. **Catalog 过长**：808 个函数的文档字符串塞进 prompt，超长且噪音大
5. **无迭代能力**：AI 不能"看一眼效果、再微调"——这正是人类创作者最核心的工作方式

---

## 三、Lumerai 目标架构设计

### 3.1 定位

> **Lumerai = 为媒体创作优化的 AI 编码执行环境**
>
> Claude Code 之于代码库 = Lumerai 之于媒体项目

新模式：AI **写脚本**调用 Lumerai 标准库 → 沙箱执行 → 看到结果 → 继续迭代

```python
# AI 写的脚本示例（不是 AI "选择"的 plan）
import lumerai as lm

clip = lm.clip_load("interview.mp4")
scene = lm.detect_scenes(clip)
trimmed = lm.clip_trim(clip, start=scene[2].start, end=scene[2].end)
graded = lm.color_grade(trimmed, preset="cinematic_warm", strength=0.8)
lm.timeline_insert(graded, at=lm.timeline_current_end())
```

执行后 AI 看到：预览帧 + 时间轴状态 + stdout/stderr，然后决定下一步。

### 3.2 目标架构图

```
┌─────────────────────────────────────────────────────────┐
│                    极简 UI（三件套）                      │
│   ┌───────────────┐ ┌──────────────┐ ┌───────────────┐  │
│   │    时间轴      │ │    输入框    │ │     预览       │  │
│   │   (AI 画布)   │ │   (对话流)   │ │  (实时渲染)   │  │
│   └───────────────┘ └──────────────┘ └───────────────┘  │
└──────────────────────────┬──────────────────────────────┘
                           │ WebSocket / SSE
┌──────────────────────────▼──────────────────────────────┐
│                    Lumerai Core                          │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │          Agent 循环（opencode 模式）               │   │
│  │  用户意图 → LLM → 生成脚本 → 沙箱执行 → 迭代     │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │          媒体沙箱执行环境（Lumerai 独有）          │   │
│  │  - 受控 Python 进程（import 白名单）               │   │
│  │  - Resource limits（CPU/内存/时间）                │   │
│  │  - 捕获：timeline_patch + 预览帧 + stdout         │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │         Lumerai 标准库（现有 primitive 重新组织）  │   │
│  │  gemia.video / gemia.audio / gemia.picture       │   │
│  │  → 以 "lumerai" namespace 统一暴露               │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌────────────────────┐  ┌───────────────────────────┐  │
│  │    Session 状态层   │  │     媒体文件管理           │  │
│  │  SQLite（迁移自    │  │  代理文件、大文件引用      │  │
│  │  现有 JSON 任务记录）│  │  现有 media_library.py   │  │
│  └────────────────────┘  └───────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 3.3 标准库：808 个 primitive 的重新定位

**808 个函数不是浪费，是地基。** 重新定位方式：

#### 对外暴露给 AI 的 API（精简为约 80 个）

AI 脚本中可以调用的函数，遵循"一个函数，丰富参数"原则：

```python
# 不是暴露 50 个颜色函数，而是一个 color_grade 接受 preset 或 adjustments
lm.color_grade(clip, preset="cinematic_warm")
lm.color_grade(clip, adjustments={"exposure": 0.3, "temperature": -200, "saturation": 1.2})

# 不是 add_text / animated_text / render_lower_third，而是
lm.text_overlay(clip, text="Hello", style="lower_third", animation="slide_in")
```

#### 内部实现层（保留所有 808 个）

`effects.py` 里的 200+ 变体函数继续存在，作为对外 API 的实现细节，AI 不直接调用。

#### 标准库分层设计

```
lumerai (对外 API，~80 个函数)
    ├── timeline.*    (12 个)   — cut, concat, speed, insert, remove, fork...
    ├── clip.*        (15 个)   — load, trim, color, filter, composite...
    ├── audio.*       (15 个)   — mix, normalize, fade, eq, separate...
    ├── text.*        (8 个)    — overlay, subtitle, animate...
    ├── generate.*    (8 个)    — video, image, broll, extend...
    ├── analyze.*     (10 个)   — scenes, beats, faces, motion...
    └── export.*      (8 个)    — preset, batch, proxy...

gemia.video/audio/picture (内部实现，808 个函数，保持不变)
```

### 3.4 时间轴设计：AI 工作画布

时间轴不是传统 NLE 的固定轨道结构，而是**AI 产物的可扩展容器**。

现有时间轴数据模型已经很好，需要扩展的是**溯源字段**：

```python
# 在现有 project_model.py 的 clip 结构上扩展
{
  "id": "clip_abc",
  "asset_id": "asset_xyz",
  "track_id": "track_1",
  "source_in": 10.5,
  "source_out": 45.2,
  "effects": [...],

  # 新增：AI 产物溯源
  "provenance": {
    "session_id": "sess_001",
    "message_id": "msg_003",
    "script": "trimmed = lm.clip_trim(clip, start=10.5, end=45.2)",
    "reason": "截取采访核心段落，去除开头的调整时间"
  }
}
```

**时间轴 ↔ Session 双向映射**：

```
AI 执行脚本
    → 产生 TimelinePatch（JSON diff）
    → 应用到 Timeline
    → 版本号 +1

用户手动调整时间轴
    → 产生 SessionContext 更新
    → 下一次 AI 响应前注入当前时间轴状态

撤销
    → TimelinePatch 逆向应用
    → Session 历史标记为 reverted

时间轴 fork（实验不同方向）
    → 创建新 Session，继承父 Session 的时间轴状态
```

---

## 四、UI 设计：极简三件套

### 4.1 布局

**只有三个组件，没有其他面板。**

```
┌─────────────────────────────────────────────────────────────┐
│                       预  览                                 │
│                                                              │
│                  [ 视频帧渲染区域 ]                          │
│                                                              │
│  ◀  ▶  ⏸   ────────●──────────────────   00:23 / 01:45     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                      时 间 轴                                │
│                                                              │
│  [AI 生成的 layers，可滚动，可缩放]                         │
│  采访剪辑    ████████████████░░░░░░░░░░░░                   │
│  暖色调      ████████████████░░░░░░░░░░░░                   │
│  背景音乐           ░░░░░░░░░░░████████████░                │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                  对 话 流 + 输 入 框                         │
│                                                              │
│  你  把第 10 秒到 45 秒的采访截出来，加暖色调               │
│                                                              │
│  ◆  好的，我来看看这段素材的场景分布...                     │
│     ├─ detect_scenes("interview.mp4")  → 发现 5 个场景      │
│     ├─ 选择场景 2（10.5s–45.2s）                            │
│     ├─ clip_trim(start=10.5, end=45.2)                      │
│     └─ color_grade(preset="cinematic_warm", strength=0.8)  │
│                                                              │
│  ✓  完成。预览已更新。暖色调强度 0.8，你觉得合适吗？        │
│     如果想更强烈，可以试试 1.0~1.2。                        │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 输入你的想法...                                 发送 │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 对话流展开规则（类 Claude Code）

| 阶段 | 展示内容 | 用户体验目标 |
|------|---------|------------|
| AI 意图确认 | 一句话说明理解了什么 | 用户知道 AI 没误解 |
| 工具调用（可折叠） | 函数名 + 关键参数 + 结果摘要 | 好奇时可展开看细节 |
| 长任务进度 | 百分比 + 当前步骤名 | 不焦虑 |
| 完成说明 | 做了什么、为什么这样做 | 信任感 |
| 主动建议 | 下一步可以做什么 | 继续对话的钩子 |

**不要**：技术日志面板、参数输入表单、图标工具栏。

### 4.3 时间轴交互原则

- Layer 只显示：**标签 + 颜色 + 时间范围**，无参数控件
- 悬停 layer → 气泡显示 AI 的 `provenance.reason`
- 点击 layer → 对话框自动填入 `@layer_name` 引用
- 所有编辑操作通过对话框，时间轴只是"看"
- 时间轴的 layer type 是开放字符串——AI 可以创造 `"beat_marker"`、`"scene_boundary"` 等新类型

---

## 五、代码资产盘点

### 5.1 直接复用（✅ Keep，不改）

| 资产 | 路径 | 复用原因 |
|------|------|---------|
| **gemia.video.timeline** | `video/timeline.py` | 所有基础时间轴操作，直接作为标准库底层 |
| **gemia.video.compositing_graph** | `video/compositing_graph.py` | 图层渲染系统，架构良好 |
| **gemia.video.layers** | `video/layers.py` | 图层合成，继续作为渲染引擎 |
| **media_library.py** | `media_library.py` | 媒体文件管理，代理文件系统，继续用 |
| **project_model.py** | `project_model.py` | 时间轴数据模型（扩展 provenance 字段） |
| **gemia.audio.*** | `audio/` 全部 35 个文件 | 音频标准库，整体保留 |
| **gemia.picture.*** | `picture/` 全部 8 个文件 | 图像标准库，整体保留 |
| **gemia.video.effects** | `video/effects.py` | 200+ 效果作为内部实现层 |
| **Tauri 壳** | `tauri-app/` | 桌面容器继续用，只替换 UI 内容 |
| **代理文件/预览系统** | `video/preview.py` | 低分辨率预览逻辑继续用 |

### 5.2 需要改造（⚠️ Refactor）

| 资产 | 当前问题 | 改造方向 |
|------|---------|---------|
| **orchestrator.py** | Plan-selection 模式：执行 JSON plan | 改为：接收脚本 → 沙箱执行 → 捕获结果 |
| **ai_client.py** | 发送 808 个函数文档给 LLM | 改为：注入~80 个 API 文档 + 系统提示引导写脚本 |
| **agent_workflow.py** | 意图解析后直接选 primitive | 改为：意图解析 → 注入上下文约束 → AI 写脚本 |
| **server.py** | 4498 LOC 单体文件 | 拆分为：sessions.py / media.py / agent.py / timeline.py |
| **gemia.video.effects** | 暴露所有变体给 AI | 内部函数，不再出现在 AI 的 prompt catalog 里 |

**agent_workflow.py 的改造是最值得的**：现有的范围检测（当前时间轴/全部素材/媒体库）、负向约束检测（"不要太饱和"、"保留主体"）、风险门控（veo/blender/代码生成）——这些逻辑非常有价值，但要从"过滤可选 primitive"改为"注入系统提示约束"。

### 5.3 可以丢弃（❌ Discard）

| 资产 | 原因 |
|------|------|
| **prompt_slimming.py** | 原来用于压缩 808 个函数的 catalog prompt，新架构下 catalog 已精简到 ~80 |
| **skill_router.py / skill_telemetry.py** | "选技能"模型的产物，新架构不需要 |
| **plans/ 目录下的 JSON 格式** | 被 Python 脚本替代，历史文件可存档 |
| **registry.py（的 catalog_for_prompt 部分）** | catalog 生成逻辑重写，但 FQN 解析部分保留 |

---

## 六、风险评估

### 6.1 沙箱安全

**风险等级：高**

AI 生成的 Python 脚本在用户机器上执行。

**缓解策略：**

```python
# 安全沙箱实现思路
ALLOWED_IMPORTS = {"lumerai", "json", "math", "re", "datetime"}

def validate_script(script: str) -> bool:
    tree = ast.parse(script)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    raise SandboxViolation(f"Blocked import: {alias.name}")
        if isinstance(node, ast.Call):
            # 拦截 __import__, eval, exec, open 等危险调用
            ...
```

- **AST 级别 import 检查**：解析脚本 AST，拦截非白名单 import
- **Resource limits**：`resource.setrlimit` 限制 CPU 时间（30s）、内存（2GB）、进程数（1）
- **文件系统边界**：`lumerai` API 内部强制所有文件操作限定在项目目录
- **干运行（dry-run）预览**：先解析脚本意图（会读/写哪些文件），显示给用户确认

opencode 的 Permission 系统（allow/deny/ask）可以直接移植过来。

### 6.2 媒体处理性能

**风险等级：中**

4K 实时预览不可行，但"感觉流畅"是可以做到的。

**分层策略：**
- 时间轴结构变化 → **立即响应**（< 50ms，只改数据结构）
- 静态 thumbnail → **已有代理文件**，立即显示
- 单帧合成 → **1-3 秒**（异步，低分辨率代理）
- 完整渲染 → **后台队列**，完成后推送通知

现有 `proxy_generate()` 函数已经实现代理文件逻辑，继续用。

### 6.3 可重现性

**风险等级：低-中**

Python 脚本比 JSON plan 更可重现，因为脚本是完整的执行意图。

**保障机制：**
- 每个 layer 的 `provenance.script` 记录生成它的完整脚本
- 标准库版本锁定（`pyproject.toml` pin）
- LLM temperature 建议设为 0（生成代码时）
- 脚本中的随机种子显式化（AI 被要求在脚本里写 `seed=42`）

### 6.4 用户体验断裂点

| 断裂点 | 缓解方式 |
|--------|---------|
| AI 误解意图，执行错误操作 | 干运行预览 + 时间轴撤销（一键回滚） |
| 渲染卡住，无反馈 | SSE 实时进度流，每 2 秒推送一次 |
| 无法精确控制细节 | AI 在脚本里暴露参数，用户可以在对话中说"把 strength 从 0.8 改到 1.0" |
| 多步操作后悔了 | Timeline patch 历史，支持任意跳回历史版本 |

---

## 七、4-8 周迁移路线图

### 第 1 周：沙箱 + 最小 Agent 循环

**目标**：一个能安全执行 AI 脚本的闭环，哪怕功能极简

- [ ] 设计 `lumerai` namespace：从现有 gemia 函数中选出约 20 个核心函数，包一层简洁 API
- [ ] 实现沙箱执行器（AST 检查 + subprocess + resource limits）
- [ ] 实现最小 Session 模型（SQLite：session / message / timeline_patch）
- [ ] 接入 Claude API，替换 Gemini（或保持双轨），系统提示引导写脚本
- [ ] 实现最小 agent 循环：输入 → LLM → 脚本 → 沙箱执行 → 结果回传 LLM
- [ ] 扩展 `project_model.py`，给 clip/layer 添加 `provenance` 字段

**里程碑**：用户说"截取第 10 到 45 秒"，AI 写脚本执行，时间轴出现一个有溯源信息的 layer。

**不改动**：现有 gemia.video/audio/picture 任何一行代码。

### 第 2 周：预览系统接通 + 对话流 UI

**目标**：能看到视频，对话流体验接近 Claude Code

- [ ] 实现媒体播放器组件（替换当前 UI 中的预览区域）
- [ ] 时间轴组件（显示 layers + 播放头，layer 悬停显示 provenance.reason）
- [ ] SSE 推流（脚本执行进度 + 预览帧 URL）
- [ ] 对话流组件（工具调用可折叠展开、进度条、完成总结）
- [ ] server.py 初步拆分（提取 session 路由和 media 路由）

**里程碑**：端到端体验可用，能跑一个完整的"截取 + 色彩调整"任务并看到预览。

### 第 3 周：标准库扩展 + 权限系统

**目标**：AI 能做更多事，系统是安全的

- [ ] `lumerai` 标准库扩展到 50 个函数（覆盖音频、字幕、转场）
- [ ] 完善沙箱（文件边界 + 干运行模式 + 用户确认流）
- [ ] 移植 opencode Permission 系统（allow/deny/ask）
- [ ] 实现 Timeline patch 历史和撤销

**里程碑**：可以放心让 AI 执行，不担心文件被误操作。

### 第 4 周：agent_workflow 改造 + 系统提示优化

**目标**：AI 的脚本质量接近人工水平

- [ ] 改造 `agent_workflow.py`：范围检测/约束注入逻辑保留，输出改为"注入系统提示"而非"过滤 primitive"
- [ ] 系统提示工程：让 AI 写出规范、有注释、参数显式化的脚本
- [ ] 实现多轮迭代（AI 看到执行结果后可以自动修正）
- [ ] 测试套件：核心 20 个脚本场景的单元测试

**里程碑**：对话体验流畅，AI 在出错时能自动修复，不需要用户重新解释。

### 第 5-6 周：完整标准库 + 稳定性

**目标**：可以完成真实的视频剪辑任务

- [ ] `lumerai` 标准库完整化（目标 80 个函数）
- [ ] 后台渲染队列（长时间任务不阻塞 UI）
- [ ] Session fork（时间轴分叉实验不同风格）
- [ ] 性能优化（帧缓存、代理文件预生成）
- [ ] 错误恢复机制（脚本执行失败 → AI 自动分析 stderr → 修复）

**里程碑**：可以用 Lumerai 完成一个真实的 3-5 分钟视频剪辑任务，全程不离开对话框。

### 第 7-8 周：切换 + 收尾

**目标**：旧架构完全停用，新架构可展示

- [ ] 停用旧 plan-selection 流程（orchestrator.py 改为纯脚本执行器）
- [ ] 用户测试 + 体验迭代
- [ ] server.py 完全拆分为模块
- [ ] 文档：`lumerai` 标准库 API 文档（供 AI 的系统提示使用）

---

## 八、决策摘要

| 议题 | 决策 |
|------|------|
| 808 个 primitive 怎么处理 | **全部保留**作为内部实现；对外封装为 ~80 个 `lumerai` API |
| LLM 换不换 | 初期保留 Gemini/OpenRouter，同时接 Claude API 做对比；最终双轨 |
| 执行方式 | Python 沙箱（AST 检查 + subprocess），不用 Docker（太重） |
| 时间轴格式 | 现有 project_model.py 扩展（添加 provenance），不另起炉灶 |
| server.py | 拆分为模块，但接口不变（客户端不用改） |
| agent_workflow.py | 核心逻辑保留，输出目标改变（约束注入而非 primitive 过滤） |
| UI 框架 | 保留 Tauri，替换内容区组件 |
| 第一个里程碑 | **第 1 周末**：截取 + 色彩调整的完整闭环 |

---

*文档待审阅。审阅后请标注：哪些方向已确认、哪些需要调整、哪些需要更深入讨论。*
