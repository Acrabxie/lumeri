# Gemini Feature Delegate Prompt

Use this template when the self-loop delegates one Gemia/Lumeri feature to Gemini CLI.
It is intentionally strict: Gemini should make a small, verifiable patch, not broaden
the architecture while solving one manifest primitive.

## Implementation Prompt

```text
You are implementing exactly one Gemia/Lumeri automation feature in:
/Volumes/Extreme SSD/gemia

Feature id:
<FEATURE_ID>

Goal:
<ONE_SENTENCE_GOAL>

Hard constraints:
- Do not commit.
- Do not touch unrelated dirty files.
- Net functional source/test change should stay under 400 lines.
- Write only these files unless focused tests prove another file is required:
  - <SOURCE_FILE>
  - <TEST_FILE>
  - gemia/video/__init__.py
  - gemia/registry.py only if planner visibility fails without it
- Do not update docs/automation/five_day_seed_checklist.json, agent_log.md, shared
  QUEUE.md, or memory files during implementation. Codex updates those only after
  verification passes.
- No network and no large outputs in the repo. Put reproduction outputs under
  /Volumes/Extreme SSD/GemiaTemp/<FEATURE_ID>-repro-<DATE>.

Atomic-write rule:
- If creating or replacing a Python file, build the full file content in a temp path
  next to the target, run a syntax compile on the temp content, then replace the
  target with os.replace().
- Never stream partial Python source directly into the final target path.
- If validation fails, leave the existing final target untouched and report the temp
  file path plus the error.

Read first:
- <NEIGHBOR_SOURCE_1>
- <NEIGHBOR_TEST_1>
- <NEIGHBOR_SOURCE_2>
- <NEIGHBOR_TEST_2>
- gemia/video/__init__.py
- gemia/registry.py

Implement:
1. <EXACT_SOURCE_REQUIREMENTS>
2. <EXACT_TEST_REQUIREMENTS>
3. Export the new public function in gemia/video/__init__.py.
4. Make planner visibility pass with the smallest registry change possible.

Run validation:
- python3 -m py_compile <SOURCE_FILE> <TEST_FILE>
- python3 -m pytest -q <TEST_FILE>

Return only:
- changed_paths
- validation_results
- any required Codex follow-up
```

## Read-Only Review Prompt

```text
Read-only review only. Do not edit files.

Review feature <FEATURE_ID> in /Volumes/Extreme SSD/gemia.
Inspect:
- <SOURCE_FILE>
- <TEST_FILE>
- gemia/video/__init__.py
- gemia/registry.py if touched

Check:
- the implementation is non-destructive and tied to real media probes where required
- inputs are normalized and bounded
- mapping-shaped configs preserve stable ids
- focused tests cover planner visibility, bad inputs, and two real-video reproductions
- no unrelated architecture broadening was introduced

Return compact JSON:
{
  "verdict": "PASS|FAIL",
  "confidence": 0.0,
  "required_fixes": [],
  "notes": []
}
```

## Why This Prompt Changed

The previous broad prompt let Gemini partially write a Python module before the MCP
transport closed. Codex then had to repair syntax and semantic issues. The new prompt
reduces that failure mode by separating implementation from bookkeeping/review and by
requiring atomic source replacement after compile checks.
