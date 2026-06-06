# 07 — AI Studio provider 调研:Veo 3.1 / Lyria 3 / Nano Banana 2

> 目的:批次 2 实施前确认三个生成模型是否真的通过 AI Studio API
> (`generativelanguage.googleapis.com`)暴露,而不是只有 Vertex AI 可用。
> 同时确认 auth / pricing / $300 credit / LRO request-response 形状,
> 给 generate_image 实施提供精确依据。
>
> Date: 2026-05-30
> Sources: 章末列出,基于本次 WebSearch + Gemini 官方 docs 抓取。

---

## 结论速览

| 模型 | AI Studio API 暴露? | 模型 ID | 同步/异步 | 备注 |
|---|---|---|---|---|
| **Nano Banana 2** | ✓ | `gemini-3.1-flash-image-preview` | 同步 | 也有 `gemini-3-pro-image-preview` (Nano Banana Pro) |
| **Veo 3.1** | ✓ | `veo-3.1-generate-preview`, `-fast-`, `-lite-` | 异步 (LRO) | 60–180s 完成,需 poll |
| **Lyria 3** | ✓ | `lyria-3-pro-preview`, `lyria-3-clip-preview` | 异步 (LRO) | Pro 最长 3 分钟,Clip 30s |

**三个都在 AI Studio 暴露。** 没有"只有 Vertex"的情况。

---

## ⚠ $300 credit 资格 — 必须先和 Acrab 确认

**用户的假设"$300 赠金挂在 AI Studio"在 2026-03 后不再普适。** 关键事实:

> "Starting March 2026, Gemini API usage costs are specifically excluded
> from the $300 Google Cloud Free Trial program... if you were granted a
> Google Cloud welcome credit **before** they became ineligible, you are
> allowed to spend your remaining credits on the Gemini API and AI Studio
> up until the credits expire (after 90 days)."
> — Google AI Studio Pricing 2026 (nocode.mba/articles/google-ai-studio-pricing)

**判断规则:**

- Acrab 的 Cloud Billing 账户开通时间 **2026-03-02 之前** → $300 credit 可用于 Gemini API/AI Studio,**但有效期 90 天**(从授予日算)。若超期 → 失效。
- 账户开通时间 **2026-03-02 之后** → $300 credit **不能用于** Gemini API/AI Studio,只能用于其他 Cloud 服务(Compute Engine、BigQuery 等)。Veo/Lyria/Nano Banana 调用必须直接走付费 API key。

**行动项:** Acrab 必须确认账户开通时间 + 当前 credit 余额 + 到期日。如果 credit 实际不可用,**批次 2 是真金白银从第一次调用开始**,COST_TABLE 要反映这一点,smoke test 必须用真实金额预算。

如果 credit 还有效:Veo 单次 8s/720p 约 $2.8(Vertex 价 $0.35/sec × 8s),$300 大概 100 个测试视频,**不是无限**。

---

## 1. 认证 — 三模型统一形态

```
POST https://generativelanguage.googleapis.com/v1beta/{path}
Headers:
  x-goog-api-key: $GEMINI_API_KEY
  Content-Type: application/json
```

`GEMINI_API_KEY` 在 AI Studio 控制台生成,绑定一个 Cloud Billing 账户。**单 key 走通三个模型**,不需要每模型分配 key。

存放约定(Lumeri):应在 `~/.gemia/config.json` 的 `gemini_api_key` 字段,或独立 `gemini_studio_api_key` 字段(避免和现有 OpenRouter 走的 Gemini 3.1 Pro `model` key 混)。**不要进 shared memory / 仓库 / 测试 fixture。**

---

## 2. Nano Banana 2 (generate_image) — 同步形态

**Endpoint(注意非 predictLongRunning):**
```
POST /v1beta/interactions
Headers: x-goog-api-key, Content-Type, Api-Revision: 2026-05-20
```

**Request:**
```json
{
  "model": "gemini-3.1-flash-image-preview",
  "input": [{"type": "text", "text": "Create a picture of ..."}]
}
```

可附加 `reference_images` 走 image-to-image。

**Response:** 同步返回 base64 编码图像 + meta。**这是批次 2.1 的"base64 落地硬约束"对应的具体形态。**

**关键事实** — **provider 返回 base64,不是 URI**。Lumeri host 必须:
1. 从 response 提取 base64 字符串
2. base64.b64decode → bytes → 写入 `ctx.child_path(new_id, ".png")`
3. `ctx.registry.register_output(new_id, kind="image", path=..., ...)`
4. SSE event 只传 `asset_id` + `preview_uri`(`/sessions/<sid>/assets/<asset_id>`)
5. **base64 字符串永远不进 SSE event** — 一张 2K 图 ~3MB,SSE frame 会被撑爆;前端走 `preview_uri` HTTP GET 拿数据

