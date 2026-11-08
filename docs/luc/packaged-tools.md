# Packaged tools (`lumeri.tool/v2`)

Status: source, 2026-10-05. Not built, installed or published.

## What changed

Until now an Official Luc was data: a manifest that named a tool whose code
sat in the Runtime (`lumeri.tool/v1`, `native_tool`). The Runtime therefore
held every tool, and "moving a capability into a Luc" moved only its label.

With `lumeri.tool/v2` the package carries the code. The fourteen Video tools
now live in three packages, with a fourth for roughcut, and `lumeri_runtime/agent_tools.py` holds none:

| Package | File | Tools |
| --- | --- | --- |
| `lumeri.timeline-inspector` 0.2.0 | `tools.py` | `get_timeline` |
| `lumeri.project-media-inspector` 0.2.0 | `tools.py` | `search_media`, `probe_media` |
| `lumeri.timeline-editor` 0.2.0 | `tools.py` | the eleven `timeline_*` edits |
| `lumeri.roughcut` 0.2.0 | `tools.py` | `roughcut_media` (an engine job, below), `review_roughcut` |

The Runtime adds only `luc_discover`, `luc_describe`, `luc_invoke` and
`use_skill` around whatever the verified Official set holds
([agent-protocol.md](agent-protocol.md) is the contract for those four: a
tool's description and input schema are written in English, and what the
agent reads of them is never cut). Switching a tool
package off removes its tools from the agent; there is no native copy to fall
back to. `lumeri.project-media-inspector` is now on by default for that reason.

## Who may bring code

Only the verified Official set: a package whose bytes match the index the
Runtime trusts (the bundled index, or a channel index signed by the Official
key). The trust is the signature. Tool code runs inside the Worker process
and is not sandboxed.

- A creator's own Luc that declares `lumeri.tool/v2` is refused
  (`E_LUC_TOOL_CODE`), whether it names a new tool or an Official one.
- A creator's layer that patches or replaces the code file is refused with
  the same code; the Official Luc loads without the layer and a layer notice
  says why.
- Tool code that does not load (it raises, has no `TOOLS`, lacks `run`) makes
  only its own package unavailable.

## What tool code is given

A code file is executed once per load with two names provided, `Refused` and
`FACTS` (transition kinds, blend modes, track kinds), and must define

```python
TOOLS = {"tool_name": {"parameters": <JSON Schema>, "run": fn(host, arguments), "edits": {...}}}
```

`edits` is present on a tool that changes the Project and names the target
types, properties and areas it may change. `host` is a `ToolHost`
(`luc_packaged_tools.py`). It offers `new_id`, `read_timeline`, `list_media`,
`media_asset`, `probe_media`, `search_library`, `edit_timeline`, and for the tool kinds below `run_engine_job`, `ask_engine` and `edit_document`. Every call
is a Host API the package declared (`project.read`, `timeline.edit`) and is
authorised per call; every patch passed to `edit_timeline` is checked against
the tool's own `edits` and the package's editable boundary before the Worker
applies it with its usual revision check, idempotency and creator approval.

A package may not declare more than this surface: Host APIs beyond
`project.read` / `timeline.edit`, or permissions the tools do not need, are
refused when the package loads (`E_LUC_ADAPTER_AUTHORITY`).

## Engine jobs

A tool may be one sandboxed engine run instead of a `run` function:

```python
TOOLS = {"roughcut_media": {"parameters": ..., "job": fn(arguments) -> job, "edits": {...}}}
```

`job` returns a description (`lumeri_runtime/engine_jobs.py`): the governed
capability and sandbox profile it runs as, the engine subcommand, the asset
and the kinds of asset it takes, what it needs from the machine by name
(`ffmpeg`, `transcriber`), a `request(staged)` that writes the engine's input,
the manifest file to wait for, and a `finish(manifest)` that says what the
result is and which notes to keep beside the asset.

The Runtime runs it the same way for every package (`Worker.run_engine_job`):
the asset is staged where the sandbox can read it and removed afterwards, the
engine runs under the Gate as a job with a handle, an interrupted run is
reconciled by its manifest, and the notes are their own gated write. A job may
replace only its own notes about its own asset. The Host API is `engine.job`.

