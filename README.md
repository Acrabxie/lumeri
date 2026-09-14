<p align="center">
  <a href="https://lumeri.io/">
    <img src="docs/assets/lumeri-working.gif" width="240" alt="Lumeri working animation" />
  </a>
</p>

<h1 align="center">Lumeri</h1>

<p align="center">
  <strong>the LUI for creation</strong>
</p>

<p align="center">
  An AI creative workflow engine that turns an idea into a real, editable media project.
</p>

<p align="center">
  <a href="https://lumeri.io/"><strong>Official website</strong></a>
  ·
  <a href="#how-it-works">How it works</a>
  ·
  <a href="#install">Install</a>
  ·
  <a href="#architecture">Architecture</a>
  ·
  <a href="#skills">Skills</a>
  ·
  <a href="#use-lumeri-from-another-agent">MCP</a>
  ·
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

<p align="center">
  <a href="https://lumeri.io/">
    <img src="https://img.shields.io/badge/website-lumeri.io-5FC6DE?style=for-the-badge" alt="Lumeri official website" />
  </a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-1F2937?style=for-the-badge&amp;logo=python&amp;logoColor=white" alt="Python 3.12 or newer" />
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-1F2937?style=for-the-badge" alt="MIT license" />
  </a>
</p>

**Lumeri** is a family of AI creative tools built around a small vocabulary of
clean, composable primitives that a model can plan and execute.

**Lumeri Video** is the first product in the family: an agentic video workspace
where the model works over a persistent project using structured tools while
you watch, edit, and correct the result.

The difference from a prompt box is that nothing is thrown away. Projects
persist to disk, every tool call is structured and logged, every timeline change
is an undoable patch, and any preview can become the starting point for the next
revision. When the model gets something wrong, you fix that one clip — you do not
re-roll the whole video.

> The public product and GitHub repository name is **Lumeri**. The Python
> package and some engineering paths still use the historical name `gemia`.

## This repository and Lumeri.io

