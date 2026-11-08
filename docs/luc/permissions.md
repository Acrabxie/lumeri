# Permissions: what a Luc may declare

Decided 2026-10-06 (Acrab): a Luc that is not Lumeri's own must be able to load
and register its own Panel in the editor. It may do what an Official Luc may,
except touch what is Lumeri's own: the creator's account, the gateway and the
cloud services. One list says what a Luc can ask for, and a Luc declares what it
wants in its manifest, the way an Android app names its permissions, opened
wider.

One thing more is open, behind Lumeri's review (same decision): a Luc may take
the place of Lumeri's agent. Any author may make such a Luc; it is used on a
machine only while Lumeri's signed review of that exact file is beside it
([agent.md](agent.md)).

This page is that list, written for the person making a Luc. The same list is
available as JSON, built from the tables the Host enforces
(`lumeri_runtime/luc_permissions.py`):

```console
python scripts/luc_cli.py permissions                       # everything a Luc may declare
python scripts/luc_cli.py permissions path/to/luc           # what one Luc declares, and whether it would load
python scripts/luc_cli.py --session <id> permissions        # what each Luc on a running Runtime holds
```

A running Runtime answers the last one at
`GET /sessions/{session_id}/luc/permissions` (owner-checked like every other
Session route). The answer has `lucs` (each Luc's origin, what it asked for and
what it holds, the layers that lie on it, and Lumeri's review of it when it has
one), `off` (switched off), `not_loaded` (a Luc or a layer that was refused, and
why), `layer_notices` (a Luc that loaded without the layers of its activation
profile, and why), `agent` (who answers the creator: Lumeri's own agent, or the
one reviewed Luc standing in) and `catalogue` (the list on this page).

## How a Luc declares

Four fields of `host` in `manifest.json`:

```json
"host": {
  "protocols": ["lumeri.panel/v2"],
  "permissions": ["project.read", "project.write", "ui.mount"],
  "apis": {"required": ["project.view", "timeline.edit"], "optional": []},
  "editable": {"target_types": ["timeline_clip"], "properties": ["clip.placement"], "areas": ["timeline"]}
}
```

- `protocols`: what kind of thing the Luc is (a Panel, a Skill, a Workflow, a
  point library, an interaction, a workspace, a stand-in agent).
- `permissions`: the broad things it asks of the Host.
- `apis`: the exact Host calls it makes. Each call belongs to one permission,
  and the Luc must name both.
- `editable`: what in a Project it may change. `"none"` means it changes
  nothing.

The Host grants exactly what is declared and refuses everything else. A Luc
that names a call without its permission, or writes outside its `editable`
boundary, is refused; a Luc that declares more than it uses simply holds more.
Nothing is granted by who made the Luc: an Official Luc goes through the same
checks with the same list.

There is no install dialog. Putting the file in the folder is the person's
consent, and what each Luc holds can be read at any time with the commands
above. Four permissions are not the person's alone to give: `agent.replace`,
`agent.loop`, `engine.program` and `search.program` also need Lumeri's review
(below).

## Putting a Luc on a machine

A Luc is one `.luc` file in `~/.lumeri/lucs`, the folder beside the config
file.

```console
python scripts/luc_cli.py pack examples/luc-panel/clip-nudge clip-nudge.luc
cp clip-nudge.luc ~/.lumeri/lucs/
```

- The folder is the whole list. A file put there loads the next time Lumeri
  starts; a file taken away is gone the next time it starts.
- Each Luc gets a line in the `[luc]` table of `~/.lumeri/config.toml`
  (`"com.example.clip-nudge" = true`). Set it to `false` to switch the Luc off
  without removing the file. A written line wins.
- Of several versions of one Luc in the folder, the newest loads.
- A Luc that changes needs a new version number. The same id and version with
  different bytes is refused (`E_LUC_REGISTRY_IMMUTABLE`).
- At most 64 files, each at most 32 MiB, each an ordinary file (not a link).
- Lucs from the folder load after the whole Official set, and not at all in
  safe mode.
- The folder is read wherever the config file is: named by the launcher in
  `LUMERI_LOCAL_CONFIG_PATH`, or `~/.lumeri/config.toml` when the Runtime
  serves a signed-in person. An isolated rig that reads no config reads no
  folder.
- Whatever is in the folder, Lumeri starts. A file that is refused is named in
  `not_loaded` with its code, and the rest load.

