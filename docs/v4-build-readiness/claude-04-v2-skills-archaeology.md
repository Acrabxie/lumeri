# Claude — 04 v2 skills archaeology (并行版)

> 与 `04-v2-skills-archaeology.md` 并行。
> Codex 区分了 Layer A (skills/SKILL.md) 和 Layer B (skill_store.py),结论:两者都不直接复用,但 SKILL.md 格式可作 v4 持久化基础。我同意主线。
> 本文聚焦 Codex 没分析的 **第三层** 和 **三层的真正复用价值排序**。

## 第三层:`_combos/` skill-pair compatibility manifests

Codex doc 4 提了 `_combos/` 目录存在,但没拆。这 4 个文件其实是 **v2 时代最接近 v4 build 的工件**:

```
gemia/ai/skills/_combos/
├── stock-media+timeline-ops.yaml
├── timeline-ops+color-grade.yaml
├── timeline-ops+html-graphics.yaml
└── transition+color-grade.yaml
```

每个 combo 是一个 **可执行的多步骤 plan template**:

```yaml
# gemia/ai/skills/_combos/timeline-ops+color-grade.yaml
id: timeline-ops+color-grade
trigger_skills: [timeline-ops, color-grade]
trigger_keywords_min: [调色]
plan_template:
  - id: step_1
    function: gemia.video.timeline.cut
    args:
      start_sec: 0
      end_sec: 5
    input: "$input"
  - id: step_2
    function: gemia.picture.color.color_grade
    args:
      preset: natural
    input: "$step_1"
    output: "$output"
```

**这不是 prompt routing,是 plan/script 模板。** `$input` / `$step_1` 这种引用,等于一段隐式的 dataflow DSL,接近 lumerai sandbox 里模型会写的 Python 代码,但用 YAML 表达。

**为什么这是 v4 最相关的考古发现:**

| 维度 | v2 SKILL.md (Codex 提的) | v2 _combos | v2 skill_store (Codex 提的) | v4 build (拟做) |
|---|---|---|---|---|
| 拆解粒度 | 单 skill 内 N 个 primitive | 2+ skill 跨域组合,固定步骤 | 完整成功任务的快照 | 模型按需任意组合 |
| 谁写 | 人手写 | 人手写 | 系统从成功任务自动 derive | LLM 实时写 |
| 持久化 | YAML | YAML | JSON | Python 脚本 |
| 复用方式 | router 选 → planner 用 | 关键词命中 → 直接执行 | name 加载 → 替换 $input | 模型 import + 调用 |
| 进化方向 | → curated lumerai/ subset | **→ "build template" 库** | (基本未投产) | → 自由写代码 |

**`_combos/` 是 "build template" 的真实原型。** v4 build 如果给用户 "save this build script as a template" 功能,formal model 就是把 `_combos/*.yaml` 的格式升级到 `*.py` —— 同样的 dataflow,但写成 Python 代码而不是 YAML。

**为什么只有 4 个 combo:** 人手写组合膨胀严重(C(23,2) = 253 个 skill pair,加 triplet 是 C(23,3) = 1771)。这正是 LLM 来写 build script 的核心动机 —— 模型按需 derive combo,不需要人手枚举。**`_combos/` 文件被冻结在 4 个就是 v2 这套 manifest-driven 方案天花板的证据。**

## skill_router.py 的"PROMPT_ONLY_FALLBACK_SKILLS"

`gemia/ai/skill_router.py:17`:
```python
CORE_FALLBACK_SKILLS = ["timeline-ops", "color-grade", "transition"]
PROMPT_ONLY_FALLBACK_SKILLS = ["generative", "ad-graphics", "stock-media"]
```

两个 fallback list 揭示了 v2 时代的 task type 分类:
- 有 input 媒体 → core (剪辑/调色/转场)
- 无 input 媒体(prompt-only) → generative/ad/stock

