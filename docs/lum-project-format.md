# `.lum` - Lumeri Native Project File Format v1

Status: **Normative definition; implementation not started**  
Owner: Lumeri  
Decision: Acrab, 2026-07-20  
File extension: `.lum`  
Media type: `application/vnd.lumeri.project+zip`

## 0. Product promise

A `.lum` file is a portable, native Lumeri project checkpoint. A creator can export
one file, move it to another supported Lumeri installation, import it as a new
project, and continue parameter-level editing without needing the original workspace.

The promise is stronger than "the video still plays." A conforming round trip must
preserve the editable meaning of the project:

- timeline tracks, clips, timing, markers, transitions, effects, text and audio;
- the complete LumenFrame composition graph, including layers, groups, masks,
  keyframes, expressions, transforms, blend modes and editable text;
- references between timeline clips, compositions and source assets;
- source media and other required project dependencies, subject to the explicit font
  rule in section 8;
- render settings, project-level creative structures, stable entity IDs and
  provenance already stored in canonical project state.

A `.lum` file is not considered complete merely because it contains a rendered MP4.
Flattened media may be included as an optional proxy, but it never replaces editable
source structures.

## 1. Scope and non-goals

### 1.1 v1 includes

1. One normalized `gemia.project` snapshot as the canonical NLE-style project state.
2. One or more LumenFrame documents as native parameterized compositions.
3. Every required source-media blob that Lumeri is allowed to package.
4. An asset index that removes all dependency on absolute host paths.
5. Integrity, compatibility and portability declarations.
6. Optional cover art and regenerable proxies, clearly marked non-canonical.

### 1.2 v1 deliberately excludes

- chat messages, session transcripts and SSE event history;
- Lumeri Memory, Agent skills (`.lus`), prompts and model/provider configuration;
- accounts, cookies, credentials, API keys and authorization state;
- shell jobs, logs, temporary files, caches and diagnostic dumps;
- the user's existing undo stack and discarded patch history;
- final delivery renders unless the creator explicitly adds one as project media;
- automatic replacement of an existing local project during import;
- archive encryption or executable code embedded in a project.

Import creates a new local project checkpoint. Its undo stack starts empty. This keeps
the portable creative project separate from private conversation/runtime state and
from host-specific audit internals.

## 2. Relationship to existing Lumeri formats

| Format | Purpose | Native parameter round trip |
| --- | --- | --- |
| `.lum` | Complete Lumeri project archive | **Yes; this specification** |
| `.lumen.json` / `lumenframe.json` | One LumenFrame composition document | Composition only; no complete project or media package |
| `.otio`, `.otioz`, `.otiod` | NLE timeline interchange | Timeline-oriented; does not define complete LumenFrame preservation |
| `.lus` | Agent skill/playbook | No project content |
| `.mp4`, `.mov`, `.webm` | Flattened rendered media | No parameter editing |

OTIO may be generated from an imported `.lum` project, but OTIO is never the canonical
payload inside `.lum` v1.

## 3. Container and identification

### 3.1 Container

`.lum` is a ZIP64-compatible archive. All entry names use UTF-8 and `/` separators.
Encrypted entries, symlinks, hard links and device nodes are forbidden.

The first archive entry MUST be an uncompressed file named `mimetype` whose exact
contents are:

```text
application/vnd.lumeri.project+zip
```

These are the exact ASCII bytes, with no trailing LF.

The second entry MUST be `manifest.json`. Importers MUST inspect `mimetype`; the file
extension alone is insufficient.

### 3.2 Compression

- `mimetype` uses ZIP method `STORE`.
- JSON and other text entries SHOULD use `DEFLATE`.
- Already-compressed media SHOULD use `STORE` unless compression measurably helps.
- ZIP64 is permitted and required when normal ZIP size limits would be exceeded.
- Archive comments are forbidden.

### 3.3 JSON encoding

All JSON entries MUST be UTF-8, MUST use finite JSON numbers and MUST NOT contain
`NaN`, `Infinity` or duplicate object keys. Exporters SHOULD emit deterministic JSON
using stable key ordering and a trailing LF. Integrity hashes always cover the exact
stored, uncompressed entry bytes.

## 4. Required archive layout

