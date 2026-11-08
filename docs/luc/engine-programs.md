# An engine program

Some of Lumeri's work is done by a program it runs in the code sandbox:
FFprobe measures a piece of media, FFmpeg grabs a thumbnail or encodes,
`lumeri-engine` renders and does the rest of the engine's work, and whisper
transcribes, run by the engine inside its roughcut. Each of these is a module
with its own interface, and its original is that program as the Runtime ships
it, or as the machine has it:

| module | interface | stands in for |
| --- | --- | --- |
| `lumeri.media.facts` | `iface:media.facts/v1` | `ffprobe` |
| `lumeri.media.thumbnails` | `iface:media.thumbnails/v1` | `ffmpeg` (one frame) |
| `lumeri.encode.backend` | `iface:encode.backend/v1` | `ffmpeg` (an encode) |
| `lumeri.lumen.canvas` | `iface:lumen.canvas/v1` | `lumeri-engine lumen-canvas` |
| `lumeri.render.backend` | `iface:render.backend/v1` | `lumeri-engine quanta`, `lumeri-engine lumenframe` |
| `lumeri.timeline.render` | `iface:timeline.render/v1` | `lumeri-engine timeline-render` |
| `lumeri.engine.gemia` | `iface:engine.gemia/v1` | `lumeri-engine`, for its other 13 subcommands |
| `lumeri.transcriber.backend` | `iface:transcriber.backend/v1` | `whisper-cli`, inside the engine's roughcut |

A Luc may bring a program that stands in for one of them. The original
answers whenever no Luc does, and again whenever the program breaks.

## The Luc

A capability of protocol `lumeri.engine.program/v1` names the module and the
program:

```json
{
  "id": "engine-program.probe", "protocol": "lumeri.engine.program/v1",
  "surface": "engine-program", "module": "lumeri.media.facts", "name": "fast-probe",
  "title": {"en": "Fast probe", "zh-CN": "快速测量"},
  "description": "Measures media the way FFprobe does, faster.",
  "executable": "bin/probe", "platforms": ["darwin-arm64"]
}
```

- The Luc asks for `engine.program`, which Lumeri's review of its exact bytes
  grants; without the review it is refused (`E_LUC_REVIEW_REQUIRED`).
- `module` is one of the eight above. For `lumeri.engine.gemia`, and for
  `lumeri.render.backend`, `subcommands` may name the ones the program takes;
  without it the program takes all of the module's.
- A Luc brings one program a module (`E_LUC_ENGINE_CONFLICT`). With two Lucs
  answering for the same module, neither runs and the original answers.
- `executable` and `platforms` are as for an agent loop
  ([agent-loop.md](agent-loop.md)): a file of the Luc, at most 16 MiB,
  written under the data folder by its hash and checked every time it starts;
  on another system, or one without a sandbox, the original answers.

## How it runs

The program is started exactly as the original would be, in its place:

- the same arguments, after the program's own path; for `lumeri-engine` that
  is `<subcommand> <request.json> [<output-root>]`, and for FFprobe and FFmpeg
  their own command line;
- the same environment (`LC_ALL`, `PATH`, `TMPDIR`, and what the profile
  names, such as `LUMERI_FFMPEG_PATH`), the Project as its working folder;
- the same sandbox profile, with its time, memory, process and output limits
  and the executables it may run, except that the program may write only the
  session scratch, never the Project, even where the original may;
- no network, as for the original.

What it leaves is read as the original's would be: one JSON value on stdout
where the original answers there, and every file it declares. Then the
module's own check:

| module | the program's answer must also be |
| --- | --- |
| `lumeri.media.facts` | FFprobe's JSON: an object with a `streams` list and a `format` object that say what a timeline needs |
| `lumeri.media.thumbnails` | a JPEG (`thumb.jpg` starts with `FF D8 FF`) |
| `lumeri.encode.backend` | an MP4 (`encoded.mp4` has `ftyp` at byte 4) |
| `lumeri.transcriber.backend` | whisper's JSON (`cache/roughcut-whisper.json`): an object with a `transcription` list whose segments and words have their `text` and their `offsets` in milliseconds, starting before they end and ending at most a second after the sound it was handed (`cache/roughcut-audio.wav`), and whose words' `p` is a probability |

