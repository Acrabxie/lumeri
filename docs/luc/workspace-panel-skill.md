# The editor as Lucs: workspace, Panels and Skills

Decided 2026-10-04 (Acrab): the editor's frame is packaged as one Luc. Each
detailed Panel is its own Luc, contained in that frame Luc. A Panel Luc must be
general: it gets a region of its own (its own front end) and can do anything
inside it. Every Skill is a Luc. Every Luc declares its protocol.

Decided 2026-10-06 (Acrab): a Panel does not need a frame's leave. A Panel Luc
that no workspace names registers itself in the editor, and a Luc that is not
Lumeri's own can be one. What such a Luc may declare, and what stays Lumeri's
own, is in [permissions.md](permissions.md).

Three protocols carry this. Each Luc names its protocol in `host.protocols`, and
only the Host's adapter for that protocol reads it (`lumeri_runtime/luc_workspace.py`).
A package cannot register an adapter or widen what one accepts.

| Protocol | Adapter | What it is |
| --- | --- | --- |
| `lumeri.workspace/v2` | `workspace-frame` | The editor's frame: which modules it shows, in what order, which are visible by default, and the editor page itself (`lumeri.workspace/v1` is the same without the page) |
| `lumeri.panel/v2` | `sandboxed-panel` | One region of the editor that the Panel draws itself |
| `lumeri.skill/v2` | `agent-skill` | Instructions the agent reads before a task, plus the tools they rely on |

None of the three is an agent capability. `luc_discover` and `luc_invoke`
never show or call them. The agent reads Skills through `use_skill`. Panels and
the workspace exist only for the editor.

## Workspace (`lumeri.workspace/v2`)

One capability, surface `workspace-frame`, `product` equal to the Runtime's
product. It needs `ui.mount`, requests no Host API, and edits nothing.
`modules` is an ordered list of 1 to 32 entries:

```json
{"module": "outline", "kind": "luc", "luc": "lumeri.outline-panel", "panel": "panel.outline", "default_visible": true}
```

`kind` is one of the following:

- `core`: `preview` and `timeline`. Every workspace must contain both.
- `native`: a Panel the editor still draws itself: `outline`, `tasks`,
  `files`, `library`, `skills` or `properties`.
- `luc`: a Panel Luc, named by its Luc id and capability id.

How the editor resolves the list:

- The framework in force places the Panels it names, by Luc id and capability
  id.
- Every other loaded Panel registers itself: it is added after the framework's
  modules as a module of its own, named `luc-<capability id>-<8 hex>` (a
  lower-case name of at most 40 characters, the same at every start), with
  `"registered": "self"` and `default_visible: true`. They come in the order
  of their Luc ids.
- The editor holds 32 modules. A Panel past that is listed under
  `uncontained` with `E_LUC_PANEL_LIMIT`, is not mounted and is not served.
- If a contained Panel is absent (switched off, refused, or safe mode), its
  module is `available: false` with code `E_LUC_PANEL_UNAVAILABLE`.
  - It falls back to `native` when the module name is a built-in Panel.
  - It has no fallback when the name is new.
- A module a Panel Luc draws goes by the Panel's `title` in its tab and
  heading. If the Panel fails to load, a built-in module returns to its
  built-in name and description; a new name keeps the Panel's title and says
  the Panel could not load.
- The Official `lumeri.workspace` wins when several frameworks load. Any others
  are reported as `inactive_workspaces`.
- With no framework at all, the editor keeps its own built-in layout and
  mounts no Panel; every loaded Panel is then listed under `uncontained`.

The framework's order and default visibility only seed the editor. Once the
creator arranges modules or shows or hides one, that choice stands.

The catalog keeps `"protocol": "lumeri.workspace/v1"`: its module list has the
same shape under either protocol.

### The editor page (Stage B, decided 2026-10-04)

A v2 framework also carries the editor page:

```json
"frontend": {"root": "frontend", "entry": "index.html", "native_api": 1}
```

- `root` is one top-level folder of the package; `entry` is always `index.html`.
- `native_api` is the native bridge the page was written for (1..64).
- At most 512 files, each at most 16 MiB. The adapter pins them the same way an
  App registration pins its bundled page (SHA-256 of the sorted
  `{name: sha256}` map).

