# Claude — 05 risks (并行版)

> 与 `05-risks.md` 并行。
> Codex 的 R1 (静默错误) / R2 (fix-loop 烧 token) / R3 (sandbox-exec 废弃) 都是真风险,我都同意。
> 本文不重列技术风险,聚焦 Codex **避开没说** 的 strategic 风险。

## 战略风险 S1 — `creative_sandbox_*` 和 `runtime_vnext` 已经做了 v4 build,且基本没人在用

事实回顾:
- `gemia/creative_sandbox.py` 670 行、`gemia/creative_sandbox_runner.py` 489 行 — v2 时代为 "hidden Creative Dev Sandbox" 写的完整 sandbox 系统
- `lumerai/sandbox.py` + `lumerai/runtime.py` + `gemia/runtime_vnext.py` — 又一套 sandbox + agent loop + script generation
- 后者挂在 `/next` UI,MEMORY.md round-1 到 round-53 的 lesson 全是这套在跑

**`/next` 已经是一个 v4 build 形态的 product** —— Gemini 生成 Python → sandbox 执行 → TimelinePatch → 渲染 preview。但 v3 的产品决策(`shared-2026-05-27-lumeri-v3-a-hardening`)选了走 verb-based,不是走 script-based。`/next` 在 5 月反复 lesson + bug fix,**没看到任何 "lumeri-vnext 上线给真实用户用" 的 daily log entry**。

**真问题:** v4 build 想再做一遍 vNext 已经做的事(可能更好的 dispatcher 形态),但 **vNext 没成功的根因没被诊断**。是因为:
- (a) UI 没暴露给用户 → 工程问题,v4 可解
- (b) 模型写的 script 质量太低用户用不下去 → v4 同样会遇上
- (c) 产品定位错了,用户根本不想要 "AI 写代码做视频" → v4 也救不了

**没有 (b) 和 (c) 的明确证据排除前,v4 build 是在 vNext 的同一个 unknown 上再花 4 周。**

Codex doc 5 末尾"C 段"提了 saved-skill marketplace 的疑问,但 **没把 vNext 的存在与 v4 build 立项的合理性挂钩**。这是 Codex doc 5 最大的盲区。

## 战略风险 S2 — 加 verb 是 ratchet,只增不减

v3 当前 15 verb,5 个实现 10 个 stub。加 build = 16。

**verb 加进 system prompt 后基本不可能减。** 减 verb = 旧 saved session / saved skill 的 prompt 失效 = 用户报错。

具体到 build:一旦 schema 包含 `script: str` 字段,模型每次都会想"要不要写脚本",且没法回滚。v3 当前 5 verb 实现 + 10 stub 是个 **honest unfinished state** —— 用户看 stub 报错知道"还没做"。加 build 之后是 **honest but irreversible**。

**Codex doc 5 R2 mitigation 说 "prompt 主动 discourage build"**。这条建议依赖 prompt engineering 长期纪律,工程上是脆弱的。**更稳的 mitigation: build 默认关,用户在 UI 上显式 enable**(像 GPT-4 code interpreter 的开关)。这件事 Codex doc 3 / doc 5 都没提。

## 战略风险 S3 — token 经济学 vs Acrab 的现金流

Codex doc 5 R2 算了 $0.30 × 5 stuck loops × 10 user × week = $15/week。

**$15/week 是单 user 估算。** Lumeri 如果有 100 active user(GitHub `Acrabxie/lumeri` 当前 fork/star 数 + 实际安装数,无数据),每周 $1500。年 $78,000。**这不是技术风险,是商业风险。**

更要命的:GPT/Gemini API 价格的不确定性。如果 Gemini 3.5 Pro 涨价 30%,build 成本同步涨 30%,直接进 Acrab 口袋。**v4 build 是把 user delight 和 model API pricing 死绑** —— v3 verb 因为 token 短小,价格波动影响小,build 完全反过来。

**Mitigation 思路(Codex 没提的):**
- build script 缓存:同 script_hash + 同 input_assets 必返回相同 asset,跳过 sandbox spawn。~4 小时实现,token 节省可能 30%+ 在 saved-skill 场景。
- 用户预算门:每个 session 一个 hard cap,超了 build verb disable。
- 给 saved-build 优先级:用户用过的 saved template 不重新 LLM 生成,直接执行。这事 doc 4 §3 提了,但 Codex doc 5 没纳入经济学。

## 战略风险 S4 — 安全 vs 透明的取舍,用户不一定理解

Codex doc 5 H4 提了 Tauri entitlements。我加一条 **用户认知** 风险:

