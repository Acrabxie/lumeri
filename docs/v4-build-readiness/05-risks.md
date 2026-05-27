# 05 — Risks and unknowns for v4 build

> Date: 2026-05-28
> What I'd worry about, if I were the one putting this in front of users.

## Top 3 technical risks

### R1 — Model writes scripts that *look* right but silently produce wrong output

The agent loop catches **exit-code-nonzero** as failure and feeds the traceback back. Exit-code-zero with subtly wrong output (off-by-one keyframe range, swapped R/B channels, audio out of sync by 50ms) **looks like success** to the loop. The model emits a "done!" reply; the user opens the asset; the user discovers the bug.

Why this is a v4-specific risk (not just a v3 issue): the 5 v3 verbs are constrained — they can be wrong only in narrow ways. A `build` script can be wrong in unbounded ways because the model can write any Python.

What v3-A.M3 already showed: the model **does not call `analyze_media` voluntarily** when a v3 verb succeeds. It trusts the tool result text. There's no reason to expect it to do better self-inspection of its own scripts.

**Mitigation that's actually feasible:**
- Make `build` schema require a `verify_with` field: a short Python snippet the model agrees would catch the most likely error (e.g., `assert ffprobe(OUTPUT_PATH).duration > 0`). The sandbox runs `script` then `verify_with`; both must pass. This makes the model think about "how do I know this is right" before writing the script.
- Per-build, automatically run a thumbnail extract (cheap) and have the model see it on the next turn (forced single-step Plan-B). Forces a visual self-check on every build. Adds a model round-trip per build but the cost is bounded.

Both are tractable; neither is free. Pick one when designing.

**Worst-case scenario** I'd plan for: model writes a script that re-encodes audio at the wrong sample rate, output technically plays but has a chipmunk effect, user only notices on playback days later. The loop has no way to detect this.

### R2 — The model gets stuck in a fix-loop and burns the token budget

The agent loop's `max_tool_steps=8` cap means at most 8 build attempts before turn_error. At ~2500 tokens per attempt, an 8-attempt loop is ~20K tokens — roughly $0.30 at current OpenRouter Gemini pricing. Not catastrophic, but it adds up:

- 10 users × 5 stuck loops per week = 50 burned turns × $0.30 = **$15/week** on retries that produced nothing.
- And the user's experience: 8 spinners, then "stopped, max_tool_steps reached". That's the worst possible UX.

What I'd worry about specifically: the model can write a script that fails with a confusing traceback (e.g., numpy dtype mismatch deep inside an opencv call). The model interprets the traceback as "oh, my width is wrong" and tries 5 more variants of width, when the real issue was bit-depth. Each iteration spawns ffmpeg, runs partial work, gets the same wrong-direction error.

**Mitigation:**
- Pre-flight AST check (described in doc 03): catches the trivial mistakes before sandbox spawn. Cuts low-confidence loops by an order of magnitude.
- "Last-traceback heuristic" in the system prompt: if the SAME error class appears twice in a row, the model should stop and ask the user rather than burn another attempt.
- Lower default `max_tool_steps` for `build`-heavy turns specifically. Or: separate `max_build_attempts=3` counter, mirroring how `max_visual_inspections` was carved out from `max_tool_steps` for analyze_media.

### R3 — `sandbox-exec` deprecation lands during v4's lifetime

Apple has said for years that sandbox-exec is "internal use". They haven't deprecated it. They might. If it goes away in macOS 27, v4 needs another isolation layer or it ships broken.

Why this is a real risk: there's no like-for-like replacement on macOS. The App Sandbox requires the whole app to be sandboxed (entitlement file in code signature, which has wide-ranging effects on Lumeri's filesystem access). Virtualization.framework is overkill per-build. Docker is great but third-party.

**Mitigation:**
- Build `gemia/tools/build.py`'s sandbox layer behind a `SandboxAdapter` interface from day one. Default impl: sandbox-exec. Alternate impl ready to write: Docker-based for server deployment.
- Don't promise build-via-sandbox as a marketing feature; it's an implementation detail.
- Track Apple's macOS release notes for sandbox-exec deprecation each year.

---

## Hidden costs Acrab may not have priced in

### H1 — The `lumeri.*` package surface is a forever maintenance commitment

The moment v4 exposes `lumeri.video.color_grade` to model-written scripts, that signature is **stable forever** (or until you break every saved script). v2/v3 could change primitive signatures freely because no one outside `gemia.*` called them. v4 makes them public API.

What this means in practice:
- Every primitive signature change requires backward-compat shims.
- Renaming a primitive breaks user-saved templates.
- Adding required parameters breaks scripts the model wrote in the past.

There's a curation question: do you expose 100 carefully-blessed primitives, or all 813? Smaller surface is forever-maintainable. Larger surface is more capable but pinning down 813 signatures forever is a real cost.

**Quantitatively:** if you expose 100 primitives and each requires ~10 minutes/year of maintenance (deprecation rounds, doc fixes, signature stability work), that's ~17 hours/year of permanent overhead. Exposing 800 would be ~133 hours/year. The bigger the surface, the bigger the bill.

### H2 — User-facing reply quality depends on `summary` field discipline