The Official package is built from `static/v3`: the bundle build stages it into
`frontend/` and stamps the version `0.2.0+web.<first 12 hex of the pin>`.
Build metadata does not change version precedence; among builds of one version
the one installed last counts as newest. Capability descriptors write the `+`
as `_` (descriptor versions are portable identifiers).

Who gets which page (`lumeri_runtime/luc_frontend.py`, `frontends.py`, `gateway.py`):

- Only the Official `lumeri.workspace` ever supplies the page: from the stored
  channel when it verifies, otherwise from the bundled set. Creator Lucs and
  creator layers never do, because the page holds the App's bridges and the
  creator's session.
- The native App opts in when it opens the editor:
  `POST /runtime/v1/frontend/video {"manifest_sha256": …, "native_api": 1}`.
  The receipt says which page the lease serves: `"frontend": {"source": "luc",
  "luc": {...}, "origin": "official-channel" | "official-bundled", "native_api",
  "manifest_sha256"}` or `{"source": "app", "manifest_sha256"}`.
- The page is the Luc's only when the App offers the same `native_api` and the
  creator has the framework switched on (by the Worker's rules, including a new
  version keeping the choice of the newest one they had). Otherwise the App's
  own pinned copy stays in force; so does an App that sends no `native_api`.
- Every `/video/*` response says `X-Lumeri-Frontend-Source: luc | app`. A
  renderer cannot upgrade an App-copy lease; the native App itself (gateway
  token) may ask with `X-Lumeri-Frontend-Native-Api`.
- The Gateway resolves the page once per run, so a newly fetched channel
  reaches the editor at the next Runtime start, like the Worker's set.

## Panel (`lumeri.panel/v2`)

Capability fields:

- `surface`: `sandboxed-frame`.
- `title`: a locale map; English is required, at most 40 characters.
- `icon` (optional): SVG path data.
- `size`: `width` and `height` in percent of the workspace, plus
  `min_width_px` and `min_height_px`.
- `document`: `{html, scripts[], styles[]}`, naming `.html`, `.js` and `.css`
  files of the package. At most 16 scripts and 16 styles, 2 MiB in total.
- `bridge`: `{calls, events}`.

The Host builds one page from the files in this order:

1. The Panel CSP.
2. The base style.
3. The Panel's styles.
4. The bridge shim.
5. The Panel's HTML.
6. The Panel's scripts.

It stores the page's SHA-256. It refuses:

- a script containing `</script`;
- a style containing `</style`;
- a page whose files changed after load (`E_LUC_PANEL_CHANGED`).

The editor shows the page in `<iframe sandbox="allow-scripts" srcdoc>`. That
frame has:

- an opaque origin;
- no cookies and no storage;
- no access to the editor page.

The page's own CSP allows no network at all (`default-src 'none'`,
`connect-src 'none'`, `frame-src 'none'`). The editor's CSP applies to the
frame as well. The 2026-10-04 browser check confirmed each of these limits:

- fetch is blocked, both same-origin and external;
- an external image is blocked;
- `localStorage` and `document.cookie` throw;
- `parent.document` throws;
- the `lumeri` object can be neither changed nor replaced.

### Bridge

The editor sends one `init` message carrying a private `MessageChannel` port.
Every later call and event uses that port. Inside the frame the shim exposes a
frozen `window.lumeri` with these members:

| Member | Behaviour |
| --- | --- |
| `ready()` | Resolves with `{capability_id, locale, calls, events}` |
| `call(api, payload)` | Only the calls the manifest declares. Any other is refused in the frame (`E_LUC_API_UNDECLARED`) and again by the Host |
| `on(name, listener)` | Subscribes to one of the events the manifest declares (see Lifecycle) |
| `dragContext(event, ref)` | Starts a drag of one Project item toward the message composer. Requires `workspace.context` |
| `dragAsset(event, ref)` | Starts a drag of one library asset `{asset_id, media_kind}` toward the timeline. Requires `library.place`. The editor alone decides where it lands and places it; data the frame puts on the drag is never read |

Theme tokens arrive as CSS variables `--lumeri-<name>`, for example
`--lumeri-on-surface`, `--lumeri-primary-container` and `--lumeri-font`.

Bridge calls and who carries them out:

