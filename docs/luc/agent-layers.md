# Lumeri's own layers on its Lucs

Decided 2026-11-07 (Acrab): Lumeri, the agent in the App, may change every Luc
except its own agent core, the editor (`lumeri.workspace`) included. Decided
the same day: a tool Luc's Python is not agent core either; Lumeri may change
it once tools run in a sandbox of their own (phase 2 of the plan, not here).

Decided 2026-11-16 (Acrab): Lumeri may change every Luc except its **agent
core** and Lumeri's **cloud services**. On the same day Acrab delegated the
module classification to the self-Luc lead, who finalized the canonical table in
`MODULE_CLASSES` (`lumeri_runtime/luc_agent_layers.py`). Official Lucs that
cannot be layered yet refuse honestly by category instead of masking runtime
code or frozen UI as agent core.

Decided 2026-11-23 (self-Luc lead, delegated by Acrab): when the creator's layers
on a Luc form one line, Lumeri's own layer lies on the top one; a change of
Lumeri's to anything the creator's layers set always waits for the creator; and
Lumeri's layer steps aside at load time if it no longer fits the creator's layer changes,
to be laid again at Lumeri's next turn.

This is phase 1: one agent tool, `luc_layer`, and everything it needs. The
code is `lumeri_runtime/luc_agent_layers.py`.

## A change is a layer

Lumeri changes a Luc the way a creator does ([permissions.md](permissions.md),
"A layer on a Luc"): with an Extension activated in the activation profile
named after that Luc. Lumeri's signed bytes are never edited, so safe mode, or
switching the layer off, gives the Luc back exactly as it shipped.

- Each Luc carries at most one layer of Lumeri's, `agent.<luc id>`, installed
  in the Local Registry with origin `lumeri-agent`.
- Every change is a new version of that layer (`0.1.N`), built by replaying
  the whole edit log on the Official Luc (plus any creator line underneath it).
  Undo selects the version before when it lies on the same Official Luc and
  parent; otherwise undo drops Lumeri's layer cleanly while leaving the
  creator's layers intact. After an Official update or a change in where
  `agent.<luc id>` should lie (`trigger`: `"luc_update"` or `"creator_layers"`),
  held or out-of-position layers wait for `auto_rebase` on the creator's next
  turn: before the first model call and without invoking any model, the Runtime
  deterministically replays Lumeri's layer onto the target `parent` once per
  `(luc, base, parent_source_hash)` (unless in safe mode, disabled, or untouched
  when the creator explicitly excluded `agent.<luc id>` without changing parent
  layers), records `auto_rebase` (`rebased`, `waiting`, `declined`, `failed`, or
  `blocked_by_creator`, with `trigger` and `parent` whenever creator layers or a
  parent change are involved), and injects a single English summary paragraph
  into that turn's model context once (`notified: true`). Creator layers are
  never rebased automatically. `luc_layer(op="rebase")` remains available at any
  time.
- The record of versions and of the change waiting for the creator is
  `agent-layers/<luc id>.json` beside the Local Registry.
- When the creator's active layers on an Official Luc form a single unbranched
  line (`L1 → … → Ln`), `agent.<luc id>` lies on the top creator layer (`Ln`)
  with a four-key `extends` record (`parent`), stays ordered last in the
  profile, and any change by Lumeri to a file or manifest field touched by those
  creator layers waits for creator confirmation (`overrides`, reused without
  re-asking while target, creator sha256, and creator layer stay unchanged).
- When there are no creator layers or the creator's layers fork into branches,
  `agent.<luc id>` lies directly on the Official Luc (three-key `extends`) and
  creator-owned files and fields are refused (`luc_layer_creator`).
- When the creator disables, replaces, or removes an ancestor layer under
  `agent.<luc id>`, load-time resolution (`official_luc._creator_layers`) lets
  `agent.<luc id>` step aside cleanly so the creator's remaining layers continue
  to load, and Lumeri's layer is re-laid at the start of Lumeri's next turn.
- If a creator's layer lies on Lumeri's own `agent.<luc id>` layer, `read`,
  `propose`, `undo`, `disable`, and `rebase` refuse (`luc_layer_creator`) and
  leave the profile untouched until that layer is removed; `list` continues
  normally.
- Only Official Lucs. A Luc the creator added to `~/.lumeri/lucs` is changed
  in that folder (`luc_layer_local`).

## The tool

`luc_layer` is offered beside `luc_discover`, `luc_describe`, `luc_invoke` and
`use_skill`, never in safe mode. Its tool declaration stays compact (at most
~105 characters) so every signed 16 KiB model request fits beside the rest of
the toolbox; instead, `list` (`how`), `read` (`next`), and invalid-edit
refusals spell out all four `edits` item shapes and the apply/wait/refuse rules.

