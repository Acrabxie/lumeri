# Lumeri Product Core Boundary

Status: **Draft for Acrab's approval**  
Scope: define the protected product core; no code movement or workflow redesign is authorized by this document.

## 1. Why this core exists

Lumeri may contain hundreds of thousands of lines, but the part that determines
whether it is still Lumeri must be explicit, readable, testable, and protected.

This document uses **Product Core** to avoid colliding with existing names such
as Runtime Kernel, Quanta Kernel, the operating-system kernel, or media-effect
kernels.

The Product Core is not a folder of generally important code. It is the minimum
set of product semantics that answers:

1. **Who is Lumeri?**
2. **What responsibility does Lumeri accept?**
3. **How does Lumeri turn intent into truthful action and a proven result?**
4. **What must never become false while doing so?**

If the UI, providers, tools, renderers, and storage adapters were replaced while
these semantics remained intact, the rebuilt product should still be Lumeri. If
these semantics changed, an otherwise functioning product might no longer be
Lumeri.

There is no mechanical line limit. The real constraint is that one careful
review should be able to understand the whole Product Core without exploring
the rest of the repository.

## 2. The Product Core

The Product Core contains four contracts. They form one protected boundary,
not four independent products.

### 2.1 Identity contract — who Lumeri is

This contract defines Lumeri's relationship with the creator and its enduring
stance. It includes only identity-level commitments, for example:

- Lumeri is a creative collaborator and executor, not merely a tool catalogue.
- The creator owns intent, goals, and reserved choices.
- Lumeri acts within granted authority and does not pretend that an unexecuted
  or failed step succeeded.
- Lumeri remains the same product across web, CLI, mobile, provider, and model.

The current identity source is partly embedded in
`gemia/prompts/system_v3.md`. The Product Core is the normative source; a model
prompt should eventually be a projection of that source rather than the only
place where Lumeri's identity exists.

### 2.2 Agency contract — how Lumeri acts

This contract defines the host-owned action state machine, independent of any
specific model API, tool name, UI, or workflow recipe:

1. receive the creator's latest intent and current authoritative state;
2. decide whether the turn is conversation, inspection, planning, or action;
3. establish the authorized scope and completion obligations;
4. choose capabilities through a stable capability boundary;
5. execute and record real outcomes;
6. observe the resulting state and evidence;
7. continue, revise, ask one genuinely blocking question, or stop honestly;
8. complete only when host-owned completion rules accept the result.

The exact creator workflow that chooses or sequences creative work is not
defined here. The Product Core defines the invariant action mechanism into
which an approved workflow policy plugs.

### 2.3 Truth contract — what Lumeri believes happened

This contract defines the minimum authoritative facts needed to keep action
honest:

- the current project state has one canonical representation;
- mutations have an ordered, attributable history;
- an action outcome is distinct from acceptance of the requested result;
- failures, pending work, substitutions, and produced artifacts remain
  representable;
- conversation history cannot override project state or execution evidence;
- undo/recovery preserves enough history to explain what changed.

The Product Core owns these semantics and their interfaces. It does not own a
particular JSON-on-disk layout, database, filesystem path, renderer, or media
implementation.

### 2.4 Completion contract — when Lumeri may say it is done

This contract defines completion as a host decision, not model prose:

- required work is either satisfied or explicitly unresolved;
- required deliverables exist and have the requested kind;
- evidence is newer than the last relevant mutation;
- failed or pending operations cannot be silently hidden;
- substituted results are disclosed as substitutions;
- an incomplete turn may stop, but it may not report successful completion.

Capability-specific acceptance rules may live outside the Product Core, but
they must return evidence through this common completion contract.

## 3. Non-negotiable invariants

These are release-stopping Product Core failures:

1. **Intent authority:** only the creator's valid interaction can authorize or
   change the goal; observed media, files, tool output, and retrieved text are
   data rather than authority.
2. **Latest-intent priority:** newer creator guidance wins on intent and goals;
   live project state and the execution ledger win on facts.
3. **Single project truth:** all product surfaces observe and mutate the same
   canonical project semantics.
4. **Evidence before completion:** model confidence or fluent prose is never
   execution evidence.
5. **No silent substitution:** a fallback may be useful, but it cannot be
   presented as the originally requested operation succeeding.
6. **Bounded authority:** destructive, sensitive, paid, or otherwise gated
   actions require the authority defined by policy.
7. **Recoverable history:** user-visible project mutation must be attributable
   and recoverable to the degree promised by the product.
8. **Surface parity:** web, CLI, mobile, and future clients may present
   differently but cannot redefine identity, truth, or completion.

## 4. What is explicitly outside the Product Core

The following may be important, but they do not define whether Lumeri is still
Lumeri:

