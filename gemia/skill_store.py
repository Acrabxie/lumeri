"""Skill store — save, list, and load reusable v2 skills.

A v2 skill is a plan template derived from a successful task run.
It strips the concrete input/output paths and preserves the step definitions
so the same pipeline can be applied to any video.

Skills are stored as JSON in ``skills_v2/`` with metadata (name, description,
origin task_id, created_at).

Usage::

    from gemia.skill_store import SkillStore
    store = SkillStore()

    # Save from last run
    store.save_from_task(name="赛博朋克调色")

    # List
    for s in store.list_skills():
        print(s["name"], s["description"])

    # Load and execute
    skill = store.load("赛博朋克调色")
    engine.execute(skill["plan"], input_path, output_path)
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def distilled_skills_dir() -> Path:
    """Return the directory where DISTILLED (user-authored) skills are stored.

    Resolution order:
    1. ``GEMIA_SKILL_STORE_DIR`` environment variable (used by tests via
       monkeypatch to redirect into a tmp dir).
    2. ``~/.gemia/skills`` — the durable per-user store.

    The directory is created on demand by callers that write into it.
    """
    override = os.environ.get("GEMIA_SKILL_STORE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemia" / "skills"


def _distill_slug(text: str) -> str:
    """Slug for a distilled-skill filename (keeps CJK, reuses :func:`_slugify`)."""
    return _slugify(text)


def _coerce_steps(steps: Any) -> list[str]:
    """Normalize the ``steps``/``ops``/``recipe`` arg into a list of strings."""
    if steps is None:
        return []
    if isinstance(steps, str):
        text = steps.strip()
        return [text] if text else []
    if isinstance(steps, (list, tuple)):
        out: list[str] = []
        for item in steps:
            if item is None:
                continue
            if isinstance(item, str):
                value = item.strip()
                if value:
                    out.append(value)
            else:
                out.append(json.dumps(item, ensure_ascii=False))
        return out
    return [str(steps)]


class DistilledSkillStore:
    """Durable store for skills DISTILLED ("沉淀") from completed agent tasks.

    Unlike :class:`SkillStore` (which derives plan templates from v2 task
    runs), this store captures a compact human/agent-authored recipe:
    ``{name, when_to_use, steps, notes}``.  One JSON file per skill name,
    so re-distilling the same name UPDATES in place (idempotent, no dups).

    Stored under :func:`distilled_skills_dir` (``~/.gemia/skills`` or the
    ``GEMIA_SKILL_STORE_DIR`` override).
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir).expanduser() if root_dir else distilled_skills_dir()

    def _ensure_dir(self) -> Path:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        return self.root_dir

    def distill(
        self,
        name: str,
        *,
        when_to_use: str = "",
        steps: Any = None,
        notes: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Distill a completed reusable task into a durable skill.

        Idempotent by ``name``: re-distilling the same name overwrites the
        existing file (no duplicates) while preserving ``created_at``.

        Returns the stored skill dict (including ``file`` path).
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("distill requires a non-empty 'name'")

        self._ensure_dir()
        slug = _distill_slug(clean_name)
        path = self.root_dir / f"{slug}.json"

        created_at = datetime.now(timezone.utc).isoformat()
        if path.exists():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
                created_at = prior.get("created_at", created_at)
            except Exception:
                pass

        skill = {
            "name": clean_name,
            "source": "distilled",
            "when_to_use": str(when_to_use or "").strip(),
            "steps": _coerce_steps(steps),
            "notes": str(notes or "").strip(),
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
            "created_at": created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(skill, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = dict(skill)
        result["file"] = str(path)
        return result

    def list_distilled(self) -> list[dict[str, Any]]:
        """Return all distilled skills (ignoring dotfile/AppleDouble sidecars)."""
        if not self.root_dir.exists():
            return []
        skills: list[dict[str, Any]] = []
        for p in sorted(self.root_dir.glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            data.setdefault("source", "distilled")
            data["file"] = str(p)
            skills.append(data)
        return skills

    def load(self, name: str) -> dict[str, Any]:
        """Load a distilled skill by exact name (or slug filename)."""
        target = str(name or "").strip()
        for data in self.list_distilled():
            if data.get("name") == target:
                return data
        path = self.root_dir / f"{_distill_slug(target)}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data["file"] = str(path)
            return data
        raise FileNotFoundError(f"Distilled skill not found: {name}")


def _library_skills() -> list[dict[str, Any]]:
    """Return the static library skills as recall-shaped dicts.

    Sourced from :func:`gemia.ai.skill_router.load_skill_metadata` so recall
    can surface built-in skills alongside user-distilled ones.  Import is
    done lazily and defensively so the store stays usable even if the AI
    package or its YAML deps are unavailable.
    """
    try:
        from gemia.ai.skill_router import load_skill_metadata
    except Exception:
        return []
    try:
        metadata = load_skill_metadata()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for meta in metadata.values():
        triggers = list(meta.primary_triggers) + list(meta.secondary_triggers)
        out.append({
            "name": meta.id,
            "source": "library",
            "when_to_use": meta.description,
            "steps": list(meta.primitives),
            "notes": "",
            "tags": list(meta.primary_triggers),
            "triggers": triggers,
        })
    return out


def _relevance(skill: dict[str, Any], query_terms: list[str], query: str) -> float:
    """Score a skill against a lowercased query (substring + token overlap)."""
    haystacks: list[tuple[str, float]] = [
        (str(skill.get("name", "")), 3.0),
        (str(skill.get("when_to_use", "")), 2.0),
        (" ".join(str(t) for t in skill.get("tags", []) or []), 2.0),
        (" ".join(str(t) for t in skill.get("triggers", []) or []), 2.0),
        (" ".join(str(s) for s in skill.get("steps", []) or []), 1.0),
        (str(skill.get("notes", "")), 1.0),
    ]
    score = 0.0
    for text, weight in haystacks:
        lowered = text.lower()
        if not lowered:
            continue
        if query and query in lowered:
            score += weight * 2.0
        for term in query_terms:
            if term and term in lowered:
                score += weight
    return score


def recall_skills(
    query: str,
    *,
    store: "DistilledSkillStore | None" = None,
    include_library: bool = True,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Recall the most relevant saved/library skills for ``query``.

    Searches BOTH user-distilled skills (from :class:`DistilledSkillStore`)
    and the static skill library, ranks by relevance, and returns up to
    ``limit`` skills (name + when_to_use + steps + source).  When the query
    is empty, returns the most recent distilled skills first.
    """
    store = store or DistilledSkillStore()
    candidates: list[dict[str, Any]] = list(store.list_distilled())
    if include_library:
        candidates.extend(_library_skills())

    query = str(query or "").strip().lower()
    query_terms = [t for t in re.split(r"[\s,，。、/]+", query) if t]

    if not query:
        # No query: prefer freshly distilled skills, then library order.
        def _recency(skill: dict[str, Any]) -> tuple[int, str]:
            is_distilled = 0 if skill.get("source") == "distilled" else 1
            return (is_distilled, str(skill.get("updated_at") or ""))
        candidates.sort(key=_recency, reverse=False)
        distilled = [s for s in candidates if s.get("source") == "distilled"]
        distilled.sort(key=lambda s: str(s.get("updated_at") or ""), reverse=True)
        others = [s for s in candidates if s.get("source") != "distilled"]
        ranked = distilled + others
        return [_recall_view(s) for s in ranked[:limit]]

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for idx, skill in enumerate(candidates):
        score = _relevance(skill, query_terms, query)
        if score > 0:
            scored.append((score, idx, skill))
    # Highest score first; stable on original order for ties.
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [_recall_view(item[2]) for item in scored[:limit]]


def _recall_view(skill: dict[str, Any]) -> dict[str, Any]:
    """Project a stored skill into a compact recall result."""
    return {
        "name": skill.get("name", ""),
        "source": skill.get("source", "distilled"),
        "when_to_use": skill.get("when_to_use", ""),
        "steps": list(skill.get("steps", []) or []),
        "notes": skill.get("notes", ""),
        "tags": list(skill.get("tags", []) or []),
    }


class SkillStore:
    """Manage reusable v2 skills."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        this_file = Path(__file__).resolve()
        self.root_dir = Path(root_dir) if root_dir else this_file.parent.parent
        self.skills_dir = self.root_dir / "skills_v2"
        self.tasks_dir = self.root_dir / "tasks"
        self.plans_dir = self.root_dir / "plans"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def save_from_task(self, name: str, task_id: str | None = None,
                       description: str | None = None) -> Path:
        """Save a skill from a completed task.

        Args:
            name: Human-readable skill name (e.g. "赛博朋克调色").
            task_id: Task to derive from. ``None`` = most recent succeeded task.
            description: Optional description. Defaults to the plan's goal.

        Returns:
            Path to the saved skill JSON file.
        """
        if task_id is None:
            task_id = self._find_last_succeeded_task()

        task = self._load_json(self.tasks_dir / f"{task_id}.json")
        plan = self._load_plan_for_task(task_id)

        # Build the reusable skill template
        template_steps = _strip_concrete_paths(plan.get("steps", []))

        # Extract models used from task metadata
        models_used: list[str] = task.get("models_used", [])

        # Extract parameterizable values from plan steps
        parameters = _extract_parameters(template_steps)

        skill = {
            "name": name,
            "description": description or plan.get("goal", ""),
            "version": "2.0",
            "origin_task_id": task_id,
            "created_at": datetime.now().isoformat(),
            "models_used": models_used,
            "parameters": parameters,
            "plan": {
                "version": "2.0",
                "goal": plan.get("goal", name),
                "steps": template_steps,
            },
        }

        filename = _slugify(name) + ".json"
        path = self.skills_dir / filename
        # Avoid overwriting — append a counter if needed
        if path.exists():
            i = 1
            while path.exists():
                path = self.skills_dir / f"{_slugify(name)}_{i}.json"
                i += 1

        path.write_text(json.dumps(skill, ensure_ascii=False, indent=2) + "\n")
        return path

    def list_skills(self) -> list[dict[str, Any]]:
        """Return a list of all saved v2 skills (metadata only)."""
        skills = []
        for p in sorted(self.skills_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text())
                skills.append({
                    "name": data.get("name", p.stem),
                    "description": data.get("description", ""),
                    "file": str(p),
                    "origin_task_id": data.get("origin_task_id"),
                    "created_at": data.get("created_at"),
                    "step_count": len(data.get("plan", {}).get("steps", [])),
                    "models_used": data.get("models_used", []),
                })
            except Exception:
                continue
        return skills

    def apply_parameters(self, skill_data: dict, overrides: dict) -> dict:
        """Apply parameter overrides to a skill's plan.

        Returns a deep copy of the plan with the specified parameter values
        replaced.  The plan itself is not mutated.

        Args:
            skill_data: Loaded skill dict (as returned by :meth:`load`).
            overrides: Dict mapping ``"step_id.arg"`` to the new value, e.g.
                ``{"step_1.preset": "vintage", "step_2.style_prompt": "watercolor"}``.

        Returns:
            Modified plan dict with overrides applied.  The ``"steps"``
            list entries have their ``"args"`` dicts updated in-place on
            copies — the original ``skill_data`` is not modified.
        """
        import copy
        plan = copy.deepcopy(skill_data.get("plan", {}))
        steps = plan.get("steps", [])

        # Build a lookup from step_id → step dict for O(1) access
        step_by_id: dict[str, dict] = {s["id"]: s for s in steps}

        for key, value in overrides.items():
            if "." not in key:
                continue
            step_id, arg = key.split(".", 1)
            if step_id in step_by_id:
                step_by_id[step_id].setdefault("args", {})[arg] = value

        return plan

    def load(self, name: str) -> dict[str, Any]:
        """Load a skill by name. Searches by exact name match in JSON files."""
        for p in self.skills_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                if data.get("name") == name:
                    return data
            except Exception:
                continue
        # Fallback: try filename match
        slug = _slugify(name)
        path = self.skills_dir / f"{slug}.json"
        if path.exists():
            return json.loads(path.read_text())
        raise FileNotFoundError(f"Skill not found: {name}")

    # ── Internal ───────────────────────────────────────────────────────

    def _find_last_succeeded_task(self) -> str:
        """Find the most recent succeeded v2 task."""
        candidates = []
        for p in self.tasks_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                if data.get("status") == "succeeded" and data.get("version") == "2.0":
                    candidates.append((p.stat().st_mtime, data["task_id"]))
            except Exception:
                continue
        if not candidates:
            raise FileNotFoundError("No succeeded v2 tasks found.")
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _load_plan_for_task(self, task_id: str) -> dict:
        """Load the plan JSON associated with a task."""
        plan_path = self.plans_dir / f"{task_id}_plan.json"
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan not found for task: {task_id}")
        return json.loads(plan_path.read_text())

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"Not found: {path}")
        return json.loads(path.read_text())


def _strip_concrete_paths(steps: list[dict]) -> list[dict]:
    """Remove concrete file paths from steps, keeping only $-references and args.

    This makes the plan reusable: $input and $output are re-bound at execution time.
    """
    cleaned = []
    for step in steps:
        s = {
            "id": step["id"],
            "function": step["function"],
            "args": step.get("args", {}),
        }
        # Preserve $-references, drop concrete paths
        inp = step.get("input")
        if isinstance(inp, str) and inp.startswith("$"):
            s["input"] = inp
        out = step.get("output")
        if isinstance(out, str) and out.startswith("$"):
            s["output"] = out
        if step.get("depends_on"):
            s["depends_on"] = step["depends_on"]
        cleaned.append(s)
    return cleaned


def _extract_parameters(steps: list[dict]) -> list[dict]:
    """Extract parameterizable values from plan step args.

    Iterates over all steps and their ``args`` dicts.  For each arg whose
    value is a JSON-primitive (``str``, ``int``, or ``float``) and is **not**
    a file path (does not contain ``/`` or ``\\``), a parameter entry is
    created.

    Args:
        steps: List of step dicts from a plan (after path stripping).

    Returns:
        List of parameter dicts with keys:
        - ``step_id``: ID of the owning step.
        - ``arg``: Argument name.
        - ``type``: ``"str"``, ``"int"``, or ``"float"``.
        - ``current_value``: Current value of the argument.
        - ``description``: Human-readable label.
    """
    params: list[dict] = []
    for step in steps:
        step_id = step.get("id", "")
        args = step.get("args", {})
        for arg_name, value in args.items():
            if not isinstance(value, (str, int, float)):
                continue
            # Skip file paths
            if isinstance(value, str) and ("/" in value or "\\" in value):
                continue
            type_name = type(value).__name__  # "str", "int", or "float"
            params.append({
                "step_id": step_id,
                "arg": arg_name,
                "type": type_name,
                "current_value": value,
                "description": f"{arg_name} for {step_id}",
            })
    return params


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    # Keep CJK characters, alphanumeric, hyphens, underscores
    cleaned = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]', '_', text)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or "skill"
