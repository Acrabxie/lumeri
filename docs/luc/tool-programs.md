# A tool program

Lumeri's agent tools are Official Lucs: each `lumeri.tool/v2` package carries
Python code that the Runtime runs for the agent's call. Their interfaces are of
the sandboxed class (`docs/luc/interfaces.md`, rule 6), so a replacement is
code too, and it runs only in the code sandbox. A layer on one of these Lucs
may bring a program that answers for a capability in place of the Official
code. The program decides what the tool answers; the Runtime still does every
read and every edit, through the same calls and checks the Official code goes
through. The program never sees a credential, a file or the network.

Fifteen Official Lucs take a program: 17 interfaces, 27 capabilities
(`timeline.inspect`, `timeline.add-track` and the rest of the timeline editor,
`media.probe`, `media.caption`, `storyboard.edit`, ...). The catalog
(`docs/luc/interfaces-catalog.md`) lists them as sandboxed `lumeri.tool/v2`
interfaces.

## The layer

A layer adds two things to the Luc: the program file, and a `program` field on
the capability it answers for.

```json
{
  "manifest_patches": [{
    "op": "add", "path": "/host/capabilities/0/program",
    "value": {"executable": "bin/answer", "platforms": ["darwin-arm64"]}
  }],
  "file_operations": [{
    "op": "add", "path": "bin/answer", "source": "payload/0", "result_sha256": "..."
  }]
}
```

- `executable` is a file the layer adds, at most 16 MiB, run as it is. The
  Runtime writes it under its data folder, named by its hash, and checks the
  hash every time it starts it.
- `platforms` are the systems the file is built for: `darwin-arm64`,
  `darwin-x86_64`. On another system, or one without a sandbox, the program
  does not run and the Official code answers; nothing breaks.
- The Luc's own files stay as they shipped. A layer that changes the package's
  code is refused (`E_LUC_TOOL_CODE`); one that adds a file no capability runs
  is refused (`E_LUC_INTERFACE_CLASS`). Everything else the interface fixes
  (protocol, surface, tool, input and output) stays fixed, as for any layer.
- It asks for nothing: the program reaches only the Host APIs the Official Luc
  declared, within its editable boundary.

The layer goes in `~/.lumeri/lucs` like any layer, or in the creator's
activation profile. `examples/luc-tool-program` is one: `get_timeline` for
`timeline.inspect`, in Rust; `scripts/build_tool_program.sh` builds it.

## The sandbox

The same sandbox as an agent loop's program (`docs/luc/agent-loop.md`): it may
run its one file, read that file, the system libraries and the machine's page
size, and nothing else. It cannot open another file, write anything or reach
the network; its environment is empty and its working directory is `/`. Its one
way out is its standard output. One process runs for each call and is closed
when the call is answered.

## The exchange

Protocol `lumeri.tool.program/v1`, one JSON line each way:

| from | line | meaning |
| --- | --- | --- |
| Runtime | `{"v":1, "kind":"call", "tool", "arguments"}` | the agent's call; `arguments` already keep the input schema |
| program | `{"v":1, "kind":"host", "method", "args"}` | one ToolHost call (below), as often as it needs |
| Runtime | `{"v":1, "kind":"host_result", "ok":true, "value"}` | what the call answered |
| Runtime | `{"v":1, "kind":"host_result", "ok":false, "error":{"code","message"}}` | what the call refused; the program decides what to do with it |
| program | `{"v":1, "kind":"result", "model"}` | the tool's answer: it must keep the interface's output schema |
| program | `{"v":1, "kind":"refused", "code", "message"}` | the tool refuses: `code` is one of the interface's error codes, `message` at most 2000 characters |

The ToolHost calls, each with exactly these arguments:

| method | args | value |
| --- | --- | --- |
| `new_id` | `prefix` | an id derived from this call |
| `read_timeline` | | `project_revision`, `timeline`, `can_undo`, `can_redo` |
| `list_media` | | the Project's assets |
| `media_asset` | `asset_id` | one asset, or `null` |
| `probe_media` | `asset_id` | its measured facts |
| `search_library` | `query`, optional `limit` (1..50) | the matching ranges |
| `asset_notes` | `asset_id` | the notes beside one asset |
| `note_asset` | `patch` | the revision the note made |
| `ask_engine` | `spec` | the engine's answer |
| `run_engine_job` | `spec` | `{"status", "body"}` |
| `start_engine_job` | `spec` | `{"ok", "model"}` |
| `run_document_patch` | `patch` | `{"status", "body"}` |
| `edit_document` | `patch` | `{"ok", "model"}` |
| `edit_timeline` | `patch` | `{"ok", "model"}` |

Each is a Host API the Official Luc declared, checked at every call; an edit
also has to fall inside the Luc's editable boundary. What the editor is shown
of an edit, and whether the timeline changed, come from the host call that made
it.

Each answer must come within 10 seconds; the host calls run outside that clock.
A call makes at most 256 host calls. A line is at most 4 MiB.

## When the program breaks

A program that does not start, answers late, too large or out of shape, asks
for a host call that does not exist or with other arguments, answers outside
the output schema or with an error code the interface does not list, makes too
many host calls, or ends, is stopped. The Official code then answers the same
call, so the agent reads what Lumeri's own tool would have told it.

One exception: when the program had already changed something (a successful
`note_asset`, `run_engine_job`, `start_engine_job`, `run_document_patch`,
`edit_document` or `edit_timeline`), the call is not answered a second time.
It is refused with `E_LUC_PROGRAM_BROKE`, and what the program changed stands.

For the rest of that start the Official code answers every call of that Luc,
and each Session hears `luc.module_fell_back` once (`module` is the Luc's id,
`gate: "run"`, with the code: `E_LUC_PROGRAM_START`, `E_LUC_PROGRAM_PROTOCOL`,
`E_LUC_PROGRAM_ACTION`, `E_LUC_PROGRAM_RESULT`, `E_LUC_PROGRAM_LIMIT`,
`E_LUC_PROGRAM_DEADLINE`, `E_LUC_PROGRAM_EXIT`, `E_LUC_PROGRAM_CHANGED`).
Three breaks of the same layers leave them off at later starts until
`luc_cli.py modules reset <luc id>`.

## What it does not cover

- A request the editor makes directly (an engine job, a rough-cut review, a
  document edit through the Worker's own routes) still runs the Official code;
  only the agent's call goes to a program.
- Stopping the turn does not stop a program mid-call; the deadline per answer
  and the bound on host calls end it.

## Held by

`tests/test_luc_tool_program.py`: the load gate takes a layer that adds a
program and refuses one that changes the code or adds anything else; the
Runtime accepts exactly the answers the interface allows; a real Worker's
agent reads the same answer from a program as from the Official code, the
Official code answers every break and the rest of the start, a program that
changed the Project and broke is not answered twice, and a fenced process
answers over the pipe and reads no file. Where `scripts/build_tool_program.sh`
has built it, a compiled program answers a real Worker's agent from the
sandbox.
