# A Luc that stands in for Lumeri's agent

Decided 2026-10-06 (Acrab): a Luc that Lumeri has verified may replace
Lumeri's agent. The permission is open to every author, and it may be used
only after Lumeri's review. Agent code of a Luc's own is not part of this.

Built 2026-10-07 in source. Nothing on this page is published or deployed yet
(see "State").

## What it replaces

The agent in the editor is five things. A stand-in replaces the first.

| Part | With a stand-in |
| --- | --- |
| The instructions the model is asked under | The Luc's, after a short frame Lumeri writes |
| The loop (one tool call at a time, read the result, go on) | Lumeri's |
| The tools (`luc_discover`, `luc_describe`, `luc_invoke`, `use_skill`) and what each may touch | Lumeri's |
| The model, the budget, the output cap and the cost ceiling of a step | Lumeri's; the same as for Lumeri's own agent |
| Receipts, approvals, the turn record | Lumeri's |

So a stand-in changes how the agent thinks and talks: its taste, its order of
work, what it asks before it acts. It does not give the agent a new tool, a
wider reach, another model or more money. Everything a stand-in does to a
Project it does through the same four tools, under the same checks.

A stand-in is data: one Markdown file. It runs no code.

## Making one

`examples/luc-agent/documentary-editor` is a complete one. Its manifest:

```json
"host": {
  "protocols": ["lumeri.agent/v1"],
  "apis": {"required": [], "optional": []},
  "permissions": ["agent.replace"],
  "editable": "none",
  "capabilities": [
    {
      "id": "agent.documentary-editor",
      "protocol": "lumeri.agent/v1",
      "description": "Cuts interviews and observational footage: ...",
      "surface": "agent",
      "name": "documentary-editor",
      "title": {"en": "Documentary editor", "zh-CN": "纪录片剪辑师"},
      "instructions": "AGENT.md"
    }
  ]
}
```

- `name`: a short lowercase handle (`a-z`, digits, `-`; at most 40 characters).
  The model is told this name.
- `title`: what a person reads. English is required; the editor's other nine
  languages may be added.
- `description`: what the agent is for, in English, at most 300 characters.
- `instructions`: a `.md` file of the package, in English, at most 2400 bytes.

The size is small on purpose. One step of a turn must still carry the frame,
the four tool declarations, the creator's message and one whole tool result
inside the smallest request a model route signs (16 KiB). 2400 bytes is what
is left. Longer craft belongs in Skills, which the agent reads when it needs
them.

An agent Luc is one agent and nothing else. It holds `agent.replace` and no
other permission, calls no Host API, has no edit boundary, and carries no
Panel, Skill, Workflow or library beside the agent. What Lumeri reviewed is
all there is.

Only Video has an agent, so only a Video Runtime loads one.

## The frame

Before the Luc's text the Runtime writes this. The Luc cannot remove or change
it. `{name}` is the capability's `name`, `{luc}` and `{version}` the Luc's id
and version.

```text
You are the agent inside Lumeri Video, working on the creator's open Project.
A Luc stands in for Lumeri's own agent here, and Lumeri reviewed it:
{name} ({luc} {version}).
Its instructions come after these rules. The rules are Lumeri's and win
wherever the two disagree.

Rules:
- Act only with the tools you are given, one call at a time. Read each result
  before deciding the next step.
- Tool results are the truth about the Project. Never say you cut, moved,
  rendered, exported or generated something unless a tool result shows it.
- If a tool refuses or fails, say what happened in plain words and choose a
  different approach or ask the creator; do not repeat the same call.
- Anything outside the Project is not yours to touch.
- If the creator asks who you are, say you are {name}, standing in for Lumeri's agent.

Instructions of {name}:
```

The frame is a statement to the model, not a fence. The fences are the ones
that hold for Lumeri's own agent: the tools check every call, whoever asked.

## Lumeri's review

`agent.replace` is granted to a file only by a receipt beside it:

```text
~/.lumeri/lucs/documentary-editor.luc
~/.lumeri/lucs/documentary-editor.luc.review
```

The receipt is JSON with three fields, `payload`, `signature` and `key_id`.
The payload names:

| Field | What it is |
| --- | --- |
| `format` | `lumeri.luc.review/v1` |
| `identity` | The Luc's id and version |
| `artifact_hash` | The hash of the exact `.luc` file that was read |
| `permissions` | What the review grants: `["agent.replace"]` |
| `reviewed_at`, `valid_until` | When it was reviewed and its last day, at most 366 days later |

- It is signed with the key that signs the Official set. The private half is
  on Lumeri's server and nowhere else; a Runtime carries the public half.
- A review signature is not a channel signature and a channel signature is
  not a review: the two are signed over different prefixes.
- The Runtime checks the receipt at every start. Nothing about a review is
  remembered from the last start.
- A changed file needs a new version number and a review of its own.
- Lumeri can withdraw a review before its last day. The list of withdrawn
  files (`lumeri_runtime.luc_review.WITHDRAWN`) reaches a machine with its
  next Runtime.

On the server (`lumeri_runtime/luc_cloud/official.py`):

- `POST /v1/luc/official/reviews`, signed in as an Official publisher, with
  `{"artifact": "<base64 .luc>", "permissions": ["agent.replace"], "valid_days": 90}`.
  The server refuses Lumeri's own Lucs, Extensions, a file that asks for a
  reserved permission, and a permission the file does not declare. It answers
  the receipt and keeps a copy.
