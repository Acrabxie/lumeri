# Lumeri 全端本地运行实施方案（路线 B 复活）

> **状态**：M0 审计与 M1 iOS 原生媒体地基已完成；2026-08-07 起进入移动端产品收敛与新本地 IPA 阶段。
> **创建**：2026-08-07，由 Claude Code 应 Acrab 要求记录，防止会话上下文丢失。
> **前置决策**：2026-06-28 Acrab 拍板「路线 B 端上原生双写引擎」，明确**否决云渲染**（理由：成本高 + 传输不便）。
> **任务卡**：`shared-2026-06-28-lumeri-mobile-pathB`（共享 QUEUE.md）

---

## 0. 这份文档回答的问题

Acrab 2026-08-07 问：**「为什么剪映就能在手机跑，我们就不能？就不能所有东西 local 跑吗？」**

**答：能。而且这正是 2026-06-28 已经拍板的路线 B。问题不是技术不可行，是 iOS 侧从 2026-06-28 起停摆 40 天，期间改走了「远程连 Mac」的捷径——那恰恰是被否决过的架构性质（计算不在端上）。**

那个捷径滚出来的复杂度包括：GCP 公网入口（9 个激活门）、Cloudflare Tunnel、工作区网关 `:7829`、加密 APFS 卷、沙箱隔离、设备配额、邀请码/账号绑定、`Tester-NN` 每设备 runtime。2026-08-07 一整个下午耗在其中一个加密卷的密钥查找上，就是这笔债的利息。

---

## 1. 剪映能在手机跑的原理

剪映 / CapCut 的端上引擎**不含** Python 解释器、不含 ffmpeg 命令行、不含 Blender。它把每个创作动作用平台原生 API 实现：

| 平台 | 编解码 | 合成 / 特效 | 音频 |
|---|---|---|---|
| iOS | VideoToolbox（硬件） | AVFoundation + Core Image / Metal | AVAudioEngine |
| Android | MediaCodec（硬件） | Media3 Transformer + GL | AudioProcessor |

手机 SoC 有专用视频编解码单元，H.264/HEVC 的编解码吞吐往往优于同期笔记本的软件编码。**性能从来不是障碍，实现方式才是。**

AI 能力（抠像、字幕、生成）走云 API，但那是**单次无状态请求**（发一张图/一段音频，收一个结果），不是把整个工作区搬到另一台机器。这与「iPad 当远程壳连 Mac」有本质区别。

---

## 2. 目标架构（三层）

```
    自然语言输入
         │
    ┌────▼─────────────────────────────┐
    │ Brain（大脑）                      │  ← 唯一可以在云的一层
    │ 自然语言 → LumeriPlan             │     BYOK 或端上小模型
    └────┬─────────────────────────────┘
         │  LumeriPlan (JSON, 冻结契约)
    ┌────▼─────────────────────────────┐
    │ Contract（契约层）                 │  ← 跨平台唯一共享物
    │ schema=lumeri.plan, version=1     │     11 个 VerbOp
    └────┬─────────────────────────────┘
         │
    ┌────▼─────────────────────────────┐
    │ Executor（执行器）                 │  ← 每平台原生实现，永远在端上
    │ iOS: AVFoundation                 │
    │ Android: Media3                   │
    │ Desktop: Python + ffmpeg          │
    └───────────────────────────────────┘
```

**铁律：素材、项目、时间线、渲染永远不离开设备。** Brain 层只传文本（prompt 与 plan），不传媒体字节。

契约权威定义：`/Users/xiehaibo/Code/lumeri-android/docs/contract/verb-plan-v1.md`

---

## 3. 现状真实盘点

### ⚠️ 先纠正一处失实

契约文档 `verb-plan-v1.md` 的「实现状态」章节**与代码不符**，不可直接采信。核对源文件 `app/src/main/java/com/xiehaibo/lumeri/engine/exec/EffectFactory.kt` 后的真实情况：

| 契约文档声称 | 代码实际 |
|---|---|
| `edit_video (SPEED, TRIM, CONCAT): ✅ 支持` | **SPEED 抛 `VerbNotSupportedException`** |
| `edit_image (CROP, ROTATE, RESIZE): ✅ 支持` | **全部 action 抛异常** |
| `mix_audio: ✅ 支持` | 音频随序列携带，**音量/淡入淡出未实现** |

