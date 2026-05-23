# Lumeri opencode Base Plan

Date: 2026-05-23

## Decision

Stop hand-building an opencode-like surface in `static/next.html`. Lumeri vNext should expose and consume an opencode-shaped contract first, then keep replacing bespoke UI/runtime glue with that contract.

Reference source:

- Local reference: `/Volumes/Extreme SSD/GemiaReferences/opencode`
- Package checked: `opencode-ai` 1.15.10 source tarball
- License: MIT, retained in the reference source

## What To Copy Conceptually

- Session is the primary unit: `GET/POST /session`, `GET /session/:id`, `GET /session/:id/message`.
- User work enters the system as message parts: `POST /session/:id/prompt_async`.
- Runtime progress is event-driven: `/event` and `/global/event` emit OpenCode-style SSE events such as `message.updated` and `message.part.updated`.
- Tool work is represented as message parts, not hidden logs: shell commands return `tool` parts with `running/completed/error` state.
- Workspace files are addressable through `/file`, `/file/content`, `/file/status`, `/find`, and `/find/file`.

## First Compatibility Slice

Implemented in:

- `gemia/opencode_compat.py`
- `server.py`
- `static/next.html`
- `tests/test_server_static_web.py`

Routes now available while `LUMERAI_VNEXT=1`:

| Route | opencode role | Lumeri backing |
| --- | --- | --- |
| `POST /session` | create session | `RuntimeService.create_session()` |
| `GET /session` | list sessions | `sessions/*/meta.json` |
| `GET /session/:id` | get session | `SessionStore` metadata |
| `GET /session/:id/message` | message timeline | Runtime + sandbox events mapped to OpenCode `MessageV2.WithParts` |
| `POST /session/:id/prompt_async` | async user prompt | background Runtime Kernel task |
| `POST /session/:id/shell` | shell tool call | Creative Sandbox runner |
| `GET /event` / `/global/event` | SSE bus | mapped `session.updated`, `message.updated`, `message.part.updated` |
| `GET /file` / `/file/content` / `/file/status` | workspace file API | canonical Lumeri repo/workspace root |
| `GET /find` / `/find/file` | search API | local text/file scan |

`/next` now creates sessions through `/session`, sends execution prompts through `/session/:id/prompt_async`, reads OpenCode messages through `/session/:id/message`, and runs terminal commands through `/session/:id/shell`.

## Non-goals For This Slice

- Do not vendor or rewrite the full SolidJS opencode app yet.
- Do not remove existing `/runtime/*` routes; they remain the Lumeri-specific execution backend.
- Do not expose secret-like filenames through the file API.
- Do not use OpenClaw.

## Next Product Step

Replace the current custom transcript renderer with a message-part renderer:

1. Render `TextPart` as assistant/user prose.
2. Render `ToolPart` as tool rows with state, command, stdout/stderr, and artifacts.
3. Render `PatchPart` and file/tool metadata directly into the editor tabs.
4. Move runtime event polling to `/event` SSE instead of `/runtime/events/:id` polling.