```text
mimetype
manifest.json
project/
  state.json
compositions/
  index.json
  <composition-id>.json
assets/
  index.json
blobs/
  sha256/
    <first-two-hex>/<64-hex-digest>
```

Optional entries:

```text
preview/
  cover.webp
  timeline-proxy.mp4
extensions/
  <reverse-dns-owner>/...
```

No other top-level directory has defined v1 semantics. A future minor version may add
optional top-level entries. New required semantics must be announced through
`required_capabilities` in the manifest.

## 5. `manifest.json`

### 5.1 Required shape

```json
{
  "format": "lumeri.project",
  "lum_version": "1.0",
  "minimum_reader_version": "1.0",
  "created_with": {
    "product": "Lumeri",
    "version": "1.0.0",
    "build": "local"
  },
  "project": {
    "source_project_id": "project_01abc",
    "title": "Lumeri product film",
    "created_at": "2026-07-20T20:00:00Z",
    "updated_at": "2026-07-20T20:30:00Z"
  },
  "entrypoints": {
    "project_state": "project/state.json",
    "composition_index": "compositions/index.json",
    "asset_index": "assets/index.json"
  },
  "required_capabilities": [
    "project.gemia.v1",
    "assets.content-addressed.v1"
  ],
  "optional_capabilities": [],
  "portability": {
    "status": "complete",
    "missing_asset_ids": [],
    "external_font_requirements": []
  },
  "entries": [
    {
      "path": "project/state.json",
      "media_type": "application/json",
      "bytes": 18422,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "required": true
    },
    {
      "path": "compositions/index.json",
      "media_type": "application/json",
      "bytes": 92,
      "sha256": "123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0",
      "required": true
    },
    {
      "path": "assets/index.json",
      "media_type": "application/json",
      "bytes": 67,
      "sha256": "23456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef01",
      "required": true
    }
  ]
}
```

The example digest and byte count are illustrative.

### 5.2 Field rules

| Field | Rule |
| --- | --- |
| `format` | MUST equal `lumeri.project`. |
| `lum_version` | `MAJOR.MINOR`; this document defines `1.0`. |
| `minimum_reader_version` | Lowest reader version allowed to import the archive. |
| `created_with` | Informational only; MUST NOT grant trust or authority. |
| `project.source_project_id` | Stable source identity for provenance; import assigns a new local project ID. |
| `entrypoints` | Relative archive paths only. All three v1 entrypoints are required. |
| `required_capabilities` | An importer MUST understand every item or reject before creating a project. |
| `optional_capabilities` | Unknown items may be preserved or ignored without changing canonical project meaning. |
| `portability.status` | `complete`, `requires_fonts`, or `incomplete`. |
| `entries` | Integrity inventory for every archive entry except `mimetype` and `manifest.json`. |

Every archive entry except `mimetype` and `manifest.json` MUST appear exactly once in
`entries`. Every path named by `entrypoints`, composition index, asset index or
canonical project state MUST therefore appear there as well.

### 5.3 Capability tokens defined by v1

| Token | Meaning |
| --- | --- |
| `project.gemia.v1` | `project/state.json` uses normalized `gemia.project` schema v1. |
| `composition.lumenframe.v1` | At least one editable LumenFrame document is present. |
| `composition.multi.v1` | More than one named composition may be referenced. |
| `assets.content-addressed.v1` | Packaged blobs are addressed and checked by SHA-256. |
| `assets.font-dependency.v1` | External font requirements are explicitly declared. |
| `preview.cover.v1` | Optional non-canonical cover image exists. |
| `preview.timeline-proxy.v1` | Optional non-canonical playable proxy exists. |

Capabilities that affect editable meaning belong in `required_capabilities`.
Regenerable conveniences belong in `optional_capabilities`.

## 6. Canonical project state

`project/state.json` is a normalized `gemia.project` snapshot. It preserves the full
current canonical project payload, including timeline, assets, render settings,
shotlist, Quanta state, UI editing state and existing metadata.

The following export transformations are mandatory:

1. `account_id` MUST be set to `null`. Import binds the project to the importing user.
2. Absolute paths, `file://` URLs and session-local `/file/...` URLs MUST NOT appear.
3. Every packaged asset reference MUST resolve by `asset_id` through
   `assets/index.json`.
