# 08 — 异步架构分析:模型握调度权(方向 c)

> Date: 2026-05-30
> 焦点:论证方向 (c) — 把异步暴露给模型,模型自主决定何时 wait、何时
> 并行、何时去做别的事。(a)/(b) 简短记录作被否决备选。
>
> 前置依赖:doc 07 已确认 Veo / Lyria 走 LRO,8s 视频典型 60–180s 完成。
> 这是为什么"等不等待"必须做架构决策的根因。

---

## 1. 三方向对比 + 为何 (c) 符合 v3 精神

### (a) Host 阻塞 — 否决

模型调 `generate_video(prompt=...)`,host 进 dispatcher 后 `await asyncio.sleep(10)` 循环 poll,直到 LRO `done`,再返回 asset_id。**模型视角:就是一个慢 verb。**

| 问题 | 说明 |
|---|---|
| **整个 turn 阻塞 60–180s** | run_turn 不能完成,SSE 连接久挂,前端只能转圈;现有 `tool_exec_progress` 模型早期对 ffmpeg 的连续秒数进度的契约失效 |
| **session_manager 资源耗尽** | 当前 SessionManager 默认 `idle_timeout_sec` 假设 turn 是秒级。3 min/turn 直接撑爆并发能力 |
| **模型不能在等期间做别的** | 模型只能等待,不能并行 fire 另一个 Lyria job、不能边等边和用户对话 |
| **不可中断** | 用户改主意要 cancel,host 没机制传给 Veo cancel(Veo 也不一定支持 cancel,但至少 host 该能放弃 polling) |

被否决原因:**纯工程问题集合,且与 v3 "host doesn't synthesize state, model drives" 原则冲突 —— 是 host 在替模型决定"它要等"。**

### (b) Host 后台 poll,模型不知情 — 否决

模型调 `generate_video`,host 立刻返回 placeholder asset_id,在 background task 跑 poll。job 完成时 host 自动 emit 一个新 SSE event。**模型视角:verb 立刻成功,但 asset 几分钟后才能用。**

| 问题 | 说明 |
|---|---|
| **模型不知道 asset 还没好** | 下一个 turn 模型可能 `analyze_media(v_007)` 然后失败 — host 必须 magic-handle "asset 还在 pending" 这种情况,实现 messy |
| **谁决定等?** | 如果下一个 verb depend 这个 asset(例 `arrange_timeline([v_007, v_008])`),host 又得 silently 等。**Host 在替模型编排,违 v3 精神。** |
| **错误回路不清晰** | Veo 失败的 traceback 进哪一个 turn?进的太晚模型已经把话题转走;进的太早(强插)又是 host synthesize 行为 |
| **测试性差** | mock 一个 LRO 完成 callback 比 mock 一个 sync verb 复杂得多 |

被否决原因:**Host 替模型 magic 编排 = v2 那个错误的回归**。MEMORY.md 里 v2 的根问题就是 host 端 Python 假装在模型旁边narrative —— (b) 是另一种形态的同一错误。

### (c) Model-driven async — 推荐

模型调 `generate_video(prompt=...)`,host 立刻提交 LRO,返回 **job 句柄**:
```json
{
  "job_id": "veo_a1b2c3",
  "asset_id_pending": "v_007",
  "status": "submitted",
  "estimated_eta_sec": 120,
  "summary": "Submitted Veo 3.1 generation request: '...'. ETA ~120s."
}
```

模型现在有 3 个新工具:
- `check_job(job_id)` — 不阻塞,问一下当前状态
- `wait_for_job(job_id, max_wait_sec)` — 阻塞等(模型显式选择)
- 跨 turn 看 asset_registry,pending asset 显式标在那

**模型可以选择的:**
1. 提交完后告诉用户"我在生成视频,预计 2 分钟,你想做点别的吗?"
2. 同 turn 继续提交 Lyria audio 并行,然后 `wait_for_job` 等其中一个
3. 同 turn `wait_for_job(veo_id, max_wait_sec=180)` 等完后做下一步
4. 这一 turn 不等,等用户下次开口时再 `check_job`