`examples/luc-panel/clip-nudge` is a complete Luc of this kind: one Panel that
lists the clips on the timeline and moves one a second earlier or later.

## A layer on a Luc

Decided 2026-10-11 (Acrab): there are three levels and each word means one
thing. Lumeri carries Lucs. A Luc is one package: a Panel, a Skill, a tool. An
Extension is a layer on exactly one Luc. It is never a name for the Luc
itself.

A layer is how a person makes a Luc their own without making another Luc:
reword a Skill, restyle a Panel, give it another title. It is a `.luc` file
that names one Luc in `extends` and says what to change in it
([distribution.md](distribution.md) is the file format).

```console
python scripts/luc_cli.py extension create clip-nudge.luc com.example.nudge-title 1.0.0 nudge-title/
python scripts/luc_cli.py pack nudge-title nudge-title.luc
cp nudge-title.luc ~/.lumeri/lucs/
```

`extension create` makes a layer that changes nothing yet. Its changes are the
three lists in its `manifest.json`: values of the Luc's manifest, whole files,
and passages inside a text file. `manifest_patch` and `source_patch` in
`lumeri_runtime.luc_distribution` write one entry against the Luc as it is.

- A layer goes in the same folder as a Luc, counts among its 64 files, and is
  read at the same moment. It lies on the Luc it names: one from the folder, or
  one of Lumeri's own.
- It has an id of its own and its own line in the `[luc]` table. Set the line
  to `false` and the Luc is as its author wrote it. Of several versions of one
  layer the newest lies, and a layer that changes needs a new version number,
  as for a Luc. Its id may not begin with `lumeri.` or be the id of a Luc.
- It changes what its Luc is, never what it may do. A layer may reword,
  restyle, replace a file, and take authority away. One that would give its
  Luc another permission, another Host call or a wider edit boundary is left
  off (`E_LUC_REAUTH_REQUIRED`). More authority comes from the Luc's author,
  in a new version of the Luc. On one of Lumeri's own Lucs it can also come
  from the creator, who approves a layer in that Luc's activation profile
  (`official-channel.md`, not in this repository). A layer from the folder then
  lies on the Luc as the creator approved it and is held to that.
- Of Lumeri's own Lucs it lies only on those made of what any Luc may bring
  (the open table under Protocols): a Panel, a Skill, a Workflow, a library, an
  interaction. It never reaches the editor page, a tool whose code runs inside
  the Runtime or a tool built into it (`E_LUC_PROTOCOL_RESERVED`). On
  Lumeri's library Luc it may bring the library's catalog and verification,
  and nothing else ([library-layers.md](library-layers.md)). It never
  lies on a Luc Lumeri reviewed, because the review is of that file as it is
  (`E_LUC_REVIEW_MISMATCH`).
- The Luc a layer leaves is held to everything on this page, as if it had come
  from the folder like that: the names of Lumeri's other Lucs stay taken, and
  what it tells the agent is English. The capability names of the other Lucs
  that load from the folder stay theirs too (`E_LUC_NAMESPACE`): a layer never
  leaves another Luc without its name.
- It is made for one version of its Luc. When the Luc changes, the layer waits
  until it is made again on the new one (`E_LUC_REBASE_REQUIRED`).
- Several layers on one Luc are laid in the order of their ids. When two change
  the same thing in different ways, the later one is left off
  (`E_LUC_LAYER_CONFLICT`); two that say the same thing do not disagree.
- A layer that is left off goes alone. Its Luc loads as it would without it,
  and so do the other layers on it. A layer whose Luc is not there has nothing
  to lie on (`E_LUC_PARENT_REQUIRED`).
- No layer is laid in safe mode.

`lucs` in the Runtime's answer names the layers on each Luc under `layers`, in
the order they were laid; everything else in that row is the Luc as they leave
it. A layer that was left off is in `not_loaded` with its file, its code and
the Luc it is for (`extends`).

One of Lumeri's own Lucs can also carry layers that were activated in the Local
Registry profile named after it (`official-channel.md`, not in this repository).
Those are laid first and the folder's on top. When they are held back the Luc
loads without them and says so in `layer_notices`.

## Permissions

