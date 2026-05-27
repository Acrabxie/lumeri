# 02 — Sandbox options for Lumeri v4 build capability

> Date: 2026-05-28
> Constraint: Acrab's primary machine is macOS arm64 (Apple Silicon). Distribution target: Tauri-packaged desktop app for end users on the same architecture.

## What the sandbox has to do

For v4 "model writes a script and runs it", the threat model is:

1. The model is not malicious but it is **carelessly creative** — it may try to `rm -rf`, write to `~/.ssh`, exfiltrate the OpenRouter key via `urlopen`, or shell out to `git` and push something dumb. The sandbox is what makes "let the model run code it just wrote" not horrifying.
2. The user's source media and the session output directory MUST be writeable.
3. Network egress should default to `deny` so a confused script doesn't leak file contents.
4. ffmpeg / ffprobe MUST be exec-able (without them, 95% of primitives don't work).
5. Standard-library Python MUST work; opening files for read in the system Python stdlib (e.g. importing numpy from site-packages) MUST work.

## Five options compared

### Option 1 — `sandbox-exec` (Apple's built-in)

Built into every macOS, no install. Uses Apple's TrustedBSD MAC framework. Profile is a Lisp-flavored DSL.

| Dimension | Reality |
|---|---|
| **Isolation strength** | Filesystem: allow/deny per-path with subpath/literal granularity. Network: granular allow/deny by socket type. Process: can restrict to specific binaries with `(allow process-exec ...)`. Resource: combine with `setrlimit` for CPU/AS. **Strongest of any per-process option on macOS without virtualization.** |
| **macOS availability** | Built in. No install. Works on every macOS Acrab might run. |
| **Startup overhead** | Measured: **~30-50ms total for sandboxed Python startup, same as raw Python.** sandbox-exec itself adds <5ms. Confirmed via 5 trials each in this audit. ffmpeg invocation overhead identical to raw after page cache warm-up. |
| **Integration cost** | `gemia/creative_sandbox_runner.py:293-313` already implements a working profile generator and `_sandbox_command` helper for v2. ~500 lines of reusable scaffolding. v3 hasn't touched it yet but the design intent is sound. |
| **Distribution cost** | **Zero.** Nothing for the user to install. Works inside the Tauri app's privilege boundary out of the box. |

**Caveat:** Apple has been signalling for years that `sandbox-exec` is "for internal use" and could be deprecated in some future macOS. As of macOS 15 (Sequoia) it still works and is unchanged. There is no announced replacement for third-party use. Real risk is "Apple deprecates this in 2027" — not "Apple deprecates this next month". The fallback would be Application-Sandbox extensions, which require Mac App Store distribution; not viable for Lumeri's likely distribution model.

### Option 2 — Docker

Container with strict resource and filesystem boundaries. Hugely battle-tested.

| Dimension | Reality |
|---|---|
| **Isolation strength** | Strongest off-the-shelf option. Full filesystem namespacing, network isolation, cgroup limits. |
| **macOS availability** | **Not installed on Acrab's box** (confirmed via `which docker`). Requires Docker Desktop or OrbStack. ~1-4 GB install. Subscription terms on Docker Desktop. |
| **Startup overhead** | Cold container start: 200ms-1s+. Warm pool of containers can cut this. Order of magnitude worse than sandbox-exec for "one container per primitive call". |
| **Integration cost** | Need a base image with Python + numpy + opencv + ffmpeg pre-installed (~500MB-1.5GB image). Mount source dir + session workdir as bind mounts. Network = `--network none`. Plus orchestration: container lifecycle, log streaming, kill timeouts. |
| **Distribution cost** | **High.** Bundling Docker with a Tauri desktop app is hostile to end users. Asking users to install Docker themselves is friction the user-base won't tolerate. |

**Verdict for v4:** Docker is the right answer for *server-deployed* Lumeri but a non-starter for the *desktop-distributed* build we have today. Keep in mind as a future option if Lumeri ever runs server-side.

### Option 3 — `subprocess` + `RLIMIT_CPU` / `RLIMIT_AS` + restricted PATH

Soft isolation: fork a child Python process, drop its CWD to the workdir, set environment to only expose what's needed, set `setrlimit(RLIMIT_CPU, ...)` and `RLIMIT_AS` for resource caps. No filesystem or network isolation.

This is what `lumerai/sandbox.py:1-276` already does, paired with an AST blacklist (line 14 onward) that scans for forbidden imports.

| Dimension | Reality |
|---|---|
| **Isolation strength** | **Weak.** AST blacklist can be evaded (e.g. `__import__('os')`, `getattr(__builtins__, 'eval')(...)`). No filesystem isolation: the child process can read `~/.ssh/id_rsa` because the OS only sees the same UID. No network isolation. **Treat as defense in depth, not the primary line of defense.** |
| **macOS availability** | Native, no install. |
| **Startup overhead** | Negligible — same as raw `subprocess`. |
| **Integration cost** | Low if you accept the weak isolation. `lumerai/sandbox.py` is the existing prototype but the v3 spec called it "花架子" and it was right: it can't actually stop a determined script. |
| **Distribution cost** | Zero. |

**Verdict:** Useful as a layer (always set resource limits even when using sandbox-exec) but not adequate alone. The AST blacklist is genuinely a fig leaf.

### Option 4 — Pyodide / WebAssembly

Run Python in a sandboxed WASM runtime (browser or Node).

| Dimension | Reality |
|---|---|
| **Isolation strength** | Very strong by construction — WASM is a guest VM, can't escape the host. |
| **macOS availability** | Need a host: browser or Node.js. Node is on Acrab's box (`v25.9.0`). |
| **Startup overhead** | Pyodide cold init: 1-3 seconds (loading the WASM blob + Python interpreter). Warm: fast. |
| **Integration cost** | **Catastrophic for our use case.** Pyodide has *no* ffmpeg, *no* opencv-python (or rather a minimal port without GPU/native codec deps), *no* numpy beyond a bundled subset. Re-creating the 813 primitives inside Pyodide means re-implementing them in pure WASM-compatible Python or porting native deps. Years of work. |
| **Distribution cost** | Pyodide bundle is ~10-30 MB. Acceptable. |

**Verdict:** Wrong tool. Pyodide is for "let users run user-written Python in the browser"; we need "let the model run scripts that call our existing ffmpeg-and-numpy pipeline".

### Option 5 — `firejail` / `nsjail` (Linux-only)

Listed because it's frequently mentioned in sandbox discussions.

| Dimension | Reality |
|---|---|
| **macOS availability** | **None.** Linux-only. Confirmed via `which firejail` and `which nsjail`. Both not present. |

**Verdict:** Not applicable. Keep in mind if Lumeri ever ships a Linux server.

### Bonus options briefly considered

- **`RestrictedPython`** (Plone-flavored Python AST rewriter). Not installed. Provides bytecode-level restrictions but the same problems as Option 3 — can be evaded, and our primitives import numpy/cv2/ffmpeg which RestrictedPython does not understand. Skip.
- **Apple App Sandbox via entitlements.** Requires the whole app to be sandboxed (entitlement file in code signature). Affects every part of Lumeri, not just the build runtime. Architectural change too large for v4 scope. Defer.
- **macOS Virtualization.framework / Apple's lightweight VMs.** Real VMs, strong isolation. Overhead ~seconds per VM. Distribution: user needs to install nothing (it's in macOS) but startup overhead disqualifies it for "one VM per build attempt".

