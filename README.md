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
  An AI Creative Workflow Engine for turning an idea into a real, editable media project.
</p>

<p align="center">
  <a href="https://lumeri.io/"><strong>Website</strong></a>
  ·
  <a href="https://video.lumeri.io/">Lumeri Video</a>
  ·
  <a href="https://quanta.lumeri.io/">Quanta</a>
  ·
  <a href="https://cli.lumeri.io/">CLI</a>
  ·
  <a href="https://skills.lumeri.io/">Store</a>
  ·
  <a href="https://docs.lumeri.io/">Docs</a>
</p>

<p align="center">
  <strong>English</strong>
  ·
  <a href="README.zh-CN.md">简体中文</a>
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

You say what you want to make. Lumeri plans it, does the work with structured
media tools while you watch, and leaves you a real project: a timeline you can
open, rearrange, trim and keep directing. Generation is where the work starts,
not where it ends.

- **Project-native** — work lives in a persistent project and timeline, not a
  disposable chat response.
- **Structured by design** — the model plans with explicit media tools and
  applies reviewable timeline patches instead of emitting opaque editor macros.
- **Local first** — projects, imported media, timelines and output stay on your
  device by default.
- **Open foundation** — the engine, media tools, project model and local
  workspace are here under the MIT license.

## The family

