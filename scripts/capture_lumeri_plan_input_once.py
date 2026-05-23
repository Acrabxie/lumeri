#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gemia.ai.prompt_slimming import build_effective_request, estimate_tokens, strip_for_planning  # noqa: E402
from gemia.ai.clarification_policy import build_clarification_policy  # noqa: E402
from gemia.ai.primitive_specs import media_text_trust_boundaries, primitive_specs_for_skills  # noqa: E402
from gemia.ai.provider_audit import audit_provider_payload  # noqa: E402
from gemia.ai.skill_context import build_skill_plan_prompt_bundle  # noqa: E402
from gemia.ai.skill_router import route as route_planner_skills  # noqa: E402


DEFAULT_REQUEST = "加转场并做冷色调色"
DEFAULT_SERVER = "http://127.0.0.1:7788"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-pro-preview"


def main() -> None:
    args = _parse_args()
    record = build_capture_record(
        request=args.request,
        input_path=args.input_path,
        output_path=args.output_path,
        answers=_parse_json_object(args.answers_json, "answers-json"),
        project_state=_load_project_state(args),
        model=args.model or _configured_model(),
        endpoint=args.endpoint,
        use_cache_control=not args.no_cache_control,
    )
    txt_path = write_capture(record, out_dir=Path(args.out_dir).expanduser(), also_json=args.also_json)
    print(txt_path)
    print(f"selected_skills={record['request_meta']['selected_skills']}")
    print(f"route_source={record['request_meta']['route_source']}")
    print(f"tokens_est={record['request_meta']['planning_prompt_budget']['total_tokens_est']}")


def build_capture_record(
    *,
    request: str,
    input_path: str,
    output_path: str,
    answers: dict[str, Any] | None,
    project_state: dict[str, Any] | None,
    model: str,
    endpoint: str = DEFAULT_ENDPOINT,
    use_cache_control: bool = True,
) -> dict[str, Any]:
    effective_request, raw_request = build_effective_request(request, answers)
    slim_project_state = strip_for_planning(project_state, effective_request)
    route_result = route_planner_skills(
        request,
        clarifications=answers,
        project_state=slim_project_state,
    )
    video_context = _video_context_from_project(slim_project_state)
    bundle = build_skill_plan_prompt_bundle(
        route_result.skills,
        effective_request=effective_request,
        has_video_context=bool(video_context),
    )
    active_specs = primitive_specs_for_skills(route_result.skills)
    selected_primitives = [str(spec["name"]) for spec in active_specs if spec.get("name")]
    context_trust_boundaries = media_text_trust_boundaries(slim_project_state, video_context)
    clarification_policy = build_clarification_policy(route_result.skills, active_specs, answers=answers)
    user_payload: dict[str, Any] = {
        "runtime_envelope_version": "agent-runtime-0.1",
        "request": effective_request,
        "raw_request": raw_request,
        "input_path": input_path,
        "output_path": output_path,
        "active_primitive_specs": active_specs,
        "runtime_policy": {
            "plan_schema": "2.1",
            "allow_parallel_groups": True,
            "cache_control": "openrouter_ephemeral_best_effort",
            "provider": "openrouter",
            "model": model,
            "reasoning": "planner_json",
            "verbosity": "compact",
        },
        "planning_mode": {
            "passes": (["video_context"] if video_context else []) + ["primitive_plan", "execute"],
            "planner": "skills",
            "selected_skills": route_result.skills,
            "selected_primitives": selected_primitives,
            "route_source": route_result.source,
            "route_confidence": route_result.confidence,
            "route_latency_ms": round(route_result.latency_ms, 3),
            "combo_stubs": bundle.combo_ids,
        },
        "clarification_policy": clarification_policy,
    }
    if answers:
        user_payload["clarifications"] = answers
    if slim_project_state is not None:
        user_payload["project_state"] = slim_project_state
    if video_context:
        user_payload["video_context"] = video_context
    if context_trust_boundaries:
        user_payload["context_trust_boundaries"] = context_trust_boundaries
    user_payload["planning_prompt_budget"] = bundle.token_budget(user_payload)
    user_payload["planning_prompt_budget"]["active_specs_tokens_est"] = estimate_tokens(active_specs)

    request_body = _openrouter_payload(
        model=model,
        static_system_prompt=bundle.static_chunk,
        dynamic_system_prompt=bundle.dynamic_chunk,
        user_payload=user_payload,
        use_cache_control=use_cache_control,
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "timestamp": ts,
        "tag": "plan-v2-skills-capture-once",
        "dry_run": True,
        "note": "This is a local capture only. It does not call OpenRouter or native Gemini.",
        "endpoint": endpoint,
        "headers": {
            "Authorization": "<not included: dry-run capture>",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://local-lumeri-desktop",
            "X-Title": "lumeri-desktop",
        },
        "request_meta": {
            "provider": "openrouter",
            "model": model,
            "message_count": len(request_body["messages"]),
            "selected_skills": route_result.skills,
            "route_source": route_result.source,
            "route_confidence": route_result.confidence,
            "combo_stubs": bundle.combo_ids,
            "planning_prompt_budget": user_payload["planning_prompt_budget"],
            "provider_payload_audit": audit_provider_payload(request_body),
        },
        "request_body": request_body,
    }