| Permission | Who may hold it | What it allows |
| --- | --- | --- |
| `ui.mount` | any Luc | Show its own Panel or interaction in the editor, keep a little state for it, and hand what it shows to the creator's next message. |
| `project.read` | any Luc | Read the open Project (timeline, storyboard, files, library, notes) and the creator's work in flight across their Projects. |
| `project.write` | any Luc | Change the open Project, only inside the editable boundary the Luc declares. |
| `session.control` | any Luc | Stop work the creator started. |
| `agent.replace` | reviewed Luc | Stand in for Lumeri's agent: the Luc's instructions take the place of Lumeri's own in the creator's turns. The loop, the tools, the model and the budget stay Lumeri's. |
| `agent.loop` | reviewed Luc | Decide how the creator's turns run: the Luc's own program, fenced in a sandbox with no network and no files, chooses each next step of Lumeri's agent loop, or answers for one part of the turn (fitting the request, wording guidance, keeping the conversation, its own tools). The model, the stored conversation and the budget stay Lumeri's. |
| `engine.program` | reviewed Luc | Stand in for one of Lumeri's engines: the Luc's own program measures media, grabs thumbnails, encodes, renders or transcribes in place of FFprobe, FFmpeg, lumeri-engine or whisper, run as that engine runs, in its sandbox, writing only its scratch folder and never the Project. Lumeri checks what it leaves and runs its own engine again when it breaks. |
| `search.program` | reviewed Luc | Answer the library search: the Luc's own program, fenced in a sandbox with no network and no files, reads the question and each asset's name and notes and says which assets match, in place of Lumeri's own search. Lumeri checks each answer and searches itself when it breaks. |
| `agent.policy` | reviewed Luc | Tune Lumeri's agent policy within fixed limits: the Luc's policy data sets the history window, identical-call stop, guidance preamble and hidden capability ids. Lumeri checks every field and keeps its own policy when it breaks. |
| `account.access` | Lumeri only | Read or change the creator's Lumeri account, sign-in or plan. |
| `gateway.access` | Lumeri only | Call models or paid services through Lumeri's gateway. |
| `cloud.access` | Lumeri only | Reach Lumeri's cloud services. |

The three marked "Lumeri only" are reserved. No Host API leads to them: the
account, the gateway and the cloud are reached by the Runtime itself, not by
any Luc. The names exist so that a Luc asking for one is refused by name
(`E_LUC_PERMISSION_RESERVED`) and not as a spelling mistake
(`E_LUC_PERMISSION`).

The five marked "reviewed Luc" are open to every author and granted to a file
only by Lumeri's review of it. No Host API leads to them either: what each
opens is a protocol, `lumeri.agent/v1`, `lumeri.agent.policy/v1`, `lumeri.agent.loop/v1` with
`lumeri.agent.part/v1`, `lumeri.engine.program/v1` and
`lumeri.search.program/v1`. An agent loop is a program, not words: it runs as a
child process in the operating system's sandbox, with no network, no files and
no environment, and only on a machine that has one (macOS today); it decides
each next step of Lumeri's agent loop, and the Runtime performs it
([agent-loop.md](agent-loop.md)). An agent part is the same kind of program,
answering for one part of the turn ([agent-parts.md](agent-parts.md)). An
engine program stands in for one of Lumeri's engines (FFprobe, FFmpeg,
`lumeri-engine` or whisper) for one module: it runs as that engine runs, in that engine's
sandbox, writing only its scratch folder, and Lumeri's own engine runs again
when it breaks ([engine-programs.md](engine-programs.md)). A search program
answers the library search in the agent loop's sandbox, and Lumeri's own
search answers again when it breaks ([search-programs.md](search-programs.md)).

## A permission that needs Lumeri's review

Who answers the creator, and how a turn runs, is not for a Luc to decide by
declaring it, nor is which program does the engines' work or finds the
footage. A Luc that asks for `agent.replace`, `agent.policy`, `agent.loop`, `engine.program`
or `search.program` loads only while a receipt is beside it:

```text
~/.lumeri/lucs/documentary-editor.luc
~/.lumeri/lucs/documentary-editor.luc.review
```

- The receipt is a small JSON file signed by Lumeri with the key that signs
  the Official set. It names one file by the hash of its bytes, the
  permissions the review covers and the day the review ends (at most 366 days
  after it was made).
- It covers that file and no other. A changed file, a new version, or another
  Luc needs a review of its own (`E_LUC_REVIEW_MISMATCH`).
