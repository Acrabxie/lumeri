# Lumeri Agent-Loop Phase 1 — prompt surgery + orchestration temperature/model hardening

**Branch:** `feat/agent-loop-phase1` (base `5b0d274`)
**Commits:** `b45408e` (Fix A) · `0856034` (Fix B) — author+committer `Acrab <Acrabxie@users.noreply.github.com>`
**Basis:** `docs/agent-performance-diagnosis.md` §7 items 1–2 (root causes RC1, RC4-prompt-half, RC5, RC6).
**Status:** verified, **not merged, not pushed**. Activation requires a sidecar restart (see *User actions*).

## Context

The three-party diagnosis established that the orchestrator resolves to a frontier model
(`google/gemini-3.1-pro-preview`), so Lumeri's agent gap is **structural** (how the loop is
prompted/configured) — not raw model capability. Phase 1 lands the two cheapest, highest-leverage
fixes. It deliberately does **not** touch `agent_loop_v3.py`.

## What changed

### Fix A — Prompt surgery (RC1 + RC4 prompt-half) · `gemia/prompts/system_v3.md`
- Removed the two anti-planning passages ("…not on a plan you wrote at the start"; "**One step at a
  time is fine.** …").
- Replaced with **"Plan multi-step work, then adapt"**: for anything beyond a single obvious action,
  outline a short plan first, execute one step at a time, and revise it from real results; a single
  obvious action just gets done. Preserves the existing *iterate from observation* principle.
- Added an **honest persistence** working-principle (RC4): re-check the pinned intent before declaring
  done; continue if steps remain; stop only when the goal is genuinely met or you are truly blocked
  (and then say exactly what's blocking). Explicitly preserves the no-fake-progress strength — never
  imply done when it isn't, never re-issue a call the host already stopped.

### Fix B — Orchestration temperature + model visibility (RC5 + RC6) · `gemia/gemini_client.py`
- **RC5 (temperature):** new `_resolve_orchestration_temperature()` → env `LUMERI_V3_TEMPERATURE` →
  config `lumeri_v3_temperature` → **default `0.2`**; parsed to float, clamped to `[0.0, 1.0]`, any
  parse failure falls back to `0.2` (never raises). Stored as `self.orchestration_temperature`.
  `stream_turn(...)` signature changed `temperature: float = 0.7` → `temperature: float | None = None`:
  when the caller passes nothing (the agent loop's path) it uses the low orchestration temperature; an
  explicit value (a future creative-generation caller) still overrides. **The loop call site is
  unchanged.**
- **RC6 (flash-tier landmine):** at client construction a single `logger.info` records the **resolved**
  provider/model/temperature — never the api_key, credentials, or any config contents. `_is_weak_model()`
  (model contains `"flash"` or equals the vertex/gemini flash defaults) triggers a loud `logger.warning`,
  making a silent flash-tier downgrade visible. Warning-only; it does not change behavior.

## Verification

- **Delivery gate:** full suite **1426 passed / 5 known pre-existing / 0 regressions** (at original
  delivery; the commits are byte-identical since).
- **Close-out re-check (this doc), on the worktree code:**
  - `_resolve_orchestration_temperature`: default `0.2`; env `0.05`→`0.05`; clamp `5`→`1.0`, `-1`→`0.0`;
    bad→`0.2`.
  - `_is_weak_model`: `True` for `gemini-3.5-flash` and `gemini-2.0-flash`, `False` for
    `gemini-3.1-pro-preview`.
  - `stream_turn` defaults to `None` and uses `self.orchestration_temperature`.
  - Hermetic loop/prompt tests (`test_v3_tool_protocol`, `test_v3_failure_breaker`,
    `test_prompt_slimming`) = **10 passed**.
  - A full-suite re-run was intentionally skipped in this close-out: in a sandbox-restricted session it
    produces permission/socket noise that is **not comparable** to the green baseline, and the diff is
    unchanged from the delivery-verified commits.

## Deferred to Phase 2 (not in this change)

- **RC2** — intra-turn context compaction (the hard scaling ceiling; top priority for Phase 2).
- **RC3** — parallel tool dispatch.
- RC1 optional plan/todo ledger; RC4 host-side completion-check gate.

## User actions (Acrab)

1. **Restart the sidecar** to activate Phase 1 — the new prompt + temperature only take effect on restart.
2. *(optional)* Pin in `~/.gemia/config.json` — **your action; that file holds the API key**:
   `lumeri_v3_provider=openrouter`, `lumeri_v3_model=google/gemini-3.1-pro-preview`,
   `lumeri_v3_temperature=0.2`. Defuses the RC6 flash-tier landmine; the temperature key is optional
   (the code already defaults to `0.2`).
3. **push / merge** when ready.

## Compliance

Acrab identity throughout; `~/.gemia/config.json` never read or printed; nothing pushed or merged;
`main`, the other worktrees, and the live sidecar untouched. The exFAT `._pack-*.idx` "non-monotonic
index" lines are a benign macOS AppleDouble artifact on the external SSD and are ignored.
