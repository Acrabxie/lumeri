# Lumeri Video v3 前端 — 评估与设计愿景

目的:彻底理解现状 → 记录「好(保持)/差(待改)」→ 给出高级质感暗黑响应式视频编辑 UI 方向。供后期逐步重构,现在不改代码。
配套: phase0-system-map.md(系统地图)。授权: 用户 2026-07-08「你自己看情况处理」,自主连续通读、随读随落盘、全 ✅ 后一次汇报。

## 定位
视频编辑 agent 前端:左对话(agent 干活)+右时间线/资产(编辑结果)。vanilla JS 单文件无构建、SSE 驱动。参照系=剪映/达芬奇/Premiere 专业暗黑工具,不是普通 SaaS 深色皮。

## 架构一句话
index.html 只是空壳 `<div id="app">`,整个 UI 由 v3.js `render()` 全量拼字符串注入;后端 server.py 原生 http.server 手写路由,`/sessions/{id}/stream` 推 SSE,20 种 event kind 驱动。

---
## 理解覆盖度 (刷全 ✅ 才动优化)
| 维度 | 状态 |
|---|---|
| 架构/主循环/数据流 | ✅ |
| M1 传输层(重连/重放/20 handler/主子 agent 分流) | ✅ |
| 配色令牌 | ✅ |
| 动效令牌地基(ease-enter/move/exit + dur-fast 120/mid 200 + @keyframes spin) | 🟡 应用面/reduced-motion 待核 |
| CSS 组件逐行(1017 行,只透 :root 64 行) | 🟡 |
| M2 消息流 DOM/样式 | 🟡 state 已懂,DOM 未看 |
| M3 Composer 交互(斜杠/附件/发送/键盘) | ⬜ |
| M4 工具卡/subagent UI | ⬜ |
| M5 Plan mode UI | ⬜ |
| M6 时间线交互(拖拽/trim/split/filmstrip/波形) | 🟡 函数名+端点知道,手感没看 |
| M7 资产库 / M8 字幕 / M9 菜单弹窗 | ⬜ |

---
## ✅ 做得好的 (保持)
1. 契约先行: contract.json + v3_contract.py 单源 + 双端 drift 测试,20 event kind/14 error code/6 ask control 全枚举。重构安全网。
2. no silent drop: 未知 kind、坏 JSON 都浮 debug banner(v3.js:700,835)。
3. 断点重放: last_event_id(内存+localStorage 双存)+自动重连 1.5s,断线不丢不重头。
4. 主/子 agent_id 二分: subagent 子工具挂父卡下,不串主列表(childCallState:476)。
5. 错误恢复元数据: tool_exec_error 带 recovery/hint/valid_options,渲染可操作错误卡。
6. 素材签名守卫: renderAssets(376) `asset_id:kind:final:source:summary`,防每条 SSE 重拉素材。
7. 时间线是真 canvas 编辑器: px/秒、自适应标尺、滚轮缩放、拖拽 playhead、filmstrip+波形、trim/split/delete 走和模型 verb 同一 /timeline/op 端点。
8. reasoning 迁移: 工具调用前流式文本当"诊断"移到卡片(524),自我修正读成 reason→fix 一个弧。
9. 动效有令牌地基: easing(enter/move/exit 三条 cubic-bezier)+duration(fast 120/mid 200)令牌化,非随手写。
10. 设计令牌体系: surface 分层+brand+shadow+radius+easing 全变量化。

## ⚠️ 做得不好的 (P0/P1/P2 待改)
### P0 硬伤
- 每条 SSE 全量 render()(v3.js:833): 流式每 token 重建整个 #app。掉帧/抖动/丢焦点/丢滚动的总根。→ 增量渲染 / rAF 合批 / streaming 文本单独追加。纯前端不碰协议。
- innerHTML 拼字符串: XSS 面 + 无法局部更新 + 重建丢 UI 状态。→ 迁 createElement/局部 patch。
- 200+ 条重复 v3.css link(index.html)+`phase3www www` 空格笔误。→ 任务卡 task_7fddf8df 合并成一条。

