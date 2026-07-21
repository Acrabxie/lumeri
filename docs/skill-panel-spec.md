# Lumeri Skill 前端扩展规范（v1）

状态：已实现。适用于内置 Skill 和安装到 `~/.gemia/skill-packs/<skill-id>/` 的本地 Skill 包。

## 目标与边界

Skill 可以在 Lumeri Video 工作区注册一个或多个可拖动、可缩放、自动补齐的交互面板，也可以在取得 `timeline.components` 权限后受控扩展时间轴组件区。所有前端扩展必须使用 Lumeri 已有的原生控件和视觉 token，不得注入 HTML、CSS、JavaScript、网络地址或直接项目修改代码。

用户提交面板后，Lumeri 创建一个普通 Agent turn。会话隔离、预算、Plan Mode、工具权限、完成门和人工验收规则保持不变。

## Skill 包结构

```text
my-skill/
├── SKILL.md
├── panels/
│   └── main.json
└── timeline/
    └── actions.json
```

`SKILL.md` 在现有 frontmatter 中增加 `panels`。路径必须相对 Skill 根目录，必须是 JSON，禁止 `..`、绝对路径、隐藏文件和越过 Skill 目录的符号链接。

```yaml
---
id: my-skill
description: |
  说明何时使用以及何时不用。
triggers:
  primary: [示例]
  secondary: [example]
primitives:
  - gemia.example.operation
est_tokens: 400
panels:
  - panels/main.json
permissions:
  - timeline.components
timeline_components:
  - timeline/actions.json
---
```

一个 Skill 最多注册 8 个面板。`id` 必须与目录名一致，使用 ASCII kebab-case。

## Panel JSON

```json
{
  "schema_version": 1,
  "id": "main",
  "title": "面板标题",
  "description": "告诉用户这个面板做什么。",
  "icon": "sliders",
  "intent": "提交后希望 Lumeri 完成的创作目标。",
  "submit_label": "交给 Lumeri",
  "lifecycle": "persistent",
  "default_size": {"width": 38, "height": 64},
  "fields": []
}
```

顶层字段不允许扩写；未知字段会使该面板注册失败，但不会影响其他 Skill 或 Lumeri 启动。

### 顶层字段

| 字段 | 规则 |
|---|---|
| `schema_version` | 必须为 `1` |
| `id` | Skill 内唯一的 kebab-case，最多 64 字符 |
| `title` | 工作区标签和面板标题，最多 48 字符 |
| `description` | 用户可见说明，最多 240 字符 |
| `icon` | 只能使用 Lumeri `icons.svg` 白名单图标 |
| `intent` | 提交给 Agent 的目标说明，单行且最多 300 字符 |
| `submit_label` | 主操作按钮文字，最多 24 字符 |
| `lifecycle` | `persistent` 或 `temporary`；省略时为 `persistent` |
| `default_size` | 百分比权重；宽 16–100，高 18–100 |
| `fields` | 1–12 个原生字段 |

### 面板生命周期

- `persistent`（默认）：注册后出现在工作区“+”菜单；用户打开后可关闭，关闭后仍能从“+”再次打开。
- `temporary`：当前规范版本首次被发现时自动加入工作区，不出现在“+”菜单；用户关闭后，该版本不再出现。

宿主根据规范化后的面板内容生成只读 `revision`，Skill 不得自行填写。临时面板在关闭前刷新页面仍会保留；开发者修改面板 JSON 后 `revision` 改变，新版本会再自动出现一次。这保证调试面板不会成为永久入口，同时不会锁死下一轮调试。

### 撤下与重新发布

Skill 只能撤下自己注册的面板。要撤下面板，从该 Skill 自己的 `SKILL.md` 中删除对应的 `panels` 路径；宿主下次加载面板清单时不再注册它，刷新工作区后也不会继续显示在“+”菜单或已打开模块中。未被 `panels` 引用的 JSON 文件是惰性的，可以保留，之后把同一路径加回清单即可重新发布。

一个 Skill 不能声明撤下其他 Skill 或 Lumeri 内置模块。不要通过删除 JSON 文件来表达撤下；缺失的已声明文件属于无效 Skill 配置，会产生隔离错误。

### 字段通则

每个字段都必须有 `id`、`type`、`label`。可选 `description`、`required`、`default`。字段 ID 使用 kebab-case；包含 `password`、`secret`、`token`、`api-key` 的字段会被拒绝，面板不得收集凭据。

支持的类型：