4. A legacy `source_path` field, when structurally required by the current schema,
   MUST be rewritten as `lum://assets/<percent-encoded-asset-id>`.
5. Timeline composition assets MUST carry a portable `metadata.comp_ref` containing a
   `composition_id`; host paths and cache paths are forbidden.
6. Stable clip, track, marker, shot, Quanta and asset IDs MUST survive unchanged.

An importer translates `lum://` references to its managed local content store before
passing the project through the current normalizer and validator. `lum://` is an
archive-internal identifier, not a general network URL.

### 6.1 Portable `comp_ref`

```json
{
  "composition_id": "cmp_product_29_60",
  "t_in": 0.0,
  "t_out": 31.0,
  "step": 1,
  "document_sha256": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

`composition_id`, `t_in` and `t_out` are required. A cached rendered clip may be
packaged as an optional proxy, but the composition document is the source of truth.

## 7. LumenFrame compositions

### 7.1 `compositions/index.json`

```json
{
  "schema": "lumeri.compositions",
  "version": 1,
  "primary_composition_id": "cmp_main",
  "compositions": [
    {
      "id": "cmp_main",
      "title": "Main composition",
      "path": "compositions/cmp_main.json",
      "role": "primary",
      "duration": 78.0,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "asset_ids": ["opening_capture", "product_capture"]
    }
  ]
}
```

Composition IDs MUST be unique and MUST match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`.
Each listed path is required and must be present in the manifest integrity inventory.
The `asset_ids` list is a declared dependency set; importers MUST also inspect the
document and reject undeclared dangling references.

### 7.2 Composition document rules

Each composition entry is a complete normalized LumenFrame document. Export MUST
preserve every understood field, including fields that do not currently render, so
that a later compatible Lumeri can recover the authored intent.

Within a composition document:

- layer, group and document IDs remain stable;
- editable text remains text, never a raster substitute;
- shapes, masks, effects, transforms, keyframes, expressions, time remapping,
  visibility, locking, lanes and blend modes remain parameter data;
- declarative HTML/CSS layer content may remain editable, but scripts, event-handler
  attributes, `javascript:` URLs and undeclared network loads are forbidden; rendering
  such layers remains sandboxed and offline;
- composition asset entries reference `asset_id` or `lum://assets/<id>`, never a host
  path;
- nested or pre-rendered sections SHOULD retain their source composition through
  `composition_id` rather than only retaining a flattened video;
- unknown non-critical fields MUST be preserved during re-export when the importer can
  do so without changing their meaning.

Multiple compositions are a v1 container feature because real Lumeri productions may
build a master from separately authored sections. A simple project may contain only
one composition. A project with no LumenFrame content uses an empty composition list
and omits the composition capabilities.

## 8. Assets and fonts

### 8.1 `assets/index.json`

```json
{
  "schema": "lumeri.assets",
  "version": 1,
  "assets": [
    {
      "id": "opening_capture",
      "kind": "video",
      "name": "opening-capture.mp4",
      "media_type": "video/mp4",
      "required_for_edit": true,
      "required_for_render": true,
      "blob": {
        "path": "blobs/sha256/ab/abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        "bytes": 1048576
      },
      "media": {
        "duration": 9.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0
      }
    }
  ],
  "font_requirements": []
}
```

The example blob path is illustrative; its directory prefix MUST equal the first two
hex characters of the real digest.

### 8.2 Asset rules

1. Asset IDs are unique across the package.
2. Equal byte content is stored once, even when several asset IDs refer to it.
3. The blob filename is the lowercase SHA-256 digest with no extension. The original
   display name and media type live in the index.
4. Every `required_for_edit` or `required_for_render` asset MUST have a valid packaged
   blob in a `complete` archive.
5. Missing media may not be silently skipped. A non-complete export must name every
   missing asset ID and visibly report the reduced portability to the creator.
6. Importers MUST resolve assets by ID and digest, not by filename.
7. Original absolute paths, usernames, volume names and download URLs are private host
   data and MUST NOT be copied into the package.

### 8.3 Fonts

Fonts require an honesty rule because some licenses prohibit redistribution.

- A redistributable project font SHOULD be packaged as an asset with `kind: "font"`.
- A font that cannot be packaged MUST appear in `font_requirements` with its PostScript
  name, family, style and expected file digest when known.