[**lumeri.io**](https://lumeri.io/) is the product site for the Lumeri family,
where the hosted service and its accounts live.

**This repository is the local creative runtime** — the engine, the verbs, the
project model, the layer core, and the web workspace, under the MIT license.
Run it from here and you get one local workspace on your own computer, driven by
your own model provider: no registration, no login, no account switching, no
hosted email, and no billing. Your projects, media, and credentials stay on that
machine.

Hosted authentication, email delivery, cloud account management, billing, and
subscription systems are deliberately **not** part of this codebase — see the
public/private boundary in [SECURITY.md](SECURITY.md). Nothing in this
repository phones home to a Lumeri account, and none of it requires one. For
what the hosted service offers, see [lumeri.io](https://lumeri.io/).

## Why Lumeri

- **Project-native** — work lives in a persistent project on your disk, not in a
  disposable chat response.
- **Structured by design** — the model plans with explicit media verbs and
  applies reviewable patches, instead of emitting opaque editor macros.
- **Two real document models** — a clip timeline for cutting and a layered
  composition for design work, bridged like precomps (see
  [Architecture](#architecture)).
- **Local-first** — one local workspace, your own model provider, your own
  files, no account required (see [above](#this-repository-and-lumeriio)).
- **Teachable** — the model routes over a keyword-indexed skill library, and
  distills what worked into reusable skills of your own (see
  [Skills](#skills)).
- **Extensible** — third-party repositories register new layer types, ops, and
  effects through `lumenframe.registry`, so the editing language grows without
  forking the core.

## How it works

```text
Import media
→ Persist project, timeline, and layer document
→ Model plans, then calls media verbs over multiple turns
→ TimelinePatch / LayerPatch update the project
→ Render and inspect a preview
→ Revise from structured feedback
→ Export MP4 or OTIO
```

Each turn runs through the same host-side path: the plan gate decides whether
the model may mutate anything, the budget guard accounts for real money and real
time, the verb executes, and the turn ledger records what actually changed. The
loop ends when the ledger says the acceptance criteria are met — completion is a
derived fact, not something the model can simply claim.

## Install

Python 3.12+ and FFmpeg are required.

```bash
git clone https://github.com/Acrabxie/lumeri.git
cd lumeri
python -m pip install -e ".[dev]"

# macOS
brew install ffmpeg

# Ubuntu
sudo apt-get install ffmpeg
```

Start the workspace:

```bash
python server.py          # or: python -m gemia serve
# Open http://127.0.0.1:7788/
```

Use `--port` (or `LUMERI_PORT`) if 7788 is taken. Projects and media live under
`~/.gemia/`; render scratch goes to a temp directory you can redirect with
`LUMERI_V3_OUTPUT_ROOT`.

Optional extras:

```bash
pip install -e ".[interop]"   # EDL / FCP7 XML / FCPX interchange adapters
pip install -e ".[mcp]"       # expose Lumeri to other agents over MCP
```

### Choose a model provider

Lumeri does not ship a key. Open the provider setup panel in the workspace (or
`POST /config`) and pick one:

| Provider | Auth |
|---|---|
| OpenAI subscription via local Codex | this computer's own ChatGPT login — no API key |
| Google Vertex AI | GCP ADC (`gcloud auth`) + project/region |
| Google Gemini API | `GEMINI_API_KEY` |
| OpenAI | `OPENAI_API_KEY` (custom `base_url` allowed) |
| Anthropic Claude | `ANTHROPIC_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Custom | any OpenAI-compatible endpoint |

Credentials are stored locally on that computer and are never committed to Git.
**Test connection** in the same panel runs a real streaming probe with no side
effects.

The Codex subscription option invokes the Codex CLI on the same machine and only
accepts a local **Sign in with ChatGPT** session. Lumeri never reads, copies, or
stores Codex credentials, and every person signs in with their own account.

### macOS

macOS is the reference platform. Nothing beyond the steps above is required —
`brew install ffmpeg`, then `python server.py`.

It is also the only platform where Lumeri will run model-generated code. The
`build` and `run_shell` verbs execute under a two-tier `sandbox-exec` kernel
profile: full read/write inside the workspace; outside it, read and create-new
only, with pre-existing files protected from modification, truncation, deletion,
and rename. That kernel boundary is the only enforcement Lumeri trusts, so where
`sandbox-exec` is missing or unusable the verbs fail closed — refused outright
rather than run unprotected. The profile still grants IOKit access, so Metal and
Blender-backed effects can initialize the GPU without leaving the sandbox.

Voiceover is local and free: `narrate` speaks through the built-in `say` engine,
offline and with no API key. Pass `voice` to choose a system voice (`Ava`,
`Samantha`, `Tingting`, `Meijia`, …) or omit it for the default.

Permissions stay yours. macOS TCC consent is owned by the user, and Lumeri never
reports a capability as granted merely because it launched a subprocess.

For the Codex subscription provider, install and sign in to the CLI first:

```bash
brew install node        # or any Node.js LTS
npm install -g @openai/codex
codex login
```

### Windows 10/11

Install 64-bit Python 3.12+, Git, and a complete FFmpeg package whose `ffmpeg`
and `ffprobe` are on `PATH`. Then, in PowerShell in the cloned repository:

```powershell
.\scripts\windows\setup.ps1
.\scripts\windows\start.ps1
```

`start.ps1` runs the source checkout directly on `http://127.0.0.1:7788/` and
opens the browser workspace — it does not build or install an EXE. Run
`doctor.ps1` for a non-destructive prerequisite and port check, or pass
`-Port 7790` to both `doctor` and `start` when 7788 is occupied. PowerShell
execution policy is left unchanged; if your machine blocks local scripts, review
them first and allow them for the current process only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

To use the Codex subscription provider on Windows, install and authenticate the
CLI first:

```powershell
winget install OpenJS.NodeJS.LTS
npm install -g @openai/codex
codex login
```

Reopen PowerShell after installing Node.js before running `npm`. API-key
authentication reported by `codex login status` does not count as subscription
access — complete **Sign in with ChatGPT** instead.

Editing, rendering, export, local voiceover (via Windows SAPI), Windows fonts,
Blender discovery, and OpenTimelineIO bundles all run natively. Model-generated
`build` / `run_shell` code stays locked by default, because native Windows has no
equivalent of the macOS kernel sandbox Lumeri relies on. The local owner may
explicitly disable **Sandbox** in the Lumeri menu to allow PowerShell and Python
execution with full computer access; Lumeri never enables that mode on its own.

### Linux

Install FFmpeg from your package manager and run the same two commands as
macOS. Editing, rendering, export, and OTIO interchange all work; voiceover
falls back to `espeak` if it is installed. As on Windows, there is no
`sandbox-exec`, so `build` / `run_shell` fail closed.

## Architecture

### The two document models

This is the part that makes Lumeri different from a chat wrapper, and the part
worth understanding before reading the code.

**The clip timeline** (`gemia/project_model.py`) is the familiar NLE document:
video/overlay/audio tracks holding clips with in/out points, transitions, and
effects. Every mutation — from the model *or* from a human dragging a clip in the
UI — goes through the `TimelinePatch` vocabulary in `lumerai/patches.py`. One
vocabulary means one undo stack and one audit log.

**The layer document** (`lumenframe/`) is the compositing side: a tree of layers
where a `composition` node holds children, time is a property of every layer
(`start` / `duration` / `source_in` / `source_out` / `speed`), and 54 registered
`LayerPatch` ops cover transforms, masks, clipping, adjustment layers, keyframes,
grades, and effects.

The two meet like After Effects precomps. `lumen_comp_to_timeline` renders a
window of the layer document into a content-addressed cache file and places it on
the timeline as an ordinary video clip carrying `metadata.comp_ref` provenance —
so the renderer, the track invariants, and export need zero special cases. The
reference stays live: export re-renders the window when the layer document's hash
changes.

### Layout

| Path | Responsibility |
|---|---|
| `server.py` | Local HTTP entry point, static workspace, `/config` |
| `gemia/v3_routes.py` | Session API (`/sessions/*`) and SSE streaming |
| `gemia/agent_loop_v3.py` | Multi-turn model ↔ tool loop |
| `gemia/tools/` | The verb dispatch table the model calls |
| `gemia/registry.py` | Auto-discovered media primitives |
| `gemia/ai/skills/` | Retrieval-routed skill packs |
| `gemia/plan_mode.py` | Read-only planning gate |
| `gemia/budget_guard.py` | Cost and time ceilings |
| `gemia/turn_ledger.py` | Deterministic record of what changed |
| `gemia/subtasks.py` | Bounded parallel sub-agent fan-out |
| `gemia/project_model.py` · `project_store.py` | Timeline model and persistence |
| `gemia/project_render.py` · `project_export.py` | Preview renderer and full-quality export |
| `lumenframe/` | Layered composition core (`model.py`, `ops.py`, `registry.py`) |
| `lumerai/patches.py` | TimelinePatch vocabulary |
| `lumerai/otio_adapter.py` | OpenTimelineIO interchange |
| `lumerai/sandbox.py` · `gemia/sandbox_v4.py` | Sandboxed execution |
| `gemia/mcp/` | Lumeri as an MCP server |
| `static/v3/` | Web workspace — timeline, chat, preview |

### What the model can reach

| Surface | Count | Where |
|---|---|---|
| Agent verbs (function-calling schemas) | 104 | `gemia/tools/_schema.py` |
| Media primitives (picture / audio / video) | 835 | `gemia/picture`, `gemia/audio`, `gemia/video` |
| LayerPatch ops | 54 | `lumenframe/ops.py` |
| Skill packs (+ 4 combos) | 24 | `gemia/ai/skills/` |

Verbs are the model's API; primitives are the Python functions behind them, and
[skills](#skills) are how the model finds the right one without carrying all 835
in its prompt.

Third-party repositories extend the layer core through `lumenframe.registry`
(new layer types, ops, and effects), so the editing language grows without
forking.

## Skills

835 primitives will not fit in a prompt, and a model that has to rediscover your
house style on every task is not much of a collaborator. Skills solve both — in
two layers.

### The shipped library

24 skill packs live in `gemia/ai/skills/`, one directory each, built around a
`SKILL.md` whose YAML frontmatter declares what it is for and when to stay out
of the way:

```yaml
id: timeline-ops
description: 时间线结构编辑：裁剪、截取、加速、倒放…… 只改画面色彩用 color-grade。
triggers:
  primary: [裁剪, 截取, 加速, 倒放, trim, cut, speed, concat, reverse]
  secondary: [时间轴, 片段, 区间, clip, timeline, retime]
primitives: [gemia.video.timeline.cut, gemia.video.timeline.speed, ...]
est_tokens: 520
```

Routing is progressive disclosure, cheapest path first: keyword match against the
request, then an optional LLM fallback, then a static fallback set — a different
one for prompt-only projects with no footage yet. At most three packs load per
request, so the prompt carries a few hundred tokens of relevant craft instead of
the whole catalog. Triggers are bilingual because requests are.

Four `_combos/` entries cover skill pairs that keep co-occurring
(`timeline-ops+color-grade`, `transition+color-grade`, …) with a ready plan
template, skipping a planning round-trip. To see which packs are actually
earning their place:

```bash
lumeri-skill-stats --days 7        # add --json for machine-readable output
```

### Skills you teach it

The second layer is yours. When a multi-step task works, `save_skill` distills it
into a compact recipe — `{name, when_to_use, steps, notes}` — stored as one
`.lus` file per name under `~/.gemia/skills`. Re-distilling the same name updates
it in place, so a skill sharpens over time instead of spawning near-duplicates.
`recall_skills` searches your distilled skills *and* the shipped library before
work begins.

The store validates before it writes, and a rejection writes nothing: skills
carrying secrets, absolute user paths, or no steps at all are refused with a
typed `E_LUS_*` error. The `.lus` format itself is small and boring on purpose —
64 KiB ceiling, canonical byte-stable serialization with a checksum, and metadata
readable from the first 8 KiB so recall can scan many skills cheaply.

Relevant environment variables: `GEMIA_SKILL_STORE_DIR` (relocate the store),
`GEMIA_SKILL_ROUTER`, `GEMIA_SKILL_LLM_FALLBACK`.

## Use Lumeri from another agent

Lumeri can act as an MCP server, exposing a curated, frozen toolset — 13 read and
timeline verbs, byte-identical to their internal names, plus 5 MCP-native session
lifecycle tools. The verbs route through the same plan gate and budget guard as
the in-app loop and mirror the usual SSE events with `origin: "mcp"`; the
lifecycle tools wrap `SessionManager` directly and are gated separately.

```bash
pip install -e ".[mcp]"
```

The stdio entry point is `gemia.mcp.server:run_stdio`. Assets cross the boundary
as `lumeri://session/{id}/asset/{aid}` resources that resolve to absolute paths —
never base64, because a single large video would otherwise materialize hundreds
of megabytes of JSON on both sides and land in the model's context.

## Safety model

- **Plan mode** — a per-session read-only gate. The allow/block split was derived
  by reading every dispatcher, not by guessing from names: `inspect_timeline`,
  `render_preview`, and `remember` are blocked because they register assets or
  write durable files.
- **Budget guard** — the only host-side spending gate. It tracks cumulative cost
  and elapsed time and returns a fixed-limit block; an approval cannot raise the
  cap, and the host never silently substitutes a cheaper tool.
- **Sandbox** — model-generated code runs only under the macOS kernel profile
  described under [macOS](#macos), and fails closed everywhere else.
- **Skill validation** — a distilled skill carrying secrets or absolute user
  paths is rejected before anything is written to disk.
- **No secrets in the repo** — provider credentials stay in local config, never
  in code or committed files.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Tests

```bash
python -m pytest tests/ -q
ruff check .
```

48 test modules cover verb contracts, timeline and layer patches, render/export
behavior, OTIO round-trips, self-correction, the turn ledger, sandbox isolation
and escape attempts, plan-mode coverage, MCP toolset drift, sessions, and the web
server. CI additionally runs a Windows job that exercises the documented
`setup.ps1` / `start.ps1` path end to end.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) for the
architectural rules, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Contributors

See [CONTRIBUTORS.md](CONTRIBUTORS.md).

## License

MIT — see [LICENSE](LICENSE).