- The Runtime checks it at every start. Without a receipt the Luc is refused
  by name (`E_LUC_REVIEW_REQUIRED`); with one that is not Lumeri's, out of
  date or withdrawn it is refused the same way, and Lumeri's own agent answers.
- A review grants the reviewed permissions it names (`agent.replace`,
  `agent.policy`, `agent.loop`, `engine.program`, `search.program`) and nothing else. A reviewed Luc is still refused the three
  reserved permissions and every reserved protocol.
- A receipt beside a Luc that asks for nothing reviewed changes nothing.
- Lumeri can withdraw a review before its day; the list of withdrawn files
  reaches a machine with its next Runtime (`E_LUC_REVIEW_WITHDRAWN`).

`python scripts/luc_cli.py permissions my-agent.luc` says whether a packed
file has a review that holds (`needs_review`, `review`, `refusal`).

## Host APIs

Every call a Luc can make, the permission it belongs to and which kind of Luc
can make it. "Panel" is a bridge call of a `lumeri.panel/v2` Luc; "interaction"
is used by a `lumeri.interaction/v1` Luc; "tool code" runs only from a tool's
own code, which is Lumeri's for now (see Protocols).

| API | Permission | Who can call it | What it does |
| --- | --- | --- | --- |
| `project.view` | `project.read` | Panel | Read one whole Project view: the timeline or the storyboard. |
| `selection.view` | `project.read` | Panel (answered by the editor) | Read the selected timeline clip as the editor shows it. |
| `files.view` | `project.read` | Panel | List one folder of the Project, one level deep. |
| `files.read` | `project.read` | Panel | Read one text file of the Project. |
| `library.view` | `project.read` | Panel | List the Project library: each asset's kind, size and a summary of its notes; no bytes. |
| `library.thumbnail` | `project.read` | Panel | Get one asset's card picture. |
| `library.notes` | `project.read` | Panel | Read the notes kept about one asset, and its roughcut. |
| `library.job` | `project.read` | Panel | Read where one roughcut job stands. |
| `tasks.view` | `project.read` | Panel | List the creator's work in flight across their Projects: running turns and the jobs they opened. |
| `tasks.stop` | `session.control` | Panel | Ask one running job to stop. |
| `timeline.edit` | `project.write` | Panel | Apply one timeline operation (insert, move, trim, split, effects, transition, track, undo, redo) through the Worker's own edit path. |
| `library.place` | `project.write` | Panel | Put one asset on the timeline, after the last clip of a track that takes it. |
| `library.roughcut` | `project.write` | Panel | Start a roughcut of one video or sound: a transcript, pauses and filler, a score; written as notes. |
| `library.roughcut.review` | `project.write` | Panel | Record one decision about a roughcut. |
| `panel.state` | `ui.mount` | Panel | Keep up to 16 KiB of state for one Panel, per creator. |
| `skills.view` | `ui.mount` | Panel | List the agent's Skills and whether each is ready; never their instructions. |
| `workspace.context` | `ui.mount` | Panel (answered by the editor) | Put one thing the Panel shows into the creator's next message. |
| `workspace.open` | `ui.mount` | Panel (answered by the editor) | Ask the editor to open one of the creator's Sessions. |
| `interaction.mount` | `ui.mount` | interaction | Open an interaction for the creator on a Host surface. |
| `interaction.update` | `ui.mount` | interaction | Update an open interaction with the creator's latest value. |
| `project.read` | `project.read` | interaction | Read the Project at a named revision. |
| `canvas.inspect` | `project.read` | interaction | Read one canvas target (a text layer) and the revision it is at. |
| `canvas.preview` | `project.read` | interaction | Show an unconfirmed change on the canvas; nothing is saved. |
| `canvas.transform.apply` | `project.write` | interaction | Apply a position or scale change to a text layer after the creator confirms it. |
| `engine.ask` | `project.read` | tool code | Put one read-only question to the engine in its sandbox. |
| `engine.job` | `project.write` | tool code | Start one sandboxed engine run over one asset; it leaves notes or a new asset. |
| `document.edit` | `project.write` | tool code | Apply one operation to a Project document a packaged tool defines. |
| `notes.edit` | `project.write` | tool code | Write one note beside one asset. |

None of these calls leads to the account, the gateway or the cloud.

## Protocols

What a Luc from the folder may bring:

