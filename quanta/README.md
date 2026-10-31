# Quanta

English | [简体中文](README.zh-CN.md)

Lumeri Quanta is the next-gen PPT: a presentation that plays, waits for the
viewer, branches and remembers what they chose. A Quanta work is one `.qua` file.

| Path | What it is | License |
|---|---|---|
| [`format/SPEC.md`](format/SPEC.md) | The `.qua` file format, version 4 | CC0 1.0 |
| [`format/examples/`](format/examples/) | Four sample files that pass Lumeri's checker | CC0 1.0 |

You can build a reader, writer, player or converter for `.qua` from the
specification alone. Quanta's layout engine is already in
[`gemia/quanta/`](../gemia/quanta/) and the canvas renderer, LumenFrame, in
[`lumenframe/`](../lumenframe/). The v4 reader, players and editor are not in this
repository yet; they will be added here under the MIT License.

## License

Everything in [`format/`](format/) is dedicated to the public domain under
[CC0 1.0](format/LICENSE). Use it in any product, under any license, without asking
and without attribution.

Everything else in this repository, including Quanta's code, is under the
repository's [MIT License](../LICENSE).

Product page: [quanta.lumeri.io](https://quanta.lumeri.io/)
