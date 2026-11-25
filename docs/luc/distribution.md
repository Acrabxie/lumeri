# Luc Distribution & Immutable Layering v0.1

> The file format of a Luc and of an Extension, and the rules by which layers
> compose. Written for Distribution v0.1 and still what the code does
> (`lumeri_runtime/luc_distribution`). What it does not say is where a layer
> lives on a machine and how a person switches one: that is "Where a layer
> lies on a machine" below, and [permissions.md](permissions.md).

## Layer hierarchy

The valid ownership chain is:

```text
Lumeri Host
└── Luc (targets Lumeri)
    └── Extension (lies on the Luc)
        └── Extension (lies on an Extension on that Luc, up to depth 8)
```

Decided 2026-11-21 (Acrab): an Extension may lie on another Extension. The
chain is `Lumeri Host → Luc → Extension → Extension → …`, and every Extension
still has exactly one parent: the Luc, or another Extension on that Luc. This
replaces the part of the 2026-10-11 decision that let an Extension lie only on
a Luc. The words keep their meaning: a Luc is one package and is never called an
extension; an Extension is a layer.

A standalone Luc has `kind: "luc"` and declares its Host APIs, permissions, editable boundary, capabilities, compatibility, and source tree. An Extension has `kind: "extension"` and exactly one `extends` record in one of two shapes:

- Direct on a Luc: `{ "luc": "<identity>", "version": "<compatible range>", "base_source_hash": "<sha256>" }`.
- Chained on another Extension on that Luc: `{ "luc": "<base luc identity>", "extension": "<parent extension identity>", "version": "<compatible range>", "base_source_hash": "<parent extension sha256>" }`.

The `extension` field must be a valid identifier distinct from the Extension's own `identity.id`. A chain is bounded to `MAX_CHAIN_DEPTH = 8` layers (depth 1 is direct on the Luc; deeper chains fail with `E_LUC_CHAIN_DEPTH`). An Extension still cannot carry a Host declaration or target Lumeri directly.

Lumeri Core and Luc sources are read-only inputs to this module. Persistent user changes to a Luc or an Extension are authored as a new immutable Extension version; direct installed-package editing is not an operation in the API. A customization that targets Lumeri directly needs its own Luc first, then an Extension (or chain of Extensions) may target that Luc.

## Immutable source and Extension authoring

Once packaged, a Base Luc or Extension version is identified by its source hash and cannot be replaced by another source at the same identity/version in the local registry. A customization creates a separate Extension identity/version. The authoring API requires an absent destination, so creating a later Extension version cannot rewrite the previous one. Resolver inputs are read-only and all operations apply to derived copies.

The authoring seam is `create_extension_source(parent, extension_id, version, output)`, reached as `luc_cli.py extension create`: it reads the parent package (either a standalone Luc or an installed/packaged Extension, emitting the three-key or four-key `extends` shape accordingly), creates a new Extension scaffold, and leaves the parent and prior Extension versions unchanged. `manifest_patch` and `source_patch` write one change each against the target parent state (`resolve_luc(luc, chain_of_ancestors)` when authoring on a chained Extension). The agent has no tool that makes a layer; a person or an agent with a shell uses these. There is no `edit_installed_luc` operation. A successful draft still requires package verification, resolution, tests, authority review, and explicit activation authorization if authority expands.

## One `.luc` package envelope

Both standalone Lucs and distributed Extension layers are packaged in a `.luc` ZIP envelope with a canonical `manifest.json`. The manifest `kind` selects their semantic role. There is no second `.extension`/`.luext` installer path.

The packer normalizes and sorts relative paths, rejects traversal, symlinks, duplicate paths, and special files, excludes named VCS/cache/temp files, writes canonical JSON, stores regular files in lexical order, uses fixed ZIP timestamps and normalized `0644`/`0755` modes, and uses uncompressed ZIP entries for byte-stable output. Verification bounds file/entry/total sizes and compression ratios, then validates the manifest, file list, file hashes, modes, and Extension payload references.

Identities are distinct:

