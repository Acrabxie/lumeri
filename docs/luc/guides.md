# Skills, Workflows and point libraries are Lucs

Lumeri 1.0 had three kinds of thing beside the Luc, each with a file format
and a cloud of its own: the Skill (`.lus`), the Workflow (a Skill Cloud
entry), and the point library (`.lup`). In 1.1 each of them is a Luc. There is
one package (`.luc`), one folder on a machine (`~/.lumeri/lucs`), one switch
per Luc (`[luc]` in `~/.lumeri/config.toml`), one Official set, and one cloud
(Luc Cloud).

| What it was | What it is now | Protocol | How the agent meets it |
| --- | --- | --- | --- |
| Skill (`.lus`) | a Skill Luc | `lumeri.skill/v2` | `use_skill` |
| Workflow (Skill Cloud `workflow`) | a Workflow Luc | `lumeri.workflow/v1` | `use_skill` |
| Point library (`.lup`) | a library Luc | `lumeri.library/v1` | `luc_discover`, `luc_describe`, `luc_invoke` |

All three are data: text and JSON the package carries. None brings code,
calls a Host API, asks a permission or edits a Project, so any Luc may bring
them ([permissions.md](permissions.md)). What they name (tools, capabilities,
Skills) keeps its own authority. A Skill's fields are in
[workspace-panel-skill.md](workspace-panel-skill.md); the other two are here.

What the agent reads of any of them is English
([agent-protocol.md](agent-protocol.md)). A `title` is for the creator and
may carry the editor's ten languages.

## Workflow (`lumeri.workflow/v1`)

A Workflow is the order of a whole job: stages, each with short instructions
of its own and the Skills to read for it. A Skill says how to do one kind of
task; a Workflow says which tasks, in what order, and when each is done.

Capability fields:

- `surface`: `agent-workflow`.
- `name`: a lowercase handle. Skills and Workflows share one set of names,
  because the agent reads both with `use_skill`.
- `description`: when to use it, at most 300 characters.
- `stages`: 2 to 12, in order. Each is
  - `title`: at most 80 characters;
  - `instructions`: a `.md` file of the package;
  - `skills` (optional): up to 4 Skill names to read at this stage.
- `guidance` (optional): a `.md` file of the package on running the job as a
  whole: what may be skipped, where to pick up later, when to stop and ask,
  what counts as done.
- `uses` (optional): up to 32 tool names or capability ids the stages rely on
  beyond what their Skills name.
- `title` (optional): what the creator reads, a locale map with English, at
  most 40 characters.

The guidance and all stage instructions together are at most 4800 characters,
and the whole Workflow must fit one tool result. A Workflow that does not is
refused when it loads (`E_LUC_CAPABILITY_INVALID`); it is never read cut.

`use_skill` lists ready Workflows after the Skills, under "Workflows:".
Reading one answers the guidance and every stage at once:

```json
{"workflow": "product-promo",
 "guidance": "The creator may skip or reorder stages ...",
 "stages": [
  {"stage": 1, "title": "Understand the goal and the footage", "instructions": "...",
   "skills": ["ground-creative-brief", "inventory-source-media"]},
  {"stage": 2, "title": "Plan the story and the look", "instructions": "...",
   "skills": ["design-promo-structure", "craft-promo-visual-system"]}
]}
```

What is read is re-checked against the hash taken at load
(`E_LUC_WORKFLOW_CHANGED`).

A Workflow is ready while all of this holds; otherwise `luc_discover` lists it
under `unavailable` and the Skills Panel shows why:

| Code | What it lacks |
| --- | --- |
| `E_LUC_LANGUAGE` | Its name, description, guidance or a stage is not English |
| `E_LUC_WORKFLOW_USES` | Something in `uses` is not there for the agent (`uses` lists it) |
| `E_LUC_WORKFLOW_SKILL` | A stage names a Skill that is not ready (`skills` lists them) |
| `E_LUC_SKILL_NAME` | A Skill or Workflow that comes first has this name |
| `E_LUC_SKILL_LIMIT` | 24 Skills and Workflows are ready already |