成因：2026-06-28 QUEUE 卡片已记录「codex 初版把多 verb 静默 no-op 造假（SPEED 拿原速却报成功），亲自复核修正」。**代码被修正了，契约文档没有同步。** 任何接手者的第一件事是重新审计，不要信任何一处 ✅。

### Android（路线 B 参考实现）

真接通（代码级确认）：
- `color_grade`：WARM / COOL / VINTAGE / CINEMATIC / TEAL_ORANGE
- `transform_geometry`：ROTATE / SCALE / CROP
- `edit_video`：TRIM / CONCAT
- `arrange_timeline`、`export`：由序列构建层处理

诚实抛异常（未实现）：
- `add_overlay`、`composite`、`edit_image`(全部)、`transform_geometry(PERSPECTIVE)`、`edit_video(SPEED)`、`edit_video(REVERSE)`

另有 `lumeri-packaging-next/android` 的 alpha.18 走的是另一条路（内嵌 Python Agent loop + 27 工具），与路线 B 的原生 verb executor **是两套东西**，需 Acrab 决定保留哪条或如何合并。

### iOS

2026-08-07 更新：`~/Lumeri Video` 已有通过真实媒体输出断言的
`LumeriNativeMediaExecutor`，支持 arrange、TRIM、CONCAT、CROP、ROTATE、
SCALE 与 P720/P1080 导出。它尚未接入产品工作区；带登录的测试壳与包含本地
执行器的普通构建仍是互斥路径，因此还不能称为可交付的路线 B App。

### 桌面

Mac / Windows / Linux：Python + ffmpeg + Blender，功能完整，公开 Windows 源码已可直接跑 `127.0.0.1:7788`。**这条已经是真 local。**

---

## 4. iOS 技术选型：为什么必须纯原生

### 三条候选路线

| | 做法 | 判定 |
|---|---|---|
| **B1 纯原生** | AVFoundation + Core Image/Metal 重写全部 verb | ✅ **采用** |
| B2 嵌入式 | 内嵌 Python 解释器 + 链接 ffmpeg 静态库 | ❌ 否决 |
| B3 远程壳 | 连 Mac（现状） | ❌ 已证明是债 |

### 为什么否决 B2

1. **ffmpeg 命令行根本用不了**：iOS 沙箱禁止 `fork`/`exec` 外部二进制。现有 Python 工具全部通过命令行调用 ffmpeg，即使嵌入了 Python 解释器也无法执行。
2. **ffmpeg 作为静态库虽可行但代价高**：`ffmpeg-kit` 已于 2025 年初归档停止维护；GPL 组件需按 LGPL 配置构建并处理授权义务；包体显著增大；且仍需重写所有调用层。
3. **Blender 无解**：不可能上 iOS。
4. 结论：B2 省不掉重写，只换来一堆授权与维护负担。

### B1 可用的系统能力

- `AVMutableComposition`：剪辑、拼接、变速、多轨
- `AVVideoComposition` + `AVVideoCompositing`：合成、转场、自定义渲染
- `Core Image` / `Metal`：调色、模糊、透视、自定义 shader
- `AVAssetExportSession` / `AVAssetWriter`：导出（走 VideoToolbox 硬件编码）
- `AVAudioEngine` / `AVAudioUnitTimePitch`：音频混音、变速不变调
- `AVVideoCompositionCoreAnimationTool` + `CALayer`/`Core Text`：文字与图形叠层
- `AVAssetImageGenerator`：抽帧

**11 个 verb 全部有对应原生 API，没有一个需要外部依赖。**

---

## 5. iOS 逐 verb 实现映射

