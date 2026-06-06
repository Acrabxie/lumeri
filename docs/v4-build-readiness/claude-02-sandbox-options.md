# Claude — 02 sandbox options (并行版)

> 与 `02-sandbox-options.md` 并行。
> Codex 已比对 5 方案,我同意推荐 sandbox-exec。
> 本文不重做对比,聚焦 Codex 没拆透的 **两个 sandbox 已经同时存在** 的事实。

## 当前 repo 有两套 sandbox,这是真问题

```
gemia/creative_sandbox.py            670 行   workspace API (无执行)
gemia/creative_sandbox_runner.py     489 行   sandbox-exec + AST 校验 + ffmpeg/python
gemia/creative_sandbox_permissions.py 343 行  policy + AST 校验
lumerai/sandbox.py                   276 行   subprocess + RLIMIT + AST 校验
lumerai/_sandbox_child.py             18 行   sandbox 内 entrypoint
lumerai/runtime.py                   460 行   in-sandbox API (8 函数)
lumerai/patches.py                    72 行   TimelinePatch host-side 应用
```

**两套都是真的、都能跑、都有测试。** Codex doc 2 推荐"用 sandbox-exec, reuse `gemia/creative_sandbox_runner.py:293-313`", Codex doc 3 推荐"在 `gemia/tools/build.py` 写新 dispatcher"。这两条建议组合起来意味着 **会变成三套 sandbox** —— 没有人在 v4 spec 里说要废掉哪套。

### 两套的实质差异

| 维度 | `gemia/creative_sandbox_*` | `lumerai/sandbox.py` |
|---|---|---|
| 执行模型 | 任意 argv (`python script.py`, `ffmpeg ...`) | 只 `python -m lumerai._sandbox_child <script>` |
| 隔离机制 | sandbox-exec wrapper(macOS only,fallback 透明) | subprocess + `RLIMIT_CPU` + `RLIMIT_AS` + AST 白名单 |
| 工作目录 | `workspaces/<session_id>/{scripts,artifacts,previews,skills,logs}` | `temp/lumerai-sandbox/run-*` 临时目录 |
| Python import 白名单 | `csv, datetime, json, lumerai, math, pathlib, random, statistics, time` | `lumerai, json, math, re, datetime, pathlib, typing` |
| 网络 | profile `(deny network*)`,可 opt-in | 无 OS 层网络隔离(靠 AST 拒 `urllib/requests/http`) |
| 输出形态 | discovered artifacts + JSONL events | stdout 上的 TimelinePatch JSON |
| 调用方 | `server.py` `/run-skill` 旧路径 (灰色) | `gemia/runtime_vnext.py` 的 `/next` UI |
| 测试 | `tests/test_creative_sandbox_*.py` (14 tests) | `tests/test_lumerai_runtime_kernel.py` 系列 |

**这是两个产品在两个 UI 上分别做了一遍。** v4 build 如果在 v3 agent_loop 里加,就是第三遍。

### 为什么不应该有第三套

`agent_loop_v3.py` 是 host 进程,`gemia.tools.DISPATCHER` 是 host 内同进程 dispatcher。`build` verb 本质是 **agent_loop 进程内一个 dispatcher,内部 spawn 一个 sandboxed 子进程**。这与 `runtime_vnext.py` `/next` UI 通过 `lumerai.sandbox.execute_script()` spawn 子进程的形态是 **同一种**。

差异只在:
- v3 agent_loop 用 SSE + tool_call,不需要 TimelinePatch JSON parser(可以用结构化 dispatcher result)
- v3 没有 `runtime_vnext` 的 session/project_state 概念,assets 是 v3 自己的 registry

但 spawn-sandbox-子进程-拿-stdout-stderr-退出码 这个核心是 **可以共用** 的。

### 建议:claude-02 的硬主张

**v4 build 应该 reuse `lumerai/sandbox.py:execute_script()`,不写 `gemia/tools/build.py` 自己的新 sandbox 层。**

