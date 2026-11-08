# The Luc ↔ Agent protocol

Status: source, 2026-10-06. Not built, installed or published.

This is the one description of how a Luc reaches the agent and what the agent
may read of it. Where another document in this folder disagrees, this one and
the code it names are right. The code is `lumeri_runtime/official_luc.py`
(what the agent is handed), `lumeri_runtime/luc.py` (the Host) and
`lumeri_runtime/luc_interactions.py` (human waits). The tests are
`tests/test_luc_agent_protocol.py`.

## The two rules

1. **English is the protocol's one language.** Everything a Luc says *to the
   agent*, and everything the agent says to find a Luc, is English.
2. **Nothing the protocol says is read cut.** An answer that would not fit one
   tool result is shortened by whole items and says how many it left out, or
   it is refused. The agent never acts on half a schema or half a list.

Both are enforced by the Runtime, at the agent's side. Neither is a
convention a package is trusted to follow.

## What the agent is handed

In Runtime 1.1 the Video agent has no tools of its own. It is handed exactly
these four, built from the verified Official set that is switched on and the
Lucs the person added to this machine:

| Tool | Takes | Gives |
| --- | --- | --- |
| `luc_discover` | `query` (English keywords, at most 500 characters), `limit` (1 to 16, default 8) | The best matches, and counts of what is not listed |
| `luc_describe` | `capability_id` | How to call that one capability |
| `luc_invoke` | `capability_id`, `input` (an object matching the described schema), `target` (`{kind, id}`, for a Project edit) | The capability's result |
| `use_skill` | `skill` (one of the names in the tool's own description) | That Skill's instructions, or that Workflow's stages |

`use_skill` is there only while at least one Skill or Workflow is ready. With nothing to
discover and nothing unavailable to report, the three `luc_*` tools are not
handed out at all. Beside them the agent keeps the Runtime's own plan,
`update_plan` ([agent-parts.md](agent-parts.md)); it reaches no Luc and changes
nothing in the Project.

The step system prompt (`VIDEO_AGENT_STEP_PROFILE`) is fixed and hashed and
does not mention Lucs. One kind of Luc takes its place whole: a stand-in agent
that Lumeri has reviewed ([agent.md](agent.md)). The four tools and every rule
on this page are the same under a stand-in. Everything the agent knows about them it reads from
these four declarations and their answers. That is why the declarations carry
the rules ("Write the query as English keywords"), and why the ready Skills
are listed inside `use_skill`'s description.

Only Video has an agent turn in 1.1. Quanta and the other products are not
handed these tools.

### `luc_discover`

```json
{"capabilities": [{"id": "timeline.trim-clip", "protocol": "lumeri.tool/v2",
                   "description": "...", "package": "lumeri.timeline-editor",
                   "origin": "official", "publisher": "Lumeri", "role": "luc",
                   "migration_state": "LUC_CODE"}],
 "more": 28,
 "unavailable": [{"identity": {"id": "...", "version": "..."}, "code": "E_LUC_...", "message": "..."}],
 "more_unavailable": 1}
```

- Every capability the agent may use is a candidate. The query only ranks
  them: a capability scores one for each query word found in its id,
  description and protocol, and ties go by id. An empty query lists them by
  id.
- `more` counts the candidates not listed. There is no paging: to see others,
  ask with different keywords.
- `unavailable` names what is switched on but cannot be used now, and why
  (the codes are below). `more_unavailable` counts rows left out for room.
- A package made for another product (`E_LUC_PRODUCT`) and a package that only
  draws the editor (a workspace, a Panel) are not reported: the agent is not
  missing them.
- `origin` is `official` for Lumeri's own Lucs and `local` for one a person
  put in the `lucs` folder ([permissions.md](permissions.md)). Both reach the
  agent the same way and under the same two rules. `publisher` is the author
  the Luc names, or empty when that name is not written in English. A file in
  that folder that did not load is not reported to the agent.

### `luc_describe`

```json
{"capability": {"id": "timeline.trim-clip", "protocol": "lumeri.tool/v2", "surface": "packaged-agent-tool",
                "description": "Change which part of its source a clip plays. ...",
                "package": "lumeri.timeline-editor", "tool": "timeline_trim_clip", "code": "tools.py",
                "input_schema": {"type": "object", "...": "..."},
                "requested_apis": {"required": ["project.read", "timeline.edit"], "optional": []},
                "effective_apis": ["project.read", "timeline.edit"], "unavailable_optional_apis": [],
                "permissions": {"requested": ["project.read", "project.write"], "granted": ["project.read", "project.write"]},
                "editable": {"target_types": ["timeline_clip", "timeline_track"], "properties": ["..."], "areas": ["timeline"]},
                "effective_editable": {"...": "..."},
                "capability_effect": {"kind": "native_project_edit", "writes_project": true, "...": "..."}},
 "package": {"identity": {"id": "lumeri.timeline-editor", "version": "0.2.0"}, "source_hash": "...", "artifact_hash": "...",
             "origin": "official", "publisher": "Lumeri", "role": "luc", "migration_state": "LUC_CODE"}}
```

It carries what the agent needs in order to call: the exact input schema, what
the capability may edit, and the authority it runs with. A packaged tool adds
its `tool` name and the `code` file it runs from; an interaction adds its
`canvas_operation`.

The Host keeps the rest, because the Host is the one that uses it:
`output_schema` and `primitive_schemas` (the Host validates results and picks
renderers with them), `host_api_registry`, and `readme` (prose for people).

`layout.adjust` also lists the Project's text layers the creator could be
asked to move:

```json
"targets": [{"kind": "lumenframe_text_layer", "id": "title-1", "name": "Title", "text": "...", "transform": {}}],
"more_targets": 3,
"project_revision": 4
```

A target's `text` is the creator's own words. It is shown so the agent can
tell one title from another, shortened to 80 characters and an ellipsis.
`more_targets` counts every text layer that is not listed, whether the Host
stopped at twelve or the result ran out of room.

### `luc_invoke`

```json
{"package": {"id": "lumeri.timeline-editor", "version": "0.2.0"}, "capability_id": "timeline.trim-clip", "result": {}}
```

- A tool that declines returns its own `{"error": {"code", "message"}}`.
- An interaction (`lumeri.interaction/v1`) mounts in the editor and the turn
  waits for the creator. No model request is open and none is made while it
  waits; the same tool call then receives the creator's answer, or their
  cancellation, and the turn continues. Stop ends the turn. A Worker restart
  invalidates the wait (`E_LUC_BACKEND_RESTART`) instead of starting over.
- A Project edit names its target. Confirmation is a creator action on the
  Host's own route; no argument the model can write stands in for it
  (`E_LUC_CONFIRMATION_REQUIRED`).

### `use_skill`

```json
{"skill": "timeline-editing", "instructions": "..."}
```

A Skill is a Luc (`lumeri.skill/v2`) that tells the agent how to do one kind
of task with the tools it has. See
[workspace-panel-skill.md](workspace-panel-skill.md) for its fields and for
when it is ready.

The same tool reads a Workflow (`lumeri.workflow/v1`), the order of a whole
job. Its answer is how to run the job as a whole (`guidance`, when the
Workflow has it) and every stage at once:

```json
{"workflow": "product-promo", "guidance": "...",
 "stages": [{"stage": 1, "title": "...", "instructions": "...", "skills": ["..."]}]}
```

A point library (`lumeri.library/v1`) is an ordinary capability: found with
`luc_discover`, read with `luc_invoke` (`{"op": "catalog"}`, then
`{"op": "entry", "entry": id}`). It answers recipes and changes nothing; the
agent makes the calls a recipe names. Both are in [guides.md](guides.md).

## What a Luc can put before the agent

| What | Where the agent meets it | Bound |
| --- | --- | --- |
| Capability `id` | `luc_discover`, `luc_describe` | English |
| Capability `description` | `luc_discover`, `luc_describe` | English |
| Capability `input_schema` (property names, descriptions, enums) | `luc_describe` | English; the whole description fits one result |
| Skill `name` and `description` | `use_skill`'s declaration | English; description at most 300 characters; at most 24 Skills |
| Skill `instructions` | `use_skill`'s answer | English; at most 4800 characters; the whole answer fits one result |
| Workflow `name` and `description` | `use_skill`'s declaration | As a Skill's; Skills and Workflows share the 24 |
| Workflow `guidance` and stages (`title`, `instructions`, `skills`) | `use_skill`'s answer | English; 2 to 12 stages; the whole answer fits one result |
| Library catalog (entries, recipes, guidance) | `luc_invoke`'s answer | English; each recipe fits one result; a long catalog is shortened by whole entries |
| A tool's result | `luc_invoke`'s answer | The tool's own; see "Known limits" |

A Luc cannot add to the system prompt, cannot add a tool beside the four, and
cannot speak to the agent outside these answers. The one exception is the
reviewed stand-in of [agent.md](agent.md), whose instructions are the prompt.

## English

"English" is a rule a machine can check: **every letter is an ASCII letter.**
Digits, punctuation, arrows, emoji and other symbols are free
(`official_luc.not_english`).

What it covers, and what happens otherwise:

| Text | If it is not English |
| --- | --- |
| A `luc_discover` query | Refused: `arguments_invalid`, "query must be English keywords, for example: trim clip". The agent asks again in English |
| A capability's `id`, `description` or `input_schema` | The capability is loaded and withheld from the agent. `luc_discover` lists it under `unavailable` with `E_LUC_LANGUAGE`; `luc_describe` and `luc_invoke` answer `luc_capability_not_found` |
| A Skill's `name`, `description` or `instructions` | The Skill is not ready: `E_LUC_LANGUAGE`. `use_skill` does not offer it, and the Skills Panel tells the creator why |
| A Workflow's `name`, `description`, guidance or a stage | The same as a Skill |
| A library's catalog or guidance | The library is loaded and withheld from the agent, like a capability: `E_LUC_LANGUAGE` |
| A library `catalog` query | Refused: `arguments_invalid` |

What it does **not** cover:

- **The creator.** They write to the agent in any language and are answered in
  theirs. Turning a request into English keywords is the agent's work.
- **What the creator reads.** A Skill's or Panel's `title` is a locale map, and
  a Panel's own strings are in ten languages. Neither is protocol text.
- **What a Project holds.** A caption, a note, a title's text, a file name and
  a tool's result are content. They pass through as they are.
- **The editor and the direct routes.** A withheld capability stays loaded:
  the Host still runs it for the editor. Only the agent is not offered it.

The check runs on the capability as loaded, after a creator's Extension layers
are applied, whichever way they reached the Luc. An Extension that rewrites an
Official description in another language therefore takes that capability away
from the agent on that machine, and the `unavailable` row says so.

One Official package is affected: `lumeri.timeline-ops-skill` (off by default)
carries the 1.0 planner prompt in Chinese. Switched on, it loads and is
withheld. `tests/test_luc_agent_protocol.py` pins that, with every Official
Luc switched on, it is the only one.

## Never cut

`agent_turn` shortens any tool result longer than 6000 characters
(`MAX_TOOL_RESULT_CHARS`) and marks where it cut. The protocol's own answers
are kept under that, so the mark never appears in them:

| Answer | How it is kept whole |
| --- | --- |
| `luc_discover` | Matches are dropped from the weakest until the list fits, then `unavailable` rows from the last; `more` and `more_unavailable` count them |
| `luc_describe` | The Host's own material is left out; targets are listed while they fit and the rest counted. If the description still does not fit it is refused: `luc_description_too_large`, "this capability's description does not fit one result" |
| `use_skill` | A Skill's instructions are at most 4800 characters. Both a Skill's and a Workflow's whole JSON answer, including escaped text, are measured when the package loads; one that exceeds 6000 characters is refused then |
| A library (`luc_invoke`) | Each recipe is measured when the package loads. A catalog is shortened by whole entries and `more` counts the rest; a query brings the wanted ones into the part shown |

As shipped the largest description is about 3300 characters, and discovery at
`limit` 16 fits with every Official Luc on.

## Who may reach the agent

- Only the verified Official set: the bundle sealed into the Runtime, or a
  signed channel that replaced it (`official-channel.md`, not in this repository).
- Only what is switched on: `[luc]` in this machine's config file, then the
  Local Registry ([packaged-tools.md](packaged-tools.md), "The registry").
- A creator customises a Luc with Extensions: layers on that one Luc
  ([permissions.md](permissions.md), "A layer on a Luc";
  [distribution.md](distribution.md)). Extensions change manifests and files
  and never what the Luc may do; tool code from anyone but the signed index is
  refused (`E_LUC_TOOL_CODE`). The agent is not told which layers lie on a Luc:
  it reads the Luc as they leave it.
- A capability runs with the Host APIs and permissions its package was granted.
  Discovering or describing it grants nothing.

## Codes

Refusals of the four tools (the agent reads `{"error": {"code", "message"}}`):

| Code | Meaning |
| --- | --- |
| `arguments_invalid` | A wrong argument, including a query that is not English |
| `luc_capability_not_found` | No such capability for the agent (unknown, off, or withheld) |
| `luc_package_not_found` | The capability has no verified Official provenance |
| `luc_description_too_large` | The description would not fit one result |

Reasons in `unavailable` rows:

| Code | Meaning |
| --- | --- |
| `E_LUC_LANGUAGE` | Loaded, but what the agent would read is not English |
| `E_LUC_SKILL_USES` | A Skill names a tool the agent does not have (`uses` lists them) |
| `E_LUC_SKILL_NAME` | Another ready Skill or Workflow has this name |
| `E_LUC_SKILL_LIMIT` | More than 24 Skills and Workflows |
| `E_LUC_WORKFLOW_USES` | A Workflow names a tool the agent does not have (`uses` lists them) |
| `E_LUC_WORKFLOW_SKILL` | A stage of a Workflow names a Skill that is not ready (`skills` lists them) |
| `E_LUC_LIBRARY_USES` | A library's recipes call a capability the agent does not have (`uses` lists them) |
| `E_LUC_ADAPTER_RUNTIME` | This Runtime has no implementation for the package's protocol |
| `E_LUC_OFFICIAL_BINDING` | The package is bound to a native tool this Runtime lacks |
| `E_LUC_TOOL_CODE` | The package's tool code did not match the signed index, or its tool names are taken |
| `E_LUC_API_PERMISSION` | This Host does not grant what the package requires |
| `E_LUC_REGISTRY_IMMUTABLE` | The package's content changed without a new version |

## Other bounds on a turn

These are `agent_turn`'s and apply to every tool, not only to Lucs: each step
the model sees the newest 80 messages of the conversation, and the same call
with the same arguments three times running ends the turn as a loop. There is
no cap on the number of steps and none on how long a creator may take to
answer an interaction.

## Observed with a real model

2026-10-06, source `7075074`, default switches (29 capabilities, 2 Skills),
`gpt-5.6-sol` on the creator's Codex subscription. A real Worker in a temporary
data root; the model sat in the seat the tests script and was reached through
the loopback Codex bridge (`127.0.0.1:7808`), not through the App's Model
Broker route. Three requests in Chinese, a fresh Project for each (three
3-second image clips; for the second, one title as well):

| Request | What the model did | Calls | In the Project afterwards |
| --- | --- | --- | --- |
| "把第二个片段剪短两秒" | `use_skill` timeline-editing; discover; describe and invoke `timeline.inspect`; describe and invoke `timeline.trim-clip`; inspect again | 8 | clip 2 lasts 1 s, was 3 s |
| "标题的位置我想自己在画面上拖一下，拖完你告诉我最后放在哪了。" | discover (`layout.adjust` came first); inspect; describe; invoke with the target from `targets` and a valid `initial` at the first try; wait | 6 | title at x 120, y -60 |
| "在第一个和第二个片段之间加一个柔和的转场。" | `use_skill` transitions; discover; inspect; describe and invoke `timeline.add-transition` | 7 | 0.6 s dissolve after clip 1 |

- Every discovery query was English, for example "inspect timeline clips
  tracks transitions". No call was refused and no answer was cut (the longest
  tool result was 3319 characters). Each step made one tool call. Each reply
  was in Chinese and named no tool and no id.