v3-A.M3 confirmed: the model paraphrases tool result `summary` fields when talking to the user (it said "暖色调（Warm）滤镜" because color_grade's summary said `look='warm'`). This was a good emergent property of the v3 verbs because we wrote thoughtful summaries.

`build` produces output whose summary is **written by the model in its tool args** (per the proposed schema). The model's choice of `summary` becomes the user-facing description. If the model writes `summary: "ran the script"`, the user sees `"我跑了一下脚本"`. If it writes `summary: "10-frame parallax sweep from foreground to background of v_002"`, the user sees that.

There's no host-side way to fix this — it's the model's job. But the system prompt needs to demand high-quality summaries from `build` specifically (more than from v3's 15 verbs, where the host writes the summary).

This is a **prompt engineering cost** that's easy to skip and hard to retrofit.

### H3 — Sandbox profile drift / "but it worked on my machine"

The sandbox profile must allow access to:
- Python's site-packages (for `import numpy, cv2`)
- Homebrew's ffmpeg + ffprobe at known paths
- Any third-party model files Lumeri uses (face detection weights, etc.)

If Acrab installs new homebrew formulas, moves Python, changes the ffmpeg path, or installs Lumeri on a different mac with a different layout — the profile breaks. v3-A already needed a specific ffmpeg (homebrew-ffmpeg tap, not standard homebrew), with subtle implications for `add_overlay`. v4 build will multiply these subtleties because the model can attempt operations the v3 5-verb set never exercised.

**Hidden cost:** every environment difference becomes "model wrote correct script, sandbox blocked it, model concluded the primitive doesn't exist, retried with worse code". Hard to debug from the user's side.

Mitigation: have a `lumeri doctor` (or equivalent) that probes the sandbox at startup and lists what the model is allowed to do. This is ~50 lines of code but its existence reduces the "but it worked on my machine" mystery.

### H4 — The Tauri app's privilege boundary may collide with sandbox-exec

Tauri apps run their backend (Rust) in a single process. When that process forks `sandbox-exec`, it inherits the Tauri app's filesystem perms. If the user wraps the Tauri app in macOS App Sandbox for distribution (likely for notarization), the entitlements need to allow forking sandbox-exec — which is a thing Apple has been increasingly skeptical of (you may need the `com.apple.security.cs.disable-library-validation` entitlement, or similar).

**Untested.** v3-A runs the server.py directly via `python3 server.py`, not via the Tauri webview's bundled Rust. The minute v4 build wants to run sandbox-exec via the actually-Tauri-bundled-Rust app, there's a real chance entitlements/notarization complications appear.

Mitigation: prototype this *before* committing to the architecture. Spawn `sandbox-exec` from inside the current Tauri build and see if it works.

### H5 — Determinism / reproducibility expectation

Users will save a "skill" (a build script that worked) and run it on different inputs later. They'll expect the same script to behave the same way. But:
- ffmpeg output isn't byte-stable across versions.
- numpy/opencv versions affect output.
- Sandbox quirks (e.g. `TMPDIR` being different) can affect tempfile-using primitives.

When the user comes back in 6 months and says "this saved skill produces different output now", the answer is going to be "the deps changed". That's not satisfying.

Mitigation: pin Python + numpy + cv2 + ffmpeg versions in a `requirements-runtime.txt` and a homebrew bundle script. Document that saved skills are tied to the Lumeri version that produced them.

---

## What I'd want to know before designing v4

These are unknowns I couldn't resolve from the code:

1. **Does Acrab want users to write code themselves, or only the model?** Affects the surface (do we need a code editor in the UI?) and the threat model (untrusted user input is a much harder problem than trusted-model-careless-output).
2. **Is `build` cumulative across turns?** Can the model write a script in turn 1, then a follow-up script in turn 3 that imports a function from the turn-1 script? Or is each `build` invocation independent? Affects how the sandbox manages state between calls.
3. **What's the latency budget for a build call?** 30s feels fine for a one-off creative request. If the model goes into a 5-retry loop, that's 2.5 minutes. Acceptable?
4. **Saved-template UX: who curates?** If users save skills aggressively, the system prompt's "saved templates" list grows unbounded. Does the user pick which 10 to surface to the model? Does the model auto-pick? Does the host vector-search by request?
5. **Failure visibility:** when build fails, does the user see the model's full script? Sanitized? Just the error? Affects how scary v4 feels to use.

These are Opus questions, not implementation questions. Flag them when handing off.

---

## What I'd worry about, if I were doing this project

Three things that aren't strictly "technical risks" but would keep me up:

**A. The 15-verb canned set already covers ~80% of real creative tasks.** v3-A.M3 worked with 5 verbs. The marginal value of `build` is the long tail. Long-tail features get a lot of design love and not a lot of use. Make sure there's a real use case (not a hypothetical one) before committing the 4-week implementation cost.

**B. Token cost asymmetry.** Once `build` is in the schema, the model may reach for it instead of the cheap canned verbs because "writing a script feels more thorough". This is a real failure mode for LLM agents — they over-build. The system prompt has to actively discourage this, and may need monitoring/telemetry to detect drift.

**C. The "saved skill" feature is a small business waiting to happen** (Lumeri marketplace of skills) — but only if the skill format is portable enough to share. Right now there's no path to making saved skills work between users (paths differ, models differ). If the answer is "every user re-builds their own", the saved-skill value prop is much weaker than it sounds.

I would not start v4 until the team has a clear answer to "what's the demo that proves this is worth 4 weeks of work?" The 15-verb v3-A.M3 demo is already pretty good. Building one's own primitives is cool but it's a developer thrill, not necessarily a user-facing win.

---

*Risk assessment based on the code as it stands on 2026-05-28 commit `ee3c433` (plus the editor's in-progress edits to v3 layer). All concrete claims back-referenced to docs 01-04.*