**定价(2026-05):**
- 2K image: **$0.101**
- 4K image: **$0.150**
- Batch API: 2K **$0.067**(50% off,但 batch 是异步,适合后续优化,批次 2.1 用同步)
- Free tier: **无**(必须 paid API key)
- 速率限制: 自由/付费分级,具体限值 Google 控制台查

**已知坑(2026 全年高频问题):**
> "Since the start of 2026, failures in the Gemini image generation API
> have occurred almost every month. ~70% of 503 errors recover within
> 60 minutes; full recovery typically 30–120 minutes."

→ generate_image dispatcher 必须有清晰的 503 错误归类(`error_class: "provider_unavailable"`),不要悄悄重试,把 traceback 真实反馈给模型让模型决定降级方案。telemetry 这条信号有商业价值。

---

## 3. Veo 3.1 (generate_video) — 异步 (LRO) 形态

**Initiate:**
```
POST /v1beta/models/veo-3.1-generate-preview:predictLongRunning
Headers: x-goog-api-key
```

**Request body:**
```json
{
  "instances": [{
    "prompt": "8-second clip of ...",
    "image": {"inlineData": {"mimeType": "image/png", "data": "<base64>"}},
    "lastFrame": {...},
    "referenceImages": [...],
    "video": {...}
  }],
  "parameters": {
    "aspectRatio": "16:9" | "9:16",
    "resolution": "720p" | "1080p" | "4k",
    "durationSeconds": "4" | "6" | "8",
    "numberOfVideos": 1,
    "personGeneration": "allow_all" | "allow_adult"
  }
}
```

**Immediate response:**
```json
{
  "name": "projects/*/locations/*/operations/*",
  "done": false,
  "metadata": {...}
}
```

**Poll:**
```
GET /v1beta/{operation.name}
Headers: x-goog-api-key
```
→ 同结构,`done: true` 时 `response` 字段就绪。

**Final response:**
```json
{
  "name": "...",
  "done": true,
  "response": {
    "generateVideoResponse": {
      "generatedSamples": [{
        "video": {
          "uri": "gs://... (downloadable via Files API)",
          "mimeType": "video/mp4"
        }
      }]
    }
  }
}
```

**Download:** 从 `video.uri` 走 Files API GET 拿到 MP4 字节,host 写入 `ctx.child_path(new_id, ".mp4")`。

**完成时间:** 8s/720p 典型 **60–180s**,看负载。这是为什么 (c) 异步设计必要 —— host 不能阻塞整个 turn 等 3 分钟。

**模型变体 + 价格(approx,Vertex 价 — AI Studio 走 token 计费可能差异):**
- `veo-3.1-generate-preview`(主): ~$0.50/sec(含 audio)→ 8s 约 **$4.0**
- `veo-3.1-fast-generate-preview`: ~$0.35/sec(不含 audio)→ 8s 约 **$2.8**
- `veo-3.1-lite-generate-preview`: ~$0.05–0.08/sec → 8s 约 **$0.40–0.64**(便宜测试用)
- AI Studio free tier: **10 个测试视频** total(不是每月),用完就纯付费

**批次 2 实施建议:** smoke test 用 `lite` 变体省钱,生产 default `fast`,只有用户明确要 cinematic 才上 `generate-preview`(完整版)。budget_guard 的 COST_TABLE 按 lite/fast/full 分三档。

---

## 4. Lyria 3 (generate_audio) — 异步 (LRO) 形态

**Endpoints:**
```
POST /v1beta/models/lyria-3-clip-preview:predictLongRunning       # 30s 最大,快
POST /v1beta/models/lyria-3-pro-preview:predictLongRunning        # 3 min 最大,贵
```

请求/响应形态 **与 Veo 同构**(`predictLongRunning` + `done` polling)。具体 instances/parameters schema docs 没给完整(预览阶段),需在第一次实际调用时根据 4xx 错误响应反推 —— 这是 preview API 的常态。

**已知:** 支持 text prompt + 可选 image-to-music。8 语种 vocal(EN/DE/ES/FR/HI/JA/KO/PT)。`tempo`/`time-aligned lyrics` 是 parameter。

**定价:** AI Studio 公开 preview "currently at no cost"(2026-05 状态,one source 报告),但 **不应假设永久** —— Google 通常 preview 几个月后转付费。批次 2 实施时把 COST_TABLE 设为 0,加一行注释 "preview 期免费,转付费需更新"。telemetry 会暴露使用频次,Acrab 可在 GA 时再决策。

**变体选择:** clip(30s)更适合 BGM/ident/jingle 这类创作任务,Pro(3 min)用于 long-form。批次 2 先实现 clip。

---

## 5. LRO Polling 模式(Veo 和 Lyria 共用)

Python pseudocode(基于 Google 官方 cookbook):
```python
op = client.operations.predict_long_running(model, body)
while not op.done:
    await asyncio.sleep(10)
    op = client.operations.get(op.name)
result = op.response.generate_video_response.generated_samples[0]
file_bytes = client.files.download(result.video.uri)
```

