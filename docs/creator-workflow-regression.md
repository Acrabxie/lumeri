# Creator workflow regression

This regression records a recommendation, not a product state machine:

`understand → plan → edit → inspect → revise → export`

It first renders a short review window, applies a harmless review marker through
`ProjectStore`, renders the same window again, and measures proxy-cache reuse.
Only this regression command uses `--full-export` to decide whether its optional
full-length measurement is included. This does not change Lumeri's direct-export
behaviour and does not require a user to preview before exporting.

## Public fixture

```bash
python scripts/run_creator_workflow_regression.py
```

The repository stores only
`tests/fixtures/creator_workflow/public_short.json`. The runner generates its
small video and geometric image at runtime in an external temporary directory;
no media binary is committed. The default run also constructs an equivalent
runtime-only LumenFrame document and completes a real native short-range render,
so the public check covers both the ProjectStore proxy path and LumenFrame's
native frame path.

## External project

The runner accepts `--project-state`, `--project-root` with `--project-id`, or
`--lumenframe-document`. LumenFrame input performs a real native short-range
render through `compile_to_layer_stack(..., default_resolver)`; an explicit full
export renders the whole document through the same native path. For ProjectStore
or project-state input, an explicit full export continues to use Lumeri's
existing `export_project` entrypoint. These are regression measurement paths,
not new product gates or required user steps.

An external manifest can keep private paths beside the private project:

```json
{
  "schema": "lumeri.creator-workflow-regression.input.v1",
  "source": {
    "kind": "lumenframe_document",
    "path": "picture-master.lumen.json"
  },
  "expected_duration_sec": 78,
  "review_window": {
    "start_sec": 37.5,
    "duration_sec": 8
  },
  "review_time_sec": 39.7
}
```

`source.kind` may also be `media`, `project_state`, or `project_store` (the last
uses `root` plus `project_id`). Relative paths resolve from the manifest's own
directory, not the current shell directory.

Outputs and receipts are rejected when their destination is inside the source
repository. Receipts contain only high-level duration, resolution, frame/cache
metrics, artifact hashes, and an input binding hash; source paths, titles,
project ids, and cache internals are omitted.
