# Built-in Skills (`gemia/ai/skills/`)

This directory contains the built-in skill definitions for Lumeri.

## Architecture & Lifecycle

1. **v3 Engine (Current Runtime)**:
   - Each subdirectory contains a `SKILL.md` specification with YAML frontmatter.
   - The store loader (`gemia/skill_store.py`) scans these files to index built-in metadata, allowing the model to recall workflows via the `recall_skills` tool.
   - The underlying actions are executed through the registered native tools in `gemia/tools/` (`DISPATCHER`) and the craft libraries in `lumenframe/`.
   - **Contract integrity**: The contents of existing `SKILL.md` files (such as `timeline-ops/SKILL.md`) are referenced and validated by interface contracts (`contracts/interfaces/data/gemia.skill.context.v1.json`). Do not delete or mutate their text arbitrarily.

2. **1.1 Line (Luc Architecture)**:
   - Skills, craft point libraries, and workflows are packaged as first-class `.luc` bundles (`skill` kind, `library` kind).
   - For reference on how skills evolve in Lumeri 1.1, see [`docs/luc/guides.md`](../../../docs/luc/guides.md) and [`docs/luc/library-layers.md`](../../../docs/luc/library-layers.md).
