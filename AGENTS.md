# Gemia Agent Instructions

## Memory Read Order

For trusted local Gemia sessions, read memory in this order before changing code or runtime state:

1. Shared agent loop:
   - `/Users/xiehaibo/.agents/shared-agent-loop/ROLES.md`
   - `/Users/xiehaibo/.agents/shared-agent-loop/QUEUE.md`
   - `/Users/xiehaibo/.agents/shared-agent-loop/MEMORY.md`
   - today's shared daily log, plus yesterday if the loop was active
2. Gemia project memory:
   - `/Users/xiehaibo/.gemia/memory/ROLES.md`
   - `/Users/xiehaibo/.gemia/memory/QUEUE.md`
   - `/Users/xiehaibo/.gemia/memory/MEMORY.md`
   - `/Users/xiehaibo/.gemia/memory/MODEL_PROFILE.json`
   - today's Gemia daily log, plus yesterday if active
3. Local project records and source files in `/Users/xiehaibo/Code/gemia`.

## Write Rules

- Use shared `QUEUE.md` for cross-agent ownership, delegation, blockers, and handoffs.
- Use Gemia memory for project-specific durable facts, local preferences, and model defaults.
- Use Gemia `daily/YYYY-MM-DD.md` for short raw project progress notes.
- Keep runtime state in `.gemia/automation`, `.gemia/bridge`, `.gemia/server`, or `.gemia/workspace`, not in memory.
- Do not store secrets, tokens, passwords, API keys, or raw private conversations in shared or Gemia memory.

## Model Defaults

- Primary planner: `gemini-3.1-pro-preview` (`Gemini3.1pro`)
- Image generation: `gemini-3.1-flash-image-preview` (`nano banana2`)
- Video generation: `veo-3.1-generate-preview` (`veo3.1quality`)
- Audio generation: `lyria-3-pro-preview` (`lyric2pro`)