具体形态:
1. `gemia/tools/build.py` 是个薄 dispatcher,把 v3 tool_call 转换成 `execute_script(script=..., project_state=..., session_id=..., output_dir=...)` 调用
2. `lumerai/sandbox.py` 已经做了 AST 校验 + RLIMIT + tempdir + child env injection
3. `gemia/creative_sandbox_runner.py` 的 sandbox-exec wrapping **应该 lift 进 `lumerai/sandbox.py`**(目前 lumerai sandbox 没用 sandbox-exec,只靠 AST + RLIMIT,Codex doc 2 正确指出这"是花架子")
4. `gemia/creative_sandbox*.py` 是上一波"hidden coding env" 的遗产,**v4 应明确弃用或显式归到 v3 路径**,不要让它和 v4 build 并存

工作量加项:把 `creative_sandbox_runner._sandbox_command` (`:258-269`) 和 `_sandbox_profile` (`:293-313`) 移到 `lumerai/sandbox.py`,~4 小时。这件事 Codex doc 2 暗示了(建议 sandbox-exec)但 doc 3 没显式安排。

## 另一个 Codex 漏掉的方案:LXD on macOS 不可行,但 systemd-style sysextension?

macOS 26+ 引入 system extensions / DriverKit-style 隔离。**目前不是 sandbox 候选** —— DriverKit 是给驱动用的,不是给 user 进程的。提一下避免 Opus 阶段被这个名字误导。

**真正应该重新评估的:macOS 26.0 引入的 `sandbox-exec` 替代品 `App Management TCC`。** TCC 不能替代 sandbox-exec(粒度完全不同),但 Apple 可能在某次升级里把"非 entitled 进程不能 fork sandbox-exec"这条收紧。Codex doc 5 R3 提了 sandbox-exec 被废除的风险,但 **TCC tightening** 是更可能、更近、更细粒度的失败模式 —— 不是 binary 消失,是某次小升级里 `sandbox-exec` 调用前需要新的 entitlement。

监控点:每次 macOS Beta SDK release notes 里搜 "sandbox-exec" 和 "App Management"。

## AST 黑名单的真实价值(Codex 说是花架子,我同意但补一下)

Codex 说 `lumerai/sandbox.py` 的 AST 黑名单 "can be evaded (e.g. `__import__('os')`, `getattr(__builtins__, 'eval')(...)`)" —— 对的,但要补充:

**`lumerai/sandbox.py:42` `BLOCKED_NAMES = {"__builtins__", "__loader__", "__spec__", ...}`** 试图拦 `__builtins__` 名字,但只在 **`ast.Name` (Load) 节点** 检查 (`:74-76`)。这意味:
```python
# 能拦
__builtins__.eval("...")
# 拦不住 — __builtins__ 是表达式属性而不是裸 Name
type(open).__bases__[0].__subclasses__()
# 拦不住 — 字符串拼接绕过
import importlib; importlib.import_module("o" + "s")
# 但 import 已经被 ALLOWED_IMPORT_ROOTS 拒了,所以这个 specific 例子不行
```

**AST 黑名单的真实价值不是 security wall,是 ergonomic guard:** 防止模型不小心写出明显坏代码(`import os` typo),帮模型快速失败而不是在 sandbox 里出错。把它当 "linter for AI-generated code" 而不是 "security boundary"。

Security boundary 必须是 sandbox-exec (filesystem/network/process scope) + RLIMIT (resource scope)。两者结合才完整。

## 总结

- Codex doc 2 推荐 sandbox-exec — 同意,无补充
- **但** Codex 没说 v4 build 应该 reuse 已有的 `lumerai/sandbox.py`,而不是写 `gemia/tools/build.py` 自己的 sandbox 层 — 这是 v4 spec 必须明确的事
- 当前 repo 有 **两套 sandbox 并存**(`creative_sandbox_*` + `lumerai/sandbox`),v4 必须选一个归一,不能三套并存
- AST 黑名单是 linter,不是 wall;sandbox-exec 才是 wall
- 关注 macOS 26+ App Management TCC tightening,比 sandbox-exec 被废除更可能先来

---

*验证:`creative_sandbox_runner.py:258-313`, `lumerai/sandbox.py:14-50`, `runtime_vnext.py` 调用链。*