**为什么 (c) 符合 v3 精神(直接引用 system_v3.md):**

> "You can call `analyze_media` to actually look at an asset you've
> produced... Use this when you want to check your work before
> committing to the next step."

`analyze_media` 已经是同一精神 —— host 不预先看,模型选择何时看。`check_job` / `wait_for_job` 是这个精神在异步空间的扩展。

> "If a tool fails, fix the root cause. Don't retry the same call;
> read the error, change what's wrong, then try again."

(c) 让模型看到 traceback(`job` failed 的具体原因),决定改 prompt 重提交还是降级路径。(a) 是把 traceback 包到 `tool_exec_error` 里,可以;(b) 是 host 在背景 narrate,模型无法读取。

> "One step at a time is fine. You don't have to plan a whole sequence
> ahead. Pick the first action you'd take, then react to its result."

(c) 让 react 的粒度从"等完了再 react"变成"提交完就 react"。模型决定 react 是 wait 还是 chat 还是 fire 下一个。

**核心区别:** (a)/(b) 把"是否等待"从模型手里拿走;(c) 还给模型。

---

## 2. agent_loop 需要新增什么

### 2.1 JobRegistry — 新增,与 AssetRegistry 平级

```python
# gemia/tools/_jobs.py (新文件,~200 行)

@dataclass
class JobRecord:
    job_id: str                       # "veo_a1b2c3"
    kind: str                         # "video" | "audio" | "image-async" | "build"
    provider: str                     # "ai_studio:veo-3.1-fast"
    operation_name: str               # LRO name returned by predictLongRunning
    pending_asset_id: str             # "v_007" — pre-allocated in AssetRegistry
    submitted_at: str
    estimated_eta_sec: float
    last_polled_at: str | None
    last_polled_status: str           # "submitted" | "queued" | "running" | "done" | "failed"
    final_path: Path | None           # set when done
    final_error: str | None           # set when failed
    summary: str                      # human-readable

class JobRegistry:
    def submit(self, *, kind, provider, op_name, pending_asset_id, eta, summary) -> JobRecord:
        ...
    def get(self, job_id) -> JobRecord: ...
    def list_pending(self) -> list[JobRecord]: ...
    def update_from_poll(self, job_id, status, *, final_path=None, error=None) -> JobRecord:
        ...
    def compact_text_for_prompt(self) -> str:
        """Render pending jobs into the system prompt next turn."""
```

放在 `ToolContext.jobs`(同 `ToolContext.registry`),agent loop 在 `__init__` 实例化,持久化到 session 目录(`sessions/<sid>/jobs.json`)— 跨 turn 必须存活。

### 2.2 AssetRegistry 扩 "pending" 概念 — 必改

```python
@dataclass
class AssetRecord:
    asset_id: str
    kind: str
    path: Path                        # may be a placeholder (e.g. ctx.output_dir / "v_007.pending")
    summary: str
    created_at: str
    lineage: tuple[str, ...] = ()
    status: str = "ready"             # NEW: "ready" | "pending" | "failed"
    job_id: str | None = None         # NEW: link to JobRegistry if pending
```

`AssetRegistry.compact_text()` 渲染时:
```
- v_007 [video pending] generating from 'cat dancing in snow' (job veo_a1b2c3, ETA ~120s)
```

这样模型在 system prompt 里就能看到"我提交过 v_007,还没好"。

**verb 收 pending asset 作 input 的策略:**
- `analyze_media(v_007)` 当 v_007 还 pending 时 → dispatcher 应直接 raise `ValueError("asset v_007 is still pending; call check_job(veo_a1b2c3) or wait_for_job first")`。模型读到 traceback 知道下一步。
- 其他 verb 同理。**Host 不替模型隐式等待。**

### 2.3 三个新 verb 加进 DISPATCHER + TOOL_SCHEMAS