| Protocol | What it is |
| --- | --- |
| `lumeri.panel/v2` | A Panel: its own region of the editor, drawn with its own HTML, CSS and JavaScript in a sandboxed frame whose only way out is the bridge calls it declares. |
| `lumeri.skill/v2` | A Skill: instructions the agent reads before a kind of task. It carries no authority. |
| `lumeri.workflow/v1` | A Workflow: the stages of a whole job in order, and the Skills each stage reads. The agent reads it like a Skill. It carries no authority. |
| `lumeri.library/v1` | A point library: a catalog of recipes, each the capabilities to call and the arguments that make a look. Reading it changes nothing; the capabilities keep their own authority. |
| `lumeri.interaction/v1` | An interaction: the agent asks the creator for a value on a surface the Host draws. |
| `lumeri.workspace/v1` | A workspace: which modules the editor shows and in what order. Lumeri's own workspace wins while it is on. |
| `lumeri.agent/v1` | A stand-in agent: instructions that take the place of Lumeri's own agent prompt. Any Luc may bring one; it is used only while the Luc holds agent.replace, which Lumeri's review grants. |
| `lumeri.agent.policy/v1` | An agent policy: policy data that tunes the history window, identical-call stop, guidance preamble and hidden capability ids within fixed bounds. Any Luc may bring one; it is used only while the Luc holds agent.policy, which Lumeri's review grants. |
| `lumeri.agent.loop/v1` | An agent loop: a program that decides each next step of Lumeri's agent loop, run in a sandbox with no network and no files. Any Luc may bring one; it runs only while the Luc holds agent.loop, which Lumeri's review grants. |
| `lumeri.agent.part/v1` | An agent part: a program that answers for one part of Lumeri's agent turn (fitting the request, wording guidance, keeping the conversation, its own tools), run in the same sandbox. Any Luc may bring one; it runs only while the Luc holds agent.loop. |
| `lumeri.engine.program/v1` | An engine program: a program that stands in for one of Lumeri's engines (FFprobe, FFmpeg, lumeri-engine or whisper) for one module, run as that engine runs, in its sandbox, writing only its scratch folder. Any Luc may bring one; it runs only while the Luc holds engine.program, which Lumeri's review grants. |
| `lumeri.search.program/v1` | A search program: a program that answers the library search in place of Lumeri's own, run in the agent loop's sandbox. Any Luc may bring one; it runs only while the Luc holds search.program, which Lumeri's review grants. |

What is Lumeri's own (`E_LUC_PROTOCOL_RESERVED`), and why:

| Protocol | Why |
| --- | --- |
| `lumeri.workspace/v2` | The editor page itself. It holds the App's bridges and the creator's session. |
| `lumeri.tool/v2` | An agent tool whose code runs inside the Runtime, the process that holds the creator's sign-in. That code is trusted by Lumeri's signature, not fenced by a sandbox. |
| `lumeri.tool/v1` | A tool built into the Runtime, named by Lumeri's own packages. |
| `lumeri.skill/v1` | A prompt built into the Runtime, named by Lumeri's own packages. |
| `lumeri.panel/v1` | A Panel the Runtime's embedding App draws, named by Lumeri's own packages. |
| `lumeri-point-library/v1` | A Point library built into the Runtime, named by Lumeri's own packages. |
| `lumeri.custom-page/v1` | A page defined by data. It is still switched off in Lumeri's own set and not opened yet. |
| `lumeri.custom-backend/v1` | The backends a page defined by data calls. Not opened yet, like those pages. |
| `lumeri.module/v1` | A module declaration built into the Runtime, named by Lumeri's own packages. |

A protocol in neither table is also refused.

## A Panel of its own

A Panel Luc needs no workspace's leave. Every loaded Panel that the workspace
in force does not place registers itself: it is added to the editor's module
list after the workspace's own modules, under the name
`luc-<its capability id>-<8 hex>`, shown once when the editor first meets it.
After that the creator's own layout decides, as for every module.

- The tab and heading are the Panel's `title`, which needs English and may
  carry the editor's other nine languages.
- The page runs in a sandboxed frame with no network (`connect-src 'none'`),
  no access to the editor page and no storage of its own. Its only way out is
  `lumeri.call(api, payload)` for the bridge calls it declared, and
  `lumeri.on(event, handler)` for the events it declared.
- The editor holds 32 modules. A Panel past that is listed under `uncontained`
  with `E_LUC_PANEL_LIMIT` and not shown.
