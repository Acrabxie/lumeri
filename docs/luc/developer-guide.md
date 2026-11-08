# Making a Luc

This page is the way in for a person who wants to make a Luc. It says what a
Luc is, how to write one, how to try it on your own machine and how to put it
on the Luc hub. Everything deeper is in the pages it links to.

Written 2026-10-30 against this branch (`luc/luc-finder-20261026`, 47d1a217).
Two things below are in this source but **not live yet**: the hub's platform
rules (section 3) and adding a Luc from inside the App (section 6). They are
marked where they appear.

## 1. What a Luc is

A Luc is one `.luc` file: a manifest and the files it names. Lumeri itself is
built from Lucs (its own are listed in `official_luc/REGISTRY`), and a Luc you
make loads beside them. What yours may bring:

| Protocol | What it is |
| --- | --- |
| `lumeri.panel/v2` | A Panel: its own region of the editor, with its own HTML, CSS and JavaScript in a sandboxed frame. |
| `lumeri.skill/v2` | A Skill: instructions the agent reads before a kind of task. |
| `lumeri.workflow/v1` | A Workflow: the stages of a whole job, and the Skills each stage reads. |
| `lumeri.library/v1` | A point library: recipes, each the capabilities to call and the arguments that make a look. |
| `lumeri.interaction/v1` | An interaction: the agent asks the creator for a value on a surface the Host draws. |
| `lumeri.workspace/v1` | A workspace: which modules the editor shows and in what order. |
| `lumeri.agent/v1` | A stand-in agent. It is used only with Lumeri's review (section 4). |

