# Lumeri

**Lumeri** is a family of AI creative tools that share one idea: give the model a small vocabulary of clean, composable primitives, and let it plan the work.

**Lumeri Video** is the first product in the family — a programmable engine where an AI plans and executes video, image, and audio operations by calling primitive Python functions.

> The public product and GitHub repository name is **Lumeri**. The Python package and some internal paths still use the historical engineering name `gemia`.

## The Lumeri family

| Product | What it does | Status |
|---|---|---|
| **Lumeri Video** | AI plans a pipeline of primitive video/image/audio ops and executes it | in this repo |
| **Lumeri Audio** | Music and sound-design workflows on the same primitive/plan/skill model | early |
| **Lumeri Image** | Still-image workflows — grading, retouching, style transfer as a plan | early |
| **Lumeri PPT** | Deck generation and edit as a plan of slide-level primitives | early |
| **Lumeri CAD** | Parametric CAD workflows driven by natural-language plans | exploration |

Each product ships as its own surface, but they share the same three ideas below.

---

## Core ideas

### 1. Primitive API

Around ninety pure Python functions across three domains — color grading, blur, keying, optical flow, audio repair, and more. Each function has a clear signature and docstring. The model reads those docstrings to know what's available.

```
gemia.picture.color.color_grade(image, preset="cyberpunk")
gemia.video.timeline.cut(input_path, output_path, start_sec=0, end_sec=3)
gemia.audio.frequency.eq(audio, bands={...})
```

Picture functions automatically work on video — the engine extracts frames, applies the operation per-frame, and re-encodes with the original audio. The model doesn't need to know this; it just picks the right function.

### 2. Skills

A Skill is a saved execution plan. Run a prompt, get a good result, save it as a Skill. Apply it to any video later — no AI call needed.

```
run → save-skill → run-skill → done
```

Skills are JSON templates with `$input` / `$output` variable binding. Concrete paths are stripped; the pipeline is portable.

Skills record which models were used and expose adjustable parameters:

```json
{
  "name": "赛博朋克调色",
  "parameters": [
    {"step_id": "step_1", "arg": "preset", "type": "str", "current_value": "cyberpunk"}
  ]
}
```

### 3. Orchestrator

Describe what you want in natural language. Lumeri sends your prompt plus the full function catalog to the planner. The planner returns a structured plan (not code, not ffmpeg commands). The engine executes it.

If the prompt is vague, the model asks clarifying questions first (Ask mechanism).

---

## Models

Two layers, configured separately, because they are not interchangeable.

### The planner — pick any of five

The planner reads your prompt and every primitive's docstring, then writes the
plan. It is a general reasoning model, so you can run it on whichever provider
you already pay for. Set `lumeri_v3_provider` in `~/.lumeri/config.json` and add
that provider's key:

| Provider | Value | Credential |
|---|---|---|
| Google Vertex AI | `vertex` | `vertex_project` + `gcloud` ADC, or a GCP VM service account with the *Vertex AI User* role |
| Google AI Studio | `gemini` | `gemini_api_key` |
| OpenAI | `openai` | `openai_api_key` (optional `openai_model`) |
| Anthropic | `claude` | `anthropic_api_key` |
| OpenRouter | `openrouter` | `openrouter_api_key` (optional `openrouter_model`) |

Run `python -m gemia setup` for an interactive walkthrough, or write the file
yourself — the headless instructions print the exact JSON for each provider.

### The generative models — Google, with one exception

These produce actual pixels and audio, and they are wired to specific models
rather than to an interface, so they are not a free choice:

| Role | Model | Swappable |
|---|---|---|
| Image generation / editing | Nano Banana (Gemini image) on Vertex | Yes — point `image_base_url` at OpenRouter or any OpenAI-compatible endpoint |
| Video generation | Veo on Vertex | No |
| Music / sound generation | Lyria on Vertex | No |

Video and audio carry a failover chain within the same family, so a preview
model that goes away does not take the pipeline down with it.

### Search — optional, keyless by default