- A workspace still places the Panels it names, by Luc id and capability id,
  and they are not listed twice.

Events a Panel may subscribe to:

| Event | When |
| --- | --- |
| `project.changed` | The open Project changed. |
| `locale.changed` | The creator changed the editor's language. |
| `theme.changed` | The editor's theme changed. |
| `panel.visibility` | The Panel was shown or hidden. |
| `panel.resized` | The Panel's region changed size. |
| `tasks.changed` | The creator's work in flight changed. |
| `selection.changed` | The selected timeline clip changed. |

Bridge calls, their inputs and the frame's rules are in
[workspace-panel-skill.md](workspace-panel-skill.md).

## The editable boundary

A Luc that holds `project.write` says what it may change: `target_types`,
`properties` and `areas`. The boundary must lie inside what its write calls
can reach, and each write is checked against it before the Worker sees it
(`E_LUC_EDIT_BOUNDARY`).

What each write call can reach:

| Call | Target types | Properties | Area |
| --- | --- | --- | --- |
| timeline.edit | `timeline_clip`, `timeline_track` | the seven timeline properties below | `timeline` |
| library.place | `timeline_clip` | clip placement and presence | `timeline` |
| library.roughcut, library.roughcut.review, notes.edit | `library_asset` | asset notes | `library` |
| engine.job | `library_asset` | asset notes and presence | `library` |
| document.edit | `project_document` | document content | `project` |
| canvas.transform.apply | `lumenframe_text_layer` | transform position and scale | `canvas` |

The properties a boundary may name:

| Property | What changes |
| --- | --- |
| `clip.placement` | Where a clip sits: its track and start. |
| `clip.presence` | Whether a clip exists: adding, deleting, splitting. |
| `clip.source_range` | Which part of the source a clip plays. |
| `clip.effects` | A clip's effects. |
| `clip.transition` | The transition into a clip. |
| `track.presence` | Adding a track or changing its settings. |
| `timeline.history` | The creator-visible undo and redo. |
| `asset.notes` | The notes kept beside a library asset. |
| `asset.presence` | A new asset made from an existing one. |
| `document.content` | One Project document a packaged tool defines. |
| `transform.position` | A text layer's position on the canvas. |
| `transform.scale` | A text layer's scale on the canvas. |

What each `timeline.edit` operation changes. A Panel may send an operation
only when its boundary covers all of it; an operation not listed (a marker, the
timeline format) is outside every boundary.

| Operation | Target | Properties it changes |
| --- | --- | --- |
| `insert_clip` | `timeline_clip` | clip.placement, clip.presence |
| `delete_clip` | `timeline_clip` | clip.placement, clip.presence |
| `move_clip` | `timeline_clip` | clip.placement |
| `trim_clip` | `timeline_clip` | clip.placement, clip.source_range |
| `trim_head` | `timeline_clip` | clip.placement, clip.source_range |
| `split_clip` | `timeline_clip` | clip.presence, clip.source_range |
| `set_clip_time` | `timeline_clip` | clip.placement, clip.source_range |
| `set_clip_effects` | `timeline_clip` | clip.effects |
| `add_transition` | `timeline_clip` | clip.transition |
| `add_track` | `timeline_track` | track.presence |
| `set_track` | `timeline_track` | track.presence |
| `undo` | `timeline_clip` | timeline.history |
| `redo` | `timeline_clip` | timeline.history |

What no Luc edits, whatever it declares (`E_LUC_PROTECTED_STATE`):

| Property | What it is |
| --- | --- |
| `project.identity` | Which Project this is. |
| `project.cloud_identity` | Its identity in Lumeri's cloud. |
| `account.state` | The creator's account. |
| `session.credentials` | The creator's sign-in. |
| `runtime.configuration` | The Runtime's own settings. |
| `history.internals` | The stored history behind undo and redo. |

## What is Lumeri's own

A Luc from the folder is refused, and Lumeri starts without it, when it:

- names `account.access`, `gateway.access` or `cloud.access`;
- names `agent.replace` without Lumeri's review of that exact file beside it;
- brings a protocol that is not in the open table: the editor page, or code
  that runs inside the Runtime;
- has an id that begins with `lumeri.`, or the id of an Official Luc;
- names a capability that an Official Luc has, whether that Luc is switched on
  or off.

A layer from the folder is left off when the Luc it would leave is any of
these, and when its own id is.