A Luc never touches what is Lumeri's own: the creator's account, the gateway
and the cloud services. Code that would run inside the Runtime
(`lumeri.tool/v2`) is Lumeri's own too. The full table, with the reason for
each reserved protocol, is in [permissions.md](permissions.md#protocols).

## 2. Start from an example

Copy the one closest to what you want:

| Example | What it shows |
| --- | --- |
| `examples/luc-panel/clip-nudge` | A Panel that lists the clips on the timeline and moves one a second earlier or later. The most complete example. |
| `examples/luc-agent/documentary-editor` | A stand-in agent. Loads only with Lumeri's review beside it. |

There is no example yet for a Skill, a Workflow or a point library from
outside Lumeri. Do not copy `examples/luc-cloud`: those are internal fixtures
(one uses `lumeri.skill/v1`, which is Lumeri's own and refused from the
folder). How each is written is in [workspace-panel-skill.md](workspace-panel-skill.md#skill-lumeriskillv2)
and [guides.md](guides.md).

The manifest of `clip-nudge`, cut to its shape:

```json
{
  "format": "lumeri.luc.distribution/v1",
  "kind": "luc",
  "identity": {"id": "com.example.clip-nudge", "version": "1.0.0"},
  "author": "Example Author",
  "summary": "A Panel of its own: lists the clips on the timeline and moves one a second earlier or later.",
  "readme": "README.md",
  "compatibility": {"runtime": ">=1.1.0 <2.0.0", "platforms": ["desktop"]},
  "host": {
    "protocols": ["lumeri.panel/v2"],
    "apis": {"required": ["project.view", "timeline.edit"], "optional": []},
    "permissions": ["project.read", "project.write", "ui.mount"],
    "editable": {"target_types": ["timeline_clip"], "properties": ["clip.placement"], "areas": ["timeline"]},
    "capabilities": [{"id": "panel.clip-nudge", "protocol": "lumeri.panel/v2", "...": "..."}]
  }
}
```

(The example in the tree has no `platforms` yet; it is shown here with one.)

- `identity.id` is yours, in reverse-domain form. Lumeri's own Lucs use ids
  that start with `lumeri.`.
- `identity.version` is a semantic version. A Luc that changes needs a new
  one: the same id and version with different bytes is refused
  (`E_LUC_REGISTRY_IMMUTABLE`).
- A title the creator sees is given in the editor's ten languages (`zh-CN`,
  `zh-TW`, `en`, `es`, `fr`, `de`, `ja`, `ko`, `pt`, `ar`), as `clip-nudge`
  does. What a Luc says to the agent is in English and whole
  ([agent-protocol.md](agent-protocol.md)).

## 3. Platforms

Lumeri runs on two platforms, `desktop` and `android`. A Luc says where it
runs in `compatibility.platforms`:

- a non-empty list with no repeats, of `desktop`, `android` or both;
  anything else is refused (`E_LUC_COMPATIBILITY`);
- a Runtime that is not on the list refuses the Luc when it loads
  (`E_LUC_PLATFORM`), and the rest load;
- leave it out and every Runtime loads it.

The phone has less than a computer: it has no media engine yet, so a Luc
whose required Host APIs need the engine is not installed there
(`E_LUC_API_REQUIRED`). Mark such a Luc `["desktop"]`. What the phone offers
is in the Android Luc protocol v0.1 (`docs/android-luc-protocol-v0.1.html` in
the App's repository, which is not public yet).

**Not live yet** — on the hub (section 6): the desktop App adds only Lucs for
desktop and the Android App only Lucs for Android; adding one for another
platform is refused before it is stored (`422 E_LUC_PLATFORM`), and search
shows the Lucs for the asker's platform. Whether a Luc with no `platforms`
counts for every platform or for desktop alone is still Acrab's decision; the
hub has a switch for either (`hub.md`, not in this repository). Until it is
decided, write `platforms`.

## 4. Permissions

A Luc declares what it wants, the way an Android app names its permissions,
and the Host holds it to that. In the manifest:

- `host.permissions`: what it may do (`ui.mount` to be shown at all,
  `project.read`, `project.write`, …);
- `host.apis`: the Host calls it makes, `required` or `optional`;
- `host.editable`: for a Luc that changes the project, exactly what it may
  change. `clip-nudge` may move a clip and nothing else, so a delete or a
  split from it is refused (`E_LUC_EDIT_BOUNDARY`).

To see what a Luc asks for and whether it would load:

```console
python scripts/luc_cli.py permissions                    # everything a Luc may declare
python scripts/luc_cli.py permissions clip-nudge.luc     # what this one declares
```

One permission is not yours to grant: `agent.replace`, which lets a Luc stand
in for Lumeri's agent. Anyone may make such a Luc, but it loads only while a
review signed by Lumeri for that exact file sits beside it
(`my-agent.luc.review`). The hub does not hand reviews out. The whole list,
the editable boundary and every refusal code are in
[permissions.md](permissions.md).

## 5. Try it on your machine

```console
python scripts/luc_cli.py pack examples/luc-panel/clip-nudge clip-nudge.luc
python scripts/luc_cli.py permissions clip-nudge.luc
mkdir -p ~/.lumeri/lucs && cp clip-nudge.luc ~/.lumeri/lucs/
```

Open your workspace again. Then:

- The folder `~/.lumeri/lucs`, beside the config file, is the whole list: a
  file put there loads at the next start, a file taken away is gone at the
  next start. Of several versions of one Luc, the newest loads.
- Each Luc gets a line in `~/.lumeri/config.toml`:

  ```toml
  [luc]
  "com.example.clip-nudge" = true
  ```

  Set it to `false` to switch the Luc off and keep the file.
- A Luc that is refused does not stop Lumeri. It is named with its code in the
  `not_loaded` list of `python scripts/luc_cli.py --session <id> permissions`.
- Safe mode loads Lumeri's own Lucs only, nothing from the folder.
- Limits: at most 64 files in the folder, each at most 32 MiB, each an
  ordinary file (not a link).

The rest of what `luc_cli.py` does is in `python scripts/luc_cli.py --help`.

## 6. Put it on the Luc hub

The hub is [luc.lumeri.io](https://luc.lumeri.io). Sign in, choose a handle
once (your Lucs are then at `<handle>/<package>`), and add the `.luc` file on
the upload page.

- A package is private until you make it public on its Settings tab.
- Public means: a Luc that is instructions only can be read by anyone at once;
  a Luc that carries code or asks the Host for anything is public once Lumeri
  has approved that exact version.
- Nobody downloads a file by hand. A Luc's page gives a prompt to copy and
  paste to one's agent; the agent fetches the file, checks its SHA-256 and
  puts it in `~/.lumeri/lucs`, and asks first if the Luc asks for anything.

**Not live yet** — adding from inside the App: the Runtime can send a Luc
from the `lucs` folder to the hub for the signed-in person
(`POST /runtime/v1/luc/hub/publish`), checking its declared platforms first,
but no App screen offers it yet (`hub.md`, not in this repository).

What the hub does not do yet is in `hub.md` (not in this repository).

## 7. Read next

| To | Read |
| --- | --- |
| Write a Panel | [workspace-panel-skill.md](workspace-panel-skill.md) |
| Write a Skill, Workflow or point library | [guides.md](guides.md) |
| Know what reaches the agent, and how | [agent-protocol.md](agent-protocol.md) |
| Stand in for Lumeri's agent | [agent.md](agent.md) |
| Everything a Luc may declare | [permissions.md](permissions.md) |
| The hub: handles, review, reports | `hub.md` (not in this repository) |
| How Lumeri's own Lucs are shipped | `official-channel.md` (not in this repository) |

Laying a Luc over one of Lumeri's own modules, so it changes or replaces that
module and Lumeri goes back to the original when the layer is removed or
breaks, is being built on another branch (`luc/agent-core`) and is not in
this tree.
