# Merge Debt Inventory（合并债盘点）

Status: read-only snapshot, evidence + per-item recommendation. **No final verdicts here** — save/kill
decisions belong to the reviewer (QUEUE 骨头④). Nothing was mutated to produce this document.

- Snapshot date: 2026-07-06 (afternoon, local)
- Snapshot HEAD: `51caf35` "update public contributors" (2026-07-06 15:56 -0700), branch `feat/lumenframe-ask`
- Repo: `/Volumes/Extreme SSD/gemia`; secondary worktree root: `/Volumes/Extreme SSD/GemiaTemp/worktrees/`
- Caveat: **concurrent agents were editing during the scan.** The main worktree's dirty count moved
  38 → 41 files while this was written, and a new worktree (`export-honesty-p1`) appeared mid-scan.
  Numbers are correct as of each command's run; re-run the quoted commands before executing anything.

Recommendation vocabulary used below:

| Tag | Meaning |
|---|---|
| MERGE-NOW | Clean merge-tree, content still relevant; mergeable after tests |
| SALVAGE-PARTIAL | Branch as a whole is dead, but specific commits/APIs are worth extracting |
| KEEP-PARKED | Intentionally paused work; keep the branch, optionally reclaim the worktree |
| KILL-CANDIDATE | Evidence says zero unique content (or superseded); deletion loses nothing tracked |
| PROTECTED | Do not touch (active autonomous loop / in-flight concurrent task) |

---

## 1. `main` vs `feat/lumenframe-ask` (HEAD)

Evidence (`git rev-list --left-right --count main...HEAD`):

```
0	228        # main has 0 unique commits; HEAD is 228 ahead
```

- `main` tip: `4abac12` "ci: add GitHub Actions test workflow and CONTRIBUTING guide", **2026-06-14** —
  three weeks stale. Era: pre-PR#1, i.e. before timeline v1 M4+–M7, lumenframe, the agent-loop Phase 1
  work, protocol contract (`gemia/v3_contract.py`), plan mode — essentially before everything from the
  last three weeks. `origin/main` is the same commit (`4abac12`), so local `main` == remote `main`.
- `git merge-base --is-ancestor main HEAD` → **true**: HEAD strictly descends from main; a fast-forward
  is a pure pointer move, no merge commit, no conflicts possible.
- `git worktree list` (28 entries at scan time, 29 after `export-honesty-p1` appeared): **no worktree has
  `main` checked out**, so the branch ref can be moved without touching any working tree.
- Conclusion: a **local** fast-forward of `main` to `51caf35` is mechanically trivial (see runbook §6-A).
  Pushing `origin main` is a *separate user decision*: origin is the public repo and per project policy the
  login implementation must never be pushed there — the 228 commits include the email-login work, so
  `git push origin main` is **not** a mechanical follow-on. Alternatives considered: (a) merge main into
  HEAD — pointless, main has nothing unique; (b) leave main stale — keeps confusing every tool that
  defaults to main (CI, gh, new clones). FF is the only sensible local move.

小结：main 落后 HEAD 228 个提交、0 个独有提交，tip 停在 2026-06-14；没有任何 worktree 检出 main，
本地快进纯指针移动、零冲突。推 origin 是另一个决策（公开仓、含登录实现，需用户拍板）。

---

## 2. Worktree table

Method: `git worktree list --porcelain` + per-worktree `git -C <wt> status --porcelain | wc -l` (dirty) +
`git rev-list --count HEAD..<branch>` (unique). All paths existed except the one marked.