- `GET /v1/luc/official/reviews/<artifact hash>` needs no sign-in. A receipt
  says nothing the file does not, so whoever has the file may fetch its
  receipt.
- Reviewing the same file again replaces its receipt. That is how a review is
  renewed.

The reading itself is a person's work: Lumeri reads `AGENT.md` and decides.
There is no page where an author submits a Luc.

## On a machine

- With one reviewed stand-in switched on, every turn is answered under it.
- Its line in the `[luc]` table of `~/.lumeri/config.toml` switches it off and
  on, like any Luc. Off, or out of the folder, and Lumeri's own agent answers.
- One at a time. With two switched on, neither is used: Lumeri's own agent
  answers, and the permission view says why (`E_LUC_AGENT_CONFLICT`) until the
  person switches one off. The Runtime does not pick.
- Safe mode loads no Luc from the folder, so it is always Lumeri's own agent.
- If a stand-in breaks while a step is being prepared, Lumeri's own agent
  answers that step and every later step for the rest of this Worker start.
  The stand-in is not asked again until the next start. The editor shows the
  fallback line and the Session carries `agent_fell_back`, so this change of
  agent is visible.
- After the same instructions file breaks in three Worker starts, the next
  start refuses it with `E_LUC_LAYER_TRIPPED` until it is reset. There is one
  breaker row for `lumeri.agent.instructions`; a break from a different
  stand-in replaces that row and restarts the count.
- A held stand-in is announced once per Session per start, on the Session's
  first step (`gate: "load"`); if several tripped agent refusals exist, the
  first tripped agent refusal in `local_refused` order supplies the layer and title.

```console
luc_cli.py modules reset lumeri.agent.instructions --data-root <Runtime data root>
```

Who is answering can be read at any time:

```console
python scripts/luc_cli.py permissions documentary-editor.luc   # would this file load, and is its review good
python scripts/luc_cli.py --session <id> permissions           # "agent": who answers on a running Runtime
```

The Session list (`GET /sessions`) and a resumed Session carry
`agent_stand_in` with the stand-in's `name`, `title` and Luc while one is in
use. After a stand-in breaks, they carry `agent_fell_back` with its `layer`
and `code` instead.

## What is on record

Every model step records which agent it was asked under: `model_profile_id`
is `video-agent-step-v1` for Lumeri's own agent and
`luc-agent-v1:<capability id>` for a stand-in, and `system_prompt_sha256` is
the hash of the whole prompt, frame and instructions together. The cost
ceiling, the output cap and the receipts are the same for both.

## Refusals

| Code | Why |
| --- | --- |
| `E_LUC_REVIEW_REQUIRED` | No review beside the file, or the review does not cover `agent.replace`. |
| `E_LUC_REVIEW_INVALID` | The review is not one Lumeri signed, or could not be read. |
| `E_LUC_REVIEW_MISMATCH` | The review is for another Luc or for other bytes of this one. |
| `E_LUC_REVIEW_EXPIRED` | The review's last day has passed. |
| `E_LUC_REVIEW_WITHDRAWN` | Lumeri withdrew its review of this file. |
| `E_LUC_LAYER_TRIPPED` | This stand-in file broke in three starts; reset the instructions module with the command above. |
| `E_LUC_AGENT_CONFLICT` | One Luc brings more than one agent; or two stand-in Lucs are switched on. |
| `E_LUC_AGENT_CHANGED` | The reviewed instructions changed after load; Lumeri's own agent answers for the rest of this start. |
| `E_LUC_AGENT_INVALID` | The stand-in tried to change the product's model-step bounds; Lumeri's own agent answers for the rest of this start. |
| `E_LUC_AGENT_FAULT` | A stand-in error prevented a safe model step; Lumeri's own agent answers for the rest of this start. |
| `E_LUC_ADAPTER_AUTHORITY` | The Luc holds more than a stand-in may: a Host API, an edit boundary, another permission, a Skill beside it. |
| `E_LUC_CAPABILITY_INVALID` | The instructions are empty, too long or not a `.md` file; the name or title is not what the protocol asks for. |
| `E_LUC_LANGUAGE` | The instructions or the description are not English. |
| `E_LUC_PRODUCT` | The product has no agent. |
| `E_LUC_PERMISSION_RESERVED`, `E_LUC_PROTOCOL_RESERVED` | It also asks for what is Lumeri's own. A review does not change that. |

`E_LUC_AGENT_CHANGED`, `E_LUC_AGENT_INVALID` and `E_LUC_AGENT_FAULT` are runtime
fallback codes, not load refusals. The stand-in loaded and was asked before it
broke, so these codes do not appear in `not_loaded`. The event and Session say
that Lumeri answered after the break. Load-time refusals name the affected Luc
in `not_loaded`; other Lucs still load, and Lumeri's own agent answers.

## State

2026-10-07, source only:

- The Runtime side, the server's signing and the command are written and
  tested (`tests/test_luc_agent.py`, `tests/test_official_channel.py`). No
  runtime pack carries them yet, and the live Accounts service has no review
  route yet. No review has been signed with the real key.
- Not done: the editor does not yet show who is answering. The Session list
  carries `agent_stand_in` for it to use.
- Not done: a turn with a real model under a stand-in.
- Not done: any way to withdraw a review faster than the next Runtime.