Everything else an Official Luc may do, it may do. An author with a non-English
name is shown to the person as written; the agent is told only the Luc's id,
because what the agent reads of a Luc is English
([agent-protocol.md](agent-protocol.md)). The same rule decides what of the Luc
reaches the agent: a capability or Skill whose agent-facing text is not English
loads and is withheld from the agent (`E_LUC_LANGUAGE`).

A Skill from the folder cannot take the name of one of Lumeri's Skills: Lumeri's
are counted first, and the second Skill of a name is not ready
(`E_LUC_SKILL_NAME`). Workflows share those names, since the agent reads both
the same way. A Skill, a Workflow or a library that names a tool or capability
the agent does not have loads and is not offered until it does
([guides.md](guides.md)).

## Refusals

When a file in the folder does not load:

| Code | Why |
| --- | --- |
| `E_LUC_LOCAL_FILE` | Not an ordinary file of at most 32 MiB, or it (or the folder) could not be read. |
| `E_LUC_LOCAL_LIMIT` | The folder holds more than 64 `.luc` files. |
| `E_LUC_ARCHIVE` | The file is not a `.luc` archive. |
| `E_LUC_ARTIFACT` | The file is not a Luc. |
| `E_LUC_NAMESPACE` | Its id or a capability name is Lumeri's own. |
| `E_LUC_COMPATIBILITY` | It was made for another Runtime version. |
| `E_LUC_PLATFORM` | It was made for another Runtime platform. |
| `E_LUC_PROTOCOL_RESERVED` | It brings a protocol that is Lumeri's own. |
| `E_LUC_PERMISSION_RESERVED` | It asks for a reserved permission. |
| `E_LUC_REVIEW_REQUIRED` | It asks for a permission that needs Lumeri's review and has no review beside it, or the review does not cover that permission. |
| `E_LUC_REVIEW_INVALID` | The review beside it is not one Lumeri signed, or could not be read. |
| `E_LUC_REVIEW_MISMATCH` | The review is for another Luc or for other bytes of this one. |
| `E_LUC_REVIEW_EXPIRED` | The review's last day has passed. |
| `E_LUC_REVIEW_WITHDRAWN` | Lumeri withdrew its review of this file. |
| `E_LUC_AGENT_CONFLICT` | It brings more than one stand-in agent. Also shown, with no file refused, while two stand-in Lucs are switched on: Lumeri's own agent answers until only one is. |
| `E_LUC_POLICY_CONFLICT` | It brings more than one agent policy. Also shown, with no file refused, while two agent policy Lucs are switched on: Lumeri's own policy governs until only one is. |
| `E_LUC_LOOP_CONFLICT` | It brings more than one agent loop. Also shown, with no file refused, while two agent loop Lucs are switched on: Lumeri's own loop runs until only one is. |
| `E_LUC_PART_CONFLICT` | It brings more than one program for the same part of the agent turn. Also shown, with no file refused, while two Lucs bring one for the same part: Lumeri's own code answers for it until only one is. |
| `E_LUC_ENGINE_CONFLICT` | It brings more than one program for the same engine module. Also shown, with no file refused, while two Lucs bring one for the same module: Lumeri's own engine answers for it until only one is. |
| `E_LUC_SEARCH_CONFLICT` | It brings more than one search program. Also shown, with no file refused, while two search program Lucs are switched on: Lumeri's own search answers until only one is. |
| `E_LUC_REGISTRY_IMMUTABLE` | It changed without a new version number. |
| `E_LUC_PERMISSION` | It names a permission that does not exist. |
| `E_LUC_API_REQUIRED` | It requires a Host API that does not exist or is not available. |
| `E_LUC_API_PERMISSION` | It requires a Host API without naming that API's permission. |
| `E_LUC_EDIT_SCOPE` | It asks to write without a boundary, or its boundary goes past what its write calls reach. |
| `E_LUC_PROTECTED_STATE` | Its boundary names something no Luc edits. |
| `E_LUC_ADAPTER_AUTHORITY` | A capability uses more than the Luc declared (a Panel bridge call that is not among its `apis`), or the Luc holds more than its kind may (a stand-in agent with a Host API, an edit boundary or a Skill beside it). |
| `E_LUC_CAPABILITY_INVALID` | A capability is not what its protocol asks for: a title without English, an event the Host does not offer. |
| `E_LUC_ADAPTER_RUNTIME` | This Runtime cannot carry out a protocol it brings. |
| `E_LUC_PRODUCT` | Its workspace is for another product, or it brings a stand-in agent to a product that has no agent. |
| `E_LUC_LOAD` | Anything else. The Luc is left out and Lumeri starts. |