| op | What it does |
| --- | --- |
| `list` | Every Official Luc loaded in the Worker: open Lucs row by row (with protocols, what may change, Lumeri's layer version, a change waiting for the creator, and any `auto_rebase` record), plus refused Lucs grouped by category and reason (`refused`) |
| `read` | Any loaded Official Luc's files and capabilities (including refused Lucs, with `category`, refusal `code`, and `why`); with `file`, one text file or `manifest.json` from `start_line`, as many lines as fit one tool result |
| `propose` | Edits in order, and a one-sentence `summary` the creator reads |
| `undo` | The version before (or the Luc alone) |
| `disable` | Lumeri's layer off; versions are kept |
| `rebase` | Replay the layer on the Official version that ships now |

An edit is one of:

```json
{"kind": "text", "file": "panel/outline.css", "find": ".outline {", "replace": ".outline { font-size: 15px;"}
{"kind": "file", "file": "appearance/style.css", "content": ":root { --accent: #ff7a1a; }"}
{"kind": "delete", "file": "README.md"}
{"kind": "manifest", "op": "replace", "path": "/host/capabilities/0/title/en", "value": "Story"}
```

`find` must occur exactly once in the file as it is in force. A manifest edit
that adds to or removes from a list becomes, in the layer, a replacement of
that whole list.

## What applies now, what waits, what is refused

| Class / rule | Code | What falls here & why; how it could open |
| --- | --- | --- |
| Applies at once | — | Markdown, CSS, JSON data; a Panel's title, description, icon or size; a Skill's or Workflow's words and the tools it names; a tool's description; an interaction's description; the editor's modules and appearance (`lumeri.workspace`: `/host/capabilities/0/modules` and `appearance` only, matching `iface:workspace.video`) |
| Overrides a creator's layer | — | Waits for the creator; only while Lumeri's layer lies on the creator's line (`overrides` reused while target, creator sha256, and creator layer remain unchanged) |
| Waits for the creator | — | A file a browser or the Runtime runs (`.js`, `.mjs`, `.html`, `.svg`, ...); a Panel's `document` or `bridge`; any Host API or permission the profile never held; any override of a creator layer's target |
| Refused — `agent_core` | `luc_layer_core` | Lumeri's own brain wiring and gatekeeping machinery (tool assembly, model routing, budgets/receipts, the Luc layer/activation/confirmation machinery, execution gate, sandbox, Runtime process base, and the editor pieces that speak or confirm for the creator), plus stand-in agent prompt (`lumeri.agent/v1`, `agent.replace`), native runtime tools/panels (`lumeri.tool/v1`, `lumeri.panel/v1`), editor page files under `frontend/`, and any unlisted `lumeri.module/v1` (closed by default). See `MODULE_CLASSES` in `lumeri_runtime/luc_agent_layers.py` (61 modules). Stays closed so Lumeri cannot remove its own gates |
| Refused — `cloud_service` | `luc_layer_cloud` | Accounts, sign-in, gateway, ChatGPT route, cloud feature switches, model-key settings, Luc cloud/hub, and Official channel/publisher/registry (13 modules in `MODULE_CLASSES`). Only Lumeri ships these |
| Refused — `runtime_code` | `luc_layer_sandbox` | Runtime-internal code and legacy/native engines that are neither agent core nor cloud service, so the rule allows changing them once a safe execution path exists: a tool Luc's `.py`; the 22 `runtime_code` modules in `MODULE_CLASSES` (project/timeline/session/media/engine/legacy bindings); `lumeri.lumenframe2` (renderer protocol awaits layer rules); `lumeri.vector-motion-library` (native-rendered library); `lumeri.timeline-ops-skill` (1.0 native skill). Opens when tool/runtime code runs in a sandbox, renderer layer rules exist, or `timeline-ops-skill` moves to `lumeri.skill/v2` (and `MODULE_CLASSES` + the interface contract are updated) |
| Refused — `frontend_frozen` | `luc_layer_frozen` | Built-in editor front-end modules (`lumeri.editor.builtin-panels`, `lumeri.editor.legacy-runtimes`, `lumeri.editor.placement`, `lumeri.editor.quanta-page`, `lumeri.editor.quanta-v4`; 5 modules in `MODULE_CLASSES`). Kept frozen until Lumeri's design kit (LADK) defines how the front end is built and phase 3 adds a sandboxed UI frame |
| Refused — `not_opened` | `luc_layer_closed` | `lumeri.custom-pages` (`lumeri.custom-page/v1`) and `lumeri.custom-backends` (`lumeri.custom-backend/v1`): custom pages are not opened yet; opens once custom pages ship |
| Refused — fields / authority | `luc_layer_field` / `luc_layer_reserved` | A permission only Lumeri gives; a capability's `id`, `protocol` or `surface`; a workspace's `description`; `/metadata`, `/compatibility` and every top-level field but `summary`; `/host/protocols` |

Only a Skill, Workflow or library Luc may gain or lose a capability. Any
change on a module Luc requires updating both `MODULE_CLASSES` in
`lumeri_runtime/luc_agent_layers.py` and the corresponding interface contract
under `contracts/interfaces/`.

A change that waits for the creator is shown in the editor as a card (`luc-ui.js`
`createLayers`) with Lumeri's summary, the files, and the code diff. If the
person running this machine configured a `[luc_hooks]` `layer_change` command in
`LUMERI_LOCAL_CONFIG_PATH`, the Runtime asks that command once per pending
proposal (isolated temp directory, four environment variables only, `stdin`
matching [schemas/layer-change-v1.schema.json](schemas/layer-change-v1.schema.json),
bounded `stdout`/`stderr`, process-group timeout kill). A strict
`{"decision": "apply"}` or `{"decision": "decline"}` answer settles the
proposal with `answered_by: "hook"`; any failure (`timeout`, `exit`, `output`,
`start`, `config`) leaves the proposal `pending` (`hook: {"state": "error", "code": ...}`)
for creator confirmation. A hook running at the start of a turn can delay the
first model call by up to the hook timeout (default 30 seconds, maximum 120 seconds)
with nothing shown while it runs. Only the
creator's press answers a card: `POST /sessions/{id}/luc/layers/proposals/{proposal}`
with `{"decision": "apply"}` or `{"decision": "decline"}` (`answered_by: "creator"`), refused while the
Worker acts for the agent (`E_LUC_CONFIRMATION_REQUIRED`). A newer change of
Lumeri's to the same Luc replaces a card not yet answered (without running any
hook for the superseded proposal); one answered after
the Luc changed under it fails (`failed`) and applies nothing.