- `source_hash`: SHA-256 of canonical JSON for the manifest without `integrity`, including the sorted file digest/mode index. Each indexed digest is checked against the packaged bytes.
- `artifact_hash`: SHA-256 of the complete `.luc` bytes. This identifies a transport envelope and does not claim publisher authenticity.
- `resolved_source_hash`: SHA-256 of the derived Luc manifest/file tree after patches.
- `resolved_hash`: SHA-256 of the ordered immutable layer identities plus resolver version and runtime/Host API policy compatibility identity.

SHA-256 is integrity, not a signature. v0.1 does not publish to or claim trust from production Luc Cloud.

## Immutable Extension operations

Extensions can carry three operation families:

1. `manifest_patches`: JSON Pointer `add`, `replace`, or `remove`, with a hash of the original parent value. Root identity, role, parent, file index, and integrity cannot be changed.
2. `file_operations`: add, replace, or delete with exact parent and result hashes. Added/replacement bytes live inside the Extension `.luc` payload and are never read from an arbitrary external path during resolution.
3. `source_patches`: UTF-8 text anchors with parent file hash and hashes of the up-to-64-byte before/after context. The anchor must match exactly once and context must match; line-number-only edits are not accepted.

Every new Extension version is a separate source directory and identity/version reference. The authoring and rebase APIs reject an already existing destination; they never rewrite old Extension bytes.

## Resolver and conflicts

`resolve_luc(base, ordered_extensions, runtime_version, host_grants)` verifies every artifact, Base identity (`E_LUC_PARENT_ID`), parent chain placement (each chained Extension must appear after its parent, matched by `base_source_hash` and `extends.extension`; missing or later parent raises `E_LUC_PARENT_REQUIRED`, mismatched hash/version/id raises `E_LUC_PARENT_HASH`/`E_LUC_PARENT_VERSION`/`E_LUC_PARENT_ID`, and depth above 8 raises `E_LUC_CHAIN_DEPTH`), and compatibility before composing a derived in-memory tree. The Base and Extension inputs are never written. Each layer's operations are checked against the derived state of the Base Luc plus all of its own ancestor Extensions (which is the Base Luc itself for a direct layer). The resolver applies ordered patches to derived copies, recomputes the manifest file index and derived source hash, computes Host authority relative to the original Base Luc, and validates the result before returning it. Platform constraints intersect across the Base Luc and every layer in the list, while `RESOLVER_VERSION` (`"0.1.0"`) and the `resolved_hash` formula stay unchanged so flat direct-on-Luc lists resolve byte-for-byte identically.

Conflict results are deterministic:

| Classification | Meaning |
| --- | --- |
| `cleanly_composable` | Disjoint operation targets compose in the profile's explicit order. |
| `chain_override` | A descendant Extension overrides a manifest field, file, or source target changed by an ancestor in its own chain; the descendant's result stands. |
| `compatible_same_value` | Non-ancestor layers set the same semantic target to the same operation/value; it is applied once and reported. |
| `incompatible_same_field` | Non-ancestor layers set an overlapping manifest field or file target differently; resolution fails. |
| `source_patch_conflict` | A source anchor/context or overlapping source/file patch cannot be applied cleanly; resolution fails. |
| `authority_conflict` | A resulting declaration is unsupported, malformed, or violates the Host API/editable policy; Host validation fails closed. |

The implementation does not use last-write-wins across independent branches. A layer may override an earlier layer's target only when every prior layer that touched that target is strictly an ancestor in its own chain. Non-compatible conflicts raise `ResolutionConflict`; no partial result is returned, leaving any active immutable selection untouched.

## Multiple Extensions

Profiles supply an explicit ordered Extension list with parents before their children. The order is part of the resolved identity. Disjoint operations compose; ancestor-descendant chains compose sequentially with `chain_override` keeping the descendant's value; identical values on the same semantic target between non-ancestor layers apply once and are reported; incompatible same-field, source-anchor, or file-target changes between non-ancestor layers (or same-array add/remove operations across non-ancestors) fail closed. Reordering Extensions changes the identity even when the current operation set happens to produce equivalent bytes, preserving the requested composition provenance.

## Derived Resolved Luc

