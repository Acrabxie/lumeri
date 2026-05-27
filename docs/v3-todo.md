# v3 polish backlog (post v3-alive, pre-production)

Findings from Codex `exec review --base main` on 2026-05-27 after the
v3-alive milestone (`docs/v3-alive-evidence.log`). None of these blocked
the milestone — they're edge cases that didn't surface in the
trim → grade → export path. Fix when real usage triggers them OR while
touching the same files for the 15-verb expansion.

## F2: `edit_video` speed crashes on silent video [P2]

**Cause** — `gemia/tools/edit_video.py:142-144` hard-codes
`[0:a]{atempo_chain}[a]` plus `-map [a]`. Silent videos (b-roll,
screen recordings, synthetic clips) fail with
`Stream specifier ':a' ... matches no streams`.

**Fix** — ffprobe the source for an audio stream before building the
filter graph. When absent, run video-only `setpts` without `atempo`
and drop the audio `-map`.

**ETA** — ~10 lines + 1 smoke test (silent testsrc input).

## F3: image overlay positions use drawtext-only variables [P2]

**Cause** — `gemia/tools/add_overlay.py:107-111` reuses the `_POSITIONS`
table built for drawtext (`text_w`, `text_h`). The `overlay` filter
does not define those — its variable namespace is `W`/`H` (main
dimensions) plus `w`/`h` (overlay dimensions). So `center`,
`bottom_center`, `top_right`, etc. fail for `kind="image"`.

**Fix** — separate `_OVERLAY_POSITIONS` table for image kind, using
the overlay filter's variable namespace. Do not refactor `_POSITIONS`
itself.

**ETA** — ~12 lines + 1 smoke test (image overlay at `center`).

---

Both surfaced by Codex review on 2026-05-27. Not blocking the A/B/C
direction decision. Both are real bugs but neither manifested in the
milestone use case.

---

Selected next direction: A (Tauri SSE integration for real creative tasks).
See chat for prerequisite decisions and Opus spec.