- Such a package MUST set `portability.status` to `requires_fonts`.
- Import MUST check the local font, offer a disclosed substitution or keep the text
  editable but unresolved. It MUST NOT silently claim exact visual fidelity.
- A strict portable export MUST fail if an exact required font cannot be embedded.

## 9. Integrity, privacy and archive safety

### 9.1 Integrity

Before creating a local project, an importer MUST verify:

- the `mimetype` entry and manifest version;
- every required entry's byte length and SHA-256 digest;
- every asset blob's byte length and SHA-256 digest;
- JSON schema shape and all cross-file references;
- composition and asset IDs for uniqueness;
- project and composition semantic validation.

A mismatch rejects the entire import. Partial project creation is forbidden.

### 9.2 Path and extraction safety

Importers MUST reject:

- absolute paths, drive-letter paths, backslashes and NUL characters;
- `.` or `..` path segments and percent-encoded traversal equivalents;
- duplicate normalized entry paths;
- symlinks, hard links, devices and encrypted entries;
- entries not declared in the manifest, except `mimetype` and `manifest.json`;
- hostile file counts, impossible declared sizes, excessive compression ratios or
  archives larger than configured disk policy.

Import MUST preflight available disk space from declared uncompressed sizes. Extraction
occurs only inside a newly created private staging directory. No archive path is ever
joined directly to a user-selected broad directory.

### 9.3 Privacy and authority

Project content is untrusted data. Importing `.lum` never authorizes shell commands,
network requests, paid generation, plugin installation or file access outside the
staging/project roots. Executable files may be stored only as inert user assets and are
never run by import.

The exporter MUST scan canonical JSON for credentials and forbidden host paths before
writing the archive. A positive secret scan blocks export and reports the offending
logical field without printing the secret.

## 10. Versioning and forward compatibility

`lum_version` uses `MAJOR.MINOR`.

- A different major version is incompatible unless an explicit migrator exists.
- A newer minor version may be imported only when all `required_capabilities` are
  understood and `minimum_reader_version` is satisfied.
- Unknown optional capabilities may be ignored, but their files SHOULD be preserved on
  re-export when safe.
- Unknown required capabilities block import before extraction or project creation.
- Schema versions inside project, composition and asset entries evolve independently;
  the manifest declares the container contract, not every inner migration.

Writers MUST NOT emit a future version number for behavior they do not implement.

## 11. Export transaction

A conforming exporter performs these steps:

1. Acquire a stable project read boundary and capture the normalized state plus all
   referenced composition documents.
2. Resolve the transitive asset dependency graph.
3. Refuse or explicitly downgrade missing media/font portability; never silently omit.
4. Sanitize account data, secrets, URLs and host paths.
5. Rewrite all project and composition references to package asset/composition IDs.
6. Write canonical files and content-addressed blobs into a private staging archive
   named `<target>.lum.partial`.
7. Reopen the staged archive and run the same structural, integrity and reference
   validator used by import.
8. Atomically rename the validated archive to `<target>.lum`.
9. Report the portability status, packaged asset count/bytes and any disclosed font
   requirements to the creator.

If the project changes after step 1, that later change is not part of the checkpoint.
The exporter records the captured `updated_at` and patch sequence when available.

## 12. Import transaction

A conforming importer performs these steps:

1. Inspect `mimetype` and `manifest.json` without extracting arbitrary entries.
2. Enforce version, capability, safety and disk-space policy.
3. Verify hashes, sizes, schemas and all cross-references in a private staging area.
4. Allocate a new local project ID. Preserve the package ID only as
   `source_project_id` provenance.
5. Place blobs into Lumeri's managed content store and build an asset-ID-to-local-path
   map without trusting original filenames.
6. Rewrite `lum://` references to managed local references.
7. Materialize project state and all composition documents, normalize and validate.
8. Run a non-mutating open/render preflight at representative frames when the required
   render capabilities are available.
9. Atomically promote the staged project into the durable project store.
10. Open it as a new project with an empty undo stack and visibly disclose any font
    substitution or portability limitation.

Failure at any step removes only the private staging data and leaves all existing
projects unchanged.

## 13. Required errors