| Call | Host API (permission) | Carried out by | Does |
| --- | --- | --- | --- |
| `project.view` | `project.view` (`project.read`) | Runtime: `POST /sessions/{id}/luc/panels/{capability}/call` | Returns `{revision, view, data}` for `view` `shotlist` or `timeline` (at most 1 MiB) |
| `workspace.context` | `workspace.context` (`ui.mount`) | Editor page | Adds `{kind, id, label, scene_id?, asset_id?, track_id?}` to the composer. Kinds: `outline_scene`, `outline_shot`, `timeline_clip`, and `skill` (`id` is a Skill's `name`). The editor stamps the surface with the module name. The composer folds `skill` references into their own list the agent reads with `use_skill`, apart from the Project content block |
| `tasks.view` | `tasks.view` (`project.read`) | Runtime | Returns `{sessions}`: this creator's Sessions with a turn in progress or running jobs, each `{session_id, project_id, project_name?, turn_in_progress, jobs: [{job_id, capability_id, opened_at, cancel_requested_at?}]}` |
| `tasks.stop` | `tasks.stop` (`session.control`) | Runtime | Asks one job to stop: `{session_id, job_id}`. Returns `{status, job_id, cancel_requested_at}`; the job reads `running` until its work ends. Refusals: `E_LUC_TASK_SESSION_NOT_FOUND` (missing or another creator's), `E_LUC_TASK_JOB_NOT_FOUND`, `E_LUC_TASK_JOB_NOT_CANCELLABLE` |
| `files.view` | `files.view` (`project.read`) | Runtime | Lists one folder of the Session's Project: `{path?}` gives `{path, entries}`. Paths stay inside the Project (`E_LUC_FILES_PROJECT_PATH_REFUSED`). The Runtime's own records at the Project's top (`project.json`, its documents, the governance store, `sessions/`) are not files of the Project: they are neither listed nor read |
| `files.read` | `files.read` (`project.read`) | Runtime | Reads one text file of the Project: `{path}` gives `{path, content}` |
| `panel.state` | `panel.state` (`ui.mount`) | Runtime | `{op: "get"}` or `{op: "set", value}`. One JSON value per creator and Panel, at most 16 KiB (`E_LUC_PANEL_STATE_TOO_LARGE`), kept across reloads and devices of that creator. Replaces the storage the frame does not have |
| `timeline.edit` | `timeline.edit` (`project.write`) | Runtime | One timeline patch (`op` and its fields, as the Worker's timeline patch takes them) plus `revision`, the Project revision the Panel read. Returns `{status: "applied", revision}` or `{status: "approval_required", revision}`. A stale revision is refused (`E_LUC_TIMELINE_PROJECT_REVISION_CHANGED`) and nothing changes |
| `skills.view` | `skills.view` (`ui.mount`) | Runtime | Returns `{skills}`: every Skill Luc in the order the agent meets them, each `{name, title?, description, luc, ready}`; one not ready adds `code` (`E_LUC_LANGUAGE`, `E_LUC_SKILL_USES`, `E_LUC_SKILL_NAME`, `E_LUC_SKILL_LIMIT`) and, for missing tools, `uses`. The same reading `use_skill` is built from; never the instructions |
| `workspace.open` | `workspace.open` (`ui.mount`) | Editor page | Asks the editor to open `{kind: "session", session_id}`. Returns `{opened}`; the editor decides |
| `selection.view` | `selection.view` (`project.read`) | Editor page | Takes `{}`. Returns `{selection}`: the selected timeline clip as the editor shows it, or `null`. `{kind, id, track_id, label, media_kind, revision, fps, editable, time: {start, duration, source_in, source_out}, geometry}`. `geometry` is `null` for a clip with no picture, else the centre `x` and `y`, `scale` and `rotation` on the canvas at the playhead, with `base_width`, `base_height` and `motion` (the motion path's offset there) so a centre can be turned back into the clip's own `x` and `y`, and `rotation_editable`. `revision` is the Project revision the editor's timeline was read at (`null` when it has none, and then `editable` is false). Changes still go through `timeline.edit` |

| `library.view` | `library.view` (`project.read`) | Runtime | Returns `{assets, truncated}`: each asset of the Session's Project library `{asset_id, name, kind, mime_type, size_bytes, duration, notes: {count, labels, tags}, roughcut}`. `kind` is `video`, `image`, `audio` or `file`; `duration` is the measured running time, or `null` before the media is measured. No bytes |
| `library.thumbnail` | `library.thumbnail` (`project.read`) | Runtime | `{asset_id}` gives `{asset_id, picture}`: a video's or still's card picture as a `data:` URL (a JPEG cut in the media sandbox; a small SVG as itself), or `null` for a sound, a file, or a picture that could not be cut |
| `library.notes` | `library.notes` (`project.read`) | Runtime | `{asset_id}` gives `{asset_id, notes, roughcut}`: the notes kept about the asset, and its roughcut (`null` if none) as 1.0's review reads it |
| `library.place` | `library.place` (`project.write`) | Runtime | `{asset_id, track_id?}` puts the asset after the last clip of that track, or of the first track that takes its kind. One `insert_clip` through the Worker's governed timeline patch on the revision it read: `{status, revision, clip_id, track_id, at}`. Refusals: `E_LUC_LIBRARY_ASSET_NOT_FOUND`, `_NOT_PLACEABLE`, `_NO_TRACK`, `_TRACK_KIND`, `_TRACK_NOT_FOUND` |
| `library.roughcut` | `library.roughcut` (`project.write`) | Runtime | `{asset_id, language}` starts 1.0's roughcut of a video or sound as a job: `{status: "started", job_id}`. `E_LUC_LIBRARY_NOT_TRANSCRIBABLE` for anything else |
| `library.job` | `library.job` (`project.read`) | Runtime | `{job_id}` of a roughcut this Session started gives `{status}`: `running`, `done`, `stopped`, `approval_required` (with `approval`), or `failed` with `error: {code, message}` (for example `E_LUC_LIBRARY_TRANSCRIBER_UNAVAILABLE`). `E_LUC_LIBRARY_JOB_NOT_FOUND` for any other job |
| `library.roughcut.review` | `library.roughcut.review` (`project.write`) | Runtime | `{asset_id, target_type, target_id, action, text?}` records one decision: a `cleanup` suggestion accepted or rejected, a `transcript` line corrected, or the `take` selected, kept as an alternative or rejected. A later decision about the same target replaces the earlier one. Returns `{roughcut}` as it now stands; `E_LUC_LIBRARY_NO_ROUGHCUT` when there is none |

Every Runtime answer is at most 1 MiB (`E_LUC_VIEW_TOO_LARGE`). A Runtime call
reaches only the Worker operation behind it, bound to the caller's identity: a
Panel reaches nothing the creator's own editor cannot.

`session.control` is the permission to stop work the creator started. It
writes nothing in a Project, so it needs no editable boundary; `project.write`
still does.

Rules that tie the bridge to the manifest:

- Every bridge call must be a Host API the Luc requests.
- A Panel may request only Host APIs the bridge can carry.
- A Panel edits the Project only through `timeline.edit` or the library's
  write calls (`library.place`, `library.roughcut`, `library.roughcut.review`),
  never through the frame itself. Only a Panel that declares one may declare
  an editable boundary; any other Panel's `editable` is `none`.
- Each timeline operation reaches a target type and a set of properties
  (`PANEL_EDIT_REACH` in `luc_workspace.py`, the same terms as
  `native_luc_catalog.py`); each library write call reaches its own
  (`PANEL_CALL_REACH`: placing is `timeline_clip` placement and presence in
  `timeline`, a roughcut and its review are `library_asset` notes in
  `library`). The Runtime refuses a write the Panel's boundary does not cover,
  before the Worker sees it (`E_LUC_EDIT_BOUNDARY`). Markers and the timeline
  format are outside every Panel's boundary.
- An accepted edit takes the same path as the creator's own edit in the
  editor: revision check, approval gate, durable receipt. The editor then
  reloads its timeline.

Runtime routes, owner-checked against the Session:

- `GET /sessions/{id}/luc/workspace`: the catalog.
- `GET /sessions/{id}/luc/permissions`: what each Luc on this machine asked
  for and holds, which are off or were refused, and the whole list a Luc may
  declare ([permissions.md](permissions.md)).
- `GET /sessions/{id}/luc/panels/{capability}/document`: the page. Only for a
  Panel that is a module of the catalog, placed or self-registered.
- `POST /sessions/{id}/luc/panels/{capability}/call`: one bridge call.

These map errors to HTTP statuses as follows:

| Error | Status |
| --- | --- |
| `E_LUC_NOT_FOUND` | 404 |
| `E_LUC_API_UNDECLARED`, `E_LUC_API_PERMISSION`, `E_LUC_API_UNAVAILABLE`, `E_LUC_EDIT_BOUNDARY` | 403 |
| any other Luc refusal | 400 |

The editor side is `static/v3/luc-panel-host.js`. It does the following:

- validates every message from the frame;
- caps a Panel at 8 bridge calls in flight;
- treats a frame that navigates away from its page as a failure
  (`E_LUC_PANEL_NAVIGATED`);
- fetches a Panel's page once per document hash.

### Lifecycle

A Panel hears only the events its manifest declares:

| Event | Payload | When |
| --- | --- | --- |
| `project.changed` | `{}` | The Project changed; read it again. A Panel in a region of its own hears it each time the Project's revision moves, whoever moved it. A Panel in one of the editor's named modules hears it at the points the editor refreshes that module |
| `locale.changed` | `{locale}` | The creator switched language |
| `theme.changed` | `{}` | Reserved: the editor has one theme and does not send it yet |
| `panel.visibility` | `{visible}` | Once after `init`, then on every change: tab switched, editor hidden or shown. A hidden Panel should stop polling |
| `panel.resized` | `{width, height}` | Once after `init`, then at most every 100 ms while its box changes, in whole CSS pixels |
| `tasks.changed` | `{}` | Work started or ended, and every 5 s while the tasks module is shown; sent instead of `project.changed` to it. The Panel needs no timer of its own |
| `selection.changed` | `{selection}` | The selected timeline clip changed: a `timeline_clip` ref, or `null` |

Without pointer drag the Panel can still add context through
`call("workspace.context", ref)`. The Official outline Panel does this on
Enter or on a double click.

## Skill (`lumeri.skill/v2`)

Capability fields:

- `surface`: `agent-instructions`.
- `name`: a lowercase handle.
- `description`: when to use it, at most 300 characters, in English.
- `instructions`: a `.md` file of the package, 1 to 4800 characters, in English.
  The complete JSON result (name, instructions and escaped characters) must
  also fit 6000 characters; a Skill that does not is refused when it loads.
- `uses`: 1 to 32 tool names or capability ids.
- `title` (optional): what the creator reads in the editor, a locale map like a
  Panel title (English required, at most 40 characters). The agent reads the
  `name`.

A Skill requests no Host API, no permission and no edits. The tools it names
keep their own authority.

The agent sees one `use_skill` tool. Its description lists each ready Skill as
`name: description`. Calling it returns the instructions, re-checked against
the hash taken at load. A Skill is ready only while everything in `uses` is
available to the agent. An item counts as available if it is any of these:

- a tool in the toolbox;
- an agent-visible Luc capability;
- an Official `lumeri.tool/v1` capability whose native tool is back in the
  toolbox when switched off (note: `lumeri.tool/v2` packaged tools such as
  `lumeri.timeline-editor` have no `VIDEO_TOOLS` native fallback when switched off).

A Skill that is not ready is listed as unavailable by `luc_discover`, under one
of these codes:

- `E_LUC_LANGUAGE`: its `name`, `description` or `instructions` are not
  English. English is the one language of what a Luc says to the agent
  ([agent-protocol.md](agent-protocol.md)); the `title` is for the creator and
  is exempt.
- `E_LUC_SKILL_USES`: something in `uses` is missing.
- `E_LUC_SKILL_NAME`: a duplicate name.
- `E_LUC_SKILL_LIMIT`: more than 24 Skills and Workflows.

The editor reads the same rows through `skills.view`, so the Skills Panel shows
exactly what `use_skill` offers and why the rest wait.

A Workflow (`lumeri.workflow/v1`) is read through the same tool and shares the
Skills' names and their limit; a point library (`lumeri.library/v1`) is a
capability a Skill may name in `uses`. Both are in [guides.md](guides.md).

The Skills ride in a tool declaration because the step system prompt is fixed
and hashed.

## Official packages

| Package | Protocol | Contains |
| --- | --- | --- |
| `lumeri.workspace` 0.5.0+web.<pin> | workspace/v2 | Video modules: preview, outline, tasks, timeline, then properties, files, library, skills (every one but preview and timeline is a Panel Luc); and the editor page from `static/v3` |
| `lumeri.outline-panel` 0.1.0 | panel/v2 | The shot-list outline, drawn by the Panel, in 10 languages, with context drag to the composer |
| `lumeri.tasks-panel` 0.2.0 | panel/v2 | Work in flight across the creator's Projects: sessions with a turn in progress and their running jobs, grouped by Project; Stop asks a job to end, a busy session opens in the editor. Reads on `tasks.changed` (the editor's beat) and when shown, never while hidden |
| `lumeri.files-panel` 0.1.0 | panel/v2 | The Session's Project folder, folders first; text files open in a read-only preview, media files are listed. Remembers the last folder with `panel.state` and starts over at the top when that folder is gone |
| `lumeri.properties-panel` 0.1.0 | panel/v2 | The selected clip's centre, scale and rotation on the canvas and its start, duration, in and out points. A typed value is one `timeline.edit` patch (`move_clip`, `trim_clip`, `set_clip_time`, `set_clip_effects`) on the revision it read, inside a boundary of `timeline_clip` placement, source range and effects. With a LumenFrame canvas open and no clip selected, the module shows the editor's own canvas inspector instead |
| `lumeri.library-panel` 0.1.0 | panel/v2 | The Project's media, each card with its picture, running time and notes, and its other files. A video, still or sound goes onto the timeline by dragging it onto a track or with "Add to timeline"; a video or sound is prepared as a roughcut (a job it follows) whose take, transcript and suggested cuts are decided on in the card. Inside a boundary of `timeline_clip` placement and presence and `library_asset` notes |
| `lumeri.skills-panel` 0.1.2 | panel/v2 | The Skills and Workflows the agent has, ready ones first and Workflows before Skills; a ready one goes into the next message by click or drag, one not ready says what it lacks |
| `lumeri.timeline-editing-skill` 0.3.0 | skill/v2 | How to place, trim, split, move, retime and sequence clips with the 1.1 timeline tools |
| `lumeri.transitions-skill` 0.3.0 | skill/v2 | How to add, change or remove transitions; defaults to a short dissolve |
| `lumeri.production-guides` 0.1.0 | skill/v2, workflow/v1 | Eight Skills for making a whole piece (brief, footage, structure, cut, look, sound, inspection, delivery) and the `product-promo` Workflow that puts them in six stages ([guides.md](guides.md)) |

Grant handling:

- `ui.mount` has no Host API behind it, so the Host checks that grant itself.
  Without it, the workspace and the Panel are unavailable (`E_LUC_API_PERMISSION`)
  and the editor keeps its own layout.
- A workspace made for another product is unavailable (`E_LUC_PRODUCT`), and
  its product keeps its own workspace.
- Unavailable editor-only packages are not reported to the agent.

## Not yet

- **Stage B in users' hands.** The source carries it (runtime and shell); the
  installed App does not until an approved App packaging.
- The v1 seam `lumeri.timeline-ops-skill` (`lumeri.skill/v1`) remains as
  compatibility evidence, off by default. Its prompt is Chinese, so switched
  on it loads and is withheld from the agent (`E_LUC_LANGUAGE`). The v1 tasks
  Panel descriptor now lives only as a test fixture
  (`tests/fixtures/luc-v1/tasks-panel`) for the `native-ui-panel` adapter;
  `lumeri.tasks-panel` itself is the v2 Panel.
- The Library Panel does not analyse an asset with the model (in 1.1 that is
  a model turn, asked for in the conversation), cannot play a roughcut's take
  or seek to a suggestion (the frame cannot load media), and leaves opening a
  file named in the conversation to the editor's own files tray.
- The LumenFrame canvas inspector (layers, appearance, the Luqu canvas
  controls) stays the editor's own, inside the properties module. The Properties
  Panel reads a canvas only through the timeline clip it belongs to, and does
  not follow a drag on the canvas until the drag is let go.
- The 1.0 Skill Space (cloud guides read with `load_cloud_guide`) stays with the
  1.0 editor; on 1.1 the skills module is the Skills Panel, and a guide comes
  over as a Skill or Workflow Luc ([guides.md](guides.md)).
- **Release order.** Every Runtime so far calls itself 1.1.0, so a package
  cannot ask for a newer one by version. What keeps an older Runtime off a set
  it cannot run is the index: a state it does not know (`LUC_CODE`, then
  `LUC_DATA`) makes it refuse the whole index and keep the set it has
  ([guides.md](guides.md), "Releasing a set that has these"). Giving each
  Runtime its own version, and packages that floor, is still open.
