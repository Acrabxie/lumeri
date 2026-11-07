# An agent loop

Lumeri's agent turn is a loop: ask the model, run the tools it asked for in
order, and stop when it answers, when the creator stops it, or when it repeats
itself. The decisions in that loop are one module, `lumeri.agent.loop`, with
one interface, `iface:agent.loop/v1`
(`contracts/interfaces/agent/agent.loop.v1.json`). Its original is the
Runtime's own code (`lumeri_runtime/agent_turn.py`), and it decides whenever
no Luc does.

A Luc may bring a program that decides instead: protocol
`lumeri.agent.loop/v1`, permission `agent.loop`. The program decides; the
Runtime does. Every model step, tool call, event, receipt and budget is the one
Lumeri's own loop would make. The program never sees a credential, a file or
the network.

The parts the loop leans on (fitting the request, wording guidance, keeping
the conversation, the native toolbox) are modules of their own; a program
answers for one of them under the same permission, over
`lumeri.agent.part/v1` (`agent-parts.md`).

## The capability

```json
{
  "id": "agent-loop.codex",
  "protocol": "lumeri.agent.loop/v1",
  "description": "Decides each next step the way Codex's turn loop does.",
  "surface": "agent-loop",
  "name": "codex",
  "title": {"en": "Codex loop", "zh-CN": "Codex 循环"},
  "executable": "bin/codex-lumeri-loop",
  "platforms": ["darwin-arm64"]
}
```

- `executable` is a file of the Luc, at most 16 MiB, run as it is. The
  Runtime writes it under its data folder, named by its hash, and checks the
  hash every time it starts it.
- `platforms` are the systems the file is built for: `darwin-arm64`,
  `darwin-x86_64`. On another system, or one without a sandbox, the program
  does not run and Lumeri's own loop decides (`E_LUC_LOOP_PLATFORM`,
  `E_LUC_SANDBOX`).
- The manifest declares the permission `agent.loop` and nothing else: no Host
  API, `editable: "none"`. A Luc brings one agent loop
  (`E_LUC_LOOP_CONFLICT`); with two switched on, neither runs.
- `agent.loop` is granted only by Lumeri's review of the file
  ([permissions.md](permissions.md)).

## The sandbox

The program runs under `sandbox-exec` with a profile that denies everything,
then lets it run its one file, read that file and the system libraries, and
read the machine's page size, which a compiled program's runtime asks for
before its `main`. It cannot open another file, read any other system setting,
write anything, or reach the network; its environment is empty and its working
directory is `/`. Its one way out is its
standard output.

## The exchange

For each action the Runtime writes one JSON line to the program's standard
input and reads one JSON line from its standard output:

```text
request  {"v":1, "turn_id", "step", "state", "observation", "hints"}
answer   {"v":1, "state", "action"}
```

`state` is the program's own: whatever it answered last time comes back in
the next request (at most 256 KiB). `hints` are the bounds it works inside:
the tool names, the budget left in micro-units, `window_messages` (80) and
`doom_loop_repeats` (3).

The observation is what just happened:

| kind | fields | when |
| --- | --- | --- |
| `turn_start` | `message`, `history` | the creator's message, and the settled conversation before it (at most 79 messages) |
| `model_reply` | `content`, `tool_calls`, `finish_reason` | after each model step |
| `tool_result` | `call_id`, `ok`, `model`, `content` | after each answered call; `content` is the exact text the conversation keeps |
| `guidance` | `items`, `content` | the creator spoke before a model step; `content` is the message added to the conversation |

The action is what to do next:

| kind | fields | allowed when |
| --- | --- | --- |
| `ask_model` | `window` | every call of the last reply is answered; `window` is the newest 1..80 messages of the conversation, exactly as the Runtime keeps it, starting at a user message |
| `run_call` | `call_id` | it names the next unanswered call of the last reply |
| `skip_call` | `call_id`, `reason` | the same; the model reads `{"error":{"code":"skipped","message":reason}}` (reason at most 500 characters) |
| `end` | `outcome: "completed"` | right after a model reply in words with no calls |
| `end` | `outcome: "failed"`, `reason` | every call answered; `reason` is `doom_loop`, `model_output_truncated` or `model_protocol_error` |

The Runtime keeps the conversation. A program that wants to send less of it
sends a shorter window; it cannot change or add a message. A model step that
the Runtime refuses or that fails (budget, sign-in, no model), or that was sent
and whose answer is never confirmed (`model_result_unconfirmed`), ends the turn
without asking the program; none of these is a break. After the third identical call running the
Runtime ends the turn itself (`doom_loop`), whatever the program would decide.

Each answer must come within 10 seconds; the model and the tools run outside
that clock. When the turn ends or the creator stops it, the Runtime closes the
program's input, waits 2 seconds and kills it.

These numbers (4 MiB per line, 256 KiB of state, 10 s per answer, 2 s grace,
256 MiB of memory, 5 s of CPU) and the sandbox itself are Acrab's decision of
2026-10-26.

## When the program breaks

A program that does not start, answers late, too large or out of shape, exceeds
its memory or CPU bound, asks for an action it may not take, or ends, is
stopped. Before every action the Runtime has written the action and its key to
the Session's `agent-loop-checkpoint.json`, so Lumeri's own loop carries the
turn on from the last action performed (including across a Worker crash): the
reply in hand is finished from its first unanswered call, no model step is
asked twice and no call runs twice.

For the rest of that start Lumeri's own loop decides, and the Session hears
`luc.module_fell_back` once (`module: "lumeri.agent.loop"`, with the code:
`E_LUC_LOOP_START`, `E_LUC_LOOP_PROTOCOL`, `E_LUC_LOOP_ACTION`,
`E_LUC_LOOP_LIMIT`, `E_LUC_LOOP_DEADLINE`, `E_LUC_LOOP_MEMORY`,
`E_LUC_LOOP_CPU`, `E_LUC_LOOP_EXIT`, `E_LUC_LOOP_CHANGED`). Three breaks of the
same file leave its Luc off at later starts until
`luc_cli.py modules reset lumeri.agent.loop`.

## Held by

`tests/test_luc_agent_loop.py`: a real Worker with the model scripted; a
program that decides as Lumeri does produces the same model requests, events
and conversation; every way of breaking hands the turn back without asking
anything twice; the Runtime accepts exactly the answers the contract allows;
and the sandbox denies files, writes, network, environment and every system
setting but the page size. Where `scripts/build_codex_loop.sh` has built it,
the same file runs a program derived from Codex's turn loop (a crate,
`codex-rs/lumeri-loop`, in a Codex checkout): it decides a whole turn in the
sandbox with the same model requests, events and conversation as Lumeri's own
loop, and stops a repeated call before it runs.