Order: Lumeri's own come before every other Luc's, and among each, Skills
before Workflows. So no other Luc can take a name from Lumeri's, and a
Workflow of Lumeri's own never waits on a Skill that is not Lumeri's. A
Workflow from the folder may name Lumeri's Skills or another Luc's.

The editor reads the same rows through `skills.view`. A Workflow's row has
`"kind": "workflow"` and its stage titles (`stages`), never the instructions.

## Point library (`lumeri.library/v1`)

A point library holds named, tested answers to one kind of creative request: a
look, a move, a title style. Each entry is a recipe: the capabilities to call
and the arguments that make it. The library is a catalog. It performs nothing.

Capability fields:

- `surface`: `point-library`.
- `description`: what the library is for, at most 300 characters.
- `catalog`: a `.json` file of the package, at most 256 KiB.
- `uses`: 1 to 32 tool names or capability ids. Every step of every recipe
  calls one of them.
- `guidance` (optional): a `.md` file, at most 2400 characters: how to choose
  among the entries.
- `title` (optional): what the creator reads.

The catalog is `{"entries": [...]}`, 1 to 128 entries with distinct ids. Each
entry has exactly these fields:

- `id`: kebab-case, at most 64 characters.
- `title`: at most 80 characters.
- `description`: what it is for, at most 300 characters.
- `steps`: 1 to 8, in order. Each is `capability` (one of `uses`), `arguments`
  (an object: the part of the call that makes the look) and optionally `note`
  (at most 200 characters: what the agent still has to add).
- `tags` (optional): up to 8 short words that help a query find it.
- `notes` (optional): at most 600 characters: what to check afterwards.

Every entry must fit one tool result, and so must the guidance beside any one
catalog row; otherwise the library is refused when it loads.

The Host owns how a library is read. Its input is always

```json
{"op": "catalog", "query": "warm film"}
{"op": "entry", "entry": "warm-film"}
```

`catalog` answers the guidance and each entry's `id`, `title` and
`description`. A `query` (English keywords) only puts the likely entries
first. A catalog too long for one result is shortened by whole entries and
`more` counts the rest; a query brings the ones wanted into the part shown.

`entry` answers one recipe, and what to do with it:

```json
{"library": "library.looks",
 "entry": {"id": "warm-film", "title": "Warm film", "description": "...",
           "steps": [{"capability": "media.edit", "arguments": {"operation": "grade", "look": "warm_film"},
                      "note": "Add the asset to grade."}],
           "notes": "Look at skin tones afterwards."},
 "next": "Call each step's capability yourself, in order ..."}
```

Reading changes nothing. The agent then calls each step's capability itself,
through `luc_invoke` like any other call, under that capability's own
permissions, boundary and approval. A recipe is a shortcut to good arguments,
not a way around a check.

The catalog and guidance are re-checked against the hashes taken at load
(`E_LUC_LIBRARY_CHANGED`).

A library is offered to the agent while every item of `uses` is there for it
and everything the agent would read of it is English. Otherwise it stays
loaded, is withheld from the agent, and `luc_discover` lists it under
`unavailable` with `E_LUC_LIBRARY_USES` (and the missing `uses`) or
`E_LUC_LANGUAGE`. A Skill may name a library in its own `uses`; it is then
ready only while the library is.

`lumeri-point-library/v1` is the older form: a library built into the Runtime
that a Lumeri package refers to by name. Its code stays Lumeri's own. A `.lup`
bound to that code is a layer on the Luc that holds it, bringing the catalog
and verification the agent reads ([library-layers.md](library-layers.md)).
Lumeri's craft
libraries (grade, camera, composition, rhythm, cuts) answer through the tool
`craft.ask`, whose code is theirs.

## What Lumeri ships