def write_capture(record: dict[str, Any], *, out_dir: Path, also_json: bool = True) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_tag = str(record.get("tag") or "capture").replace("/", "-")
    txt_path = out_dir / f"{record['timestamp']}-{safe_tag}.txt"
    text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    txt_path.write_text(text, encoding="utf-8")
    (out_dir / "latest-capture.txt").write_text(text, encoding="utf-8")
    if also_json:
        json_path = txt_path.with_suffix(".json")
        json_path.write_text(text, encoding="utf-8")
        (out_dir / "latest-capture.json").write_text(text, encoding="utf-8")
    return txt_path


def _openrouter_payload(
    *,
    model: str,
    static_system_prompt: str,
    dynamic_system_prompt: str,
    user_payload: dict[str, Any],
    use_cache_control: bool,
) -> dict[str, Any]:
    system_block: dict[str, Any] = {"type": "text", "text": static_system_prompt}
    if use_cache_control:
        system_block["cache_control"] = {"type": "ephemeral"}
    messages: list[dict[str, Any]] = [{"role": "system", "content": [system_block]}]
    if dynamic_system_prompt:
        messages.append({"role": "system", "content": dynamic_system_prompt})
    messages.append({
        "role": "user",
        "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
    })
    return {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }


def _load_project_state(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.project_state_json:
        return _parse_json_object(args.project_state_json, "project-state-json")
    if args.project_state_file:
        return json.loads(Path(args.project_state_file).expanduser().read_text(encoding="utf-8"))
    if not args.no_server:
        state = _fetch_project_state(args.server)
        if state is not None:
            return state
    if args.empty_project:
        return None
    return _sample_project_state(args.input_path)


def _fetch_project_state(server: str) -> dict[str, Any] | None:
    url = server.rstrip("/") + "/project/current"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        for key in ("project_state", "project", "state"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        return data
    return None


def _sample_project_state(input_path: str) -> dict[str, Any]:
    return {
        "clips": [
            {
                "id": "clip1",
                "name": Path(input_path).name or "source.mp4",
                "serverPath": input_path,
                "mediaKind": "video",
                "duration": 12,
                "summary": {
                    "duration": 12,
                    "mood": "clean",
                    "key_frame": "00:03",
                    "suggested_use": "main clip",
                },
                "thumbnailStrip": ["stripped"] * 12,
                "waveformPeaks": [0.1] * 12,
            }
        ],
        "timeReferences": [{"id": "sample_range", "kind": "range", "start": 2, "end": 5}],
    }


def _video_context_from_project(project_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(project_state, dict):
        return None
    clips = project_state.get("clips") if isinstance(project_state.get("clips"), list) else []
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        kind = str(clip.get("mediaKind") or clip.get("media_kind") or "").lower()
        if kind and kind != "video":
            continue
        summary = clip.get("summary")
        if not isinstance(summary, dict):
            continue
        context = {
            key: summary.get(key)
            for key in ("duration", "mood", "key_frame", "suggested_use")
            if summary.get(key) not in (None, "")
        }
        if context:
            return context
    return None


def _configured_model() -> str:
    for env_name in ("GEMIA_PLANNER_MODEL", "GEMIA_OPENROUTER_MODEL", "OPENROUTER_MODEL"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    config = Path.home() / ".gemia" / "config.json"
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        for key in ("planner_model", "openrouter_model"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    except Exception:
        pass
    return DEFAULT_MODEL


def _parse_json_object(raw: str | None, label: str) -> dict[str, Any] | None:
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit(f"--{label} must be a JSON object")
    return data


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture one current Lumeri Plan-v2 Skills Router model input without calling the model."
    )
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--input-path", default="/tmp/lumeri-source.mp4")
    parser.add_argument("--output-path", default="/tmp/lumeri-output.mp4")
    parser.add_argument("--answers-json", default=None, help="Clarifications JSON object")
    parser.add_argument("--project-state-json", default=None, help="Inline project_state JSON object")
    parser.add_argument("--project-state-file", default=None, help="Path to project_state JSON")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Server to fetch /project/current from")
    parser.add_argument("--no-server", action="store_true", help="Do not try 7788 /project/current")
    parser.add_argument("--empty-project", action="store_true", help="Use no project_state if server/file/json is absent")
    parser.add_argument("--model", default=None)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--no-cache-control", action="store_true")
    parser.add_argument(
        "--out-dir",
        default=str(Path.home() / "Desktop" / "Lumeri Gemini Inputs"),
    )
    parser.add_argument("--also-json", action="store_true", default=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