The same job is reached three ways and is one job: the Agent through
`luc_invoke`, a Panel through its Runtime-carried call, and the editor's
direct route. With the Luc off all three answer that it is unavailable.

A job that makes a file says so with `produces` (a name and a content type)
instead of, or beside, a manifest. The Runtime imports that file as a new
asset, and only for a tool whose `edits` name `asset.presence`. Without a
manifest, `finish` is given what the engine printed.

Jobs moved: roughcut (`lumeri.roughcut` 0.1.0) and captions
(`lumeri.captions` 0.1.0; `lumeri_runtime/captions.py` is gone — styles, the
cue and look rules and the encoder choice are the package's).

A job may read several assets (`sources`, at most sixteen) and may ask the
engine before it runs (`plan`: a read-only question under its own governed
capability, and a `then(answer)` that settles the file the run produces and
what the run is asked, or refuses in the engine's words with the options it
has). `check(staged)` is the package's own look at what was staged. A run that
finishes saying `produced: False` lands nothing. Media edits moved this way:
`lumeri.media-edit` 0.1.0, fourteen operations; `lumeri_runtime/media_edit.py`
is gone. How long the engine may take stays the Runtime's (`timeout_sec` in
what the package is handed).

A job may read no asset at all (`sources: ()`), and its `finish` may refuse:
the run succeeded, but what it left is not a result (a narration that came
out silent). The caller gets the reason, the options and the engine's report.

A read-only tool may put one question to the engine inside its sandbox with
`host.ask_engine(question)` (Host API `engine.ask`, permission
`project.read`). Nothing is staged and nothing is written. Speech moved this
way, as two packages because a package either reads or writes:
`lumeri.speech` 0.1.0 (narrate, a job) and `lumeri.voices` 0.1.0 (the voices
this machine has, a question). `lumeri_runtime/speech.py` is gone.
Craft questions moved the same way: `lumeri.craft` 0.1.0 asks the five craft
libraries that answer (grade, camera, compose, rhythm, edit);
`lumeri_runtime/craft.py` is gone.

A job may be handed what the Project holds (`reads: ("timeline",)`) and name
the assets it reads from that (`sources` as a function of what was read, at
most 2048). What it produces may then be a file that describes an edit
(`application/json`, `application/zip`) as well as media. Interchange export
moved this way: `lumeri.interchange` 0.1.0. What the Runtime kept is staging a
timeline's footage for its own exports and renders
(`lumeri_runtime/timeline_assets.py`, formerly `interchange.py`).

## Documents

A tool may be one operation on a small Project document that has rules of its
own, instead of a `run` function or a job:

```python
TOOLS = {"set_delivery": {"parameters": <the operation>, "document": {...}, "edits": {...}}}
```

The description (`lumeri_runtime/document_tools.py`) carries the rules: a
`label`, the governed `capability` the write runs as, `default()`,
`validate(patch)` and a pure `apply(document, patch, context)`. Optional:
`reads` (other documents its rules are shown, by label), `beside(document,
context)` (what a reader is shown with it) and `verify(probe, document)` (a
probed file judged against it). The tool's arguments are the operation.

The Runtime keeps the document, once for all of them
(`Worker.patch_packaged_document`): the Gate, the exact revision the write is
bound to, idempotency, the evidence of what landed and deciding after an
interruption whether it did. Which file a capability writes is the Runtime's
table (`DOCUMENT_FILES`), not something a package names. The Host API is
`document.edit`; the editable boundary is `project_document` /
`document.content` / `project`.