When a layer in the folder does not lie on its Luc. A layer is also left off
with a code from the table above when that is what is wrong with it, or with
the Luc it would leave:

| Code | Why |
| --- | --- |
| `E_LUC_PARENT_REQUIRED` | The Luc it names is not on this machine, is switched off, or did not load; or what it names is another layer. |
| `E_LUC_REBASE_REQUIRED` | It was made for another version of its Luc. |
| `E_LUC_REAUTH_REQUIRED` | It would give its Luc a permission, a Host call or an edit boundary the Luc does not hold. |
| `E_LUC_LAYER_CONFLICT` | It and a layer laid before it change the same thing in different ways. |
| `E_LUC_LAYER_INVALID` | One of its changes does not fit the Luc it names; `detail` says which check failed. |

After it loaded:

| Code | Why |
| --- | --- |
| `E_LUC_API_UNDECLARED` | The Panel made a bridge call it did not declare (HTTP 403). |
| `E_LUC_EDIT_BOUNDARY` | The Panel sent a write outside its boundary (HTTP 403). |
| `E_LUC_PANEL_LIMIT` | The editor already holds 32 modules; the Panel is not shown. |
| `E_LUC_SKILL_NAME` | Another Skill or Workflow already has this name. |
| `E_LUC_SKILL_USES` | The Skill names a tool or capability the agent does not have now. |
| `E_LUC_WORKFLOW_USES` | The Workflow names a tool or capability the agent does not have now. |
| `E_LUC_WORKFLOW_SKILL` | A stage of the Workflow names a Skill that is not ready. |
| `E_LUC_LIBRARY_USES` | A recipe of the library calls a capability the agent does not have now. |
| `E_LUC_LANGUAGE` | Its text for the agent is not English; the agent is not offered it. A stand-in agent whose instructions are not English does not load at all. |

## What this does not cover

- **Code of a Luc's own that runs as an agent tool.** `lumeri.tool/v2` code
  runs inside the Runtime process, beside the creator's sign-in and the
  gateway. It is trusted by Lumeri's signature and not fenced by a sandbox, so
  it stays Lumeri's own. A layer on one of Lumeri's tools may bring a program
  that answers for it instead: the program runs in the code sandbox and
  reaches the Project only through that tool's own calls
  ([tool-programs.md](tool-programs.md)). A Luc from the folder reaches the
  agent with a Skill, a Workflow, a library or an interaction, and the Project
  with a Panel.
- **An agent with code of its own.** A stand-in agent brings instructions.
  It does not bring a loop, tools or a model; those stay Lumeri's.
- **Asking for a review.** There is no page where an author submits a Luc.
  Lumeri reads the file and signs the receipt by hand
  (`POST /v1/luc/official/reviews`, publisher accounts only).
- **Asking the person at install time.** There is no dialog that lists what a
  Luc asks for before it loads.
- **An editor with no workspace.** If the person switches Lumeri's workspace
  off and no other workspace loads, the editor keeps its built-in layout and
  shows no Panel at all, including self-registered ones.
- **Which product a Panel is for.** A workspace names its product. Nothing else
  does (a Panel, a Skill, a Workflow, a library, an interaction), so one from
  the folder loads in every product's Runtime. Only the Video editor mounts
  Panels.
- **A Panel moving its own frame.** A Panel cannot reach the network or the
  editor page, but it can point its frame at another address of the Runtime
  itself. The editor notices and marks the Panel failed
  (`E_LUC_PANEL_NAVIGATED`). Closing this is left for the security pass.
- **Getting Lucs from somewhere.** The folder is filled by hand, with Lucs and
  with layers. A store, or installing from Luc Cloud into it, is not part of
  this.
- **Making a layer in the editor.** A layer is made with the command above and
  switched in the config file. The editor has no place that shows the layers
  on a Luc, makes one or switches one.
- **Approving a layer from the folder that asks for more.** There is no such
  approval: the layer is left off. A creator approves more only at the
  command line, for a layer in the activation profile of one of Lumeri's own
  Lucs (`activation plan`, then `activation select
  --authorized-activation-hash`), and the editor has no place to do it.
