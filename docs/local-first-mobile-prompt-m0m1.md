# 给 Codex 的任务 Prompt：路线 B 复活（M0 审计 + M1 iOS 地基）

> 用法：把下面 `---` 之间的全文复制给 codex。
> 创建：2026-08-07。配套方案文档：`/Volumes/Extreme SSD/lumeri/docs/local-first-mobile-plan.md`

---

# 任务：Lumeri 路线 B 复活 —— M0 审计 + M1 iOS 端上地基

## 背景

Acrab 2026-06-28 拍板 Lumeri 移动端走**路线 B：端上原生双写引擎**，明确否决云渲染。核心是：素材、时间线、渲染永远在设备本地，云端最多只做「自然语言 → 执行计划」的文本规划。

但该任务卡自 2026-06-28 起 **40 天零推进**。期间 iOS 侧未按路线 B 开工，改做了「iPad/iPhone 当远程壳连 Acrab 的 Mac」——这恰恰是被否决的「计算不在端上」，并滚出一整套复杂度（GCP 公网入口、Cloudflare Tunnel、工作区网关 `:7829`、加密 APFS 卷、沙箱隔离、设备配额、每设备 runtime）。2026-08-07 整个下午耗在为其中一个加密卷查找密钥。

本任务是把这条线扳回路线 B。

## 必读（按顺序，读完再动手）

1. `/Volumes/Extreme SSD/lumeri/docs/local-first-mobile-plan.md` —— 完整方案，含三层架构、iOS 选型论证、11 个 verb 的逐项 API 映射、分期、验收断言。**不要重新推导方案，照它做。**
2. `~/Code/lumeri-android/docs/contract/verb-plan-v1.md` —— 冻结契约。**⚠ 该文档的「实现状态」章节已确认失实，只读契约定义部分，不要采信状态表。**
3. `~/.agents/shared-agent-loop/QUEUE.md` → 任务卡 `shared-2026-06-28-lumeri-mobile-pathB`

## 本次范围：只做 M0 和 M1，做完停下汇报

**不要顺手开始 M2 及以后。** 做完 M1 停下，等 Acrab 决定下一步。

---

## M0：审计与对齐（先做，不写新功能）

### M0-1 代码级审计 Android 真实能力

读 `~/Code/lumeri-android/app/src/main/java/com/xiehaibo/lumeri/engine/` 下的 `exec/EffectFactory.kt`、`exec/VerbExecutor.kt`、以及序列/composition 构建层。

对 11 个 verb 的每个 action，判定属于哪一类：
- **真接通**：有实际效果实现
- **诚实抛异常**：`VerbNotSupportedException`
- **空实现但合理**：效果由其他层处理（如 `arrange_timeline` 由序列构建层负责）
- **空实现且可疑**：声称支持但什么都没做 ← 重点找这类

已知起点（2026-08-07 核对，需你复核并补全）：
- 真接通：`color_grade`(WARM/COOL/VINTAGE/CINEMATIC/TEAL_ORANGE)、`transform_geometry`(ROTATE/SCALE/CROP)、`edit_video`(TRIM/CONCAT)
- 诚实抛异常：`add_overlay`、`composite`、`edit_image`(全部)、`transform_geometry(PERSPECTIVE)`、`edit_video(SPEED)`、`edit_video(REVERSE)`
- 需你判定：`mix_audio`（注释称「音量/淡入淡出待实现」）、`analyze_media`、`extract_frame`

**产出**：一张真实能力矩阵，每格附源文件路径与行号。

### M0-2 修正失实的契约文档

`verb-plan-v1.md` 的「实现状态」章节与代码不符：`edit_video(SPEED)` 与 `edit_image` 全部 action 实际抛异常却标 ✅，`mix_audio` 音量/淡入淡出未实现也标 ✅。

按 M0-1 的审计结果重写该章节。**每一条状态必须能追溯到具体代码行。**

### M0-3 两套 Android 实现的取舍分析