| | What it is | Where |
|---|---|---|
| **Lumeri Video** | From an idea to an editable timeline. Session, plan, media, preview and timeline in one project. | [video.lumeri.io](https://video.lumeri.io/) |
| **Lumeri Quanta** | Structured, playable video: states, branches and loops, kept in a local-first `.luqu` file. | [quanta.lumeri.io](https://quanta.lumeri.io/) |
| **Lumeri CLI** | `luvi` and `luqu`: the same local runtime from a terminal, with the work streaming past as it happens. | [cli.lumeri.io](https://cli.lumeri.io/) |
| **Lumeri Store** | Skills, point libraries and workflows their makers chose to publish. | [skills.lumeri.io](https://skills.lumeri.io/) |
| **Docs** | Guides for creators, references for developers and integrations. | [docs.lumeri.io](https://docs.lumeri.io/) |

Lumeri is the family name. The video product is **Lumeri Video**.

## How it works

```text
Import media
→ Persist project and timeline
→ Model calls media tools over multiple turns
→ TimelinePatch updates the project
→ Render and inspect a preview
→ Revise from structured feedback
→ Export MP4 or OTIO
```

Every step leaves something you can look at: the plan, each tool call, the
patch it applied, the preview it rendered. When Lumeri needs a decision that is
yours, it stops and asks.

## How Lumeri is built

Lumeri is four parts with a hard line between them.

| Part | What it owns | The rule it lives by |
|---|---|---|
| **App shell** | The window you download, and signing in. | Carries no creative logic. The runtime arrives on its own, so a new capability never waits for a new app. |
| **Agent core** | The model ↔ tool loop, the project and timeline model, render and export. | Small and stable. |
| **Luc** | Everything else: the editor page, panels, tools, skills, libraries. | Each one declares what it needs and is held to exactly that. |
| **Accounts & cloud** | Sign-in, plans, the Store, hosted services. | Lumeri's own. Not in this repository, and out of reach of every Luc. |

This repository is the open engine: the single-user local build, where the
agent core and the editor run as one local server with no account at all.

## Luc

> Luc belongs to the Lumeri Video 1.1 line. The public download and the engine
> in this repository are still 1.0 — see [Where this stands](#where-this-stands).

**Luc is Lumeri's unit of extension.** A panel in the editor, a tool the agent
can call, a skill, a creative library: each is a Luc, one `.luc` file with a
manifest that says what it is and what it needs.

Lumeri holds itself to this. The Video editor is put together from Official
Lucs listed in one registry — the editor's frame, its panels, the tools, the
skills — and each is a line in your config file that you can switch off.

A Luc that is not Lumeri's own stands on the same permission list. It may ask
for anything an Official Luc may, with one exception: what is Lumeri's own —
your account, the model gateway and the cloud services.

### Declared, then held to it

A Luc names what it wants in its manifest, the way an Android app names its
permissions, opened wider. This is what a Luc asks for when it adds its own
panel to the editor and moves clips on the timeline (its manifest, trimmed to
the request):

```json
{
  "format": "lumeri.luc.distribution/v1",
  "kind": "luc",
  "identity": {"id": "com.example.clip-nudge", "version": "1.0.0"},
  "compatibility": {"runtime": ">=1.1.0 <2.0.0"},
  "host": {
    "protocols": ["lumeri.panel/v2"],
    "permissions": ["project.read", "project.write", "ui.mount"],
    "apis": {"required": ["project.view", "timeline.edit"], "optional": []},
    "editable": {
      "target_types": ["timeline_clip"],
      "properties": ["clip.placement"],
      "areas": ["timeline"]
    }
  }
}
```

- `protocols` — what kind of thing it is: a panel, a skill, an interaction, a
  workspace.
- `permissions` — the broad things it asks of the host.
- `apis` — the exact host calls it makes. Each call belongs to a permission,
  and the Luc must name both.
- `editable` — what in a project it may change.

The host grants what is declared and refuses the rest. This Luc can move a
clip. It cannot delete one, and it cannot read a project file, because it asked
for neither. Its panel runs in a sandboxed frame with no network; the only way
out is a declared call. Nothing is granted by who made a Luc — Lumeri's own go
through the same checks against the same list.

| Permission | Who may hold it | What it allows |
|---|---|---|
| `ui.mount` | any Luc | Show its own panel or interaction in the editor. |
| `project.read` | any Luc | Read the open project: timeline, storyboard, files, library, notes. |
| `project.write` | any Luc | Change the open project, only inside the boundary the Luc declares. |
| `session.control` | any Luc | Stop work the creator started. |
| `account.access` | Lumeri only | The creator's account, sign-in and plan. |
| `gateway.access` | Lumeri only | Models and paid services through Lumeri's gateway. |
| `cloud.access` | Lumeri only | Lumeri's cloud services. |

Installing one is putting a file in a folder: `~/.lumeri/lucs`. There is no
install dialog. The file being there is your consent, and what each Luc holds
can be read back at any time.

### Where this stands

Luc belongs to the Lumeri Video 1.1 line. The public download and the engine in
this repository are still the 1.0 line, so nothing in this section runs from
this repository yet.

| | Today |
|---|---|
| The Video editor assembled from Official Lucs | Running in 1.1 builds |
| A Luc of your own registering its panel in the editor | Running in 1.1 builds |
| Code that runs inside the runtime: agent tools, the editor page itself | Official Lucs only |
| Skills, point libraries and workflows as Lucs, on your machine and in the Store | In progress — today they are three separate kinds |
| The Luc host, format reference and packer | Not in this repository |

## This repository

The open engine behind Lumeri Video: Python, FFmpeg, and a local web workspace.

| Path | Responsibility |
|---|---|
| `server.py` | Local HTTP entry point |
| `gemia/v3_routes.py` | Session API and streaming |
| `gemia/agent_loop_v3.py` | Multi-turn model/tool loop |
| `gemia/tools/` | Media tools built on FFmpeg and Python |
| `gemia/project_model.py` | Persistent timeline model |
| `gemia/project_render.py` | Preview renderer |
| `gemia/project_export.py` | Full-quality export |
| `gemia/quanta/` | Quanta: structured, playable video |
| `gemia/ai/skills/` | Built-in skills |
| `gemia/mcp/` | MCP server |
| `lumenframe/` | Composition engine and the craft libraries: grade, type, camera, framing, cutting, rhythm, vector motion |
| `lumerai/patches.py` | Shared timeline patch vocabulary |
| `lumerai/otio_adapter.py` | OpenTimelineIO interchange |
| `static/v3/` | Local web interface |

> The product and this repository are named **Lumeri**. The Python package and
> some engineering paths still use the historical name `gemia`.

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

Configure a supported model provider through environment variables or the
local setup UI, then start Lumeri:

```bash
python server.py
# Open http://127.0.0.1:7788/
```

This build has no account system: no registration, login, account switching,
hosted email, or Lumeri billing. It always opens one local workspace stored on
the current computer. Model-provider configuration stays on that computer and
is never committed to Git.

<details>
<summary><strong>Windows 10/11</strong></summary>

Install 64-bit Python 3.12 or newer, Git, and a complete FFmpeg package whose
`ffmpeg` and `ffprobe` commands are on `PATH`. Then open PowerShell in the
cloned repository:

```powershell
.\scripts\windows\setup.ps1
.\scripts\windows\start.ps1
```

The start script runs the source checkout directly on
`http://127.0.0.1:7788/` and opens the browser workspace. It does not build or
install an EXE. Run `doctor.ps1` for a non-destructive prerequisite and port
check, or pass `-Port 7790` to both doctor/start when 7788 is already occupied.

PowerShell execution policy is left unchanged. If your machine blocks local
scripts, review the scripts first and invoke them for the current process only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Media editing, rendering, export, local voiceover, Windows fonts, Blender
discovery, and OpenTimelineIO bundles run natively on Windows. Arbitrary
model-generated `build`/`run_shell` code stays locked by default because native
Windows does not ship the macOS kernel sandbox used by Lumeri. The local owner
may explicitly disable **Sandbox** in the Lumeri menu to allow PowerShell and
Python execution with full computer access; Lumeri never enables that unsafe
mode automatically.

</details>

<details>
<summary><strong>Use a ChatGPT plan instead of an API key</strong></summary>

Choose **OpenAI 订阅（本机 Codex）** in `/setup`. This option invokes the Codex
CLI on the same computer and only accepts a local **Sign in with ChatGPT**
session. Lumeri never reads, copies, or stores Codex login credentials, and
every person must sign in with their own OpenAI account.

On Windows, install and authenticate the Codex CLI before selecting that option:

```powershell
winget install OpenJS.NodeJS.LTS
npm install -g @openai/codex
codex login
codex login status
```

If Node.js was just installed, reopen PowerShell before running `npm`. The
provider setup panel can open the `codex login` window and refresh the local
status. API-key authentication shown by `codex login status` does not count as
subscription access; complete **Sign in with ChatGPT** instead.

</details>

## Tests

```bash
python -m pytest tests/ -q
```

The suite covers tool contracts, timeline patches, render/export behavior,
OpenTimelineIO interchange, self-correction, sandboxing, sessions, and the web
server.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Contributors

See [CONTRIBUTORS.md](CONTRIBUTORS.md).

## License

MIT
