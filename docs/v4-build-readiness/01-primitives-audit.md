# 01 — Primitives sandbox-readiness audit

> Date: 2026-05-28
> Branch: `claude/jolly-clarke-JO7E3`
> Scope: every callable in `gemia/picture/`, `gemia/audio/`, `gemia/video/`.

## Headline numbers

**The "88 primitives" figure in earlier v3 docs is wrong by an order of magnitude.** The registry that already auto-discovers primitives (`gemia/registry.py:14-29`) sweeps three packages and produces:

```
$ python3 -c "from gemia.registry import get_registry; print(len(get_registry()))"
813
```

Per-domain breakdown:

| Domain | `.py` files | Public defs (grep) | Registered primitives | In model-facing catalog |
|---|---:|---:|---:|---:|
| `gemia/picture/` | 7 | 228 | 226 | 208 |
| `gemia/audio/` | 11 | 164 | 164 | 157 |
| `gemia/video/` | 83 | 435 | 423 | 409 |
| **Total** | **101** | **827** | **813** | **774** |

Excluded from the model-facing catalog (`registry._EXCLUDED_FROM_CATALOG`, lines 33-80): 39 primitives that take `np.ndarray`/PIL/keyframe/etc. args that Gemini can't construct from JSON. Plus a handful filtered for being internal helpers despite having a public name.

`_CORE_FQNS` (registry.py:104) hand-curates 47 "always include" core verbs for the planning prompt, but the catalog itself emits 774 entries — Gemini was seeing essentially everything in v2.

## Interface purity (the load-bearing number for sandbox exposure)

Inspected all 813 via `inspect.signature` (script: `gemia/registry.get_registry` + walk):

| Bucket | Count | % |
|---|---:|---:|
| **Pure** — only `str` / `Path` / `int` / `float` / `bool` / `list` / `dict` params | **742** | **91.3%** |
| Takes `ndarray` / `PIL.Image` / `AudioData` | 69 | 8.5% |
| Takes `Callable` | 2 | 0.2% |
| Returns in-memory `ndarray` / `Image` / `AudioData` (not a path) | 72 | 8.9% |

The 69 ndarray-taking primitives are concentrated in two patterns:
- compositing blend operations (`gemia.picture.composite.blend_multiply`, `blend_overlay`, etc. — already excluded from catalog)
- low-level image filters that operate on already-loaded buffers (`gemia.picture.color.color_grade(img, ...)`)

Per `gemia/registry.py:33-80`, these already require a parallel "loaded" variant or are excluded from what the model sees. That curation logic is reusable for v4.

**Conclusion: 91% of primitives are sandbox-ready today.** A `lumeri` Python package exposing the pure subset would cover ~740 callables with zero refactor. The remaining ~70 either need (a) thin wrappers that load the ndarray from a path before calling, or (b) outright exclusion.

## Internal import coupling

61 of 101 primitive files import from other `gemia.*` modules. That's the load-bearing graph for "what gets pulled in when you `import lumeri.video.X`".

The hub modules (shared utilities every primitive touches):

```
$ grep -rhn "^from gemia\." gemia/picture/ gemia/audio/ gemia/video/ --include="*.py" \
    | awk '{print $2}' | sort | uniq -c | sort -rn | head -10
```

| Imported module | Importers | Notes |
|---|---:|---|
| `gemia.primitives_common` | ~50 | `Image`, `batchable`, `ensure_float32`, `to_uint8` — small, pure helper. Safe. |
| `gemia.video.timeline_assets` | ~12 | `probe_media`, `extract_waveform_peaks`, `cache_key_for_path`. Pure file IO + ffprobe. Safe. |
| `gemia.video.backends.*` | ~8 | `RenderProfile`, `choose_render_backend`. Pulls in opencv/PIL backends — heavy but pure-Python. |
| `gemia.video.compositing_graph` | ~6 | DAG primitives. Self-contained. |
| `gemia.picture.composite` | ~5 | blend helpers. |
| `gemia.video.export.proxy_generate` | rare | ffmpeg wrapper. |
| `gemia.audio.speaker_timeline` | rare | normalize helpers. |

None of the hubs reach back into agent_workflow / orchestrator / opencode_compat / runtime_vnext. The primitive layer is genuinely a leaf in the dependency graph — it doesn't know about session state, the planner, or the HTTP server. That's the precondition for safe sandbox exposure.

**No primitive imports `gemia.agent_workflow`, `gemia.orchestrator`, `gemia.agent_loop_v3`, or the session manager** (verified by grep, 0 hits).

## File IO escape risk

82 of 101 primitive files do file IO. Most write to explicit `output_path` arguments — sandboxable. The escape risks:

