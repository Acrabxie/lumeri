# Lumeri Agent Instructions

## Repository Structure

This is **Lumeri Video** — an agentic video project workspace.

### Core Architecture (v3)

- `server.py` — HTTP server entry point, serves web UI and delegates to v3 routes
- `lumeri/v3_routes.py` — Session HTTP API (`/sessions/*`)
- `lumeri/agent_loop_v3.py` — Multi-turn model ↔ tool conversation loop
- `lumeri/tools/` — The agent's tool table (104 tools: media, timeline, LumenFrame, shotlist, files, and agent support; `len(DISPATCHER)` is authoritative)
- `lumeri/sandbox_v4.py` — Sandboxed script execution and security boundary
- `lumeri/gemini_client.py` — Gemini API client for the agent loop
- `lumeri/ai/google_genai_client.py` — Google GenAI client for generative tools
- `lumeri/budget_guard.py` — Step and cost budget enforcement
- `lumeri/errors.py` — Structured errors with recovery protocol
- `lumeri/session_manager.py` — Session lifecycle and concurrency
- `lumeri/session_telemetry.py` — Telemetry and observability
- `lumeri/transport/sse.py` — Server-Sent Events for real-time UI updates

### Project Model

- `lumeri/project_model.py` — Timeline data model (tracks, clips, effects)
- `lumeri/project_store.py` — Persistent project state (JSON files)
- `lumeri/project_render.py` — FFmpeg-based preview rendering
- `lumeri/project_export.py` — Full-quality export (MP4, OTIO)
- `lumeri/project_inspect.py` — Project introspection utilities

### Patch System

- `lumeri_script/patches.py` — TimelinePatch vocabulary (shared by AI and human editing)
- `lumeri_script/otio_adapter.py` — OpenTimelineIO ↔ Lumeri project round-trip
- `lumeri_script/sandbox.py` — Legacy sandboxed script execution wrapper

### Composition, Quanta & Specifications

- `lumenframe/` — Composition engine and craft libraries (grade, type, camera, framing, cutting, rhythm, vector motion)
- `quanta/format/` — The `.qua` file format specification and samples (CC0)
- `docs/luc/` — Luc package specification, runtime contract, permissions and protocol
- `contracts/interfaces/` — Machine-readable interface contracts for runtime modules (JSON)

### Frontend

- `static/v3/` — Web UI (HTML/CSS/JS) with multi-track timeline, chat, preview

## Rules

1. All timeline modifications — whether from the AI agent or human direct editing — MUST go through the TimelinePatch vocabulary in `lumeri_script/patches.py`.
2. The v3 tool protocol is the only agent path. Do not add alternative agent loops.
3. Media processing uses FFmpeg via `lumeri/tools/_ffmpeg.py`. Do not add alternative media backends.
4. Do not store secrets, API keys, or credentials in code or config files in the repo.
5. Run `python -m pytest tests/ -v` before committing changes.