存在两套并行实现：
- `~/Code/lumeri-android` —— 路线 B 原生 verb executor（Media3）
- `~/Code/lumeri-packaging-next/android` —— 内嵌 Python Agent loop + 27 工具（alpha.18）

分析两者的能力覆盖、包体、维护成本、与路线 B 的契合度，**产出取舍建议但不要自行决定**——这是 Acrab 的决策点。

### M0-4 确认 iOS 现状

审计 `~/Lumeri Video`，确认（或推翻）「只有远程壳、没有任何端上执行器」这一判断。列出现有与远程壳相关的文件，供后续 M6 拆除时参考。**本次不要删除或修改它们。**

---

## M1：iOS 端上地基

在 `~/Lumeri Video` 中建立端上执行器，实现 4 个地基 verb：

| Verb | iOS 实现路径 |
|---|---|
| `arrange_timeline` | `AVMutableComposition.insertTimeRange` 逐 track 排布 |
| `edit_video(TRIM)` | `insertTimeRange` 的 in/out point |
| `edit_video(CONCAT)` | 连续 `insertTimeRange` |
| `transform_geometry(CROP/ROTATE/SCALE)` | `AVMutableVideoCompositionLayerInstruction.setTransform` + `renderSize`（CROP 需同时改 renderSize） |
| `export` | `AVAssetExportSession`，P720/P1080 |

同时需要：
- `LumeriPlan` / `VerbOp` 的 Swift `Codable` 实现，**严格对齐冻结契约**（`schema: "lumeri.plan"`, `version: 1`, `classDiscriminator = "verb"`）
- 一个执行器入口：吃 `LumeriPlan` JSON，吐产出文件路径
- 未实现的 verb **必须抛出明确错误**，错误信息包含 verb 名与 action

### M1 出口标准（硬性）

在 iPhone 模拟器上，**不连接任何外部服务**（不连 `:7788`、不连 `:7829`、不连 accounts、不连任何网络），从两段本地视频素材产出一个真实可播放的 mp4。

---

## 硬性边界：以下事情一律不做

1. **不碰任何 live 服务**：`:7788`、`:7829`、Cloudflare Tunnel、`accounts.lumeri.io`、GCP、Cloud SQL。不重启、不部署、不改配置。
2. **不碰加密卷相关设施**：`/Volumes/LumeriTesterSecure`、sparsebundle、classmates LaunchAgent。
3. **不改架构方向**。方案已定，照做。如果你认为方案有问题，**停下来说明理由并等 Acrab 回复**，不要自行改道。（2026-08-06 曾发生 14 分钟内两次推翻部署架构的情况，不要重演。）
4. **不 push、不合并、不上传 TestFlight/App Store Connect、不做正式签名分发。**
5. **不动 FlClash**（停止、重启、切配置、切订阅一律禁止，只读诊断也需先说明）。
6. **不在 iOS 上尝试 exec ffmpeg 二进制**（沙箱禁止 fork/exec），**不要引入 ffmpeg-kit**（已于 2025 年初归档停维护，且有 GPL/LGPL 授权义务）。全部走 AVFoundation / Core Image / Metal。
7. **不上传素材、项目、时间线、生成产物到任何云端**——违反 local-first 边界决策。

---

## 验收标准：这一节最重要

### 背景：这里栽过

2026-06-28 记录在案：codex 初版把多个 verb 静默 no-op 却报告成功——`SPEED` 输出原速视频却返回 success，是 Acrab 亲自复核才抓出来的。

**因此：「函数返回成功」不构成任何证据。**

### 三条铁律

1. **要么真接通，要么抛异常。** 禁止静默 no-op、禁止返回未经修改的输入、禁止用「已排入队列」「将在后续处理」搪塞。
2. **每个 verb 必须有输出级断言。** 断言对象是产出的媒体文件本身，不是函数返回值。
3. **契约文档必须与代码在同一次提交内同步。** 不允许代码改了文档不改。

### M1 四个 verb 的具体断言要求