**v4 build 应继承这个分类**,因为模型自己想清楚自己在做哪类任务有助于选对 primitive 子集。具体说:`build` 的 schema 可以加一个 `task_class: enum["edit","prompt_only","stock_assembly","other"]` 字段,迫使模型先分类再写代码。

Codex doc 3 的 schema 没这个字段。加 3 行,价值在帮模型选错的频率降低。

## skill_telemetry.py — Codex 提了但没说价值

Codex doc 4 提了 `skill_telemetry.py` 是 SQLite 路由事件日志,标"router 的调参用"。

**这是 v4 build 在 prod 阶段必须有的等价物。** v4 build 的 telemetry 至少要记:
- 模型写的 script 的 SHA256 + token 长度
- attempt 编号(同一 user turn 第几次试)
- exit_code, primary error class (我在 claude-03 §2 提的)
- 用户最终是接受还是放弃(回到 prompt 重来)

skill_telemetry.py 的 schema 不能直接搬,但 **SQLite + WAL + 单文件** 这个选型可以直接搬。~200 行代码省下。

## Codex 漏的:`gemia/skill_store.py` 不只是 abandoned

Codex doc 4 说 skill_store 只被 server.py 和自己引用,基本未投产。**严格来说不完全对** —— `gemia/__main__.py` 也调用了:

```
$ grep -n skill_store gemia/__main__.py
883: from .skill_store import SkillStore
885:    store = SkillStore()
904: from .skill_store import SkillStore
906:    store = SkillStore()
919: from .skill_store import SkillStore
921:    store = SkillStore()
935: from .skill_store import SkillStore
937:    store = SkillStore()
```

4 个 CLI subcommand 在用。`python -m gemia skill save / list / load / apply`。所以 skill_store **是 CLI feature,UI 里没暴露**。

这对 v4 重要的地方:**已经有 CLI 让用户 `python -m gemia skill save` 保存模板**。v4 如果想搞 saved-build-template,可以扩这个现有 CLI 而不是新做。Codex doc 4 的 "6-8 小时适配 SKILL.md 格式" 估算应该按 "扩 `python -m gemia skill` 子命令" 算,可能能压到 3-5 小时。

## 三层 v2 工件的真实复用价值排序

我的判断:

1. **`_combos/` 的 plan_template 格式** —— 最直接的 v4 build template 原型,但需要从 YAML 升级到 Python。复用度:格式启发,代码不可用。
2. **23 个 SKILL.md 的 `primitives:` 字段** —— 这是 47 _CORE_FQNS + 813 全集中间的 curated 子集,直接是 v4 lumeri/ 暴露面候选。复用度:数据可直接用。
3. **`skill_yaml.py` lightweight loader** —— Codex 提了,确实可重用。零依赖。
4. **`skill_telemetry.py` SQLite 模式** —— 选型可搬,schema 需重写。
5. **`gemia/skill_store.py` save_from_task CLI** —— 可扩到 save_build,~3-5 小时。
6. **`skill_router.py` keyword routing** —— v4 不需要,弃。
7. **`skill_context.py` planner prompt 组装** —— v4 prompt 形态完全不同,弃。

Codex doc 4 关注 1 / 2 / 3,我加了 4 / 5。

## Bottom line

v2 skills 系统是 3 层(SKILL.md / _combos / skill_store),不是 Codex 说的 2 层。其中 **`_combos/` 这一层最接近 v4 build 的形态**,是 v4 saved-template feature 的真正参考。

v4 不应试图复活 v2 routing(那是错的路),应该把 `_combos/` 的 plan_template 升级成可执行 Python 脚本,把 skill_store 的 CLI 入口扩出 `save_build`。**这件事 6-10 小时**,而不是 Codex 说的 6-8 小时。

---

*验证:`gemia/ai/skills/_combos/*.yaml`, `gemia/ai/skill_router.py:17`, `gemia/__main__.py:883-937`。*