| Worktree (under `/Volumes/Extreme SSD/GemiaTemp/worktrees/` unless absolute) | Branch | Tip | Last commit (date, subject) | State | Unique vs HEAD |
|---|---|---|---|---|---|
| `/Volumes/Extreme SSD/gemia` (main wt) | `feat/lumenframe-ask` | `51caf35` | 07-06 update public contributors | **DIRTY 38→41 files (live, concurrent agents)** | 0 (is HEAD) |
| `/private/tmp/claude-501/…/scratchpad/baseline-45e37e4` | detached `45e37e4` | — | — | **PATH MISSING — prunable** (stale metadata in `.git/worktrees/baseline-45e37e4`) | — |
| `agent-grounding` | `feat/agent-context-grounding` | `cca35ac` | 06-27 fix(agent-loop): counter primacy over-anchoring | clean | 0 |
| `agent-loop-phase1-closeout` | `feat/agent-loop-phase1` | `765230e` | 07-04 checkpoint: save agent loop closeout changes | clean | 2 (net tree diff = **empty**, see §4.6) |
| `agent-loop-rc3-parallel` | `feat/agent-loop-rc3-parallel` | `d7f941c` | 06-21 parallelize safe agent loop tool calls | clean | 2 (see §4.1) |
| `deck-domain-skeleton` | `feat/deck-domain-skeleton` | `4abac12` | 06-14 ci: add GitHub Actions test workflow | clean | 0 (tip **== old main tip**) |
| `deck-frontend-wysiwyg` | `feat/deck-frontend-wysiwyg` | `494b894` | 07-04 checkpoint: add deck editor scaffold | clean | 1 (see §4.4) |
| `env-manifest` | `feat/env-manifest` | `8aca47f` | 06-20 feat: inject sandbox environment manifest | clean | 1 (see §4.6) |
| `export-honesty-p1` | `feat/export-honesty-p1` | `51caf35` | 07-06 (= HEAD) | **appeared mid-scan — in-flight task #10** | 0 at scan | 
| `fiveday-promo` | `feat/fiveday-promo` | `7a0870e` | 07-06 docs(prompt): teach 'search before you cut' | clean — **PROTECTED** | 3 (see §4.5) |
| `lumenframe-ask` | `feat/lumenframe-ask-worktree` | `d32cdc5` | 06-27 feat(ask): render ask_question controls | clean | 0 |
| `lumenframe-core` | `feat/lumenframe-core` | `ebe848c` | 07-04 checkpoint: add v3 preview page | clean | 1 (see §4.6) |
| `lumenframe-effects` | `feat/lumenframe-effects` | `80b724c` | 06-27 feat(lumenframe): add 9 filters + flip_layer | clean | 0 |
| `lumenframe-inject-fix` | `feat/lumenframe-inject-fix` | `e162401` | 06-27 fix: conditional lumenframe catalog injection | clean | 0 |
| `lumenframe-m11-render` | `feat/lumenframe-m11-render` | `9569a59` | 07-04 checkpoint: save render lockfile update | clean | 1 (`uv.lock` only, conflicts) |
| `lumenframe-m2-tools` | `feat/lumenframe-m2-tools` | `87383ea` | 06-27 fix: inject lumenframe op catalog | clean | 0 |
| `lumenframe-persist` | `feat/lumenframe-persist` | `a920937` | 06-27 refactor: harden lumenframe persistence | clean | 0 |
| `lumenframe-render-verb` | `feat/lumenframe-render-verb` | `23023fe` | 06-27 feat: add lumen_render verb | clean | 0 |
| `lumenframe-text-render` | `feat/lumenframe-text-render` | `52623e4` | 07-04 chore: ignore pytest temp directories | clean | 1 (`.gitignore` +1 line) |
| `lumenframe-textstyle-ops` | `feat/lumenframe-textstyle-ops` | `ea159c8` | 06-27 fix(lumenframe): improve animate_layer/set_text | clean | 0 |
| `lumeri-codegen-freedom` | `feat/lumeri-codegen-freedom` | `656112d` | 06-27 feat(expressions): safe expression evaluator | clean | 1 (content byte-identical to HEAD, see §4.6) |
| `lumeri-freedom-phase1` | `feat/lumeri-freedom-phase1` | `5642d6c` | 06-27 feat(phase1b): expressions integrated | clean | 0 |
| `overnight-base` | `overnight/base-20260627` | `41a32b8` | 06-29 fix(reverse): mirror time_remap about t_last | clean | 0 |
| `rc4-completion-gate` | `feat/rc4-completion-gate` | `a920937` | 06-27 (same tip as lumenframe-persist) | clean | 0 |
| `timeline-de-verify` | `test/timeline-de-verify` | `ea479e4` | 06-20 docs: close out timeline DE verification | clean | 3 (see §4.3) |
| `timeline-pro-ui` | `feat/timeline-pro-ui` | `ed48370` | 07-04 checkpoint: save timeline pro ui workspace | clean | 4 (see §4.3) |
| `timeline-v3-polish` | `feat/timeline-v3-polish` | `a456799` | 06-20 polish v3 editable timeline visuals | clean | 4 (see §4.3) |
| `tool-honesty-a` | `feat/tool-honesty-a` | `0749d90` | 07-04 checkpoint: save tool honesty changes | clean | 1 (see §4.6) |
| `tool-honesty-b` | `feat/tool-honesty-b` | `4f5563d` | 06-20 docs(v3-loop): agent-loop Phase 1 close-out | clean | 0 |

