<p align="center">
  <a href="https://lumeri.io/">
    <img src="docs/assets/lumeri-working.gif" width="240" alt="Lumeri 工作动画" />
  </a>
</p>

<h1 align="center">Lumeri</h1>

<p align="center">
  <strong>the LUI for creation</strong>
</p>

<p align="center">
  AI 创意工作流引擎：把一个想法，变成一个真实、可继续编辑的媒体项目。
</p>

<p align="center">
  <a href="https://lumeri.io/"><strong>官网</strong></a>
  ·
  <a href="https://video.lumeri.io/">Lumeri Video</a>
  ·
  <a href="https://quanta.lumeri.io/">Quanta</a>
  ·
  <a href="https://cli.lumeri.io/">CLI</a>
  ·
  <a href="https://skills.lumeri.io/">Store</a>
  ·
  <a href="https://docs.lumeri.io/">文档</a>
</p>

<p align="center">
  <a href="README.md">English</a>
  ·
  <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://lumeri.io/">
    <img src="https://img.shields.io/badge/website-lumeri.io-5FC6DE?style=for-the-badge" alt="Lumeri 官网" />
  </a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-1F2937?style=for-the-badge&amp;logo=python&amp;logoColor=white" alt="Python 3.12 或更新" />
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-1F2937?style=for-the-badge" alt="MIT 许可" />
  </a>
</p>

**Lumeri** 是一个 AI 创作工具家族。它的底子是一小套干净、可组合的原语，由模型来规划和执行。

你说出想做什么。Lumeri 做出计划，在你眼前用结构化的媒体工具把活干完，最后留给你一个真实的项目：一条可以打开、重排、裁剪、继续指挥的时间线。生成是起点，不是终点。

- **以项目为本** — 作品落在持久的项目和时间线里，而不是一段用完即弃的聊天回复。
- **结构化** — 模型用明确的媒体工具做计划，以可审阅的时间线补丁落地，而不是吐出一段看不懂的编辑器宏。
- **本地优先** — 项目、导入的素材、时间线和输出默认留在你的设备上。
- **开放的地基** — 引擎、媒体工具、项目模型和本地工作区都在这里，MIT 许可。

## 家族