| Verb / Action | iOS 实现路径 | 难度 | 备注 |
|---|---|---|---|
| `arrange_timeline` | `AVMutableComposition.insertTimeRange` 逐 track 排布 | 低 | 地基，最先做 |
| `export` | `AVAssetExportSession`，或 `AVAssetWriter` 精控码率 | 低 | P720/P1080/P2160 |
| `edit_video(TRIM)` | `insertTimeRange` 的 in/out point | 低 | |
| `edit_video(CONCAT)` | 连续 `insertTimeRange` | 低 | |
| `transform_geometry(CROP/ROTATE/SCALE)` | `AVMutableVideoCompositionLayerInstruction.setTransform` + `renderSize` | 低 | CROP 需同时改 renderSize |
| `color_grade` | `AVVideoComposition(asset:applyingCIFiltersWithHandler:)`，6 个 preset 各自 CIFilter 链 | 中 | 需与 Android 观感对齐 |
| `mix_audio` | `AVMutableAudioMix` + `setVolumeRamp` 做淡入淡出 | 中 | Android 侧同样缺，可同批做 |
| `extract_frame` | `AVAssetImageGenerator.copyCGImage` | 低 | |
| `analyze_media` | `AVAsset` 的 `tracks`/`formatDescriptions` 读元数据 | 低 | |
| `edit_image(CROP/ROTATE/RESIZE)` | Core Image / `CGContext` | 低 | |
| `edit_image(BLUR/DENOISE)` | `CIGaussianBlur` / `CINoiseReduction` | 低 | |
| `add_overlay(TEXT/SUBTITLE)` | `CATextLayer` + `AVVideoCompositionCoreAnimationTool` | 中 | 中日韩字体与换行需实测 |
| `add_overlay(IMAGE)` | `CALayer` + 同上 | 中 | |
| `composite` | 自定义 `AVVideoCompositing` 或 CIFilter 混合模式 | 高 | ALPHA/SCREEN/MULTIPLY/BLEND |
| `edit_video(SPEED)` | `scaleTimeRange` + `AVAudioUnitTimePitch` 保持音高 | 高 | 音视频同步是难点 |
| `edit_video(REVERSE)` | 逐帧反向解码后用 `AVAssetWriter` 重写 | 高 | 内存与耗时需分段处理 |
| `transform_geometry(PERSPECTIVE)` | `CIPerspectiveTransform` 或 Metal shader | 高 | |

---

## 6. 分期里程碑

### M0 — 审计与对齐（先做，不写新功能）
1. 逐文件核对 Android `EffectFactory` / `VerbExecutor` 真实实现，**重写 `verb-plan-v1.md` 的实现状态章节**。
2. 决定 `lumeri-packaging-next/android`（内嵌 Python 路线）与 `lumeri-android`（原生 verb 路线）的取舍。
3. 产出真实的跨端能力矩阵。

### M1 — iOS 地基
`arrange_timeline` + `edit_video(TRIM/CONCAT)` + `export` + `transform_geometry(CROP/ROTATE/SCALE)`
**出口标准**：iPhone 上不连任何外部服务，从两段本地视频产出一个真实 mp4。

### M2 — 观感层
`color_grade`（6 preset）+ `mix_audio`（含音量/淡入淡出）+ `extract_frame` + `analyze_media` + `edit_image`
**出口标准**：与 Android 同 preset 的输出帧做色彩差异比对，落在约定容差内。

### M3 — 叠层
`add_overlay`（TEXT/SUBTITLE/IMAGE），含中日韩字体、换行、安全区
**出口标准**：字幕在 9:16 与 16:9 下均不出安全区、不裁字。

### M4 — 高难度
`edit_video(SPEED/REVERSE)` + `composite` + `transform_geometry(PERSPECTIVE)`
**出口标准**：见第 7 节的逐项断言。

### M5 — Brain 上端
端上模型或 BYOK 直连，自然语言 → LumeriPlan，App 不持 owner 密钥。

### M6 — 拆除远程壳
iOS 端上链路验收通过后，停用 `:7829` 网关、classmates Tunnel、加密卷、GCP 入口计划。

---

## 7. 验收标准（防造假，最重要的一节）

**背景**：2026-06-28 已发生过 codex 把多个 verb 静默 no-op 却报告成功（SPEED 输出原速视频却返回 success）。**「返回成功」不构成任何证据。**

### 铁律

1. **要么真接通，要么抛异常。** 禁止静默 no-op、禁止返回未修改的输入、禁止用「已排入队列」搪塞。
2. **每个 verb 必须有输出级断言**，断言对象是产出的媒体文件本身，不是函数返回值。
3. **契约文档的实现状态必须与代码在同一次提交内同步。**