Lumeri 1.0 shipped eight "standard Skills" and one workflow built into the
App. They are now one Official Luc, `lumeri.production-guides`
(`official_luc/production-guides`): the Skills `ground-creative-brief`,
`inventory-source-media`, `design-promo-structure`, `assemble-picture-cut`,
`craft-promo-visual-system`, `build-sound-pass`, `inspect-and-revise-cut` and
`verify-promo-delivery`, and the Workflow `product-promo` that puts them in
six stages. The names are 1.0's, so a workflow an account kept in Skill Cloud
still finds its Skills after it is converted.

The texts are not translations. Each was rewritten for the capabilities the
1.1 agent has, and says where that agent's reach ends: it reads the timeline
and the notes beside footage, and does not see frames, hear sound or render a
finished file. `tests/test_official_luc_production_guides.py` makes the calls
the Skills show against the real tools, so a Skill cannot tell the agent to
do what a tool refuses.

Lumeri's two older Skill Lucs (`timeline-editing`, `transitions`) stay as they
are. The built-in point libraries stay too, as said above.

## Bringing over what exists

`scripts/luc_cli.py convert` turns each older form into a Luc source folder,
which `luc_cli.py pack` then makes a `.luc`:

| From | To | Notes |
| --- | --- | --- |
| a `.lus` Skill | a Skill Luc | Its body becomes the instructions, `tools_used` becomes `uses` |
| a Skill Cloud `skill` entry (JSON) | a Skill Luc | Its steps or procedure become the instructions |
| a Skill Cloud `workflow` entry (JSON) | a Workflow Luc | Each stage keeps its title, what it produces and when it is done; its instructions, human gates and completion evidence become the guidance |
| a `.lup` library with an ops binding | a library Luc | Each catalog entry becomes a recipe of the bound tool |
| a `.lup` library with a native binding | a layer on Lumeri's library Luc | It brings the catalog and verification; the Luc keeps the code ([library-layers.md](library-layers.md)) |

```
luc_cli.py convert SOURCE OUTPUT [--name NAME] [--id ID] [--uses CAPABILITY ...]
```

`OUTPUT` is a folder that does not exist yet. The Luc is called what the older
form was called, and its id is `local.<name>`; `--name` and `--id` give
others. `--uses` replaces the 1.0 tool list with the capabilities the Skill
relies on in 1.1. The answer lists the files it made and what still waits.

The converter (`lumeri_runtime/luc_convert.py`) leaves nothing out. A field of
a 1.0 definition that has no place of its own in a Luc (inputs, outputs,
quality gates, failure policy, a revision loop) becomes a section of the text
the agent reads. A title in another language keeps its language and gets an
English one beside it, made from the name, because a Luc title needs English.

Conversion never shortens text. Something too long for its bound is refused
with the bound, so the author decides what to cut. A Skill or Workflow must
also fit a complete 6000-character JSON result, including escaped text,
stage titles and Skill names; the converter checks this before making it.
These are refused too:

- A Skill that names no tools. A Skill Luc says what it relies on (`uses`);
  convert again with `--uses`.
- A name Lumeri brings itself (`product-promo`, `transitions`). Lumeri's 1.0
  standard Skills and its workflow are already an Official Luc, and no other
  Luc may carry those names; convert under another with `--name`.
- A workflow of one stage (that is a Skill), or a stage that reads more than
  four Skills.

Three things convert and then wait, and the converter says so:

- Text that is not English. The Luc loads and is not offered to the agent
  (`E_LUC_LANGUAGE`) until its agent-facing text is rewritten in English.
- 1.0 tool names in `uses` or in a recipe (`timeline_insert_clip`). The 1.1
  agent has capabilities instead (`timeline.insert-clip`), and the converter
  names the one that replaced it when there is one of that name. The Luc loads
  and is not ready (`E_LUC_SKILL_USES`, `E_LUC_WORKFLOW_USES`,
  `E_LUC_LIBRARY_USES`) until they are renamed.