```python
# 新 verb 之一 — check_job
{
    "name": "check_job",
    "description": "Check the current status of a previously submitted async generation job. Returns immediately without waiting. Use this to see if a generate_video / generate_audio job has finished without blocking.",
    "parameters": {
        "type": "object",
        "required": ["job_id"],
        "properties": {
            "job_id": {"type": "string", "description": "Job id from generate_video / generate_audio submission."}
        }
    }
}

# wait_for_job — 显式阻塞
{
    "name": "wait_for_job",
    "description": "Wait for an async generation job to finish, up to max_wait_sec. Blocks the turn — call this only when the next step truly depends on the result. If you can do other work in the meantime, prefer check_job in a future turn instead.",
    "parameters": {
        "type": "object",
        "required": ["job_id"],
        "properties": {
            "job_id": {"type": "string"},
            "max_wait_sec": {"type": "number", "description": "Hard timeout. Default 180. Cap 300."}
        }
    }
}
```

`generate_video`/`generate_audio` 的 schema 描述也要改 — 显式说 "submits an async job and returns a job_id immediately; use check_job or wait_for_job to retrieve the resulting asset"。

### 2.4 `_drive_turn` — **不改主循环**

(c) 设计的好处:dispatcher 都是 async def 返回 dict,_drive_turn 完全不感知"这是 sync verb 还是 async-submit verb"。所有差异在 dispatcher 内部:
- 同步 verb(现有 11 个)做完工作返回 result
- `generate_*` 提交 LRO,在 JobRegistry 注册,allocate pending asset,返回 `{job_id, asset_id_pending, ...}`
- `check_job` 调 JobRegistry 一下 poll,返回最新 status
- `wait_for_job` 在 dispatcher 内 async loop poll JobRegistry,等到 done/timeout 返回

**`_drive_turn` 完全无需感知"job"这个概念。** 它只看 `turn_count` / `tool_steps_this_turn` / `tool_exec_result|error`。这是好设计的标志 —— **新概念在边缘,不入主循环**。

`run_turn` 末尾 emit `turn_complete` 时可以额外 carry `pending_jobs: [...]`,给前端渲染 "still running" indicator。但**不需要内部行为变化**。

---

## 3. 跨 turn 怎么让模型知道去 check

**机制:system prompt 渲染时把 JobRegistry 的 pending 列表写进去。**

system_v3.md 现在的 `{{asset_registry}}` 段:
```
## Session asset registry
{{asset_registry}}
```

扩展为:
```
## Session asset registry
{{asset_registry}}

## Pending async jobs
{{pending_jobs}}
```

每次 model 调用前,host 渲染 `{{pending_jobs}}` 为:
```
- veo_a1b2c3 [video, ETA was 120s, submitted 145s ago, last status: running] → v_007
- lyria_d4e5f6 [audio, ETA was 60s, submitted 89s ago, last status: done] → aud_004 (ready to use, run check_job to materialize)
```

模型读 prompt 就知道"上一轮我提交过 jobs,有的可能 done 了"。模型决定要不要 check_job。

**自动 poll 不可以做。** 如果 host 在 background 主动 poll 然后 inject 进 prompt,这就回到 (b) 的反模式 —— host 替模型 narrate state。唯一例外是 host 偶尔(如每 60s)更新 JobRegistry 的 `last_polled_status`,让模型 next turn 看到的状态不是 stale 的 "submitted"。**这点偷一手是合理的** —— 它不改 asset,只更新可观察的 state cache。

---

## 4. 并行提交多个生成任务 — (c) 天然支持

模型在同一 turn 里:
```
tool_call generate_video(prompt="cat", duration=8) → returns {job_id: veo_1, asset_id_pending: v_007}
tool_call generate_audio(prompt="lofi beats", duration=30) → returns {job_id: lyria_1, asset_id_pending: aud_003}
tool_call wait_for_job(job_id: veo_1, max_wait_sec: 180)
```

两个 job 同时跑(LRO 在 Google 后端并行),模型主动选择 wait_for_job(veo_1) 等视频好了再继续。期间 lyria_1 也在跑。