The Resolved Luc is a validated derivative, never a source of truth. Its source hash covers the derived manifest and file tree. Its separate resolved identity is derived from the Base source hash, ordered Extension source hashes, Resolver version, Runtime compatibility identity, and Host API policy fingerprint. Re-running the same ordered inputs against the same compatibility policy reproduces the same identity. Consumers can discard a cached derivative and resolve again from the immutable layers.

## Where a layer lies on a machine

A layer reaches a running Runtime in one of two ways. Both end in the same
Resolver and the same Host checks, and both are skipped in safe mode.

| | The lucs folder | An activation profile |
| --- | --- | --- |
| How | Put the `.luc` file in `~/.lumeri/lucs`. | `luc_cli.py install-local`, then `activation select --profile <luc id>`, against the Worker's Registry. |
| Lies on | A Luc from the folder, or one of Lumeri's own made only of the open protocols. | Any of Lumeri's own Lucs. |
| Switched | Its line in the `[luc]` table of the config file. | The Registry's enabled state. |
| More authority | Never: the layer is left off (`E_LUC_REAUTH_REQUIRED`). A folder layer on a Luc whose profile was approved for more is held to what was approved. | What the creator approved at `activation select` (`--authorized-activation-hash`), for that exact resolution. Never a reserved permission or one that needs Lumeri's review (`E_LUC_PERMISSION_RESERVED` in `layer_notices`). |
| Order | Parent-first by layer id (lowest id whose parent is the Luc or already laid, including profile layers under `under`). | The order the profile gives (parents before children); when creator layers form one line on an Official Luc, Lumeri's `agent.<luc id>` layer lies on the top one (always last) and steps aside at load time if incompatible with creator layer changes until re-laid at Lumeri's next turn. |
| When it does not fit | That layer alone is left off and named in `not_loaded`. | The Luc loads without any of the profile's layers and says so in `layer_notices`. |

The folder is the way meant for a person; its rules are in
[permissions.md](permissions.md), "A layer on a Luc". The profile is the
older way and is described in `official-channel.md` (not in this repository).
Tool code is never taken from a layer by either way: it comes only from the
artifact the signed index names (`E_LUC_TOOL_CODE`).

## Authority recomputation

Extensions have no principal authority of their own. The resolver passes the composed Luc manifest into the existing `lumeri_runtime.luc_host_api.HostAPIRegistry`, which recomputes requested/effective Host APIs, declared/granted permissions, and requested/effective editable boundary. It calls the existing capability-effect validation and therefore inherits the Host's protected-state rules rather than maintaining a second policy table.

The resolver compares Base authority with composed authority. Added requested/effective APIs, permissions, editable target types, properties, or areas are listed in `authority_diff.expansions` and set `requires_reauthorization`. `ActivationStore.plan_activation` also compares the candidate with the currently selected receipt, so an authority expansion introduced by a Base rebase remains visible after the candidate Extension is repacked and resolved. Its `authorization_hash` binds the profile, previous activation, candidate resolution, safe-mode state, and authority diff. `activate` recomputes this plan while holding the selection lock and accepts only that exact token; a token obtained against an older active selection is stale. The current source contract does not implement a user-facing consent UI or persistent Host grant ceremony.

Activation receipts are immutable and self-authenticate all semantic receipt fields in their `activation_id`; changing authority or lineage data without a matching identity is treated as registry corruption. `activation plan` in the local CLI previews this decision without changing the selected activation.

## Ordered profiles and installation records

`LayerProfileStore` records explicit desired Extension order and a resolved hash. Attach and detach first resolve the proposed list; attach/detach change only this profile selection, not package source and not live Runtime activation. The profile reports when fresh authority authorization would be required.

`ActivationStore` writes immutable receipts containing Base identity/source hash, ordered Extension identities/source hashes, resolved hashes, effective Host authority, runtime/resolver identity, and safe-mode state. The receipt identity also binds the exact authority result and safe-mode selection. A small atomically replaced profile cursor selects an activation receipt. Rollback and redo move that cursor between previous immutable selections; they do not reverse-patch package trees. Concurrent profile updates are serialized with an OS file lock. A Base-only safe-mode receipt cannot contain Extension layers.

## Safe mode