- **`tempfile.mkdtemp` / `NamedTemporaryFile` in 14 files** — writes to `$TMPDIR` (typically `/var/folders/...` on macOS), outside any session workdir. Will leak files unless the sandbox profile mounts `$TMPDIR` writeable, OR the sandbox profile forces `$TMPDIR` to a per-session path. The latter is cleaner.
- **`Path.home()` / `os.path.expanduser` in 4 files**: `gemia/picture/enhance.py`, `gemia/video/stock_media.py`, `gemia/video/delivery_scene.py`, `gemia/video/fonts.py`. Mostly cache directories. Each needs inspection before sandbox exposure.
- **`Path.cwd()` / `os.getcwd()`**: 0 hits. Good.
- **Hardcoded `/Users/...` or `/Volumes/...`**: 0 hits in primitives. Good.

## Network risk

Only 4 primitive files do network IO (all via `urllib.request.urlopen`):

| File | Purpose |
|---|---|
| `gemia/video/stock_media.py` | Pexels/etc. stock media API (line 165, 192, 301, 378) |
| `gemia/video/generative.py` | Veo / generative provider POSTs (line 561, 580) |
| `gemia/video/layer_flow.py` | One urlopen call at line 260 (likely a remote layer manifest fetch) |
| `gemia/video/fonts.py` | Google Fonts payload + font binary download (line 290, 324) |

These 4 files have ~30 primitives between them. Default sandbox profile should `(deny network*)`; opt-in profiles for these 4 modules. Or: pre-fetch all assets host-side, pass file paths into the sandbox, never let the sandbox touch the network.

## Subprocess risk

35 files use `subprocess.run` / `Popen`. Sampling 10 lines confirms they all invoke `ffmpeg` / `ffprobe` (no `bash`, `curl`, `git`, `python3` shell-outs from inside primitives — verified by spot grep). The sandbox profile needs `(allow process-exec (literal "/opt/homebrew/bin/ffmpeg"))` or equivalent. The existing `gemia/creative_sandbox_runner.py:293-313` profile already follows this pattern (`(allow process*)`).

## What needs to happen to expose all 813 to a sandbox-built script

| Action | Approx work | Notes |
|---|---|---|
| Create `lumeri/` thin package re-exporting `gemia.{picture,audio,video}` | 1-2 hours | Just `from gemia.X import *` plus an `__all__` curation. |
| Wrap the 69 ndarray-taking primitives with path-in/path-out variants | 8-12 hours | One wrapper per blend/composite call; reuse `cv2.imread` + `cv2.imwrite`. Or skip the bottom 50 obscure ones. |
| Audit the 4 network primitives — decide deny / opt-in / host-prefetch | 2 hours | Architectural decision, not coding work. |
| Audit the 14 `tempfile` files — fix to use session workdir or accept the leak | 2-3 hours | Most are short-lived intermediates; leak is tolerable. |
| Audit the 4 `Path.home()` files — same decision tree | 1 hour | |
| Sandbox profile: `(deny default)` + `(allow file-write* (subpath SESSION_DIR))` + `(allow process-exec ffmpeg/ffprobe)` + `(deny network*)` | 1 hour | Already prototyped in `gemia/creative_sandbox_runner.py:293-313`. |
| **Total to first-pass sandbox-ready** | **~16-24 hours** (2-3 days) | |

The expensive remainder (curate exposed primitive subset, write `lumeri` package docs the model can read) is design work, not implementation. Quoted separately when the architectural shape is decided.

## What the model already sees (today, no v4 work)

`gemia.registry.catalog_for_prompt()` already emits all 774 catalog primitives as signatures Gemini could in principle call — but v3 doesn't expose them, only 15 verb wrappers. The infrastructure to surface a much larger primitive vocabulary to the model is mostly already there; v4 either reuses it directly (verb = "call primitive by name") or builds on top (verb = "write a script that imports `lumeri`").

## What's NOT in this audit

- Per-primitive correctness (do they handle bad inputs?). v3-A.M3 Codex review already surfaced 2 cases (F2, F3); a sample of 813 will surface more.
- Performance under sandbox restrictions (RLIMIT_CPU / RLIMIT_AS).
- Behavior under concurrent invocation. Most primitives appear stateless but a few (`gemia/video/timeline_assets.cache_key_for_path`) touch shared caches.
- Whether the 813-primitive surface is actually useful as a "vocabulary" — many are deeply specialized one-offs (e.g. `gemia.video.atem_mini_import.render_atem_mini_project_import_timeline_manifest`). A curated subset of ~150 will likely be more useful than the full 813. This is design work for Opus.

---

*Audit method: grep + `inspect.signature` walk over `gemia.registry.get_registry()`. Reproduce with the commands inline above.*