Web search needs no key. Left alone it auto-detects: if you have configured a
key for `tavily`, `serper`, `brave`, `exa`, `google_cse` or `bing` it uses that
one first — a present key is taken as having opted into that engine — then
`searxng` if you have given it a `searxng_url`. With none of them configured it
falls back to DuckDuckGo, which needs nothing and is never auto-selected over an
engine you actually configured. Set `search_provider` explicitly to pin one.

Behind a firewall, set `proxy` to an `http://host:port` and every outbound model
call goes through it. Leave it empty in the cloud.

---

## Your keys, or an account

Lumeri runs in two modes, and which one you are in decides who pays the model
bill and who carries responsibility for what comes out.

**Bring your own keys.** Put a provider key in `~/.lumeri/config.json` as above
and everything runs against your account, on your quota, billed to you. Nothing
is relayed through a Lumeri service. This is the mode the repository defaults
to, and it is the whole story for a local install.

**Sign in instead.** The desktop app can sign in with a Google account, after
which provider credentials are resolved for you and you never hold a key. The
generative calls then run on platform-held credentials rather than yours.

The distinction is not cosmetic. When the platform pays for a generation, the
platform is answerable for the pixels it hands back, so platform-funded image
and video is routed through output screening on its way out — whether that
screening is actually switched on is a deployment question, answered below, and
not something to assume from this paragraph. When you bring your own key, only
your input is checked and the output is yours. `platform_funded()` is the single predicate
that decides which of the two you are in, and it is deliberately a named
function rather than an inline `True` so that the day per-user keys or credits
land, there is exactly one place to change.

Output screening is a deployment setting, not an assumption. `/health` reports
it as `output_moderation` in one of three states — enforcing, recording only, or
no detector configured — because the failure mode is invisible from the outside:
an unconfigured detector lets media through and simply records that nothing was
screened. Any deployment that takes payment should be running it in enforcing
mode and should verify that on `/health` rather than trusting a config file.

---

## Install

```bash
git clone https://github.com/Acrabxie/lumeri.git && cd lumeri

# Python 3.12+, ffmpeg required
pip install -e .

# Configure a model provider interactively...
python -m gemia setup

# ...or point at one directly
export GEMINI_API_KEY="..."        # Google AI Studio
# export OPENAI_API_KEY="..."      # or OpenAI
# export ANTHROPIC_API_KEY="..."   # or Anthropic
# export VERTEX_PROJECT="..."      # or Vertex AI, via your gcloud ADC
```

Running with no provider configured prints the exact JSON for each of the five,
rather than failing with a stack trace.

Verify:

```bash
python -m pytest tests/ -q     # ~4,300 tests, no GPU needed
```

Run it from the environment you installed into. A stray system interpreter
resolves a different set of packages, and the failures that produces look like
real ones.

---

## Demo

### Generate a test video (if you don't have one)

```bash
ffmpeg -y -f lavfi -i testsrc2=duration=5:size=1280x720:rate=30 \
  -c:v libx264 -pix_fmt yuv420p inputs/demo.mp4
```

### Demo 1 — Cyberpunk color grade

```bash
python3 -m gemia run \
  --video inputs/demo.mp4 \
  --prompt "赛博朋克风格，冷色调"
```

```
Asking the planner for a plan...
Plan: Apply cyberpunk color grading (1 step)
  step_1: gemia.picture.color.color_grade({"preset": "cyberpunk"})

Executing...
Done!
```

The engine sees `gemia.picture.*` applied to a video and auto-wraps it with per-frame processing.

### Demo 2 — Multi-step pipeline

```bash
python3 -m gemia run \
  --video inputs/demo.mp4 \
  --prompt "裁前3秒，加速2倍，调色vintage"
```

```
Plan: Trim + speed + vintage (3 steps)
  step_1: gemia.video.timeline.cut({"start_sec": 0.0, "end_sec": 3.0})
  step_2: gemia.video.timeline.speed({"factor": 2.0})
  step_3: gemia.picture.color.color_grade({"preset": "vintage"})

Executing...
Done!
```

### Demo 3 — Save & reuse as Skill