`disable_user_layers` preserves the desired Extension list for later restoration but records an empty active Extension list and a Base-only safe-mode hash. `restore_user_layers` resolves the preserved layers again. Both are selection operations and leave all Base and Extension source bytes untouched. This is the source-level mechanism for disabling user customization while retaining the official/default Luc set; it is not a product recovery UI.

## Luc Cloud and independent artifact sources

Luc Cloud is the planned canonical host for published Luc identities and immutable versions. A hosted version record is expected to bind owner, package name and version, source hash, manifest hash, publication timestamp, README, and the immutable source file tree. Updating creates another version; it never replaces the published bytes behind an existing version. This source implementation does not create or publish those Cloud records.

The distribution boundary accepts verified `.luc` bytes independently of transport. A package may come from a local file, Git repository, LAN, direct transfer, another registry, or the future Luc Cloud service; origin is recorded locally, while source identity and package validation remain transport-independent. No origin is trusted merely because of its transport.

## Local registry and decentralized sources

`LocalRegistry` models immutable version references over content-addressed local `.luc` objects. Each local record keeps declared author, manifest hash, source hash, artifact hash, README path, local registration timestamp, and origin; the timestamp is not a Cloud publication timestamp. Reinstalling the identical identity/version/hash is idempotent; the same role/id/version with a different source hash is rejected. Local files and caller-provided registries are accepted as byte sources. A Git, LAN, direct-transfer, or future Luc Cloud transport can feed the same verified `.luc` boundary, but network clients and production Cloud publication are out of scope.

Each active receipt records source identities and origin at import time in the local registry ref. Resolved output remains a cache/derivative; the Base + ordered Extension identities remain the reproduction source of truth.

## Rebase and Agent handoff

`plan_rebase(old_base, new_base, old_extension, new_version, ...)` produces a deterministic, non-mutating plan with unchanged operations, portable operations, conflicts, upstream-absorbed operations, authority changes, and test requirements. A Base value that already equals the Extension result is classified `upstream_absorbed`, and the new Extension candidate drops that redundant operation. Changes at the same semantic target become conflicts. A source patch may be portable only when its exact anchor/context still applies to the new content.