A refusal by the rules now reaches the caller as the reason (400, the
package's code and words). Before, a value refused while writing came back as
a write that did not happen, with no reason.

Documents moved: the delivery contract and the judging of a delivered file
against it (`lumeri.delivery` 0.1.0; the Runtime kept the probe of the file in
its sandbox, `delivery_probe.py`), and the design document (`lumeri.design`
0.1.0, which is shown the delivery contract; `lumeri_runtime/design.py` is
gone).

A document may be kept as a section of the Project document and move another
section with it, when the Runtime's table says so (`DOCUMENT_PLACES`). The
storyboard moved this way: `lumeri.shotlist` 0.1.0, whose `apply` is shown the
timeline and hands back the storyboard and, when shots are laid down, which
clips the timeline holds. What a clip may be, the order of clips and the
timeline's length are still applied by the Runtime (`timeline.with_clips`); a
clip that was already there keeps every field it came with.
`lumeri_runtime/shotlist.py` is gone; reading the storyboard does not need the
package.

The notes on footage moved too: `lumeri.annotations` 0.1.0 (`media.notes`,
document `annotations`). Reading and searching the notes are the Runtime's own
and work with the package off (`library_search.NOTES_DOCUMENT`); every write
goes through the package, including the notes an engine job leaves beside its
asset and the library Panel's decisions. A job that would leave notes is
refused before the engine starts when the package is off.
`lumeri_runtime/annotations.py` is gone.

A `run` tool may read the notes beside an asset (`host.asset_notes`) and ask
for one to be written (`host.note_asset`, Host API `notes.edit`, editable
boundary `library_asset` / `asset.notes`). The write is the notes package's
governed write, so its rules and its Gate apply. `lumeri.roughcut` uses this
for `media.roughcut.review`: how notes become the roughcut a creator reads and
what a decision is moved out of `legacy_roughcut.py`, which now only keeps the
1.0 batch. The Gateway asks the Worker (`/sessions/{id}/roughcut/{asset}` and
`…/review`) instead of computing either itself. A package states its Host
APIs once for all its tools, so a package may carry tools that write through
different ones.

A packaged tool capability may name the one product it is for
(`"product": "quanta"`). Another product's Runtime does not load it
(`E_LUC_PRODUCT`, as for a workspace) and its Agent is not told it is missing
anything. The Quanta state tree moved this way: `lumeri.quanta` 0.1.0
(`quanta.tree`, the `quanta` section of the Project document). The Runtime's
`quanta.py` now only walks the tree to plan a render; a change the rules
refuse answers 400 with the rule, where it used to answer 500 without one.

A long turn: discovering, describing and invoking can add up to more than one
signed request. The Agent core then lets go of the oldest tool results in
that turn, never the newest, and leaves a line saying so
(`model_ingress._fitted_window`).

First job moved: roughcut, now the `lumeri.roughcut` Official Luc 0.1.0.
`lumeri_runtime/roughcut.py` is gone; what the Runtime kept is where whisper
is on this machine (`transcriber.py`) and 1.0's panel routes around a triage
(`legacy_roughcut.py`).

## The registry

`official_luc/REGISTRY` is the one list of Official Lucs, read like a shell
profile: one line each, comments for the sections.

```
on   roughcut   lumeri.roughcut@0.2.0   all     tool   # triage footage: ...
on   quanta     lumeri.quanta@0.1.0     quanta  tool   # the Quanta state tree
off  custom-pages  lumeri.custom-pages@0.1.0  all  custom  # pages defined by data
```

The manifest stays the authority on what a Luc contains; the registry is the
authority on which Lucs exist. `build_official_luc_bundle.py` refuses to build
when they disagree (a folder with no line, a line with no folder, a different
id, version, default state, product or kind), so adding a package or bumping
its version always shows as a changed line in that one file. The Official
index is still generated, in folder-name order; the registry's own order is
for the reader.

### On a machine: `[luc]` in the config file

`official_luc/REGISTRY` says which Official Lucs exist. Which of them are on
*on this machine* is the person's choice, and it lives where their other
settings do: the `[luc]` table of `~/.lumeri/config.toml`.

```toml
[luc]
# Every Luc on this machine. true is on, false is off; a change takes effect when Lumeri next starts.
"lumeri.captions" = true
"lumeri.custom-pages" = false
"lumeri.roughcut" = true
```

- **A written line wins.** At every Worker start a Luc is on or off as its
  line says, whatever the Local Registry held; the Registry is then brought in
  line. The Gateway reads the same line when it decides whose editor page to
  serve (`lumeri.workspace`).
- **The Runtime only adds lines.** A Luc with no line gets one holding the
  state it already has, so the table is the whole list. A line that is there
  is never rewritten, and nothing outside the table is touched: comments,
  order and the other tables stay as the person (or the CLI, or the 1.0
  server) left them. `gemia/local_config.py` keeps the table verbatim when it
  rewrites the file.
- **A config the Runtime cannot safely add to is left alone**: not valid TOML,
  a link, larger than the 64 KB the App reads, or a `luc` that is not a
  `[luc]` table. Its switches are then not applied either (except a one-line
  `luc = { ... }`, which is read but never added to).
- **Safe mode reads no config.** Recovery does not depend on a file a person
  edits.
- **The table is the machine's.** The App keeps one Registry per account and
  product; the one table applies to all of them.
- **Lucs a person added are in it too.** A `.luc` file in the `lucs` folder
  beside this file (`~/.lumeri/lucs`) gets its line the first time it loads,
  set to `true`. The folder says which of those Lucs exist; the line says
  whether one is on ([permissions.md](permissions.md)). Where no config file
  is read, that folder is not read either.

The Runtime reads the file when its launcher names it in
`LUMERI_LOCAL_CONFIG_PATH` (`lumeri_runtime/luc_config.py`), or defaults to
`~/.lumeri/config.toml` when a Gateway serves a real signed-in person without a
launcher naming anything (`name_default`). A launcher that set the variable,
even to nothing, has had its say; an isolated rig or loopback test keeps to the
roots it was given. The Gateway passes the variable on to its Workers. Without
it nothing is read or written and the switches live in the Registry alone,
which is what every test and rig gets. The App shell therefore does not need
to know about this table or pass any environment variable.

Anything that lets a person switch a Luc from a screen must write that line:
a switch made only in the Registry is overruled at the next start.

## Index and compatibility

Packages that carry code have `migration_state: "LUC_CODE"` in the Official
index and an empty `native_tool_ids`. A Runtime from before this change
rejects an index holding a state it does not know, so it refuses the whole
set rather than meeting a protocol it cannot run.

Order for a release, when one is approved:

1. Ship a Runtime that knows `lumeri.tool/v2` (runtime pack seq2).
2. Only then publish an Official channel sequence containing v2 packages.

A new Runtime that syncs the current channel seq1 finds v1 packages naming
native tools it no longer has, and stays on its bundled set.

Done in that order on 2026-10-06: runtime pack seq2 (Runtime 184e9c1), then
channel sequence 2. Between the two the server's own Luc code had to move to
184e9c1 as well, or it refuses to publish the set; see `official-channel.md`,
"State on 2026-10-06".

## What is not done

- The v1 native adapter (`luc_adapters.NativeAgentToolAdapter`) is still in
  the Runtime with an empty policy table; nothing uses it.
- `OFFICIAL-LUC-INVENTORY.*` and `OFFICIAL-LUC-MIGRATION-MATRIX.md` describe
  the tree before this change; their `agent_tools.py` rows are historical.
  They are a census of the extraction, not the list of Lucs; that list is
  `official_luc/REGISTRY`.
- The 1.0 tool families under `gemia/tools/` are not in the 1.1 Runtime at
  all and are not Lucs yet.
- The Worker's other governed capabilities (encode, composition, luvi,
  canvas, render, Quanta ...) are still Runtime code. The notes kept beside footage
  (`annotations.py`) are the library's own store, which engine jobs, search
  and the library Panel all write or read; they have not moved.
- A job asked for by the Agent runs with the same actor as a direct request,
  as packaged timeline edits already do.

Tests: `tests/test_luc_packaged_tools.py`, `tests/test_luc_engine_jobs.py`,
`tests/test_runtime_roughcut.py` and `tests/test_runtime_captions.py` (the
real sandbox),
`tests/test_official_luc_timeline_editor.py`, `tests/test_official_luc_media.py`.