```bash
python3 -m gemia save-skill --name "裁切加速vintage"
python3 -m gemia list-skills
python3 -m gemia run-skill "裁切加速vintage" --video inputs/another.mp4
```

---

## CLI reference

```bash
# AI-driven execution
python3 -m gemia run --video FILE --prompt "..." [--output FILE]

# Skills
python3 -m gemia save-skill --name "NAME" [--from-task TASK_ID]
python3 -m gemia list-skills
python3 -m gemia run-skill "NAME" --video FILE [--output FILE]

# Web UI
python3 server.py    # http://127.0.0.1:8000
```

---

## Primitive modules

| Module | Count | Examples |
|--------|-------|---------|
| `gemia.picture.color` | 8 | `color_grade`, `lift_gamma_gain`, `apply_3d_lut`, `log_to_linear` |
| `gemia.picture.pixel` | 5 | `blur`, `sharpen`, `denoise`, `add_grain` |
| `gemia.picture.geometry` | 4 | `resize`, `crop`, `rotate` |
| `gemia.picture.composite` | 19 | `blend_multiply/screen/overlay…`, `chroma_key`, `luma_key`, `create_edge_mask` |
| `gemia.picture.analysis` | 7 | `histogram`, `waveform_monitor`, `vectorscope`, `check_clipping` |
| `gemia.audio.basics` | 5 | `load`, `save`, `trim`, `concat`, `mix` |
| `gemia.audio.dynamics` | 3 | `normalize`, `compress`, `adjust_gain` |
| `gemia.audio.frequency` | 3 | `eq`, `highpass`, `lowpass` |
| `gemia.audio.time_pitch` | 3 | `time_stretch`, `pitch_shift`, `detect_bpm` |
| `gemia.audio.repair` | 4 | `reduce_noise`, `remove_hum`, `de_ess`, `remove_reverb` |
| `gemia.audio.mixer` | 3 | `create_bus`, `sidechain_compress`, `auto_duck` |
| `gemia.video.frames` | 6 | `extract_frames`, `optical_flow_interpolate`, `retime`, `stabilize` |
| `gemia.video.timeline` | 4 | `cut`, `concat`, `speed`, `reverse` |
| `gemia.video.compositing` | 2 | `overlay`, `add_audio_track` |
| `gemia.video.analysis` | 4 | `get_metadata`, `detect_scenes`, `track_point`, `track_plane` |
| `gemia.video.keyframe` | 2 | `KeyframeTrack`, `apply_animated_op` |
| `gemia.picture.generative` | 4 | `generate_image`, `style_transfer`, `edit_image`, `blend_images` |
| `gemia.video.generative` | 3 | `generate_video`, `generate_video_from_image`, `extend_video` |

---

## Architecture

```
User prompt
    │
    ▼
┌──────────────────────┐
│  Planner              │  reads every primitive's docstring
│  (your chosen model)  │
└──────────┬───────────┘
           │ Plan JSON
           ▼
┌──────────────────────┐
│  PlanEngine           │  auto-bridges picture ↔ video
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│  gemia.picture    gemia.audio    gemia.video              │
│  (OpenCV/numpy)   (librosa)      (ffmpeg)                 │
│  + Nano Banana    + Lyria        + Veo                    │
└──────────────────────────────────────────────────────────┘
           │
           ▼
       Output file
```

---

## Roadmap

- ✅ Generative primitives — image (Nano Banana), video (Veo), music (Lyria)
- ✅ Skills v2 — model tracking, parameterization
- ✅ Desktop app — packaged macOS build with a managed local runtime
- Skills UI — visual skill browser in the web interface
- Sibling products — Lumeri Audio / Image / PPT / CAD on the same core

---

## Environment

- Python 3.12+
- ffmpeg / ffprobe in PATH
- A planner credential — any one of `VERTEX_PROJECT`, `GEMINI_API_KEY`,
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`
- Generative image, video and audio additionally need Vertex access; without it
  the editing primitives still run and only the generative verbs are unavailable

## Contributors

See [CONTRIBUTORS.md](CONTRIBUTORS.md).

## License

MIT
