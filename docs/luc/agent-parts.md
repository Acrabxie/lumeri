# An agent part

Lumeri's agent turn is a loop (`docs/luc/agent-loop.md`) and four parts it
leans on. Each part is a module with its own interface, and each interface is
of the sandboxed class (`docs/luc/interfaces.md`, rule 6): a replacement is
code, and it runs only in the code sandbox.

| part | module | interface | what it answers |
| --- | --- | --- | --- |
| `request-window` | `lumeri.agent.request-window` | `iface:agent.request-window/v1` | which messages one model request carries |
| `guidance` | `lumeri.agent.guidance` | `iface:agent.guidance/v1` | how the model reads what the creator said mid-turn |
| `conversation` | `lumeri.agent.conversation` | `iface:agent.conversation/v1` | what the stored conversation keeps of an interrupted turn, which messages a step reads, what the model reads of a tool's result |
| `native-toolbox` | `lumeri.agent.native-toolbox` | `iface:agent.native-toolbox/v1` | tools of its own, beside the Official set |

The originals are the Runtime's own functions, and each answers whenever no
Luc does. The original toolbox is one tool, `update_plan`: the model states
the steps of the creator's request and how far each has come, reads that back,
and the editor is handed the plan with the call's result; it changes nothing
in the Project. When the conversation outgrows the 80 messages a step reads,
the original turn has the model summarize what leaves the window (one model
step with no tools) and reads the summary, then everything after it
(`iface:agent.conversation/v1`, `compaction`); the full conversation stays on
disk. A Luc may bring a program that answers for a part instead. The
program decides the answer; the Runtime holds it to what the original could
have answered, and keeps the stored conversation, the model, the budget and
the events. The program never sees a credential, a file or the network.

## The Luc

A capability of protocol `lumeri.agent.part/v1` names the part and the program:

```json
{
  "id": "agent-part.guidance", "protocol": "lumeri.agent.part/v1",
  "surface": "agent-part", "part": "guidance", "name": "plain-guidance",
  "title": {"en": "Plain guidance", "zh-CN": "简明引导"},
  "description": "Words the creator's mid-turn guidance plainly.",
  "executable": "bin/guidance", "platforms": ["darwin-arm64"]
}
```

- The Luc asks for `agent.loop`, which Lumeri's review of its exact bytes
  grants; without the review it is refused (`E_LUC_REVIEW_REQUIRED`). A part
  shapes the creator's turn as a loop does, so it is the same permission.
- `part` is one of the four above. A Luc brings one program a part
  (`E_LUC_PART_CONFLICT`). With two Lucs answering for the same part, neither
  runs and the original answers.
- `executable` and `platforms` are as for an agent loop: a file of the Luc, at
  most 16 MiB, written under the data folder by its hash and checked every
  time it starts; on another system, or one without a sandbox, the original
  answers.

## The exchange

The sandbox is the agent loop's. One process runs for each answer: the
Runtime writes one call line, the program answers with one line, and the
process is closed. It is the exchange of a tool program
(`docs/luc/tool-programs.md`), with the part's operation as the tool:

| from | line | meaning |
| --- | --- | --- |
| Runtime | `{"v":1, "kind":"call", "tool", "arguments"}` | one operation of the part's interface |
| program | `{"v":1, "kind":"host", "method":"fits", "args":{"window"}}` | `request-window` only: does this window fit? at most 32 a call |
| Runtime | `{"v":1, "kind":"host_result", "ok":true, "value":{"fits"}}` | the Runtime measured the real request |
| program | `{"v":1, "kind":"result", "model"}` | the answer |
| program | `{"v":1, "kind":"refused", "code", "message"}` | where the operation lists the code; `message` at most 2000 characters |

Each answer must come within 10 seconds; a line is at most 4 MiB.

## What each answer may be

| part | `tool` | `arguments` | the answer |
| --- | --- | --- | --- |
| `conversation` | `settled_conversation` | `messages`: the newest 80 stored | a prefix of them, then one tool result for each call the last reply left unanswered, in order, at most 6100 characters each; the whole leaves no call unanswered |
| `conversation` | `model_window` | `messages`: the newest 80 | the newest 1..80 of them, starting at a user message, each as given |
| `conversation` | `_clipped` | the tool's result object | text of at most 6100 characters |
| `guidance` | `guidance_message` | `items`: what the creator said | text containing every item as said; the rest English, at most 2000 characters |
| `request-window` | `_fitted_window` | `conversation` | a suffix starting at a user message; each message as given or, for a tool result that is not the newest message, the let_go result; it must fit. Or refuse `agent_request_too_large`, which refuses the step |
| `native-toolbox` | `toolbox_for` | `product_id` | at most 16 tool declarations: a lowercase name of at most 48 characters that is not one of the Runtime's own (`luc_discover`, `luc_describe`, `luc_invoke`, `use_skill`), an English description of at most 1024 characters, an object JSON Schema of at most 16 KiB |
| `native-toolbox` | `call` | `tool`, `arguments` | an object of at most 64 KiB, or refuse `tool_refused`. The Runtime checks the arguments against the declared schema before it asks |

The toolbox is asked once a start. A tool of a program answers from its
arguments alone: it makes no host call, so it can neither read nor change the
Project.

## When the program breaks

A program that does not start, answers late, too large or out of shape, gives
an answer the table does not allow, refuses with a code the operation does not
list, measures too many windows, or ends, is stopped. The original answers
the same call, so the turn goes on as Lumeri's own code would carry it. A tool
of the program's toolbox is answered by the original's tool of the same name
(`update_plan`); one the original does not have is refused with
`E_LUC_PART_BROKE`. The next start's toolbox is the original's.

For the rest of that start the original answers for that part, and each
Session hears `luc.module_fell_back` once (`module` is the part's module,
`gate: "run"`, with the code: `E_LUC_PART_START`, `E_LUC_PART_PROTOCOL`,
`E_LUC_PART_ACTION`, `E_LUC_PART_RESULT`, `E_LUC_PART_LIMIT`,
`E_LUC_PART_DEADLINE`, `E_LUC_PART_EXIT`, `E_LUC_PART_CHANGED`). Three breaks
of the same bytes leave the Luc off for that part at later starts until
`luc_cli.py modules reset <module>`. `luc_cli.py modules` shows, for each
part, whether the original or a Luc answers.

## What it does not cover

- An agent loop's program (`agent-loop.md`) chooses its own window; it is
  checked against Lumeri's own `model_window`, not against a `conversation`
  part.
- Summaries are Lumeri's own. While a `conversation` program answers
  `model_window`, or a loop program decides, no summary is made or read: the
  program windows the conversation as stored.
- Stopping the turn does not stop a program mid-answer; the deadline ends it.

## Held by

`tests/test_luc_agent_parts.py`: the load gate refuses a part without the
review, a part that is not one, and two programs for one part; the Runtime
accepts exactly the answers the table allows; a real Worker with programs that
answer as Lumeri does sends the same model requests and keeps the same
conversation; what a program answers is what the model reads; a break leaves
the original answering for the rest of the start, said once and counted; a
program's tool reaches the agent; a fitting refusal ends the turn as a message
too long; and a fenced process answers over the pipe and reads no file.

`tests/test_agent_plan_and_compaction.py`: the original's plan reaches the
editor and refuses a plan the creator could not follow; a conversation that
outgrew the window is summarized once, read from the summary word for word
after it, with nothing dropped on disk and no editor event; a summary that
could not be written leaves the old window and is not asked for at every step.