### P1 质感/专业度差距 (用户最在意)
- 纯功能性深色缺"质感层": 配色已上移一档,但缺受光边(1px inset 亮边)、克制景深、hover/active 物理反馈、专业工具高信息密度。
- 时间线视觉待精修: clip 颜色硬编码双色(v3.js:868-876)未走令牌; markers/extraTracks 纯客户端刷新即丢(855-856)。
- 响应式策略未确认: 两栏在窄屏/移动端如何塌缩、时间线小屏怎么办 — 待核。
- 动效应用面/reduced-motion 未确认: 有令牌但是否全交互覆盖、是否尊重 reduced-motion — 待核。

### P2 可维护性
- 2306 行单 JS + 1017 行单 CSS,无模块边界,加功能靠塞大文件。
- render 层重复空判 `state.currentTurn?.toolCalls.get`,可抽 helper。

---
## 🎨 设计愿景 — 高级质感暗黑响应式视频编辑 UI
核心理念: **不是"深色皮",是"影棚黑"。** 专业剪辑软件的黑有层次、有受光、有密度,让画面(用户的视频)成为唯一亮点,UI 退成背景。

1. 色彩: 分层灰+单点冷光。基底不贴黑(已 L*7.6),五层 surface≥4 L* 步进; 卡片/面板顶沿 1px rgba(255,255,255,.04-.06) inset 受光边+底沿极淡暗线(M3 双层立体,非死黑投影); 品牌冰蓝 #5FC6DE 只做单点强调(选中/激活/进行中),其余全灰阶,让视频缩略图色彩跳出。
2. 密度与层级: 4/8/12/16 间距 scale,专业工具信息密度; 四级文字明度拉开层级,弱 chrome 强内容。
3. 质感来源: 克制动效(hover 80-120ms,只做物理感不花哨; streaming 光标/进度 rAF 顺滑); 真实深度靠明度分层+受光边非重黑阴影堆叠; 微观精修(圆角走令牌、图标线宽一致、hairline 分隔、品牌色细 focus 环); 时间线专业化(统一 clip 令牌色、filmstrip 清晰、波形精细、标尺呼吸、playhead 细而醒目、snap 微反馈)。
4. 响应式: 桌面左对话+右编辑可拖拽分栏; 窄屏折叠为 tab 切换(对话⇄编辑),时间线横滚+捏合缩放; 相对单位+flex/grid,宽内容自身 overflow-x,body 永不横滚; 极致打磨暗色(视频工具默认暗),令牌保留切换能力。

## 优化路线图 (后期逐步,每步 7788 实测,对照 M1 行为不变量验收)
1. P0 性能: render() 增量化/rAF 合批(先拆 streaming 文本追加,不动协议)
2. P0 清理: 合并 200 条 CSS link
3. P1 质感: 受光边+时间线令牌色+hover/active 反馈+focus 环(纯 CSS 低风险)
4. P1 响应式: 确认并实现窄屏塌缩
5. P2 可维护: 视情况模块拆分+render helper 抽取

---
## M1 行为不变量 (回归验收单,日后改动绝不能破)
- 断线自动重连(~1.5s)且不丢事件(last_event_id 重放); pill reconnecting↔live
- 每条事件都反映到 UI; 未知 kind/坏 JSON 出 debug banner 不静默吞
- 流式文本逐 token 追加; 工具调用开始时前置文本变成该卡 reasoning 行
- subagent 扇出: 子工具挂父 spawn 卡对应 agent 组,不串主列表
- 工具失败保留 error/code/recovery/hint/valid_options 供错误卡
- 刷新/重连从 last_event_id 续上,不从头重放
- plan/budget/ask/completion 四 gate 各置 pending* 驱动审批 UI

## 续读清单 (下轮从这里接,依赖本文件+phase0 地图断点续)
CSS 逐行(动效应用面/reduced-motion/组件规则/响应式@media) → M2 消息流 DOM → M3 Composer(斜杠/附件/键盘) → M4 工具卡/subagent UI → M5 Plan UI → M6 时间线交互手感 → M7 资产库 → M8 字幕 → M9 菜单弹窗。每读完追加进本文件对应小节,并把覆盖度表刷 ✅。
