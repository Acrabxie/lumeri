<!--
SYNC IMPACT REPORT
Version Change: (none) → 1.0.0
Bump Rationale: Initial ratification. Foundation created from scratch by
comprehensive codebase analysis (examples/ and build/cache/output dirs excluded
per project convention). No prior version existed.

Modified Principles:
- (none — initial adoption)

Added Sections:
- Purpose
- Core Principles (6): Primitive-First Composable Core; Model Plans, Host
  Executes; Reproducible Skills; Output Honesty; Provider Discipline; Single
  Source of Truth, Protocol Parity & Drift Gates
- Governance (Amendment Procedure, Versioning Policy, Compliance Reviews)
- Dependent Artifacts
- Foundation Metadata
- Review History

Removed Sections:
- (none)

Template Updates:
➖ plan-generator skill - no repo-owned copy present; no change needed
➖ spec-generator skill - no repo-owned copy present; no change needed
➖ tasks-generator skill - no repo-owned copy present; no change needed
➖ README.md / CONTRIBUTING.md - already consistent with derived principles; no
  change required for initial ratification

Deferred Items:
- (none)

Generated: 2026-07-09T00:00:00Z
-->

# Lumeri Foundation

**Foundation Type**: default
**Project**: Lumeri (Python package / internal name: `gemia`)

## Purpose

Lumeri is a family of AI creative tools built on one idea: give the model a small
vocabulary of clean, composable primitives and let it plan the work. Lumeri Video
is the first product — a programmable engine where an AI planner reads primitive
docstrings, returns a structured Plan, and a deterministic engine executes it over
video, image, and audio.

This foundation is the authoritative source of the principles, standards, and
governance that keep Lumeri coherent as it grows across products (Video, Audio,
Image, PPT, CAD) and surfaces (web v3, CLI, desktop, MCP). Every contributor —
human or agent — operates from the shared understanding recorded here.

## Core Principles

### I. Primitive-First Composable Core

The engine's power comes from a catalog of small, pure, well-documented functions,
not from bespoke pipelines.

- New capabilities MUST be expressed as primitives in `gemia.picture`,
  `gemia.audio`, or `gemia.video` with a clear signature and a docstring that
  fully describes behavior — the docstring is the model's only interface to the
  function and is what `registry.catalog_for_prompt()` exposes to the planner.
- Primitives MUST be pure with respect to their declared inputs and outputs and
  MUST follow the shared contracts in `primitives_common.py` (float32 `[0,1]`
  images, `[-1,1]` audio, the `@batchable` convention). Picture primitives SHALL
  remain frame-agnostic so the engine can auto-bridge them onto video.
- Cross-cutting behavior (per-frame wrapping, batching, re-encode with original
  audio) MUST live in the engine, not be reimplemented inside primitives.

**Rationale**: A uniform, docstring-driven primitive surface is what lets the
planner reason about capabilities without hardcoded knowledge, and it is what
makes picture operations transparently work on video.

### II. Model Plans, Host Executes