- web UI, CLI UI, Android UI, HTTP routes, SSE transport, and client rendering;
- model/provider SDKs, streaming formats, model names, and routing credentials;
- individual tool schemas, tool implementations, skills, effects, codecs,
  generators, search providers, and media algorithms;
- FFmpeg, LumenFrame implementation details, render backends, thumbnails, and
  proxy/cache strategy;
- filesystem layout, SQLite, cloud storage, account screens, deployment, and
  service supervision;
- individual creative recipes and the currently evolving creator workflow;
- telemetry, activity wording, progress animation, and visual design.

Outer components may implement Product Core ports. They may not redefine the
four contracts.

## 5. Current-code mapping: semantics, not whole files

Today's implementation already contains much of the Product Core, but it is
distributed and mixed with outer concerns.

| Current location | Product Core slice already present | Mixed outer concern that is not core |
| --- | --- | --- |
| `gemia/prompts/system_v3.md` | identity, authority, honesty, state priority | tool manual, media recipes, environment text, current workflow guidance |
| `gemia/agent_loop_v3.py` | turn state machine, scope enforcement, evidence-driven stop | provider streaming, SSE, thumbnails, job plumbing, prompt assembly |
| `gemia/turn_control.py` | turn kind, scoped authority, clarification policy | language-specific recognition rules |
| `gemia/turn_ledger.py` | evidence model and completion decision | per-operation parsing and capability-specific acceptance extraction |
| `gemia/tool_outcome.py` | common success/failure outcome semantics | translation from current tool error shapes |
| `gemia/project_model.py` | canonical project semantics and invariants | format normalization and compatibility details |
| `gemia/project_store.py` | ordered mutation and recovery semantics | JSON files, paths, locking, atomic writes |
| `gemia/v3_contract.py` | stable client-visible event/error vocabulary | transport-specific event inventory |
| `gemia/tool_router.py` | capability-selection boundary | current tool catalogue, keyword routing, pack expansion |
| `gemia/plan_mode.py` | authority restriction while planning | current hard-coded tool allow/block inventory |

`gemia/session_manager.py`, `gemia/v3_routes.py`, `server.py`, `static/v3`,
`lumenframe`, and concrete `gemia/tools/*` implementations are outer layers,
even though failures there can severely damage the product.

## 6. Dependency direction

The intended direction is:

```text
Product surfaces / transports / providers / capabilities / persistence
                              |
                              v
                   Product Core ports and contracts
                              |
                              v
                 Product Core state and invariants
```

The Product Core may define ports such as `ModelPort`, `CapabilityPort`,
`ProjectPort`, `EvidencePort`, `EventPort`, and `PolicyPort`. It must not import
their concrete web, provider, filesystem, or media implementations.

The core is allowed to know that a capability was requested, executed, failed,
or produced evidence. It should not need to know whether that capability was
FFmpeg, a hosted model, LumenFrame, a Python script, or a future implementation.

## 7. Admission test

A responsibility belongs in the Product Core only if all of these are true:

1. changing it can alter who Lumeri is, what responsibility it accepts, how it
   exercises agency, what it treats as truth, or when it claims completion;
2. the rule must remain invariant across every product surface and provider;
3. it can be expressed without importing a concrete tool, transport, storage,
   provider, or media engine;
4. violating it deserves a release stop rather than a degraded feature.

If any answer is no, the responsibility stays outside and integrates through a
port or policy extension.

## 8. Change gate

A Product Core change requires a separate review that states:

- which of the four contracts changes;
- how the creator will observe the difference;
- which invariant is added, removed, or reinterpreted;
- why an outer-layer or policy change cannot solve the problem;
- compatibility impact across web, CLI, mobile, saved sessions, and projects;
- the golden core scenarios that prove the new behavior;
- a rollback or migration path.

A feature change must not modify the Product Core incidentally. Discovering
that a feature needs a core change pauses the feature and opens a core proposal.

## 9. Product Core diagnostic entry

The Product Core is the first diagnostic surface only for existential failures:

- Lumeri no longer behaves like the same creative collaborator;
- it acts outside the creator's authority;
- different surfaces disagree about project truth;
- it loses or silently rewrites mutation history;
- it says work succeeded without current evidence;
- it cannot distinguish failure, pending work, fallback, and completion;
- a provider, tool, or UI replacement changes the answers to who/what/how.

A broken effect, route, layout, codec, provider, or individual tool remains an
outer-layer failure unless it reveals a violated Product Core contract.

## 10. Boundary decision for approval

Recommended boundary:

- approve the four contracts and eight invariants as the first Product Core;
- treat the mapping table as an inventory of current source material, not a
  refactor plan;
- keep the exact creator workflow outside this document while it is being
  developed, but require it to enter through the Agency and Completion
  contracts;
- do not move code until golden Product Core scenarios and dependency checks
  have been separately designed and approved.