### 逐项断言要求

| Verb | 必须断言的事实 |
|---|---|
| `edit_video(SPEED)` | 输出时长 ≈ 输入时长 ÷ speed（容差 ±1 帧）；音频音高未随速度漂移；音视频同步偏移 < 40ms |
| `edit_video(REVERSE)` | 输出首帧 ≈ 输入末帧、输出末帧 ≈ 输入首帧（像素相似度） |
| `edit_video(TRIM)` | 输出时长 = out − in；首帧内容 = 输入在 in 处的帧 |
| `color_grade` | 采样帧 RGB 直方图按 preset 方向偏移（WARM 暖向、COOL 冷向…）；NEUTRAL 必须与输入逐像素一致 |
| `transform_geometry(CROP)` | 输出分辨率符合裁剪比例；边缘像素来自裁剪区内 |
| `transform_geometry(ROTATE)` | 输出宽高按角度互换；角落像素位置正确 |
| `mix_audio` | 淡入段起点振幅 ≈ 0 并单调上升；音量缩放后 RMS 比值符合设定 |
| `add_overlay(TEXT)` | 指定时间区间内叠层区域像素发生变化，区间外不变；CJK 字符未缺字/未溢出安全区 |
| `composite` | 各 blend mode 的输出像素符合该模式的数学定义 |
| `export` | 分辨率、编码格式、音轨存在性与请求一致 |

### 交付要求

- 每个 verb 至少一个端到端测试：**真实素材进 → 真实文件出 → 对输出文件断言**。
- 测试素材固定、可重复；输出断言使用数值容差，不用人眼判断。
- 不允许用 mock 的 AVFoundation 替代真实渲染做通过证明。

---

## 8. Acrab 于 2026-08-07 补充拍板

1. iOS 主线立即继续，先完成新的本地路线 B IPA，验收后才安装到 iPad；旧远程壳 IPA 不得充当交付物。
2. iPhone 使用三栏悬浮胶囊信息架构；iPad 不套 iPhone 壳，按 Mac 工作区与功能对齐。
3. Android 手机对应 iPhone，Android 平板对应 iPad/Mac，交互视觉遵循 Material Design。
4. 两套 Android 不再作为两个产品长期并行：冻结 `LumeriPlan` 与端上 Media3 执行器是唯一媒体内核；Mac 的 JavaScript 能安全复用就共享，平台能力不能复用时由原生适配器实现。
5. Skill 生态、账号、设置与创作能力以 Mac 为基线同步；跨端发布门槛见 `~/Code/lumeri-android/docs/contract/mobile-parity-v1.md`。
6. 远程壳本轮冻结，不继续扩展；待新本地链路验收后再进入 M6 拆除，拆除仍需单独执行与验证。

---

## 9. 明确不要做的事

- ❌ 不要为了赶进度再走「端当壳、算力在别处」的捷径——那是本文档存在的原因。
- ❌ 不要在 iOS 上尝试 exec ffmpeg 二进制（沙箱禁止），也不要为此引入已停维护的 ffmpeg-kit。
- ❌ 不要在契约文档里标 ✅ 而代码抛异常。
- ❌ 不要把素材、项目、时间线上传到云端——违反 local-first 边界决策。
- ❌ 不要用「返回成功」当作 verb 已实现的证据。

---

## 10. 相关文件索引

| 内容 | 路径 |
|---|---|
| 冻结契约（权威） | `~/Code/lumeri-android/docs/contract/verb-plan-v1.md` |
| Android 效果工厂（真实状态在这） | `~/Code/lumeri-android/app/src/main/java/com/xiehaibo/lumeri/engine/exec/EffectFactory.kt` |
| Android 执行器 | 同目录 `VerbExecutor.kt` |
| iOS 工程（当前只有远程壳） | `~/Lumeri Video` |
| Android 另一套（内嵌 Python） | `~/Code/lumeri-packaging-next/android` |
| 桌面实现（已 local） | `/Volumes/Extreme SSD/lumeri`、`~/Code/lumeri-reality-production-debug` |
| 任务卡 | `~/.agents/shared-agent-loop/QUEUE.md` → `shared-2026-06-28-lumeri-mobile-pathB` |
