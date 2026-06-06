# Claude — 03 loop integration (并行版)

> 与 `03-loop-integration.md` 并行。
> Codex 推荐 verb 名 `build`,推荐 `gemia/tools/build.py` 新 dispatcher,估 ~22-31 小时。这条主线我都同意。
> 本文不重做集成路径,聚焦 Codex 没回答的 3 个 deeper question。

## 1. `runtime_vnext.py` 已经在做 build,只是不在 v3 agent_loop 里

Codex doc 3 一上来就讨论 "execute_skill vs compose vs run_script" 命名,但 **没提 `gemia/runtime_vnext.py` 已经是一个完整的 model→Gemini→Python script→sandbox→TimelinePatch→preview 循环**,只是挂在 `/next` UI 不是 `/v3`。

证据:
- `runtime_vnext.py` ~600 行,在 MEMORY.md `shared-2026-05-17-*` 多条 lesson 里反复出现
- 流程是:用户 prompt → Gemini 生成 Python script (使用 `import lumerai as lm`) → `lumerai.sandbox.execute_script()` → 拿到 TimelinePatch → `lumerai.patches.apply_timeline_patches()` 改 project state → 渲染 preview
- 已经在 `/next` 上跑通了真实创作任务(MEMORY.md 列了一堆 round-NN 视频回归)

**v4 不是从零做这件事 —— 是把 `runtime_vnext.py` 的能力,以 agent_loop_v3 兼容的形态,搬进 v3 verb 体系。** Codex doc 3 把它当全新功能估了 22–31 小时,但实际:

| 工作 | 不复用 runtime_vnext (Codex 估) | 复用 runtime_vnext |
|---|---|---|
| 模型生成脚本的 prompt | 写新的 | 已存在,见 `runtime_vnext._build_script_prompt` (实际看代码确认) |
| 调用 sandbox 跑脚本 | 写新的 | `lumerai.sandbox.execute_script()` |
| 解析 stdout 的 TimelinePatch | 写新的 | `lumerai.sandbox._parse_patch_stdout` |
| 改 project state | 不需要 (v3 用 asset registry,不是 project state) | `lumerai.patches.apply_timeline_patches` (但 v3 不用) |
| **关键差异** | v3 用 asset_id,vNext 用 project_state.timeline.clips | **这个 mapping 才是 v4 真工作** |

**v4 真工作量 = `runtime_vnext` 现有能力 + asset_id ↔ clip_id 映射 + v3 SSE 输出适配。** Codex 的 22-31 小时估算高估了 —— 因为他假设从零写 dispatcher + sandbox 集成。真实估算可能 **12–18 小时**。

## 2. 错误回路的硬问题:traceback 不是 self-evident

Codex doc 3 说错误回路用现有 `tool_exec_error` 机制就行,模型看 traceback 后修代码。这在 happy path 里对,但实测有两类 traceback 模型修不动:

**类型 A:深栈 dtype/shape 错误**
```
File ".../numpy/core/_methods.py", line 47, in _amax
    return umr_maximum(a, axis, None, out, keepdims, initial, where)
ValueError: zero-size array to reduction operation maximum which has no identity
```
模型看不到这是 "前面某个 clip_trim 返回了空数组"。会去改一个不相关的参数。

**类型 B:host-side 校验失败**
```
SandboxViolation: Blocked import at line 5: os
```
明确,但模型可能反复尝试不同方式绕过(`import sys; sys.modules['os']` 等),直到 `max_tool_steps` 耗光。MEMORY.md 里 `shared-2026-05-17-lumeri-vnext-prompt-only-mg-syntax-fallback` 这类 lesson 就是 **重复语法错误吃了一轮又一轮迭代** 的真实记录。

Codex doc 5 R2 提了 fix-loop 烧 token,但 doc 3 假设错误回路"无需新机制"。两份文档在这件事上又没对齐 —— **错误分类 + 模型 prompt 指引"什么 traceback 是值得重试的、什么是必须停下问用户的"才是 v4 错误回路的真正工作**。

具体建议:`gemia/tools/build.py` 的 dispatcher 在 wrap traceback 时,**加上一个 host-side 分类标签**:
- `error_class: "user_input"` — 模型该停下问用户
- `error_class: "script_bug"` — 模型该改脚本
- `error_class: "sandbox_violation"` — 模型该用不同 API,不是绕过
- `error_class: "primitive_failure"` — 可能 input 有问题,需要 analyze_media 后再决定

这件事 ~3 小时额外,但对 R2 缓解显著。

## 3. tool_result 形状里漏掉的 provenance

Codex doc 3 提了 tool_result 含 `script`/`stdout_tail`/`stderr_tail`。但 **漏了 lumerai 已经在做的 provenance**:

```python
# lumerai/runtime.py:_provenance()
{
    "session_id": ctx.session_id,
    "script_hash": ctx.script_hash,
    "script_line": line_no,
    "script_snippet": snippet,        # 那一行的源码
    "timestamp": ...,
    "ai_model": ctx.ai_model,
}
```

**每个 TimelinePatch op 都带这个 provenance** —— 知道这个 patch 是 ai 写的第几行代码产生的。

v4 build 如果丢掉 provenance,以下功能没法做:
- 用户问"这个调色怎么来的" → 答不出
- 模型在下一轮要 inspect 自己上一轮做了什么 → 看不到行号
- saved-skill 复用 → 不知道哪段代码出来的哪个 asset

Codex doc 4 提到 saved-skill 适配,但 doc 3 的 tool_result schema 没把 provenance 算进去。**应当在 schema 里把 `provenance: dict` 标成 reserved key,dispatcher 必须填**。

## 4. 我同意 Codex 的部分,显式列出来不重做

- verb 名 `build`(不是 execute_skill) — 同意,理由相同
- 错误回路用 `tool_exec_error` 机制 — 同意但需补错误分类(见 §2)
- 不要默认 thumbnail_for_next_message — 同意,模型应显式 `analyze_media`
- Token 估算 1k–15k per build — 同意数量级
- max_tool_steps=8 默认 — 同意起步值
- pre-flight AST check — 同意,30 行代码,值

## 重新估算 v4 build 工作量

| 任务 | Codex (从零) | 我 (复用 vNext) |
|---|---|---|
| lumeri façade 选 + 暴露 | 4-6h | 1-2h(扩 lumerai/__init__ 到 50–100 函数) |
| `gemia/tools/build.py` dispatcher | 8-12h | 4-6h(薄 shim 到 `lumerai.sandbox.execute_script`) |
| 错误分类(我加的) | — | 3h |
| Pre-flight AST | 2-3h | 2-3h |
| Schema + 注册 | 1h | 1h |
| asset_id ↔ clip_id mapping(我加的) | — | 3-4h |
| Provenance 透传到 tool_result(我加的) | — | 1-2h |
| Prompt 更新 | 2-3h | 2-3h |
| Smoke + integration test | 5-6h | 4-5h |
| **总计** | **22-31h** | **21-29h** |

**净工时差不多。** 复用 vNext 省掉了 sandbox 写新的工作,但加上了错误分类和 mapping。两者总和近似。**关键差是:复用 vNext 不会留两套 sandbox。** 见 claude-02 §1。

---

*验证:`runtime_vnext.py`, `lumerai/runtime.py:_provenance`, `lumerai/sandbox.py`, MEMORY.md round-NN lessons。*