Implementations may attach detail fields, but these stable codes are required:

| Code | Meaning |
| --- | --- |
| `E_LUM_MAGIC` | Not a `.lum` container or invalid `mimetype`. |
| `E_LUM_VERSION` | Unsupported container version or minimum reader. |
| `E_LUM_CAPABILITY` | Unknown required capability. |
| `E_LUM_MANIFEST` | Missing, malformed or inconsistent manifest. |
| `E_LUM_PATH` | Unsafe or duplicate archive path. |
| `E_LUM_INTEGRITY` | Size or digest mismatch. |
| `E_LUM_LIMIT` | File count, expansion ratio, total size or disk policy exceeded. |
| `E_LUM_SCHEMA` | Invalid project, composition or asset schema. |
| `E_LUM_REFERENCE` | Dangling, duplicate or inconsistent entity reference. |
| `E_LUM_ASSET_MISSING` | Required source asset is absent. |
| `E_LUM_FONT` | Strict fidelity requires a font that cannot be packaged/resolved. |
| `E_LUM_SECRET` | Credential-like content would enter or was found in the archive. |

Error text must be actionable and may name IDs and logical paths, but must never print
secret values.

## 14. Conformance and acceptance gates

No implementation may claim `.lum` v1 support until all gates pass.

### 14.1 Semantic round trip

1. Build a rich project containing multiple video/audio/overlay tracks, editable text,
   clip effects, transitions, markers, shotlist and Quanta state.
2. Add at least two LumenFrame compositions containing text, vector shapes, masks,
   keyframes, blend modes, nested groups and a timeline `comp_ref`.
3. Export `.lum`, remove access to the original workspace, and import into a fresh
   project root.
4. Compare normalized project and composition semantic hashes. IDs, timing, parameter
   values and cross-references must match exactly except documented import-owned fields
   such as local project ID, account ID and timestamps.
5. Change one imported text string, color, keyframe and clip trim through normal Lumeri
   editing operations. Re-rendered evidence must show each change.

### 14.2 Media portability

- Move the `.lum` file to a different absolute path and import with the source volume
  unavailable.
- Every packaged source asset must resolve by digest with no original absolute path.
- Duplicate source blobs must occupy one archive entry.
- Missing media must fail strict export or produce an explicitly incomplete package;
  it may never disappear silently.

### 14.3 Visual and audio fidelity

- Render representative frames before export and after import at the same project
  times. Deterministic layers must be pixel-identical; codec-backed sources use a
  documented tolerance.
- Final duration, frame rate, canvas size, audio clip timing, gain, fades and ducking
  must match.
- Text remains editable and exact-font status is disclosed.

### 14.4 Safety

Tests must reject path traversal, absolute paths, duplicate normalized paths, symlinks,
encrypted entries, malformed JSON, duplicate keys, hash mismatch, undeclared files,
zip bombs, dangling asset/composition IDs, unknown required capabilities and
credential-like canonical content.

### 14.5 Private production benchmark

The current 78-second Lumeri promotional-film source is the production acceptance
fixture. A conforming implementation must package the master plus every separately
authored section composition and required source asset, import it into a fresh project,
and demonstrate parameter edits inside a section - not only cutting five flattened
section videos.

## 15. Locked v1 decisions

1. `.lum` is a ZIP-based native project archive, not a renamed JSON file.
2. The default promise is portable parameter editing, not playback-only recovery.
3. `project/state.json` remains the canonical NLE-style project truth.
4. LumenFrame composition documents remain native structured data and may be multiple.
5. Asset identity is stable by `asset_id`; packaged bytes are content-addressed by
   SHA-256.
6. Absolute host paths, credentials, chat and account state never enter the archive.
7. Import creates a new project transactionally and never overwrites by default.
8. Missing media/fonts and unsupported semantics are disclosed or rejected, never
   silently flattened or dropped.
9. A rendered MP4 or proxy cannot satisfy the structured-project requirement.
10. The 78-second production film is the real acceptance gate for full parameter-level
    round-trip fidelity.

## 16. Implementation phase boundary

This document defines the format only. It does not authorize implementation, UI work,
service restart, migration of existing projects or modification of the live `:7788`
runtime. The next phase requires a separate implementation plan and Acrab's approval.