## Recommendation

**Use `sandbox-exec` for v4 build, reuse the profile generator that already exists in `gemia/creative_sandbox_runner.py:293-313`. Layer `setrlimit(RLIMIT_CPU, RLIMIT_AS)` on top via `preexec_fn` to bound runaway scripts.** Don't ship the AST blacklist (`lumerai/sandbox.py`) — sandbox-exec is the real wall; the blacklist invites a false sense of security.

Profile sketch (Lisp DSL the existing code already produces):

```scheme
(version 1)
(deny default)
(allow process*)                              ; fork/exec inside sandbox
(allow sysctl-read)                           ; needed for numpy/cv2 init
(allow file-read*)                            ; system libs, site-packages
(allow file-write* (subpath "<SESSION_WORKDIR>"))
(allow file-write* (subpath "<PER_SESSION_TMP>"))   ; tempfile target
(allow process-exec (literal "/opt/homebrew/bin/ffmpeg"))
(allow process-exec (literal "/opt/homebrew/bin/ffprobe"))
(deny network*)                               ; opt-in elsewhere
```

Wrap with `subprocess.Popen` + `preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CPU, (60, 65))` for CPU cap.

### Known risks of this recommendation

1. **`sandbox-exec` is "deprecated but unstaffed-for-removal"** in Apple's posture. Real lifetime is probably another 3-5 macOS releases at minimum, but it's not a guarantee. Mitigation: keep the sandbox layer behind an interface so we can swap to Docker (for server) or Virtualization.framework (for desktop, if Apple actually removes sandbox-exec).
2. **Profile is permissive enough to let the script call `subprocess.run(["ffmpeg", ...])` with attacker-controlled args.** ffmpeg has a CVE history with crafted media files. We're already running ffmpeg on user-supplied input outside the sandbox today, so this isn't a regression — but if v4 lets the model construct ffmpeg filter graphs, ffmpeg's own attack surface becomes the surface. Mitigation: strip dangerous filter classes from a hand-curated allowlist when the model constructs filtergraphs.
3. **`tempfile` writes** go to `$TMPDIR` (`/var/folders/...`). The profile must include `(allow file-write* (subpath "<TMPDIR>"))` or — better — the sandbox launcher should `export TMPDIR=<session_workdir>/tmp` so all tempfile usage stays inside the per-session jail. The existing `gemia/creative_sandbox_runner.py:318-355` `_command_env` already does environment scrubbing; extend it to set `TMPDIR`.

### Risk we accept by not adopting Docker

A determined attacker (or a wildly broken script) cannot break out of sandbox-exec but CAN escalate to ffmpeg-as-attacker-controlled-binary, which has had real RCEs in the past. Docker would isolate ffmpeg itself behind a kernel namespace; sandbox-exec only restricts what the process can do. For Acrab's threat model — single user, trusted model — this is acceptable. For multi-tenant SaaS, Docker (or VMs) is required.

---

*Verified via `which sandbox-exec` (`/usr/bin/sandbox-exec`), live profile execution timing, and reading `gemia/creative_sandbox_runner.py:275-313`.*
