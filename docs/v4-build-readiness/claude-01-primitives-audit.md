# Claude — 01 primitives audit (并行版)

> 日期: 2026-05-28
> 与 `01-primitives-audit.md` 并行,刻意从不同维度切入。
> 已读 Codex 版,因此本文不重复:813 vs 88 的真实数、91% 接口纯净、4 文件网络、14 文件 tempfile。这些 Codex 已做完。
> 本文聚焦 Codex 没展开但对 v4 决策更要命的 4 件事。

## 1. lumerai/__init__.py 已经是一个 8-primitive façade,不是空白

`lumerai/__init__.py:11-25` **已经在做 v4 想做的事**:

```python
from .runtime import (
    clip_color_grade,
    clip_load,
    clip_trim,
    configure_runtime,
    hyperframes_render,
    timeline_insert,
    timeline_replace,
    timeline_state,
)
```

8 个函数。设计语义是 **editor-domain**(clip, timeline, color_grade),不是 primitive-domain(`image_blur`, `video_xfade`)。

这意味 v4 build 在签字之前要明确选择两条路里的一条 —

**路线 A:扩 lumerai/ 到 50–100 个 editor-domain API**。模型在 sandbox 里写的代码长这样:
```python
import lumerai as lm
clip = lm.clip_load("v_001")
trimmed = lm.clip_trim(clip, start=2.0, end=5.0)
graded = lm.clip_color_grade(trimmed, preset="warm")
lm.timeline_replace("v_001", graded)
```

**路线 B:把 gemia.{picture,audio,video} 里 700+ 个 path-only primitive 直接 re-export 给 sandbox**。模型代码:
```python
from gemia.video.effects import video_sepia, video_zoom_in, video_concat_crossfade
video_sepia("v_001.mp4", "step1.mp4")
video_zoom_in("step1.mp4", "step2.mp4", zoom=1.5)
```

Codex 的 doc 1 假定 v4 走路线 B + 全量 lumeri/ 子集(`from gemia.X import *`),Codex 的 doc 3 假定 v4 走 `import lumeri` 一行式。两份文档在这点没对齐。

**真实代码现状站在路线 A 这边:**
- `lumerai/runtime.py` 全 460 行都在维护 clip-dict / TimelinePatch 语义,不是 primitive 透传
- `lumerai/sandbox.py:14` `ALLOWED_IMPORT_ROOTS = {"lumerai", "json", "math", "re", "datetime", "pathlib", "typing"}` —— 没有 `gemia`。换句话说 **现有 sandbox 故意不让脚本直接 import `gemia.*`**
- `lumerai/_sandbox_child.py` 跑前 `import lumerai`(只导 lumerai,不导 gemia)

**结论:** v4 不是 "选路线",是 "确认走路线 A,把 lumerai/ 从 8 个扩到 N 个"。N 怎么定才是 Opus 的事。

## 2. 815 primitive vs 15 verb,抽象层断崖

v3 的 15 verb(`generate_image / edit_video / color_grade / ...`)是 **AI-friendly 高层动作**;813 个 primitive 是 **engineer-friendly 低层操作**。中间不存在过渡层。

v4 build 真正补的是 **中层**:模型 "用 4–7 个 primitive 拼一个 verb 不能覆盖的动作"。

但是 Codex 的 doc 3 把 `build` 描述成 "the 16th verb"。这框架不对 —— `build` 不是与 15 verb 平级的另一个 verb,它是 **verb 的元层级**(verb-of-composing-verbs)。具体差异:

| 维度 | 15 verb | build |
|---|---|---|
| 输入 schema | 结构化 args (asset_id + 配置) | 自由文本 (Python script) |
| 副作用 | 1 个新 asset | N 个文件 + N 个 asset + stdout/stderr |
| 错误模式 | 已知有限集 | 任意 Python traceback |
| 成本 | 50–150 token | 1000–15000 token |
| 调用方式 | 模型从 schema 选 | 模型从零写 |

把 `build` 当 "第 16 verb" 注册到同一个 DISPATCHER 在工程上是对的(代码简洁),但 **模型的决策模型是不一样的** —— 不应该指望同一个 system prompt 段落能 handle 这两种 verb 的"什么时候用"。需要至少两段 prompt 说明,可能 Plan-B-style 视觉反馈也要为 build 单独设计。

## 3. _CORE_FQNS 47 + 23 SKILL.md 已经是 curated subset 雏形

Codex 的 doc 1 提到 `_CORE_FQNS` 47 个 (`gemia/registry.py:104`),Codex 的 doc 4 提到 23 个 SKILL.md。但 **没人把这两个对齐**。

```
$ grep -A 200 "_CORE_FQNS" gemia/registry.py | head -50
$ ls gemia/ai/skills/*/SKILL.md | wc -l
23
```

**`_CORE_FQNS` 47 个 + 23 个 SKILL.md 各自声明的 primitives** 合并去重,**很可能就是 v4 应该首批暴露的 100 个左右 primitive**(Codex 在 doc 4 末尾隐约说了同样的话,但没把 _CORE_FQNS 这条也算进去)。

具体值得查的:`_CORE_FQNS` 是谁挑的(commit history)、SKILL.md `primitives:` 字段每个 skill 平均几个、两者重叠率多少。这是 30 分钟的考古活,做完能给 Opus 一个真实候选 subset,不必凭空挑 100 个。

## 4. 接口纯净的暗坑:91% pure 但 ffmpeg 路径污染

Codex 数出 91% primitive 接口纯净。但 "纯净" 不等于 "sandbox 内能跑"。

```bash
$ grep -l "subprocess.*\(ffmpeg\|ffprobe\)" gemia/video/*.py gemia/picture/*.py gemia/audio/*.py | wc -l
35
```

35 个 primitive 文件依赖 `subprocess.run(["ffmpeg", ...])`。sandbox 的 `(allow process-exec ...)` 必须写对 ffmpeg 真实路径 —— `/opt/homebrew/bin/ffmpeg`(Apple Silicon)、`/usr/local/bin/ffmpeg`(Intel Mac)、用户自编译路径都可能。

`gemia/tools/_ffmpeg.py` 应该是统一入口,看下:

```bash
$ head -20 gemia/tools/_ffmpeg.py
```

如果有 `_locate_ffmpeg()`,那 sandbox profile 就能从中读;如果各文件自己 `subprocess.run(["ffmpeg", ...])` PATH lookup,sandbox profile 写死路径就会有"在我机器上能跑"的隐患。这件事 Codex 的 doc 5 H3 提了但没量化 —— 35 个文件直接 ffmpeg 是具体 attack surface。

## 5. 不需要重新做的事(给 Opus 阶段的 input)

Codex doc 1 完成了:
- 813 / 774 / 91.3% 这些主数据
- per-domain 分布表
- 内部 import 耦合图

我建议 Opus 阶段不要再花时间在这些上 —— 直接用 Codex 给的数。

Codex doc 1 没做但 Opus 应该做:
- 上面 §3 的 `_CORE_FQNS` ∪ SKILL.md.primitives 实际枚举
- §4 的 `gemia/tools/_ffmpeg.py` 真实结构
- 上面 §1 的路线 A vs B 的具体决策(写进 v4 spec 第一段)

---

*与 `01-primitives-audit.md` 互补;Codex 的数我已交叉验证,无异议。*