**需要什么 host 支持:**
- `httpx.AsyncClient` 单例,httpx 自带并发请求池 → submit 并行没问题
- JobRegistry 不锁全局,只在 update 单个 record 时 per-record lock → 并行 poll 不冲突
- `wait_for_job` 应该用 `asyncio.wait` 而不是裸 sleep loop,这样如果有多个 wait 在 queue 里也不互锁(虽然 v3 同 turn 内 verb 是顺序执行的,但未来可能 parallel)

**没新增基础设施。** asyncio + httpx 就够。

---

## 5. 与未来 v4 build 的同构 — 关键

**这是采纳 (c) 最重要的理由。**

v4 build 形态(doc 03 已分析):
- 模型写 Python 脚本 → host spawn sandbox 子进程 → 几十秒到几分钟跑完 → 输出 asset

**这就是另一种 LRO**。形态上完全和 Veo / Lyria 同构:
- 提交后立即返回 job 句柄
- 模型决定等还是去做别的
- 结束后 asset 落到 registry

如果今天 (c) 的 JobRegistry / check_job / wait_for_job 设计就考虑 build 兼容:
- `JobRecord.kind` 已经预留 `"build"`
- `JobRecord.provider` 已经预留任意字符串
- `JobRecord.operation_name` 对 Veo 是 LRO name,对 build 是 sandbox 进程 id
- pending_asset_id 机制对两者通用
- `check_job` / `wait_for_job` verb 对模型无区别

**意味着 v4 build 落地时 90% 的"如何让模型驾驭 async"问题已经解决。** 只剩 sandbox spawn + script 验证那一段。

反过来:如果 (c) 不做、走 (a),Veo 那点工程债跟着 v4 build 一起 carry 进未来 —— v4 build 又得重新设计一遍 async 机制。**做对一次,复用两次。**

具体接口建议:在 doc 03 提到的 `gemia/tools/build.py` 落地时,dispatcher 应该 immediately submit + register job + return `{job_id, asset_id_pending: ...}`。同步等待形式应该是更高层的 model 决策,不是 host 默认。

---

## 6. SSE 进度事件:Veo 只有 queued/running/done,怎么报?

ffmpeg 的 `out_time_us=` 给的是连续 0–100% 进度。Veo / Lyria 没这个,只有 **离散状态 + 时间消耗**。

**Lumeri 处理方式:**

`wait_for_job` dispatcher 在 poll loop 里调 `ctx.emit_progress(ProgressUpdate(...))`,每次 poll 后 emit 一次:

```python
async def _wait_for_job_dispatch(args, ctx):
    job_id = str(args["job_id"])
    max_wait = float(args.get("max_wait_sec", 180))
    deadline = time.monotonic() + max_wait
    poll_interval = 5.0  # first 30s
    submitted_at_mono = ctx.jobs.get(job_id).submitted_mono

    while time.monotonic() < deadline:
        record = await ctx.jobs.poll(job_id)  # actually hits provider
        elapsed = time.monotonic() - submitted_at_mono
        ctx.emit_progress(ProgressUpdate(
            percent=None,                              # 没有真正百分比
            message=f"{record.last_polled_status} ({elapsed:.0f}s elapsed)",
            eta_sec=max(0, record.estimated_eta_sec - elapsed),
        ))
        if record.last_polled_status == "done":
            # download, materialize asset, return
            ...
            return {"asset_id": record.pending_asset_id, "summary": ...}
        if record.last_polled_status == "failed":
            raise RuntimeError(f"job {job_id} failed: {record.final_error}")
        await asyncio.sleep(poll_interval)
        if elapsed > 30:
            poll_interval = 10.0
    # timeout
    raise TimeoutError(f"job {job_id} did not finish within {max_wait}s")
```

**`percent=None` 是真实的** —— 不要假造 `percent = min(95, 100 * elapsed / eta)` 这种伪进度,前端会渲染 indeterminate spinner + message 文本,**这是诚实的反映**。和 v3-A 已经在用的 ffprobe-unknown-duration 路径同处理(`_ffmpeg.py:158` 已有先例)。

`tool_exec_progress` 事件已经存在(`agent_loop_v3._make_progress_cb`,line 610),复用,**前端代码无需改**。