**Lumeri 集成关键点:**
- `time.sleep(10)` 在 v3 必须改 `asyncio.sleep(10)`(loop 是 async)
- Poll interval **建议 5s for first 30s, then 10s thereafter**(Veo 典型 60–180s,5s 早期捕捉快速完成,10s 后期省 API 调用)
- 单次 turn 永久 block 不可接受 → 见 doc 08 (c) 异步架构方案

**Common pitfalls(官方 issue tracker):**
- `PERMISSION_DENIED` + `SERVICE_DISABLED`: AI Studio 控制台需启用 "Generative Language API",新启用后几分钟生效
- Files API 下载偶尔 404,需要 retry with exponential backoff(2s/4s/8s)
- 8s+ 视频请求被自动 reject(`durationSeconds > 8` 当前 hard limit)

---

## 6. Provider client 实现建议(给 generate_image 用)

**新文件:** `gemia/ai/studio_client.py`(与 `gemia/gemini_client.py` 平行,后者走 OpenRouter)

约束:
- 单一 `StudioClient` 实例,session 内复用
- API key 从 `~/.gemia/config.json` 读,首次缺失 raise(不要 fallback to env var,避免无意提升权限)
- HTTP 走 `httpx.AsyncClient`,timeout 30s for sync calls,15min for LRO 整个完成周期
- 503/timeout 不自动 retry,**直接 raise 到 dispatcher 让 agent loop 上报 traceback**
- 单元测试用 `httpx.MockTransport`,**不在 CI 跑真实 API**

**provider response 解析:**
- 找官方 `google-genai` Python SDK 看是否值得引入。如果不引入,自己 wrap 三个 endpoint 即可(代码量约 200 行)
- SDK 体积大、依赖多;直接 httpx 更轻但要自己跟踪 schema 变化
- **建议:不引入 SDK,直接 httpx** —— preview 阶段 SDK 也常落后,且 SDK 引入 numpy/pandas/protobuf 等重依赖会污染 sandbox(对未来 v4 build 不利)

---

## 7. 批次 2 决策清单(给 Acrab 拍板)

1. **$300 credit 是否仍有效?** 看账户开通日 + 余额。
2. **API key 怎么存?** `~/.gemia/config.json` 字段名 + 是否启用环境变量 fallback。我建议 **不启用 fallback**(避免误调用其他人 key)。
3. **Veo 默认变体?** lite / fast / full,我建议 **fast** + 显式 prompt 关键词触发 full。
4. **Lyria 默认变体?** clip / pro,我建议 **clip**。
5. **批次 2.1 (generate_image) 跑通后,2.2 / 2.3 顺序?** 我建议 **Lyria 3 先于 Veo**,因为 Lyria preview 期可能免费、且也是异步 LRO 形态 —— 验证 (c) 异步架构 + 免费跑,验证后再上 Veo(贵)。
6. **`google-genai` SDK 引不引?** 我建议 **不引**(见 §6 理由)。

回答这 6 个后批次 2.1 可启动。

---

## Sources

- [Veo 3.1 — Google AI Studio model page](https://aistudio.google.com/models/veo-3)
- [Generate videos with Veo 3.1 in Gemini API — Google AI for Developers](https://ai.google.dev/gemini-api/docs/video)
- [Enhanced Veo 3.1 capabilities are now available in the Gemini API — Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/veo-3-1-gemini-api/)
- [Veo 3.1 Lite for AI video generation — Google blog](https://blog.google/innovation-and-ai/technology/ai/veo-3-1-lite/)
- [How developers can use Lyria 3 for AI music generation — Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/lyria-3-developers/)
- [Lyria 3 and Lyria 3 Pro on Vertex AI — Google Cloud Blog](https://cloud.google.com/blog/products/ai-machine-learning/lyria-3-and-lyria-3-pro-on-vertex-ai)
- [Nano Banana image generation — Google AI for Developers](https://ai.google.dev/gemini-api/docs/image-generation)
- [Build with Nano Banana 2, our best image generation and editing model — Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/build-with-nano-banana-2/)
- [Google AI Studio Pricing 2026: Free Tier, API Costs & Plans — nocode.mba](https://www.nocode.mba/articles/google-ai-studio-pricing)
- [Veo 3 Pricing 2026: Google AI Ultra & Vertex AI API Cost — veo3ai.io](https://www.veo3ai.io/blog/veo-3-pricing-2026)
- [Billing — Gemini API — Google AI for Developers](https://ai.google.dev/gemini-api/docs/billing)
- [DeepWiki — Gemini Cookbook REST API Usage](https://deepwiki.com/google-gemini/cookbook/9.3-rest-api-usage)
- [Veo 3.1 public API availability & pricing — Google AI Developers Forum](https://discuss.ai.google.dev/t/veo-3-1-public-api-availability-pricing-60s-1080p-multi-prompt-transitions/107501)