- A capability Lumeri does not bring. The Luc is ready only on a machine where
  another Luc brings it.

The 1.0 editor keeps reading `.lus`, `.lup` and Skill Cloud as before. Nothing
in 1.0 is taken away by this.

## The cloud

Luc Cloud (`cloud.md`, not in this repository) stores any `.luc`, whatever protocols it
brings, so a Skill, a Workflow and a library are uploaded, versioned, searched
by protocol and fetched like every other Luc. Skill Cloud stays for the 1.0
editor.

What an account already keeps in Skill Cloud is brought over on the server, so
the creator does not do it by hand. `lumeri_runtime.luc_cloud.skill_cloud`
converts one `skill` or `workflow` entry with the same converter, packs it and
publishes it in the account's own namespace, private like every package there.
The server's Luc wheel carries the converter, so the package it stores is byte
for byte the `.luc` that `luc_cli.py convert` and `pack` make of that entry on
a machine. The Accounts service offers it to the signed-in account for its own
entries:

```
POST /v1/luc/from-skill-cloud/{kind}/{id}/{version}[?name=NAME]
```

The answer names the package and repeats what waits. Bringing the same entry
again answers the package it already made. An entry that changed under a
version already brought over is refused (409): Luc Cloud never replaces a
version. What cannot be a Luc is refused with the converter's reason
(`E_LUC_CONVERT`, 422) and nothing is stored. When the server publishes the
Official set it knows Lumeri's own names, refuses them, and names the 1.1
capability that replaced a 1.0 tool. The Skill Cloud entry is left as it is.

A point library kept in Skill Cloud is a `.lup` bundle; the server does not
read those. Convert the file with `luc_cli.py convert` and publish the Luc.

## Releasing a set that has these

The Runtime published as runtime pack sequence 2 (184e9c1) does not know these
two protocols, and it stops if an Official package names a protocol it cannot
run. So an Official package that brings one is marked `LUC_DATA` in the index.
That Runtime, and the one before it, refuse a whole index with a state they do
not know and keep the set they have (the same way `LUC_CODE` kept the first
Runtime off sequence 2). `scripts/build_official_luc_bundle.py` refuses to
build an Official Workflow or library that is not marked.

From this Runtime on, an Official package whose protocol has no adapter is
only unavailable (`E_LUC_ADAPTER_RUNTIME`) and the rest of the set loads, so a
later protocol needs no mark of its own.

The order of a release is unchanged: the runtime pack first, then the server's
Luc wheel built from the same Runtime, then the channel
(`official-channel.md`, not in this repository).

## Not yet

- No real-model run has read a Workflow, a library or the production guides
  yet.
- The agent has no call that only reads the storyboard, the design or the
  delivery target: each is answered whole when it is changed. A Workflow
  picked up in a new conversation works from the timeline and from what the
  creator says.
- The agent has no call that renders a finished file or checks one against the
  delivery target. `verify-promo-delivery` sets the target, checks the cut
  against it and says plainly that the file itself was not made here.
- A library's recipes are checked against `uses`, not against the input schema
  of the capability they call; a wrong argument is refused when the agent
  makes the call.
- Skill Cloud's public catalog has no counterpart in Luc Cloud: sharing a Luc
  with everyone is not opened.
- Old Skills on a machine are brought over with the converter, by hand, and
  what an account keeps in Skill Cloud by one call for each entry. The App has
  no button for either.
- The server brings over an account's own Skill Cloud entries only. A public
  entry of another account is not copied into a namespace.
- The Accounts route that brings Skill Cloud entries over is written and
  tested (Accounts branch `luc/from-skill-cloud`) and is not on the server
  yet. It goes out with a release that carries the Luc wheel of this Runtime.
- Lumeri ships no `lumeri.library/v1` Luc of its own. Its craft libraries stay
  built in and answer through `craft.ask`.