- `text`：`placeholder`、`multiline`、`min_length`、`max_length`；最长 2000 字符。
- `select`：`options: [{label, value}]`，1–24 项。
- `multi_select`：同上，另有 `min`、`max`。
- `slider`：`min`、`max`、`step`、`default`。
- `toggle`：布尔 `default`。

控件的 DOM、样式、键盘行为、焦点环、禁用态和错误提示全部由 Lumeri 生成。Skill 只能声明语义，不能覆盖样式。

## 时间轴组件权限

`timeline.components` 是一个显式权限。只有 `SKILL.md` 同时声明该权限与 `timeline_components` 清单时，Lumeri 才会读取时间轴 JSON。没有权限的清单会被隔离并报告为无效，不影响其他 Skill 或服务启动。

```json
{
  "schema_version": 1,
  "id": "my-timeline-tools",
  "edits": [
    {
      "component": "add-title",
      "label": "片头标题",
      "placement": {"after": "export-draft"}
    }
  ],
  "widgets": [
    {
      "id": "grade-selection",
      "kind": "button",
      "label": "调色所选",
      "description": "让 Lumeri 调整当前选中片段并检查结果。",
      "icon": "droplet",
      "placement": {"after": "add-title"},
      "requires_selection": true,
      "action": {
        "type": "agent_turn",
        "intent": "为当前选中片段完成自然调色并检查结果。"
      }
    }
  ]
}
```

每个 Skill 最多声明 4 个时间轴清单；每个清单最多 12 项 `edits` 和 12 个 `widgets`。清单必须至少包含其中一种。

### 编辑已有组件

v1 只允许改宿主白名单组件的显示文字、可见性和同一区域中的位置，不允许替换点击行为。可编辑 ID：

- `redo`、`marker`、`snap`
- `export-1080p`、`export-draft`、`add-title`、`show-layout`

`placement` 必须恰好包含 `before` 或 `after`，目标只能是四个 quick-action 组件。`undo`、`split`、`delete`、`timecode` 和 `zoom` 属于核心编辑链，Skill 不能隐藏、重排或替换它们。

### 添加小组件

v1 小组件固定为宿主渲染的 `button`，放在时间轴 quick-action 区域。Skill 只能提供白名单图标、短标签、说明、位置和动作声明；不能提供 DOM、选择器、样式、脚本或 URL。

动作有两类：

- `agent_turn`：提供最多 300 字符的 `intent`，点击后进入当前会话的普通 Agent turn，继续受预算、Plan Mode、工具权限和验收流程约束。
- `host_action`：只能调用 `undo`、`split-selected`、`delete-selected`、`add-marker`、`toggle-snap`、`zoom-in`、`zoom-out`。前端实际点击宿主既有入口，不直接改时间轴状态。

`requires_selection: true` 会在没有选中片段时禁用组件；当前 turn 执行中、没有会话或宿主动作不可用时同样禁用。多个 Skill 按规范化的 Skill ID、清单 ID 顺序应用；位置冲突保持该顺序，不允许用 CSS 层级抢占。

## 交互契约

提交时前端生成一条明确的用户操作：Skill ID、面板 `intent` 和字段值。它通过当前会话的 `/sessions/{id}/turn` 进入 Agent，不调用面板自定义 URL，也不绕过工具门禁。

当当前 turn 正在执行、没有会话或必填字段无效时，面板在原位显示原因和恢复动作，不静默失败。

## 视觉规范

- 外壳复用工作区现有 module：标题栏、刷新、关闭、拖动、三向缩放和自动补齐均由宿主提供。
- 持久面板关闭后回到“+”菜单；临时面板显示“临时 · 关闭后移除”，关闭动作只消费当前 `revision`。
- 表面、圆角、颜色、字体、间距、图标和 focus ring 只取 `static/v3/v3.css` 与 `static/v3/icons.svg` 的现有 token。
- Skill 不得提供品牌色、CSS 类、像素值、SVG path 或图片图标。
- 主操作只有一个；成功默认静默进入对话流，失败必须显示原因并允许重试。
- 所有控件必须能用键盘完成；切换控件使用 `role="switch"` 与 `aria-checked`。

## 安装与验证

内置 Skill 位于 `gemia/ai/skills/<skill-id>/`。用户 Skill 包放在 `~/.gemia/skill-packs/<skill-id>/`；测试或隔离运行可用 `LUMERI_SKILL_PACKS_DIR` 指向其他目录。

运行：

```bash
python -m gemia.skill_panels --json
```

命令只在所有声明面板和时间轴清单都通过规范时退出 0。浏览器通过 `GET /skill-panels` 只取得规范化后的安全 schema，包括 `panels` 与 `timeline_components`；校验错误不会下发到浏览器。
