# A point library as a layer

Decided 2026-10-24 (Acrab, decision 3A): a `.lup` is a data layer on Lumeri's
library Luc. Installing a point library is laying a layer on that Luc, and
taking the layer off brings Lumeri's own library back.

1.0 answered a library's name with the version installed in the Project and,
with none, Lumeri's own. In 1.1 Lumeri's own library is a Luc,
`lumeri.vector-motion-library` (capability `point.vector-motion.catalog`,
protocol `lumeri-point-library/v1`), and the agent reads it through
`luc_discover`, `luc_describe` and `luc_invoke`. A version someone installs
is a layer on that Luc ([permissions.md](permissions.md), "A layer on a
Luc"). The layer brings the catalog and the verification record the agent
reads. The Luc keeps the code the library is bound to, so a layer never
brings code.

A `.lup` bound to tools (`ops`) is not a layer. It becomes a library Luc of
its own ([guides.md](guides.md)).

## Making the layer

```console
python scripts/luc_cli.py convert motion.lup motion-layer/
python scripts/luc_cli.py pack motion-layer motion-layer.luc
cp motion-layer.luc ~/.lumeri/lucs/
```

`convert` sees that the `.lup` names code built into Lumeri and makes an
Extension of the Luc that holds that code. Its answer says `"kind": "layer"`.
The layer's id is `local.<library>` (`--id` gives another), and its version is
the `.lup`'s. It holds:

- `manifest.json`, which extends that Luc at the version it has now. Its one
  manifest patch adds `library` to the capability:
  `{"version", "catalog": "library/catalog.json", "verification": "library/verification.json"}`.
- `payload/0.json` and `payload/1.json`: the `.lup`'s catalog and
  verification record, which the layer adds as those two files.

Conversion refuses a `.lup` that would be other code:

- a name other than the library's (`--name` must keep it);
- a library none of Lumeri's own Lucs holds (only the vector-motion library
  does today);
- a shape, category, binding (`implementation`) or tool list that differs
  from Lumeri's.

It also refuses data a library cannot hold (see "What a layer may say").

Two things convert and then wait, and the converter says so:

- data not all in English, the language the agent reads, is left off
  (`E_LUC_LANGUAGE`) until it is;
- `lumeri.vector-motion-library` is switched off as it ships. The layer lies
  on it once its line in the `[luc]` table says
  `"lumeri.vector-motion-library" = true`.

## What a layer may say

The converter and the Runtime hold a layer to one rule
(`luc_point_library_adapter.layer_library`). It is 1.0's rule for a `.lup`,
within the bounds of a library Luc:

- `version` is major.minor.patch;
- the catalog is a JSON object of at most 256 KiB, with 1 to 128 `entries`.
  Each entry has its own kebab-case `id` of at most 64 characters and a
  one-line `title` of at most 120;
- the verification record is a JSON object of at most 64 KiB. Its
  `deterministic` is `true`, and it names its `taste_floor` and its `tests`.

A layer on this Luc adds only the two files its library names. It changes no
file of the Luc and none of the three pins that name its code
(`native_library`, `native_version`, `native_contract_hash`). Any other layer
is left off alone, and the Luc answers as it shipped:

| What the layer does | Code |
| --- | --- |
| Changes a pin | `E_LUC_CAPABILITY_INVALID` ("pin differs"); the interface gate's reason is `E_LUC_INTERFACE_CAPABILITY` ("never another binding") |
| Adds a file its library does not name, or replaces one of the Luc's | `E_LUC_INTERFACE_CLASS` |
| Brings data the agent could not read | `E_LUC_LANGUAGE` |
| Breaks the rule above | `E_LUC_LAYER_INVALID` or `E_LUC_CAPABILITY_INVALID` |

## How it answers

With the layer on, `luc_invoke` of `point.vector-motion.catalog` answers
`catalog` and `describe` the way Lumeri's library does. The answer has the
same shape, with the layer's version, catalog and verification. It is still
only a read:

- `create`, `adjust` and `apply` are refused (`E_LUC_POINT_LIBRARY_READ_ONLY`);
- an operation the library does not know answers `E_ARG`;
- nothing is written to the timeline, and nothing asks for an approval.

The data is read again at every call and checked against what loaded. If it
changed after loading, the call is refused (`E_LUC_LIBRARY_CHANGED`).

Set the layer's line in the `[luc]` table to `false`, or take its file out of
the folder, and at the next start Lumeri's own library answers again. A newer
version of the layer is a new file, and of several versions the newest lies.

## What it does not cover

- The Project registry (`libraries.json`, `iface:point.libraries/v1`) still
  installs and activates a `.lup` per Project, beside the layers. In 1.1
  nothing reads it for the library the agent uses. A Project install changes
  no answer, and taking a layer off does not touch the registry.
- Rendering stays the code built into Lumeri on its governed path. A layer
  changes what the agent reads about the library, not what renders.
- Only a library one of Lumeri's own Lucs holds takes a layer. The craft
  libraries (grade, camera, composition, rhythm, cuts) answer through
  `craft.ask` and are not point library Lucs.

## Held by

`tests/test_luc_library_layer.py` checks that:

- a `.lup` bound to the vector-motion code converts into a layer that carries
  only its data;
- a `.lup` naming code no Luc holds, or another category, or other tools, or
  too many entries, is refused;
- in the lucs folder the layer lies on Lumeri's Luc, and its read answers with
  the layer's data, while switched off or taken away it answers with Lumeri's
  own;
- a newer version answers as itself;
- a layer that changes a pin, brings another file, replaces one of the Luc's,
  is not English, or breaks the rule is left off alone;
- data that changed after loading is refused;
- the converter and the Runtime hold one rule;
- on a real Worker, the agent reads the layer through `luc_invoke`, and
  nothing is written.