## A program the engine runs

whisper is not started by the Runtime but by the engine, inside the engine's
roughcut, which finds it by `LUMERI_WHISPER_CLI`. A program for
`lumeri.transcriber.backend` is named there instead: Lumeri's own engine runs
the roughcut as always, and starts the program where it would have started
whisper, with whisper's arguments
(`-m <model> -f <sound> -ojf -of <output-base> -np -l <language> -ng`), in
the transcription sandbox, which may write only the session scratch. The
program answers as whisper does: it writes `<output-base>.json` and exits
with 0. The engine reads that file and goes on from there, and the program's
words are the roughcut's.

When the engine's run with the program fails, the engine's working files are
cleared, and the original runs the same roughcut at once, with whisper; it
reads nothing the program wrote. The failure was the program's break only if
that run answers: a roughcut that whisper cannot do either (a video with no
sound) is the request's failure, and nothing is counted. When the engine's run
answers and whisper's JSON is not what whisper would write, that is a break
whatever the original's run then does.

The program runs only where whisper.cpp and its model are installed: on a
machine without either, the roughcut answers 503 naming what is missing, as
it does without a program, and a program cannot bring a model of its own.

## When the program breaks

A program that cannot be placed or has changed, does not start, exits with
another status than 0, outruns a limit, or leaves an answer that is not what
the original's would be, is a break. The original then runs the same request
at once, and the creator gets its answer.

For the rest of that start the original answers for that module, and each
Session hears `luc.module_fell_back` once (`module` is the module, `gate:
"run"`, with the code: `E_LUC_ENGINE_START`, `E_LUC_ENGINE_EXIT`,
`E_LUC_ENGINE_RESULT`, `E_LUC_ENGINE_LIMIT`, `E_LUC_ENGINE_DEADLINE`,
`E_LUC_ENGINE_CHANGED`). Three breaks of the same bytes leave the Luc off for
that module at later starts until `luc_cli.py modules reset <module>`.
`luc_cli.py modules` shows, for each module, whether the original or a Luc
answers.

The creator stopping the request is not a break: the program is stopped, and
nothing runs in its place.

## What it does not cover

- The programs are those of the Luc set the start has loaded. Until the
  editor or the agent first asks for a Luc, the original answers.
- A program that breaks after writing in the scratch leaves what it wrote, and
  the original runs over it. Whatever those files make, the program could have
  answered itself.
- For the engine's modules, an answer that is one JSON value with its declared
  files meets the Runtime's other checks only after the run; a failure there
  is the request's, and the original does not run it again.
- Where an engine program answers for the roughcut subcommand, it is handed
  Lumeri's own whisper, not a transcriber program.
- A run with a transcriber program that fails costs a second run of the
  roughcut, the original's; each has the profile's 600 s.
- The library search is not an engine: its program is a search program
  ([search-programs.md](search-programs.md)).

## Held by

`tests/test_luc_engine_programs.py`: the load gate refuses an engine program
without the review, one for no such module or subcommand, two for one module,
and one that asks for another permission; each run is the module's only when
it is the module's file and subcommand, and every subcommand of the engine is
exactly one module's. With programs compiled for the machine and run in the
media sandbox: a program answers in place of FFprobe from the same blob; each
way of breaking hands the same request to FFprobe; a program cannot write the
Project where FFprobe may; a thumbnail that is not a JPEG is a break; a stop
is not a break. On a real Worker, the facts the library measures are the
program's, and a program that breaks is run once, said once, counted once,
and FFprobe answers from then on. With a program compiled for the machine in
whisper's place, run by the real engine in the transcription sandbox: a
Worker's roughcut is the program's words; a program that exits with another
status, or says a word ends after the sound, hands the same roughcut to
whisper from a clean scratch and is run once, said once and counted once; and
a roughcut whisper could not do either is no break. Whisper's own JSON passes
the check, and one with no text or offsets, offsets backwards or past the
sound, or a `p` above 1, does not.