Lumeri separates creative decision-making (the model's job) from deterministic
execution and real-world accounting (the host's job).

- The planner MUST emit a normalized, validated Plan (see `plan_contract.py`) —
  known primitives, clean args, media in `input`/`output` fields — never raw code
  or shell/ffmpeg commands. The executor MUST reject anything that is not a valid
  Plan.
- The host MUST NOT silently substitute cheaper tools, auto-fall back, or take
  creative decisions on the model's behalf. Host-side gates report reality (real
  money, real elapsed time via `budget_guard.py`) and return signals such as
  `needs_approval`; the model decides what to do with them.
- Any code that executes model-authored work (creative sandbox, subagents) MUST
  run under an explicit, conservative permission policy (argv-only sandbox,
  workspace scoping) rather than a general shell.

**Rationale**: "The model holds the wheel" keeps behavior predictable and
auditable — the host never hides a decision the user should own, and untrusted
execution stays contained.

### III. Reproducible Skills

A Skill is a saved, portable execution plan — run once, reuse forever without a
new AI call.

- Saved Skills MUST strip concrete paths and bind media through `$input`/`$output`
  variables so the pipeline is portable across projects and inputs.
- Skills MUST record the models and adjustable parameters they used, so a reused
  Skill is reproducible and inspectable, not an opaque blob.
- The `.lus` skill format MUST pass its parser/validator before storage; invalid
  or unparseable skills MUST NOT be persisted.

**Rationale**: Reproducibility is a product promise — a good result captured as a
Skill must produce the same class of result later, on any input, deterministically
and without further model spend.

### IV. Output Honesty

What Lumeri reports, previews, and exports MUST match what it actually produced.

- Effects and fields that are stored but not yet rendered MUST be surfaced: writes
  of unrendered fields warn at both the tool layer and the route layer, and export
  MUST record every stored-but-unrendered field (e.g. `dropped_fields` in the
  manifest). The system SHALL warn, never silently drop and never silently reject.
- Every user-writable timeline field MUST carry exactly one honesty classification
  that travels with the field, enforced by drift tests.
- Previews and pre-delivery gates MUST reflect the real output; a "done" signal
  MUST NOT be emitted for work that was not actually performed.

**Rationale**: Creative tools lose all trust the moment the preview or the report
diverges from the file. Honesty about what was rendered is non-negotiable.

### V. Provider Discipline

Lumeri's default path runs on Google's models and nothing else is wired in by
default.

- The default generative path MUST target Google models only — Gemini (planner),
  Nano Banana (image), Veo (video), Lyria (audio) — with configuration limited to
  the two supported auth paths: `GEMINI_API_KEY` or `VERTEX_PROJECT` (ADC).
- Lumeri MUST NOT introduce third-party key gateways, key marketplaces, or hidden
  provider fan-out into the default path. Optional, opt-in providers (e.g.
  pluggable search) MUST be explicitly configured and MUST NOT become implicit
  defaults.
- The configuration surface MUST stay minimal; new required environment or
  credential surface is a material change requiring justification.

**Rationale**: A tiny, well-understood provider surface keeps the security,
billing, and privacy story simple and auditable, and keeps the product honest
about where data and money go.

### VI. Single Source of Truth, Protocol Parity & Drift Gates

Shared contracts are declared in exactly one place, and every consumer is kept in
sync by tests.

- The v3 protocol surface (SSE event kinds, ask control types, recovery hints,
  stable error codes) MUST be declared in `v3_contract.py` BEFORE any code emits
  it. Emit sites use literal kind strings only.
- Web v3 and the CLI are peer frontends and MUST maintain feature and protocol
  parity: a change landed on one surface MUST be reflected on the other, and
  unknown event kinds MUST render a visible banner on both — silent drops are bugs.
- Contracts with more than one consumer (protocol, honesty classification, plan
  shape, exported `contract.json`) MUST be guarded by drift tests that go red when
  any consumer disagrees with the source of truth.

**Rationale**: Two frontends and multiple surfaces only stay coherent if there is
one declared truth and automated gates that fail loudly on divergence.

## Governance

This foundation supersedes ad-hoc convention. When code and this document
conflict, the conflict MUST be resolved by either amending the code to comply or
amending this foundation through the procedure below — never by silently ignoring
a principle.

### Amendment Procedure

1. **Proposal**: Any contributor MAY propose an amendment via a pull request that
   modifies this file and states the rationale and the intended version bump.
2. **Review**: A maintainer MUST review the amendment for consistency with existing
   principles and for downstream impact on dependent artifacts (see below).
3. **Propagation**: The proposer MUST update or flag every dependent artifact
   affected by the change and record the outcome in the Sync Impact Report.
4. **Ratification**: A maintainer approval merges the amendment. The
   `LAST_AMENDED_DATE` and `FOUNDATION_VERSION` MUST be updated in the same change.

### Versioning Policy

This foundation is versioned with semantic versioning:

- **MAJOR (X.0.0)**: Backward-incompatible governance changes — removing or
  fundamentally redefining a principle, or overhauling the governance structure.
- **MINOR (x.Y.0)**: Adding a new principle or section, or materially expanding an
  existing one.
- **PATCH (x.y.Z)**: Clarifications, wording, typo fixes, and non-semantic
  refinements that do not change the meaning of any rule.

When a bump is ambiguous, the proposer MUST state their reasoning and recommend a
version before finalizing.

### Compliance Reviews

- Every non-trivial pull request SHOULD be checked against these principles; a PR
  that violates a principle MUST either be brought into compliance or accompanied
  by a foundation amendment.
- Drift and contract tests are the enforcement arm of Principles IV and VI and MUST
  remain green on the default branch.
- Maintainers SHOULD conduct a foundation review at least once per significant
  release, or after any major architectural change, to confirm the principles
  still describe reality.

## Dependent Artifacts

Changes to this foundation MUST be checked against, and propagated to, the
following when affected:

- `README.md` and `CONTRIBUTING.md` — public-facing statements of the core ideas,
  provider model, and contribution flow.
- `docs/protocol-parity-plan.md`, `docs/timeline-canonical-plan.md`, and related
  contract/honesty plans — the design records behind Principles IV and VI.
- `gemia/v3_contract.py`, `gemia/plan_contract.py`, `gemia/budget_guard.py` — the
  code-level sources of truth referenced by these principles.
- The CLI counterpart (lumeri-cli) — parity peer under Principle VI.

## Foundation Metadata

- **FOUNDATION_VERSION**: 1.0.0
- **RATIFICATION_DATE**: 2026-07-09
- **LAST_AMENDED_DATE**: 2026-07-09
- **FOUNDATION_TYPE**: default

## Review History

| Version | Date       | Change                                             |
|---------|------------|----------------------------------------------------|
| 1.0.0   | 2026-07-09 | Initial ratification — 6 principles derived from codebase analysis. |
