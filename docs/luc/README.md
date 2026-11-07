# Luc: format and interface reference

Luc is Lumeri's unit of extension (see [Luc](../../README.md#luc) in the main
README). The files here are its reference: the `.luc` package format, how a
Luc talks to the host and to the agent, what it may declare, and the interface
contracts of Lumeri's own modules, which a Luc can lay itself over.

They come from the Lumeri Video 1.1 source as of 2026-11-07. The engine in
this repository is still the 1.0 line, so nothing here runs from this
repository. The Luc host (`lumeri_runtime/`), Lumeri's Official Lucs
(`official_luc/`), the checker and packer scripts (`scripts/luc_*.py`) and the
tests that contracts cite are in that source and not here. Paths and test names
in these files point there.

## Read

| To | Read |
| --- | --- |
| Make a Luc | [developer-guide.md](developer-guide.md) |
| Write a Panel; the editor as Lucs | [workspace-panel-skill.md](workspace-panel-skill.md) |
| Write a Skill, Workflow or point library | [guides.md](guides.md) |
| Ship a tool the agent can call | [packaged-tools.md](packaged-tools.md) |
| Describe a value the creator sets by hand: a number, a point, a box | [interaction-primitives.md](interaction-primitives.md) |
| Know what reaches the agent, and how | [agent-protocol.md](agent-protocol.md) |
| Everything a Luc may declare | [permissions.md](permissions.md) |
| Package, version and layer `.luc` files | [distribution.md](distribution.md), [`schemas/`](schemas/) |
| Stand in for Lumeri's agent | [agent.md](agent.md), [agent-loop.md](agent-loop.md), [agent-parts.md](agent-parts.md) |
| Write code that runs in a sandbox | [tool-programs.md](tool-programs.md), [engine-programs.md](engine-programs.md), [search-programs.md](search-programs.md) |
| Install a point library as a layer | [library-layers.md](library-layers.md) |
| What an interface is and how it is kept | [interfaces.md](interfaces.md) |
| Every interface, in brief | [interfaces-catalog.md](interfaces-catalog.md) |

## Interface contracts

[`contracts/interfaces/`](../../contracts/interfaces/) holds one JSON file per
interface, in five areas: `agent`, `data`, `editor`, `machinery` and `tools`.
Each area has an `index.json`. Every contract follows
`interface-contract-v1.schema.json` and every index follows
`interface-index-v1.schema.json`, both in that folder.

## Not in this repository

- The `process` area of the contracts: how Lumeri's own processes and
  services connect (Accounts, the gateway and Workers, the App and desktop
  shells, the ChatGPT sign-in and bridge). All but one of them are
  `lumeri-only`, which only Lumeri replaces. The catalog still lists every one.
- `coverage.json` and `proof.json`, which tie the contracts to Lumeri's own
  test runs.
- How Lumeri runs its hub, signs its own Lucs and stores packages
  (`hub.md`, `official-channel.md`, `cloud.md`). The hub itself is public at
  [luc.lumeri.io](https://luc.lumeri.io).

## License

MIT, like the rest of this repository.