| Verb | 必须用数值断言的事实 |
|---|---|
| `arrange_timeline` | 输出总时长 = 各 item durationMs 之和；轨道数与请求一致 |
| `edit_video(TRIM)` | 输出时长 = out − in（容差 ±1 帧）；输出首帧与输入在 in 处的帧像素相似 |
| `edit_video(CONCAT)` | 输出时长 = 各段之和；在拼接点前后各取一帧，分别匹配对应源片段 |
| `transform_geometry(CROP)` | 输出分辨率符合裁剪比例；输出边缘像素取自裁剪区域内部 |
| `transform_geometry(ROTATE)` | 90°/270° 时输出宽高互换；角落像素位置符合旋转矩阵 |
| `transform_geometry(SCALE)` | 输出分辨率 = 输入 × scale |
| `export` | 分辨率、视频编码、音轨存在性与请求一致；文件可被 `AVAsset` 正常打开 |

### 测试要求

- 使用**固定的、可重复的真实素材**（不要随机生成，不要用纯色帧掩盖问题）。
- **不允许 mock AVFoundation** 来证明通过。必须真实渲染出文件再断言。
- 每个断言使用数值容差，不依赖人眼判断。
- 测试必须能在 iPhone 模拟器上跑通并给出可复现的输出。

---

## 本机环境的已知坑（会浪费你时间的）

1. **构建目录不要放外置 ExFAT 盘**。`/Volumes/Extreme SSD` 是 ExFAT，AppleDouble 文件（如 `._IsAppEncrypted.h`）会被 clang 当作源码编译，导致 BUILD FAILED。DerivedData 放内置 APFS。
2. **内置盘空间极度紧张**（2026-08-07 一度只剩 201 MiB）。开工前先 `df -h /` 确认，不足时按已授权的缓存清理流程处理，**只删可重建的缓存**（Xcode DerivedData、npm/pip/SwiftPM/Gradle 缓存），不碰源码、归档、模拟器状态、Android AVD、Codex runtime。
3. **`diskutil` 被本机破坏性命令护栏 wrapper 包着**，非交互 shell 里会报 `command not found: _acrab_dcg_check`。正确做法是先 `source ~/.config/safety/destructive-command-guard.sh`，**不要用绝对路径绕过护栏**。
4. **删除类操作受全机护栏约束**：禁止任何可能抹掉根目录、家目录、整卷或裸盘的命令；删除目标必须是解析过的窄路径。

---

## 汇报要求

完成后按以下结构汇报，**不要只说「已完成」**：

1. **M0 能力矩阵**：每个 verb/action 的真实状态 + 源文件路径与行号
2. **契约文档改了什么**：修正前后对照
3. **两套 Android 实现的取舍分析**：事实对比 + 建议 + 明确标注「待 Acrab 决定」
4. **M1 实现清单**：新增/修改的每个文件路径
5. **测试实证**：每个断言的**实际数值**（例如「输入 5000ms，TRIM(1000,3000) 输出 2001ms，容差内」），不是「测试通过」四个字
6. **明确说明未做什么**：哪些 verb 抛异常、哪些边界没碰
7. **诚实声明验证边界**：你验证到什么程度，什么没验证

## 记账要求（不要跳过）

1. 更新 `~/.agents/shared-agent-loop/QUEUE.md` 的 `shared-2026-06-28-lumeri-mobile-pathB` 卡片：真实状态、触碰路径、验证证据、下一步。
2. 在 `~/.agents/shared-agent-loop/daily/2026-08-07.md`（或当日文件）追加一节，记录决定、验证与教训。
3. **改动与记账必须同回合完成。** 2026-08-07 已发生过「凌晨 02:02 改了网关文件、当日日志一字未提」的情况——下一个接手的 agent 会基于过期账本做判断，比没有记录更危险。

## 如果你卡住了

- 需要 Acrab 决策的（如两套 Android 实现取舍、是否扩大范围）：**停下来问，不要自行决定**。
- 发现方案文档有错：**说明理由并等回复**，不要自行改道。
- 遇到平台限制做不到：**诚实报告做不到**，不要用 no-op 或降级实现假装完成。

---
