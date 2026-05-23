# Lumeri Codex Self-Loop

Run one small, auditable improvement iteration from the project root:

```bash
python3 scripts/codex_lumeri_self_loop.py --iterations 1 --goal "<small goal>"
```

The runner writes each iteration under `workspaces/lumeri-codex-self-loop/` by default, keeping artifacts inside the current external-disk checkout. Use `--output-root` only when that location is writable in the active sandbox.
Each iteration contains:

- `codex-prompt.md`: the iteration prompt and product guardrails.
- `context.json`: redacted shared-loop and project-state context.
- `workspace-diagnostics.json`: machine-readable capability gaps, restore hints, and next recovery focus.
- `manifest.json`: machine-readable status, paths, and diagnostics.
- `codex-result.md`: human-readable result, verification, blocker, and next follow-up.

This keeps Lumeri moving toward a Codex/Claude Code-style video coding environment:
the workspace state, execution trail, result artifact, and follow-up are files that the next agent can inspect.
The runner must not use OpenClaw and must not expand the frozen old default UI.