`plan_chain_rebase(old_base, new_base, extensions, *, new_versions, replaced=(), runtime_version, host_grants)` extends deterministic planning bottom-up across an ordered Extension chain (`lumeri.luc.chain-rebase-plan/v1`). Each layer is planned against its resolved parent snapshot (`new_base` for direct layers, or its parent's result in the same pass for chained layers): if an ancestor is blocked, descendants are marked `blocked_parent` with `blocked_by` naming the lowest blocked ancestor; author-supplied `replaced` layers are adopted verbatim when they match the new parent (`replaced`) or marked `blocked_conflict` (`replacement_parent_mismatch`); layers whose parent hash is unchanged are kept as `current`; and layers needing migration produce a per-layer `lumeri.luc.rebase-plan/v1` (`plan`, with optional `old_parent`/`new_parent` while `old_base`/`new_base` stay the Base Luc) without affecting independent branches. Authority changes compare old chain vs new chain.

When a plan has no conflicts, its candidates are kept in memory pinned to their new parent's `source_hash`. `write_rebase_candidate` materializes a new version only into an absent destination; old Base, new Base, and old Extensions remain read-only. `agent_handoff()` explicitly names those immutable inputs, the new Extension id/version write targets, and `activation_allowed: false`. Candidates still need package verification, resolution, Extension tests, authority review, and an explicit authorization if authority expands. Rebase does not auto-activate.

## Local commands

All local package, Registry, layer, and activation commands use the same `scripts/luc_cli.py` entrypoint as the existing Runtime interaction commands. Local commands below operate on local files and Registry state; they do not contact Luc Cloud or mutate a live Runtime. `list --session ...`, `discover`, `describe`, `status`, and `cancel` are the explicit Runtime routes and require a session.

```bash
python scripts/luc_cli.py pack ./my-luc ./my-luc-1.0.0.luc
python scripts/luc_cli.py verify ./my-luc-1.0.0.luc
python scripts/luc_cli.py inspect ./my-luc-1.0.0.luc
python scripts/luc_cli.py install-local ./my-luc-1.0.0.luc --registry ~/.local/share/luc-registry
python scripts/luc_cli.py list --registry ~/.local/share/luc-registry
python scripts/luc_cli.py resolve --base com.example.base@1.0.0 --extension user.acrab.layout@1.0.0 --runtime 1.0.47 --grant project.read --registry ~/.local/share/luc-registry
python scripts/luc_cli.py extension create com.example.base@1.0.0 user.acrab.layout 1.0.0 ./extension-v1 --registry ~/.local/share/luc-registry
python scripts/luc_cli.py extension inspect user.acrab.layout@1.0.0 --registry ~/.local/share/luc-registry
python scripts/luc_cli.py extension attach --base com.example.base@1.0.0 --extension user.acrab.layout@1.0.0 --registry ~/.local/share/luc-registry --runtime 1.0.47 --grant project.read
python scripts/luc_cli.py extension detach --base com.example.base@1.0.0 --id user.acrab.layout --registry ~/.local/share/luc-registry --runtime 1.0.47 --grant project.read
python scripts/luc_cli.py safe-mode on --base com.example.base@1.0.0 --registry ~/.local/share/luc-registry --runtime 1.0.47 --grant project.read
python scripts/luc_cli.py safe-mode off --base com.example.base@1.0.0 --registry ~/.local/share/luc-registry --runtime 1.0.47 --grant project.read
python scripts/luc_cli.py activation plan --base com.example.base@1.0.0 --registry ~/.local/share/luc-registry --runtime 1.0.47 --grant project.read
python scripts/luc_cli.py activation rollback --registry ~/.local/share/luc-registry
python scripts/luc_cli.py activation redo --registry ~/.local/share/luc-registry
```

`permissions` prints everything a Luc may declare; with a `.luc` file or a source folder it prints what that Luc declares and whether it would load from the `lucs` folder beside the config file, which is how a Luc that is not Lumeri's own reaches a Runtime ([permissions.md](permissions.md)). That folder is separate from the `--registry` these commands operate on. `extension create <parent> ...` also accepts an installed or packaged Extension as `<parent>` (emitting a four-key `extends` record), and `conform <extension> [--under <ancestor> ...]` accepts repeatable bottom-up `--under` ancestor layers when checking a chained Extension against its Luc's interface.

`install-local` only stores a verified immutable object. Profile attach/detach changes the desired ordered layer selection. `safe-mode on/off` selects or restores local profile state while preserving desired Extension references; it does not change live Runtime state. `activation plan` is read-only. `activation select` is a separate local activation record operation; when authority expands, it returns `REAUTH_REQUIRED` and an `authorization_hash`, which must be supplied as `--authorized-activation-hash`. That token becomes stale if the selected activation changes. No CLI command publishes to Luc Cloud or installs a Runtime.

Machine-readable Draft 2020-12 contracts are in `docs/luc/schemas/`: artifact manifest, ordered layer profile, resolved identity, authority diff, rebase plan, chain rebase plan, activation receipt, and local registry entry.

## v0.1 limits

- The Distribution module itself has no Luc Cloud transport, remote publish, signing, account model or marketplace. Those came later, outside it (`cloud.md` and `official-channel.md`, neither in this repository). The Runtime does load layers now: see "Where a layer lies on a machine".
- Extension chains are bounded to depth 8 (`MAX_CHAIN_DEPTH = 8`); Luc Cloud and Luc Hub accept and store chained Extensions up to that depth (subject to the ancestor namespace and public listing rules in `cloud.md` and `hub.md`, neither in this repository), and Lumeri's own `agent.<luc id>` layer lies on the top creator Extension when creator layers form one line (or directly on the Luc when there is no line).
- No live code execution or arbitrary filesystem/network Host grant is added.
- A local registry/profile is source-level state only; it is not synchronized and does not provide cross-process Runtime crash recovery.
- Rebase conflict assistance is deterministic planning plus an Agent handoff contract. It does not run a migration Agent or execute Extension code.
- Host authorization is represented as an activation-plan token in the source API. Product consent UI and durable Host grant persistence remain an integration task.