如果 v4 build 在 Lumeri.app 内 spawn sandbox-exec,**macOS Gatekeeper / TCC 会向用户弹窗**问"是否允许 Lumeri 控制其他进程"。第一次弹窗 + 用户随便点拒绝 = sandbox 无法工作 = 模型写的代码全 fail = 用户体验崩。

这个不是技术 bug,是 **OS UX 与 v4 build 流程的耦合**。Gatekeeper 弹窗时机不可预测(可能首次启动、可能某次升级、可能从未弹),且用户教育成本高。

Mitigation 难 —— Apple 这套 UX 是给 user safety 设计的,不是给 dev 调试。Lumeri 能做的:
- 在 Lumeri 首次启动时显式触发一次 sandbox-exec 调用,弹窗一次性教育用户
- 在 build verb fail 时,error message 明确写 "可能是 Lumeri 没有 sandbox 权限,见设置 → 隐私"

**~4 小时实现 + 持续的 user-support 成本。**

## 战略风险 S5 — v4 ship 之后,v3 verb 系统的命运

v3 当前 5 verb 实现 + 10 stub。Acrab 原计划是逐步把 10 stub 实现。

**v4 build verb 一旦上线,10 stub 的实现优先级会自然下降** —— 因为模型用 build 可以 cover stub 的能力。但这会导致:
- 长期看 v3 vocabulary 停滞在 5 个 high-level verb
- 模型 prompt 复杂度上升(选 high-level verb 还是 build,决策不明确)
- 用户体验差异化下降(每个任务都是 build,无可见性)

**这是 product strategy 题,不是工程题。** Acrab 必须明确:v4 build 是 v3 verb 体系的 **永久补充(stub 继续实现)** 还是 **替代路径(stub 不实现了)**?Codex doc 3 假设前者,但 doc 5 没 challenge 这个假设。

我的判断:如果不明确"v3 verb 集合永远不超过 30 个,build 是溢出兜底",那 v3 + v4 双轨长期是技术债。

## Codex 没说但我担心的事

**A. Lumeri 1.0 还没 ship,v3-A 是 ship readiness 工作。** MEMORY.md 显示 v3-A.M3 通过了真实创作 round trip,但 `next_action` 仍是 "implement stub verbs / 加 visible 入口"。**v3 还没真正给用户用过,就要规划 v4** —— 顺序可能不对。

**B. 单点架构师风险。** v3 / vNext / creative_sandbox 三套系统都由不同时期的不同 agent 推动(Codex/Antigravity/Claude)。MEMORY.md 里能看到多次"X 写的报告其实不准,Codex 修了一遍"。**v4 如果再加一层但 v3 / vNext / creative_sandbox 的归并没做,下一个 agent 接手会更乱**。

**C. 测试覆盖反映的真实优先级。** `tests/test_v3_infra_regressions.py` 14 passed(v3-A 收尾),`tests/test_creative_sandbox_*.py` 14 passed(creative_sandbox 验证),`tests/test_lumerai_runtime_kernel.py` 18 passed (runtime_vnext)。**三套系统都有大约同等的测试覆盖,但都没成为主力 product**。这本身就是个信号:架构没收敛。

## 决断:如果是我做这个项目,我会怎么排序

1. **先不做 v4 build。** 把 v3 的 10 stub 实现 + ship Lumeri 1.0 + 跑 1 个月真实 user telemetry。
2. **观察 telemetry:** 用户的 prompt 里有多少需求被 15 verb cover 不了。如果 < 10%,v4 build 是浪费;如果 > 30%,启动 v4 优先级最高。
3. **如果决定做 v4 build,先做归并:** `creative_sandbox_*` 和 `lumerai/sandbox.py` 合并成一套,`/next` UI 要么转生为 `/v3` 的开发者模式,要么 retire。**做这个归并比加 build 更紧迫**。
4. **build verb 不要从 stub 状态启动,先有 `/v4-experimental` 内部入口。** 跑 2 周内部测试再决定接入主 UI。
5. **预算门 + script 缓存从 day-1 上线**,不要事后补。

Codex doc 5 末尾说 "I would not start v4 until..."—— 我加强这条:**不只是 demo 没找到的问题,vNext 已存在但没成为主力,这件事本身就是个 negative signal**。

---

*基于 2026-05-28 commit `221b501` 和当前工作区的 v3-A 收尾改动。Strategic risk 部分来自 MEMORY.md 整体阅读 + QUEUE.md task 排序的判断,非单一代码证据。*
