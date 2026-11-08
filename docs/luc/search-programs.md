# A search program

Footage is found again by what someone wrote down about it: the library
search (`iface:library.search/v1`, module `lumeri.library.search`) answers a
question with the assets whose name or notes match it. The agent asks it
through `search_media`, and the editor through
`GET /sessions/{id}/library/search`. Its original is Lumeri's own search, a
case-insensitive substring over each asset's filename and the label, note,
category and tags of its notes, and it answers whenever no Luc does.

A Luc may bring a program that answers instead: one that matches by meaning,
forgives a misspelling, or ranks by what matters to its author. The original
answers again whenever the program breaks.

## The Luc

A capability of protocol `lumeri.search.program/v1` names the program:

```json
{
  "id": "search-program.meaning", "protocol": "lumeri.search.program/v1",
  "surface": "search-program", "name": "by-meaning",
  "title": {"en": "Search by meaning", "zh-CN": "按意思找"},
  "description": "Finds footage by what its notes mean, not only their words.",
  "executable": "bin/search", "platforms": ["darwin-arm64"]
}
```

- The Luc asks for `search.program`, which Lumeri's review of its exact bytes
  grants; without the review it is refused (`E_LUC_REVIEW_REQUIRED`).
- A Luc brings one search program (`E_LUC_SEARCH_CONFLICT`). With two Lucs
  bringing one, neither runs and the original answers.
- `executable` and `platforms` are as for an agent loop
  ([agent-loop.md](agent-loop.md)): a file of the Luc, at most 16 MiB,
  written under the data folder by its hash and checked every time it starts;
  on another system, or one without a sandbox, the original answers.

## How it runs

The question is checked by the original's rules first: text of at most 256
characters that is not only spaces, and a limit of 1 to 100. A question the
original refuses is refused the same way (`library_search_refused`) and
reaches no program.

The program then runs in the agent loop's sandbox (no network, no files, no
environment), one process per search, and is asked once, as a tool program
is ([tool-programs.md](tool-programs.md)):

```json
{"v": 1, "kind": "call", "tool": "search", "arguments": {
  "query": "sunset", "limit": 20, "assets": [
    {"asset_id": "asset_roof", "filename": "rooftop.mp4", "notes": [
      {"annotation_id": "an_gold", "label": "golden hour", "note": "", "category": "", "tags": ["sunset"]}]}]}}
```

`assets` is the library as the original reads it: every asset of the Project
that has an id, in the library's order, with its notes in theirs. It answers
within 10 seconds, on one line:

```json
{"v": 1, "kind": "result", "model": [
  {"asset_id": "asset_roof", "matched_on": ["label"], "annotation_ids": ["an_gold"]}]}
```

The hits are the answer, in the program's order. Each is held to what a
search can answer:

- at most `limit` of them, each one of the library's assets, named once;
- `matched_on` is some of `filename`, `label`, `note`, `category` and `tags`,
  each once;
- `annotation_ids` names only that asset's notes, each once, and names some
  exactly when something written in them matched (`matched_on` has more than
  `filename`).

The Runtime fills in each hit's `filename` from the library. A library too
large to send on one line (4 MiB) is the original's to search, and that is
not a break.

## When the program breaks

A program that cannot be placed or has changed, does not start, ends, answers
late or with more than a line, answers with anything but a result (a refusal
included: a question it is asked has none), or answers with hits that are not
an answer a search can give, is a break. The original then answers the same
search at once.

For the rest of that start the original answers every search, and the turn
whose tool searched hears `luc.module_fell_back` once per Session (`module:
"lumeri.library.search"`, `gate: "run"`, with the code: `E_LUC_SEARCH_START`,
`E_LUC_SEARCH_EXIT`, `E_LUC_SEARCH_PROTOCOL`, `E_LUC_SEARCH_RESULT`,
`E_LUC_SEARCH_LIMIT`, `E_LUC_SEARCH_DEADLINE`, `E_LUC_SEARCH_CHANGED`,
`E_LUC_SANDBOX`). Three breaks of the same bytes leave the Luc off for the
search at later starts until `luc_cli.py modules reset lumeri.library.search`.
`luc_cli.py modules` shows whether the original or a Luc answers.

## What it does not cover

- The program is that of the Luc set the start has loaded. Until the editor
  or the agent first asks for a Luc, the original answers.
- A search no turn asked for, the editor's own, tells no Session when the
  program breaks; the break is still counted and the original answers.
- The Runtime checks a hit against the library, not against the question: a
  program may match in ways the original cannot, and a wrong match that names
  real assets and notes is the program's answer.
- The same question asked twice is not held to the same answer.
- Only the search is open. What the library shows of an asset
  (`library_entry`) and where a placed asset goes (`place_on`) stay Lumeri's.

## Held by

`tests/test_luc_search_program.py`: the program reads each asset as the
original does; an answer a search can give is the hits in the program's
order, and seventeen that are not are breaks; a question the original refuses
reaches no program; a library too large for one line is the original's and no
break. The load gate refuses a search program without the review, two in one
Luc, and one that asks for another permission. With programs compiled for the
machine and run in the sandbox: a program answers in place of the original;
one that exits, does not speak JSON, refuses, or names an asset the library
does not have hands the same search to the original; one that does not answer
in time is a break. On a real Worker, the agent's own `search_media` finds the
program's hits; a program that breaks is run once, said once to the turn that
searched, counted once, and the original answers from then on; the editor's
search tells no Session; and a question the original refuses is refused the
same way with a program switched on.