Observations:

- 15 of 26 registered secondary worktrees carry **zero** unique commits — their branches are pure
  ancestors of HEAD. Every worktree except the main one is clean, so `git worktree remove` (no `--force`)
  will succeed for all of them.
- The stale `baseline-45e37e4` entry (another session's scratchpad, path gone) is exactly what
  `git worktree prune` exists for.
- Branch backup state: the `private` remote (`Acrabxie/lumeri-private`) holds 81 refs and matches the
  local tip for every branch checked — **except `feat/fiveday-promo`, which is MISSING from `private`**
  (`git rev-parse -q --verify refs/remotes/private/feat/fiveday-promo` fails). The only key branch with
  no cloud backup is also the only one an autonomous loop is actively writing to. Worth surfacing to the
  user even though this doc makes no verdicts.

小结：26 个注册 worktree 里 15 个分支对 HEAD 零独有提交、全部干净；主 worktree 因并发代理持续脏；
一个 /private/tmp 基线 worktree 路径已消失可 prune。所有关键分支在 private 远端有备份，
唯独 fiveday-promo 没有。

---

## 3. `ov-*` orphan directories (~43 per QUEUE)

Count: exactly **43** directories matching `/Volumes/Extreme SSD/GemiaTemp/worktrees/ov-*`.

They are **not git worktrees anymore**:

- None appear in `git worktree list`; `.git/worktrees/` (28 entries) contains no `ov-*` entry.
- Sampled dirs (`ov-c1-compose`, `ov-c2-layers`, `ov-c3-ops`) contain **no `.git` file** and no tracked
  checkout (no `server.py`, no `gemia/`). Contents are purely untracked debris left behind when the
  worktrees were removed: `tests/` (dominated by `tests/test_video` fixtures — 301M of ov-c1-compose's
  329M), `.pytest_cache/` (~15M), `__pycache__/`, `uv.lock` (~1M), `tests_http_harness.py`, plus exFAT
  AppleDouble `._*` sidecar files.
- Every `overnight/*` branch (53 of them) has **0 unique commits vs HEAD** (verified: max of
  `git rev-list --count HEAD..overnight/*` over all 53 = 0). So no tracked content anywhere depends on
  these directories.

Disk estimate (sampled, not full-scanned, per instruction): `du -sh` on 3 samples → 329M / 295M / 371M,
mean ≈ 332M. Extrapolated total: **43 × ~332M ≈ 14 GB** (reasonable range 12–16 GB) of pure cache/fixture
debris on the external SSD.

Recommendation: KILL-CANDIDATE as a class — but note these are *not* removable via `git worktree remove`
(git no longer knows them); they need plain `rm -rf`, which is why it's gated 需用户确认 in §6-D.
Alternative considered: keep them as pytest-cache warm starts — rejected, the branches are merged, the
worktrees are gone, and nothing can re-attach to these dirs.

小结：43 个 ov-* 目录已不是 git worktree（无 .git、不在 metadata 里），内容全是未跟踪的测试视频
fixture 和 pytest 缓存，约 14 GB；对应 53 个 overnight/* 分支全部已是 HEAD 祖先。删除不损失任何
被跟踪内容，但属 rm -rf 类操作，需用户确认。

---

## 4. Key stranded branches — salvage dossiers

Method per branch: `git cherry HEAD <b>` (patch-equivalence), `git diff --stat HEAD...<b>` (files touched),
modern conflict dry-run `git merge-tree --write-tree HEAD <b>` (exit status + conflicted-file list), plus
legacy `git merge-tree $(git merge-base HEAD b) HEAD b | grep -c '^+<<<<<<<'` for a conflict-hunk count.

### 4.1 `feat/agent-loop-rc3-parallel` (`d7f941c`, 2026-06-21)

- Unique commits: 2 — `8aca47f` "inject sandbox environment manifest" (identical to `feat/env-manifest`
  tip, i.e. this branch *contains* env-manifest) + `d7f941c` "parallelize safe agent loop tool calls".
- Footprint: 8 files, **+1147/−217**. Core of `d7f941c`: `gemia/agent_loop_v3.py` (753 changed lines),
  `gemia/budget_guard.py` (+50), `tests/test_agent_loop_parallel_dispatch.py` (+335).
- Conflict dry-run: `merge-tree --write-tree` **fails**; conflicted file is exactly one —
  `gemia/agent_loop_v3.py` — with **3 conflict hunks**. That single file is the problem: HEAD's agent loop
  was redesigned on 07-05 (circuit-breaker redesign), so the 753-line diff targets a file shape that no
  longer exists. Textual conflict count understates the semantic conflict.
- **Prior-art note for multi-agent (task #14):** `d7f941c` adds budget *reservation* APIs to
  `gemia/budget_guard.py` — `reserve(tool_name) -> tuple[BudgetDecision, BudgetReservation|None]` at
  `budget_guard.py:131` (branch revision) and `commit_reserved(reservation, actual…)` at
  `budget_guard.py:153–167`, i.e. estimate-then-settle accounting that lets N concurrent tool calls share
  one budget without racing it. `docs/multi-agent-plan.md` does **not exist yet** (task #14 pending);
  whoever writes it should read this API before inventing a new one, *even if the dispatch code dies*.
- Recommendation: **SALVAGE-PARTIAL.** (a) The parallel-dispatch rewrite of `agent_loop_v3.py`: do not
  merge; the 07-05 redesign supersedes its structure — port the *concept* when parallelism is scheduled
  (effort to re-port properly: ~2–3 days incl. tests). (b) The `budget_guard.py` reservation API +
  `tests/test_agent_loop_parallel_dispatch.py` patterns: extract via file-scoped diff/apply (§6-C),
  effort ~0.5–1 day, best timed with the multi-agent design task. Alternatives: merge wholesale
  (rejected — guaranteed semantic breakage of the breaker redesign); kill outright (rejected — the
  reservation API is finished, tested prior art the roadmap explicitly needs).

### 4.2 Timeline M6/M7 — dangling `be718b1`

QUEUE entry `shared-2026-06-13-lumeri-timeline-v1` says "M6+M7 本地 commit 未 push（留用户）" pointing at
`be718b1` "M7-D — wire duck_map through export renderer" (2026-06-19).

- `git for-each-ref --contains be718b1` → **empty**: no branch or tag contains it; it is reachable only
  via reflog. `git rev-list --count HEAD..be718b1` = 207, but that is divergent-history noise (it sits on
  the old `claude/jolly-clarke-JO7E3` lineage), not unmerged content.
- Content check: `git cherry HEAD be718b1 | grep -c '^+'` → **0**. Every commit on that lineage —
  including all nine M6-A…M7-D commits (`30e2867`, `4b3c288`, `bbbec8e`, `ab2b59e`, `f4ea688`, `fe8159c`,
  `d155bbf`, `4848874`, `be718b1`) — is patch-equivalent to something already in HEAD. Spot-confirmed in
  HEAD's tree: `duck_map` lives in `gemia/project_export.py` and `tests/test_timeline_m7_ducking.py`;
  `timeline_set_track` is registered in `gemia/tools/__init__.py` / gated in `gemia/plan_mode.py`.
- So the QUEUE line is stale as a *merge* concern: M6/M7 content is fully in HEAD; only "not pushed to
  origin" remains true (origin/main is still `4abac12`).
- Recommendation: **KILL-CANDIDATE (nothing to salvage), effort 0.** Optional zero-risk archaeology:
  tag it before the reflog expires (§6-E). Alternative: rebuild a branch from be718b1 to "recover M6/M7"
  — rejected, `git cherry` proves there is nothing to recover.

### 4.3 Timeline DE test/polish trio

Three branches share the same two test-lock commits (`9f620dd` Layer 1 HTTP/SSE contract, `a4c7e74`
Layer 2 jsdom drag-math lock) + docs close-out `ea479e4`, then diverge:

| Branch | Unique | Extra on top of shared 3 | Footprint (`diff --stat HEAD...b`) | Conflict dry-run |
|---|---|---|---|---|
| `test/timeline-de-verify` | 3 | — (tests+docs only) | 5 files, **+1269/−0** (`tests/timeline_de_v3_dom/…` +651-line test, package.json) | **CLEAN** |
| `feat/timeline-v3-polish` | 4 | `a456799` visual polish | 8 files, +1689/−35 | CONFLICTS: `static/v3/index.html`, `static/v3/v3.css`, `static/v3/v3.js` — 8 hunks |
| `feat/timeline-pro-ui` | 4 | `ed48370` checkpoint incl. new `timeline.js` (465 lines) | 13 files, +2515/−82 | CONFLICTS: same 3 static/v3 files — 10 hunks |

- Recommendation, in dependency order:
  1. `test/timeline-de-verify`: **MERGE-NOW.** Additive test-only lock of direct-edit behavior, clean
     merge-tree, gives regression cover *before* any timeline refactor per `docs/timeline-canonical-plan.md`.
     Effort ~0.5 h (merge + `python3 -m pytest` + `node --test tests/timeline_de_v3_dom`).
  2. `feat/timeline-v3-polish` / `feat/timeline-pro-ui`: **KEEP-PARKED pending reviewer judgment.** Their
     unique value is UI layers on `static/v3/{index.html,v3.css,v3.js}`, which HEAD has since evolved
     (8–10 conflict hunks each). Whether the 06-20 polish / the 465-line `timeline.js` still matches the
     canonical timeline direction is a product call, not a mechanical one. If saved: resolve conflicts in
     the three static files + visual re-verify against the UI quality bar — ~0.5–1 day each. If killed:
     both are backed up on `private` (`a456799`, `ed48370` confirmed present), so deletion is reversible.
     Note pro-ui ⊃ polish's test commits but *not* its polish commit — they are siblings, not a chain;
     do not assume merging pro-ui subsumes v3-polish.

### 4.4 Deck / PPT pivot (paused)

- `feat/deck-domain-skeleton` (`4abac12`): unique = **0**; the tip *is* the old main tip. The branch is an
  empty pointer — the "skeleton" was never committed on it. Recommendation: **KILL-CANDIDATE** (branch +
  worktree), effort 0; nothing exists to lose (also on `private`).
- `feat/deck-frontend-wysiwyg` (`494b894`): unique = 1 checkpoint commit; 25 files, **+4721/−0**, all new
  files under `frontend/deck-editor/` (Vite + TS scaffold). Conflict dry-run: **CLEAN** (purely additive).
  Recommendation: **KEEP-PARKED.** Do *not* merge while the pivot is paused (dead scaffold in HEAD would
  rot and confuse the build surface); keep the branch (backed up on `private` at `494b894`), reclaim the
  worktree directory if disk is wanted. Alternatives: merge now (rejected — adds an unbuilt frontend to a
  video-editor HEAD mid-pivot-pause); kill (rejected — the pivot is paused, not cancelled, and 4.7k lines
  of scaffold is a day of rework).

### 4.5 `feat/fiveday-promo` — PROTECTED

Active autonomous-loop worktree (`fiveday-promo`, identity Acrabxie, local-commit-only policy). 3 unique
commits at scan (FTS5 media search: `e12d662`, `72174c9`, `7a0870e`; 11 files +910/−56;
merge-tree incidentally **CLEAN**). **Do not merge, remove, rebase, or otherwise touch.** Only noted here
for completeness + the backup gap from §2 (no `private/feat/fiveday-promo` ref exists).

### 4.6 Remainder with unique commits (no other branch exceeds 5 — max anywhere is 4)

| Branch | Unique | Evidence | Recommendation |
|---|---|---|---|
| `feat/agent-loop-phase1` | 2 | `git diff --stat <merge-base>..765230e` → **empty**: the two commits (RC4 gate + checkpoint) net to a tree identical to the merge-base; the RC4 completion gate shipped to HEAD by another path (`completion_check` is in `gemia/v3_contract.py`'s 19 kinds) | KILL-CANDIDATE, effort 0 |
| `feat/tool-honesty-a` | 1 | `0749d90` checkpoint: 20 files +719/−138 across `gemia/tools/` (build.py 124, fetch.py 79, arrange_timeline.py 61…); conflict dry-run: **9 hunks** in `gemia/tools/export.py`, `gemia/tools/fetch.py`, `gemia/tools/web_search.py` — i.e. it collides with HEAD's landed fetch.py bug-fix *and* overlaps the export-honesty Phase 1 work that just started in `export-honesty-p1` (task #10) | SALVAGE-PARTIAL — reviewer should diff it against the in-flight export-honesty work **before** task #10 lands, or the same honesty ideas get implemented twice and this branch becomes permanently unmergeable. Review effort ~0.5–1 day; goes stale fast |
| `feat/tool-honesty-b` | 0 | pure ancestor | KILL-CANDIDATE, effort 0 |
| `feat/env-manifest` | 1 | `8aca47f`: 6 files +226 (manifest injection + `gemia/tools/run_shell.py` +15, `tests/test_environment_manifest.py` +88); merge-tree **CLEAN**; NB the same commit rides inside rc3-parallel (§4.1) | MERGE-NOW *candidate* — textually clean, but it predates the 07-05 agent-loop redesign, so verify the injection point still exists semantically; effort ~1 h merge + test, +1–2 h re-verify |
| `feat/lumeri-codegen-freedom` | 1 | `656112d` adds `gemia/expressions.py` (+282) + tests (+195); `git diff HEAD <b> -- gemia/expressions.py tests/test_expressions.py` → **empty**: both files byte-identical in HEAD (arrived via `feat/lumeri-freedom-phase1`, which is fully merged). The "+" in `git cherry` is a patch-id artifact of the different base | KILL-CANDIDATE, effort 0 |
| `feat/lumenframe-core` | 1 | `ebe848c` adds `static/v3/preview.html` (+404); add/add conflict — HEAD already has its own `static/v3/preview.html` | KILL-CANDIDATE unless reviewer wants a one-time `git diff HEAD ebe848c -- static/v3/preview.html` skim for lost ideas (~15 min) |
| `feat/lumenframe-m11-render` | 1 | `9569a59`: `uv.lock` only (+63/−1), conflicts with HEAD's `uv.lock` | KILL-CANDIDATE — lockfile drift, regenerate from `pyproject.toml` instead of merging |
| `feat/lumenframe-text-render` | 1 | `52623e4`: `.gitignore` +1 line (pytest temp dirs), CLEAN | Trivial: cherry-pick the one line or kill; either way <5 min |
| `feat/timeline-direct-edit` | 0 | tip `5b0d274` in HEAD; origin's copy `698b541` also ancestor of HEAD | KILL-CANDIDATE (local + origin branch both fully merged) |
| `claude/jolly-clarke-JO7E3` | 0 | PR #1 head; fully merged by content into HEAD lineage | KILL-CANDIDATE locally; origin copy is the closed PR's head, leave remote as-is |

小结：真正的救援对象只有四个——rc3-parallel 的 budget 预留 API（多代理规划的现成先例）、
timeline-de-verify 的纯测试锁（干净可直接合）、tool-honesty-a（与进行中的 export 诚实化撞车，
需在其落地前评审否则永久烂掉）、deck-frontend-wysiwyg（暂停不删）。be718b1 的 M6/M7 经
patch-id 证明已全部在 HEAD 里，QUEUE 记录过时。其余带独有提交的分支不是空 diff 就是锁文件/
字节级重复。全库没有任何分支独有提交数超过 4。

---

## 5. GitHub state

`gh` CLI works against `origin` = `https://github.com/Acrabxie/lumeri.git`.

- Open PRs: **none** (`gh pr list` empty).
- All PRs: exactly one. PR #1 "feat: timeline v1 (M1–M5) — fine-grained timeline verbs + multi-track
  export + OTIO adapter", head `claude/jolly-clarke-JO7E3`, created 2026-06-18, state **CLOSED — not
  merged** (`gh pr view 1 --json state` → `"CLOSED"`). Its content is nonetheless fully present in local
  HEAD (head commit `509f9ea` has 0 unique commits vs HEAD), so the closure cost nothing locally; it just
  means GitHub's main never advanced.
- `origin` holds only 4 branch refs (`main`@4abac12, `claude/jolly-clarke-JO7E3`,
  `feat/timeline-direct-edit`@698b541 — an ancestor of HEAD, `overnight/base-20260627`). The real backup
  is the `private` remote (81 refs, tips verified current for every branch checked, except
  `feat/fiveday-promo` — absent, see §2/§4.5).

小结：gh 可用；仅有的 PR #1 已 CLOSED（未 merge），但其内容本地已全在 HEAD；origin 只有 4 个引用，
真正的备份在 private 远端（81 个引用，fiveday-promo 缺席）。

---

## 6. Execution runbook（commands only — none were run; 标注“需用户确认”的均为破坏性/不可逆或触碰共享状态）

All commands assume `cd "/Volumes/Extreme SSD/gemia"` (path has a space — always quote). Re-verify the
evidence lines immediately before executing; concurrent agents move this repo hourly.

### A. Local fast-forward of `main` (safe class — pointer move only)

```sh
# pre-flight (all three must hold)
git merge-base --is-ancestor main HEAD && echo ff-possible
git worktree list | grep '\[main\]' || echo main-not-checked-out
git rev-list --left-right --count main...HEAD           # expect "0  <N>"

# fast-forward without checking main out ("fetch from self" refuses non-ff by construction)
git fetch . HEAD:main
```

Do **not** chain `git push origin main` here — 需用户确认 (public repo; the range includes the email-login
implementation which policy says never goes to the public origin).

### B. Merge the clean save candidates (mutating the shared main worktree — 需用户确认 while concurrent agents are active)

```sh
# 1. timeline DE test lock (§4.3, clean, test-only)
git merge --no-ff test/timeline-de-verify
python3 -m pytest -q && (cd tests/timeline_de_v3_dom && npm ci && node --test)

# 2. env manifest (§4.6, clean but semantically stale — re-verify injection point after merge)
git merge --no-ff feat/env-manifest
python3 -m pytest tests/test_environment_manifest.py -q
```

### C. Cherry-pick / partial salvage (creates commits — 需用户确认)

```sh
# rc3-parallel budget-reservation API only (§4.1): file-scoped 3-way apply, not a branch merge
git diff $(git merge-base HEAD d7f941c) d7f941c -- gemia/budget_guard.py > /tmp/budget-reservation.patch
git apply --3way /tmp/budget-reservation.patch          # then hand-reconcile + port the tests
git diff $(git merge-base HEAD d7f941c) d7f941c -- tests/test_agent_loop_parallel_dispatch.py   # read for patterns, don't apply blind

# one-liner .gitignore salvage (§4.6)
git cherry-pick 52623e4
```

### D. Worktree & branch cleanup (destructive — 全部需用户确认)

```sh
# stale metadata for the vanished /private/tmp baseline worktree (safe, removes only dead metadata)
git worktree prune --verbose

# registered worktrees whose branches have 0 unique commits (all verified clean; remove refuses if dirty)
git worktree remove "/Volumes/Extreme SSD/GemiaTemp/worktrees/<name>"
# then delete the branch; -d refuses unless merged — keep -d, do NOT reach for -D on 0-unique branches
git branch -d <branch>

# ov-* debris (§3): NOT git worktrees — plain rm. Pre-flight both lines first. ~14 GB reclaim. 需用户确认
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/overnight); do \
  git rev-list --count HEAD..$b; done | sort -u        # must print exactly "0"
ls "/Volumes/Extreme SSD/GemiaTemp/worktrees/"ov-*/.git 2>/dev/null | wc -l   # must print 0
rm -rf "/Volumes/Extreme SSD/GemiaTemp/worktrees/ov-"*

# NEVER include in any batch: fiveday-promo worktree/branch (§4.5), export-honesty-p1 (in-flight task #10)
```

### E. Zero-risk archaeology / backup gaps (creates refs — 需用户确认)

```sh
git tag archive/timeline-m6m7-be718b1 be718b1        # pin the dangling M6/M7 lineage before reflog expiry
git push private feat/fiveday-promo                  # close the only backup gap (ref push only; does not touch the worktree)
```

小结：A 类（本地快进）随时可做；B/C 类合并与摘枝需避开并发代理的脏工作区；D 类删除全部需用户
确认，其中 ov-* 是 rm -rf 而非 git 命令；fiveday-promo 与 export-honesty-p1 在任何清理批次中都必须排除。

---

## Verdicts（claude-code 终审，2026-07-06）

已执行（安全、可逆、不碰公开 origin）：
- ✅ `git worktree prune`（清掉 /private/tmp 陈旧登记）。
- ✅ 本地 `main` 快进到 HEAD（`git fetch . HEAD:main`，231 commits，严格祖先、无 worktree 占用）。**公开 origin 的 main 保持冻结**：区间含账户登录实现，按 2026-07-05 硬规则，push 公开仓前必须先做边界评审（拆出/遮蔽登录、云服务、key 处理面）——这是用户级决定，任何 agent 不得代做。
- ✅ `feat/fiveday-promo` 补进私有备份（此前是唯一漏备份的关键分支）+ `archive/timeline-m6m7` 标签固定 be718b1 + main/feat 分支同步 private。

判决（执行需用户点头的部分列成菜单）：
| 对象 | 判决 | 理由/条件 |
|---|---|---|
| ov-* 43 个目录 (~14GB) | **删**（`rm -rf`，命令见 §6） | 无 .git、未跟踪、内容为测试产物；53 个 overnight/* 分支全是 HEAD 祖先，删目录零损失 |
| tauri-app/ 残留 (~18GB) | **删** | 构建产物（node_modules+Rust target），源码已随退役 commit 移除 |
| be718b1 (M6/M7) | **无需抢救** | git cherry 证实内容已全部在 HEAD；archive 标签已打 |
| feat/agent-loop-rc3-parallel | **API 抢救后杀** | budget reservation API（d7f941c:budget_guard.py:84-151）按 docs/multi-agent-plan.md P1 做文件级抢救（~0.5-1d）；并行 dispatch 代码不再移植（被 multi-agent 方案取代） |
| test/timeline-de-verify | **救**（合并候选） | 干净、+1269 纯测试；委派 codex 复核后合入 |
| feat/env-manifest | **限期停放** | 需语义复核；30 天内无人认领则杀 |
| feat/tool-honesty-a | **待 export-honesty-p1 落地后复核** | 与在飞工作冲突（export.py/fetch.py/web_search.py）；大概率被取代，抢救其独有测试用例后杀 |
| deck-domain-skeleton / agent-loop-phase1 / lumeri-codegen-freedom / m11-render / 15 个零独有分支 | **杀**（`git branch -D`） | 空 diff/字节等同/仅 uv.lock；reflog 可回捞 |
| deck-frontend-wysiwyg | **继续停放** | +4721 纯增量，pivot 暂停是产品决定不是技术债 |
| feat/fiveday-promo | **保护** | 自治循环专属，任何清理不得触碰 |