Phase 1 keeps the card in the editor page. That page is still Lumeri's own
bytes in full, so nothing a layer brings runs beside the card; while a card is
up the editor drops the appearance a layer laid on it, so a look cannot dress
the card up, and the appearance strings can never reword a `luc*`, `gate*` or
`layer*` key. Phase 3 moves the card into the core shell.

## Loaded before it is kept

Every change is loaded on a fresh Host (`LucInteractions.build`, the same
`bootstrap_official_lucs` a start runs, with this Luc's resolved layers given
as `layer_overrides`). One the Host refuses is refused to Lumeri with the
Host's reason (`luc_layer_load`) and nothing is selected. A change that waits
for the creator is loaded the same way first, so the creator only answers
one that would load.

The fresh Host then replaces the running one (`LucInteractions.adopt`), at
once, or when no interaction waits on the old Host, since a waiting
interaction holds its proposal and confirmation there. The editor hears
`luc_layers_changed` and reloads the workspace, its Panels and its look. The
agent's toolbox follows at its next turn.

## The editor's look

The workspace capability takes an optional `appearance`:

```json
"appearance": {"style": "appearance/style.css", "strings": "appearance/strings.json"}
```

Both files live under `appearance/` in the workspace Luc. The workspace adapter
checks them as it loads (`compile_appearance`) and `GET /sessions/{id}/luc/workspace`
carries the result. The editor adds the stylesheet after its own and rewords
its copy table by key (`LumeriV3Locale.mountDataLayer`, and `unmountDataLayer`
when cleared or suppressed while a confirmation card is open).

- The stylesheet is at most 64 KiB. It takes pictures only from `data:image/`
  URLs, and refuses `@import`, `@namespace`, `image-set(`, `src(`,
  `expression(`, `javascript:`, `-moz-binding`, `behavior:`, `</` and any
  backslash escape, so no selector can send what the page shows anywhere.
- Strings are `{locale: {key: text}}` for the editor's ten languages, at most
  400 words of 500 characters.

The appearance reaches the editor from the Runtime whichever page the App
shows, so it needs no Stage B.

## A Panel that faults

When a Panel cannot be drawn — or fails while starting (`BRIDGE_SHIM`
replies `{type: "fault", code: "E_LUC_PANEL_SCRIPT"}` on `init` within the
3-second handshake) — the editor posts `{"code", "document_sha256"}` to
`POST /sessions/{id}/luc/panels/{capability}/fault`. When the reported page
matches the active Panel and Lumeri's newest change to that Panel's Luc is
under ten minutes old (`AUTO_ROLLBACK_SECONDS`), the Runtime takes it back
(`undo`) and emits `luc_layers_changed` (`rolled_back`). Later faults fall
back to the shipped Luc (or the workspace role's own Panel) and emit
`luc.module_fell_back` without rolling back Lumeri's layer.

## Routes

| Route | |
| --- | --- |
| `GET /sessions/{id}/luc/layers` | Lumeri's layers in force (`lies_on` and `auto_rebase` when present), changes waiting for this creator (`overrides`), and `hook_config_error` when `[luc_hooks]` is invalid |
| `POST /sessions/{id}/luc/layers/proposals/{proposal}` | The creator's answer |
| `POST /sessions/{id}/luc/panels/{capability}/fault` | A Panel that faulted or could not be drawn (`code`, `document_sha256`); takes back a recent layer from Lumeri or falls back |

## Not yet

- Tool code (`.py`): phase 2, tools in a sandboxed process with a ToolHost proxy.
- The editor page's own files, and the confirmation moving out of the page:
  phase 3, a core shell and a sandboxed UI frame.
- Syncing Lumeri's layers to the creator's other machines: the layers are this
  machine's, like the creator's own.
- A layer of Lumeri's on a Luc the creator added themselves.