---

## 7. (c) 总成本(给 Opus 阶段排期)

| 工作 | 小时 |
|---|---|
| `gemia/tools/_jobs.py` (JobRegistry + persistence + record types) | 4–6 |
| `AssetRegistry` 加 `status` + `job_id` 字段 + compact_text 渲染 pending | 2 |
| `_context.ToolContext.jobs` 注入 + agent_loop_v3 持久化 (session/<sid>/jobs.json) | 2–3 |
| `gemia/tools/check_job.py` dispatcher | 2 |
| `gemia/tools/wait_for_job.py` dispatcher + progress 事件 | 3–4 |
| `gemia/tools/_schema.py` 加 2 新 verb schema | 1 |
| `system_v3.md` 加 `{{pending_jobs}}` 段 + agent_loop render hook | 1 |
| `gemia/tools/generate_video.py` dispatcher(LRO submit + JobRegistry hand-off) | 5–6 |
| `gemia/tools/generate_audio.py` dispatcher(同构) | 3 |
| Smoke tests(mock LRO + 全部 verb) | 5–6 |
| Telemetry 扩字段(`is_async`, `pending_at_turn_end`, etc.) | 1 |
| Integration test:模型并行 fire video+audio,wait_for_job,asset 落地 | 3–4 |
| **总计** | **32–39h (4–5 天)** |

不含 generate_image(同步,独立 task,doc 07 已估)。

vs (a):20–25h 但不能 ship(turn 阻塞 3 分钟用户不接受)
vs (b):30–35h 但与 v4 build 不同构,后续要重做 → 总成本 ~60–70h

**(c) 是省成本路径,不是花成本路径。**

---

## 8. 已知未决

这些应在 Opus 阶段 spec 时拍板,不阻塞我现在做 generate_image:

1. **JobRegistry 持久化进程崩了怎么办?** Veo job 在 Google 那边继续跑,但 Lumeri 重启后 JobRegistry 丢了 → 模型不知道 job 存在了。**建议 jobs.json 写盘 (atomic rename),session 启动时 reload。** 缺点:崩溃后 model 视角会"突然多出 ready 的 asset",但这比丢 5 美元的 Veo 算账好。
2. **wait_for_job 命中 timeout 后,job 在 Google 那边继续跑,asset 也会最终 ready。模型怎么知道?** 下一 turn 通过 `{{pending_jobs}}` 段看到 status 已变 `done`,模型可以 `check_job` 拿。或者更简单:`check_job` 内部也 trigger download-and-materialize。
3. **同一 turn 内多个 wait_for_job 顺序还是并行?** v3 当前 `_drive_turn` 是 sequential 处理 tool_calls,改并行风险大。**建议保持 sequential**,模型如果要真并行就在多个 turn 分散提交。
4. **budget_guard 怎么算 LRO 成本?** Veo `predictLongRunning` 调用立即扣费(Google 不退);**budget_guard 应在 submit 时记 cost,不是在 wait 时**。这件事 budget_guard 接口可能要小改。
5. **frontend 怎么渲染 pending asset?** 当前 v3 frontend 假设 verb 返回 asset 立即可看。pending 状态需要新增 UI(灰色卡片 + "still generating ETA Xs")。**~1 天 frontend 工作,不在本份估算内**。

---

## 9. 行动总结

- **采纳 (c)**。(a)/(b) 否决,理由见 §1。
- (c) 工时 32–39h,与未来 v4 build 同构,实质省成本。
- 现在做 generate_image 不依赖 (c)(同步)。等 generate_image 跑通后,(c) 一并设计实施,Veo / Lyria 进同一架构。
- **不要先做 (a) 占位、之后 refactor 到 (c)** —— Veo 一旦 ship 用户开始依赖,改异步语义是 breaking change,代价比一次做对更高。

---

*与 [03-loop-integration.md](./03-loop-integration.md) doc 3 §2 / claude-03 §1 (`runtime_vnext` 复用论)互相参照。Codex doc 03 假设的 sync build verb 应在 (c) 落地后改为 async build verb。*