| | 是什么 | 在哪 |
|---|---|---|
| **Lumeri Video** | 从想法到可编辑的时间线。会话、计划、素材、预览和时间线都在同一个项目里。 | [video.lumeri.io](https://video.lumeri.io/) |
| **Lumeri Quanta** | 有结构、可播放的视频：状态、分支与回环，存成本地优先的 `.luqu` 文件。 | [quanta.lumeri.io](https://quanta.lumeri.io/) |
| **Lumeri CLI** | `luvi` 和 `luqu`：在终端里用同一个本机运行时，过程随时可见。 | [cli.lumeri.io](https://cli.lumeri.io/) |
| **Lumeri Store** | 创作者明确选择公开的 Skill、Point Library 和 Workflow。 | [skills.lumeri.io](https://skills.lumeri.io/) |
| **文档** | 给创作者的指南，给开发者和集成方的参考。 | [docs.lumeri.io](https://docs.lumeri.io/) |

Lumeri 是家族的名字。视频产品叫 **Lumeri Video**。

## 它怎么工作

```text
导入素材
→ 项目和时间线落盘
→ 模型分多轮调用媒体工具
→ TimelinePatch 更新项目
→ 渲染预览并检查
→ 根据结构化反馈修改
→ 导出 MP4 或 OTIO
```

每一步都留下看得见的东西：计划、每一次工具调用、落下的补丁、渲染出的预览。遇到该由你拿主意的事，Lumeri 会停下来问你。

## Lumeri 是怎么搭的

Lumeri 分成四个部分，彼此之间界线分明。

| 部分 | 管什么 | 守的规矩 |
|---|---|---|
| **App 外壳** | 你下载的那个窗口，以及登录。 | 不带任何创作逻辑。运行时单独送达，所以新能力不用等新版 App。 |
| **Agent 内核** | 模型 ↔ 工具的循环、项目与时间线模型、渲染和导出。 | 小而稳。 |
| **Luc** | 其余的一切：编辑器页面、面板、工具、Skill、创作库。 | 每一个都声明自己需要什么，并且只能做到声明的那么多。 |
| **账号与云服务** | 登录、套餐、Store、托管服务。 | 属于 Lumeri 自己。不在本仓库里，任何 Luc 也碰不到。 |

本仓库是开放引擎：单用户的本地版本，Agent 内核和编辑器在一个本机服务里一起跑，完全没有账号。

## Luc

> Luc 属于 Lumeri Video 1.1 这条线。公开下载版和本仓库里的引擎仍是 1.0 —— 见[现在做到哪了](#现在做到哪了)。

**Luc 是 Lumeri 的扩展单位。** 编辑器里的一块面板、Agent 能调用的一个工具、一个 Skill、一个创作库：每一个都是一个 Luc —— 一个 `.luc` 文件，带一份清单，写明它是什么、需要什么。

Lumeri 自己也守这条规矩。Video 编辑器就是由一张注册表里的官方 Luc 拼起来的 —— 编辑器的框架、它的面板、工具、Skill —— 每一个在你的配置文件里都是一行，可以关掉。

不是 Lumeri 自己出的 Luc，用的是同一张权限清单。官方 Luc 能申请的，它都能申请，只有一个例外：属于 Lumeri 自己的东西 —— 你的账号、模型网关和云服务。

### 先声明，再照此约束

一个 Luc 在清单里写明自己要什么，像 Android 应用声明权限那样，只是开得更大。下面是一个 Luc 的申请：它往编辑器里加一块自己的面板，并在时间线上移动片段（清单只留了申请的部分）：

```json
{
  "format": "lumeri.luc.distribution/v1",
  "kind": "luc",
  "identity": {"id": "com.example.clip-nudge", "version": "1.0.0"},
  "compatibility": {"runtime": ">=1.1.0 <2.0.0"},
  "host": {
    "protocols": ["lumeri.panel/v2"],
    "permissions": ["project.read", "project.write", "ui.mount"],
    "apis": {"required": ["project.view", "timeline.edit"], "optional": []},
    "editable": {
      "target_types": ["timeline_clip"],
      "properties": ["clip.placement"],
      "areas": ["timeline"]
    }
  }
}
```

- `protocols` — 它是哪一类东西：面板、Skill、交互，还是工作区。
- `permissions` — 它向宿主要的几大类许可。
- `apis` — 它具体要调用的宿主接口。每个接口归属一项权限，两样都得写。
- `editable` — 它可以改项目里的什么。

宿主只给声明过的，其余一律拒绝。这个 Luc 能移动片段；它删不了片段，也读不了项目文件，因为这两样它都没申请。它的面板跑在一个不能联网的沙箱框里，唯一的出口是声明过的调用。权限不看出身 —— Lumeri 自己的 Luc 也走同一套检查、同一张清单。

| 权限 | 谁能持有 | 允许什么 |
|---|---|---|
| `ui.mount` | 任何 Luc | 在编辑器里显示自己的面板或交互。 |
| `project.read` | 任何 Luc | 读取当前项目：时间线、分镜、文件、素材库、笔记。 |
| `project.write` | 任何 Luc | 修改当前项目，且只在它声明的范围之内。 |
| `session.control` | 任何 Luc | 停下创作者发起的工作。 |
| `account.access` | 仅 Lumeri | 创作者的账号、登录与套餐。 |
| `gateway.access` | 仅 Lumeri | 经由 Lumeri 网关的模型与付费服务。 |
| `cloud.access` | 仅 Lumeri | Lumeri 的云服务。 |

安装就是把文件放进一个文件夹：`~/.lumeri/lucs`。没有安装弹窗。文件在那里，就是你的同意；每个 Luc 手里拿着什么权限，随时可以查。

### 现在做到哪了

Luc 属于 Lumeri Video 1.1 这条线。公开下载版和本仓库里的引擎仍是 1.0，所以这一节讲的内容，目前还不能从本仓库跑起来。

| | 现状 |
|---|---|
| Video 编辑器由官方 Luc 拼成 | 已在 1.1 版本中运行 |
| 你自己的 Luc 在编辑器里注册面板 | 已在 1.1 版本中运行 |
| 跑在运行时内部的代码：Agent 工具、编辑器页面本身 | 仅限官方 Luc |
| Skill、Point Library、Workflow 统一成 Luc（本机和 Store 都是） | 进行中 —— 目前还是三种各自独立的类型 |
| Luc 宿主、格式参考和打包工具 | 不在本仓库 |

## 本仓库

Lumeri Video 背后的开放引擎：Python、FFmpeg，加一个本地网页工作区。

| 路径 | 职责 |
|---|---|
| `server.py` | 本机 HTTP 入口 |
| `gemia/v3_routes.py` | 会话 API 与流式事件 |
| `gemia/agent_loop_v3.py` | 多轮模型/工具循环 |
| `gemia/tools/` | 基于 FFmpeg 和 Python 的媒体工具 |
| `gemia/project_model.py` | 持久的时间线模型 |
| `gemia/project_render.py` | 预览渲染 |
| `gemia/project_export.py` | 全质量导出 |
| `gemia/quanta/` | Quanta：有结构、可播放的视频 |
| `gemia/ai/skills/` | 内置 Skill |
| `gemia/mcp/` | MCP 服务 |
| `lumenframe/` | 合成引擎与创作库：调色、标题、运镜、构图、剪辑、节奏、矢量动效 |
| `lumerai/patches.py` | 共用的时间线补丁词表 |
| `lumerai/otio_adapter.py` | OpenTimelineIO 交换 |
| `static/v3/` | 本地网页界面 |

> 产品和本仓库的名字是 **Lumeri**。Python 包和部分工程路径仍沿用历史名称 `gemia`。

## 安装

需要 Python 3.12+ 和 FFmpeg。

```bash
git clone https://github.com/Acrabxie/lumeri.git
cd lumeri
python -m pip install -e ".[dev]"

# macOS
brew install ffmpeg

# Ubuntu
sudo apt-get install ffmpeg
```

通过环境变量或本地设置页配置一个受支持的模型服务商，然后启动 Lumeri：

```bash
python server.py
# 打开 http://127.0.0.1:7788/
```

这个版本没有账号系统：没有注册、登录、切换账号、托管邮件，也没有 Lumeri 计费。它始终打开一个存在当前电脑上的本地工作区。模型服务商的配置只留在这台电脑上，绝不会提交到 Git。

<details>
<summary><strong>Windows 10/11</strong></summary>

安装 64 位 Python 3.12 或更新版本、Git，以及一套完整的 FFmpeg，并确保 `ffmpeg` 和 `ffprobe` 命令在 `PATH` 里。然后在克隆下来的仓库里打开 PowerShell：

```powershell
.\scripts\windows\setup.ps1
.\scripts\windows\start.ps1
```

启动脚本直接在 `http://127.0.0.1:7788/` 上运行源码，并打开浏览器工作区。它不会构建或安装 EXE。运行 `doctor.ps1` 可以做一次不改动任何东西的环境与端口检查；7788 被占用时，给 doctor 和 start 都加上 `-Port 7790`。

PowerShell 的执行策略不会被改动。如果你的电脑拦截本地脚本，请先看过脚本内容，再只对当前进程放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

媒体编辑、渲染、导出、本地配音、Windows 字体、Blender 发现和 OpenTimelineIO 打包都在 Windows 上原生运行。模型生成的任意 `build`/`run_shell` 代码默认保持锁定，因为原生 Windows 没有 Lumeri 在 macOS 上使用的内核沙箱。本机所有者可以在 Lumeri 菜单里明确关闭 **Sandbox**，允许 PowerShell 和 Python 以完整的电脑权限执行；Lumeri 绝不会自动打开这种不安全模式。

</details>

<details>
<summary><strong>用 ChatGPT 订阅代替 API key</strong></summary>

在 `/setup` 里选择 **OpenAI 订阅（本机 Codex）**。这个选项调用同一台电脑上的 Codex CLI，并且只接受本机的 **Sign in with ChatGPT** 登录状态。Lumeri 绝不读取、复制或保存 Codex 的登录凭据，每个人都必须用自己的 OpenAI 账号登录。

在 Windows 上，选择该选项之前请先安装并登录 Codex CLI：

```powershell
winget install OpenJS.NodeJS.LTS
npm install -g @openai/codex
codex login
codex login status
```

如果刚装完 Node.js，请重新打开 PowerShell 再运行 `npm`。服务商设置面板可以打开 `codex login` 窗口并刷新本机状态。`codex login status` 显示的 API key 认证不算订阅访问；请完成 **Sign in with ChatGPT**。

</details>

## 测试

```bash
python -m pytest tests/ -q
```

测试覆盖工具契约、时间线补丁、渲染与导出、OpenTimelineIO 交换、自我纠错、沙箱、会话和网页服务。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。

## 贡献者

见 [CONTRIBUTORS.md](CONTRIBUTORS.md)。

## 许可

MIT