- While the second request waited for the creator, the model was not called.
  The confirmation was made by the rig through the Host's confirmation seam,
  not by a gesture in the editor.
- 21 model calls, 46 960 input tokens and 1 062 output tokens; 22 to 31 seconds
  a request.
- Trimming left a 2-second gap before clip 3. The timeline-editing Skill says
  to leave `ripple` off unless the creator wants no gap; the model did, and
  its reply said the later clips had not moved.

Not observed: what the model does after a refusal (none happened), a Luc
withheld for its language, a crowded Project, a second request in the same
conversation, any other model, and the App's own route to a model.

## Known limits

- **One real-model run, and a small one.** See above. Every test of this path
  still scripts the model.
- **A simple edit takes six to eight model calls.** The model reads a Skill,
  discovers, and describes each capability before invoking it, even one the
  Skill already names. The declarations ask for the describe; the Host does
  not require it.
- **Ranking is plain keyword overlap.** No stemming and no synonyms: "cut" does
  not find "trim" unless a description says so. Descriptions should name the
  words a request would use.
- **No paging in discovery.** `more` says how many others there are; the agent
  narrows with keywords.
- **A tool's own result may still be cut.** The never-cut rule covers the
  protocol's answers. A tool that returns more than 6000 characters is
  shortened by `agent_turn`, with a visible mark; keeping results small is
  each tool's job.
- **No tool that makes a Luc or a layer.** A person's own Luc and their layers
  reach the agent from the `lucs` folder; the agent cannot write either from
  inside a turn.
- **Only Video.**
