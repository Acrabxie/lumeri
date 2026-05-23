"""Material-aware multi-step prompt execution for Gemia."""
from __future__ import annotations

import mimetypes
import json
import re
import subprocess
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gemia.artifacts import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    artifact_outputs as _artifact_outputs,
    is_media_output,
    media_outputs as _media_outputs,
    output_paths as _output_paths,
)
from gemia.errors import UserInputError
from gemia.plan_contract import PlanContractError
from gemia.project_model import IMAGE_DURATION, is_canonical_project
from gemia.stability import stability_gate_enabled

AgentEventCallback = Callable[[dict[str, Any]], None]
ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], None]

ALL_SCOPE_WORDS = (
    "所有",
    "全部",
    "每个",
    "每一",
    "逐个",
    "批量",
    "素材池",
    "媒体池",
    "全素材",
    "all",
    "every",
    "each",
    "batch",
)

MULTI_TIMELINE_SCOPE_WORDS = (
    "当前时间线",
    "当前时间轴",
    "时间线里",
    "时间轴里",
    "分割过的时间线",
    "timeline",
    "two clips",
    "three clips",
    "both clips",
)

MULTI_TIMELINE_COUNT_WORDS = (
    "两个",
    "两段",
    "2个",
    "2 个",
    "三个",
    "三段",
    "3个",
    "3 个",
    "3段",
    "3 段",
    "三个片段",
    "三段片段",
    "前 1.5",
    "后 1.5",
    "前1.5",
    "后1.5",
    "both",
)

TIMELINE_CONCAT_WORDS = (
    "拼接",
    "拼成",
    "剪成",
    "合成",
    "合并",
    "硬切",
    "切到",
    "叠化",
    "转场",
    "concat",
    "hard cut",
    "cut to",
    "crossfade",
    "dissolve",
    "b-roll",
    "broll",
)

ADJACENT_TRANSITION_WORDS = (
    "转场",
    "过渡",
    "叠化",
    "溶解",
    "擦除",
    "切换",
    "衔接",
    "transition",
    "crossfade",
    "dissolve",
    "wipe",
)

TRANSITION_PAIR_REQUIRED_WORDS = (
    "两段",
    "两个",
    "两个片段",
    "两个素材",
    "2段",
    "2 段",
    "2个",
    "2 个",
    "中间",
    "之间",
    "相邻",
    "前后",
    "both clips",
    "two clips",
    "between",
    "adjacent",
)

LIBRARY_SCOPE_WORDS = (
    "素材池",
    "媒体池",
    "媒体库",
    "账号媒体库",
    "media pool",
    "media library",
    "library",
)

NO_LIBRARY_WORDS = (
    "不要从媒体素材池",
    "不要从素材池",
    "不要从媒体池",
    "不要从媒体库",
    "不要使用媒体池",
    "不要用媒体池",
    "不用媒体池",
    "不要使用媒体库",
    "不要用媒体库",
    "不用媒体库",
    "不要历史会话",
    "不要旧会话",
    "不要外部素材",
    "不用外部素材",
    "不要素材",
    "不要用 library",
    "不要从 library",
    "no library",
    "without library",
    "do not use library",
    "don't use library",
    "prompt-only",
    "blank canvas",
    "no external",
    "without external",
    "no media",
)

CURRENT_TIMELINE_REQUIRED_WORDS = (
    "当前时间线",
    "当前时间轴",
    "current timeline",
    "current sequence",
)

MEDIA_FILENAME_PATTERN = re.compile(
    r"[\w.\-()\u4e00-\u9fff]+(?:\.mp4|\.mov|\.m4v|\.webm|\.mkv|\.avi|\.jpg|\.jpeg|\.png|\.webp|\.wav|\.mp3|\.m4a)\b",
    re.IGNORECASE,
)

NEGATIVE_MATERIAL_CONTEXT_WORDS = (
    "不要使用",
    "不要用",
    "不用",
    "排除",
    "不要包含",
    "exclude",
    "do not use",
    "don't use",
    "without using",
)

_RISKY_CAPABILITY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "veo",
        (
            "gemia.video.generative.generate_video",
            "gemia.video.generative.generate_video_from_image",
        ),
        ("veo", "视频生成", "生成视频", "文生视频", "图生视频", "用 veo", "call veo"),
    ),
    (
        "blender",
        (
            "gemia.video.blender_link.",
            "blender",
            "lumerilink",
        ),
        ("blender", "lumerilink", "三维", "3d", "空间效果", "立体", "hologram"),
    ),
    (
        "dev_brief",
        (
            "gemia.video.creative_runtime.write_development_patch_brief",
            "write_development_patch_brief",
        ),
        ("源码", "底层代码", "开发补丁", "brief", "dev brief", "developer brief", "写 brief"),
    ),
    (
        "stock_media",
        (
            "gemia.video.stock_media.",
            "fetch_stock_media",
            "search_stock_media",
            "pexels",
            "pixabay",
        ),
        ("pexels", "pixabay", "搜索素材", "找素材", "公开视频", "公共素材", "stock"),
    ),
)

def wants_all_materials(prompt: str, scope: str | None = None) -> bool:
    """Return whether a request should target every available material."""
    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope in {"all", "all_materials", "project_materials", "library"}:
        return True
    if normalized_scope in {"current", "selected", "single"}:
        return False
    text = (prompt or "").lower()
    return any(word in text for word in ALL_SCOPE_WORDS) or _prompt_wants_multi_timeline_materials(prompt)


def _prompt_wants_multi_timeline_materials(prompt: str) -> bool:
    text = str(prompt or "")
    lowered = text.lower()
    if any(term in text or term in lowered for term in MULTI_TIMELINE_SCOPE_WORDS):
        return True
    has_multi_count = any(term in text or term in lowered for term in MULTI_TIMELINE_COUNT_WORDS)
    has_concat_intent = any(term in text or term in lowered for term in TIMELINE_CONCAT_WORDS)
    return has_multi_count and has_concat_intent


def should_include_media_library(prompt: str, scope: str | None = None) -> bool:
    """Return whether the agent should read account media-library assets."""
    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope in {"library", "all", "all_materials", "project_materials"}:
        return True
    if normalized_scope in {"current", "selected", "single", "prompt_only", "blank_canvas"}:
        return False
    text = str(prompt or "")
    lowered = text.lower()
    if _prompt_disallows_media_library(text):
        return False
    if _prompt_contains_non_excluded_media_filename(text):
        return True
    return any(term in text or term in lowered for term in LIBRARY_SCOPE_WORDS)


def _prompt_requires_current_timeline_materials(prompt: str) -> bool:
    text = str(prompt or "")
    lowered = text.lower()
    return any(term in text or term in lowered for term in CURRENT_TIMELINE_REQUIRED_WORDS)


def _prompt_disallows_media_library(prompt: str) -> bool:
    text = str(prompt or "")
    lowered = text.lower()
    if any(term in text or term in lowered for term in NO_LIBRARY_WORDS):
        return True
    negative_prefixes = ("不要", "别", "禁止", "不能", "不许")
    library_terms = (
        "媒体素材池",
        "素材池",
        "媒体池",
        "媒体库",
        "历史会话",
        "旧会话",
        "library",
        "media library",
        "media pool",
    )
    action_terms = ("替代", "代替", "fallback", "substitute", "使用", "读取", "拿", "取")
    for prefix in negative_prefixes:
        for library_term in library_terms:
            marker = f"{prefix}{library_term}"
            marker_from = f"{prefix}从{library_term}"
            if marker in text or marker in lowered or marker_from in text or marker_from in lowered:
                return True
    for prefix in negative_prefixes:
        prefix_pos = min((pos for pos in (text.find(prefix), lowered.find(prefix)) if pos >= 0), default=-1)
        if prefix_pos < 0:
            continue
        window = lowered[prefix_pos:prefix_pos + 80]
        if any(term in window for term in library_terms) and any(term in window for term in action_terms):
            return True
    return False


def _prompt_contains_non_excluded_media_filename(prompt: str) -> bool:
    text = str(prompt or "")
    lowered = text.lower()
    for match in MEDIA_FILENAME_PATTERN.finditer(lowered):
        if not _prompt_material_position_is_excluded(lowered, match.start()):
            return True
    return False


def collect_materials(
    project_state: dict[str, Any] | None,
    *,
    account_id: str | None = None,
    include_library: bool = True,
    library_limit: int = 300,
) -> list[dict[str, Any]]:
    """Collect timeline/project/media-library assets into one deduped list."""
    materials: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_assets: set[str] = set()

    def add(raw: dict[str, Any], *, origin: str, clip_id: str | None = None, selected: bool = False) -> None:
        material = _material_from_raw(raw, origin=origin, clip_id=clip_id, selected=selected)
        if not material.get("source_path"):
            return
        asset_key = _material_key(material)
        key = _material_dedupe_key(material)
        if origin != "timeline" and asset_key in seen_assets:
            if selected:
                for item in materials:
                    if _material_key(item) == asset_key:
                        item["selected"] = True
                        break
            return
        if key in seen:
            if selected:
                for item in materials:
                    if _material_dedupe_key(item) == key:
                        item["selected"] = True
                        if clip_id and not item.get("clip_id"):
                            item["clip_id"] = clip_id
                        break
            return
        seen.add(key)
        seen_assets.add(asset_key)
        materials.append(material)

    state = project_state if isinstance(project_state, dict) else {}
    if is_canonical_project(state):
        assets = [item for item in state.get("assets") or [] if isinstance(item, dict)]
        assets_by_id = {
            str(item.get("asset_id") or item.get("id") or ""): item
            for item in assets
            if item.get("asset_id") or item.get("id")
        }
        ui_state = state.get("ui_state") if isinstance(state.get("ui_state"), dict) else {}
        selected_clip_id = str(ui_state.get("selected_clip_id") or "")
        timeline = state.get("timeline") if isinstance(state.get("timeline"), dict) else {}
        for clip in timeline.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            asset = assets_by_id.get(str(clip.get("asset_id") or ""), {})
            merged = {**asset, **_asset_fields_from_timeline_clip(clip)}
            add(
                merged,
                origin="timeline",
                clip_id=str(clip.get("id") or ""),
                selected=bool(selected_clip_id and clip.get("id") == selected_clip_id),
            )
        for asset in assets:
            add(asset, origin="project_asset")
    else:
        selected_clip_id = str(state.get("selectedClipId") or "")
        for clip in state.get("clips") or []:
            if isinstance(clip, dict):
                add(
                    clip,
                    origin="timeline",
                    clip_id=str(clip.get("id") or ""),
                    selected=bool(selected_clip_id and clip.get("id") == selected_clip_id),
                )

    if include_library and account_id:
        try:
            from gemia.media_library import list_assets

            for asset in list_assets(account_id, limit=library_limit):
                add(asset, origin="library")
        except Exception:
            # The agent loop can still operate on the active timeline if the
            # SQLite library is unavailable.
            pass
    return materials


def read_material(material: dict[str, Any]) -> dict[str, Any]:
    """Read local metadata for one material without invoking remote AI."""
    result = deepcopy(material)
    source_path = str(result.get("source_path") or "")
    path = Path(source_path) if source_path else None
    metadata = dict(result.get("metadata") if isinstance(result.get("metadata"), dict) else {})
    result["read_at"] = _utc_now()
    if not path or not path.exists() or not path.is_file():
        result["read_status"] = "missing"
        result["read_error"] = "source file is missing"
        return _normalize_material_metadata(result, metadata)

    try:
        metadata.update(probe_media(str(path)))
        result["read_status"] = "ready"
        result["read_error"] = ""
    except Exception as exc:
        result["read_status"] = "partial"
        result["read_error"] = str(exc)
        metadata.setdefault("file_size_bytes", path.stat().st_size)
    return _normalize_material_metadata(result, metadata)


def read_all_materials(
    materials: list[dict[str, Any]],
    *,
    event_callback: AgentEventCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> list[dict[str, Any]]:
    """Read every material and emit visible read events."""
    read: list[dict[str, Any]] = []
    total = len(materials)
    for index, material in enumerate(materials, start=1):
        _check_cancel(should_cancel)
        name = str(material.get("name") or material.get("source_path") or "素材")
        _emit(
            event_callback,
            "read",
            f"读取素材：{name}",
            detail=str(material.get("media_kind") or "media"),
            meta=f"Read {index}/{total}",
            body=f"准备读取本地素材元数据：{_material_line(material)}",
            index=index,
            total=total,
            material=material,
        )
        read_material_payload = read_material(material)
        read.append(read_material_payload)
        status = "succeeded" if read_material_payload.get("read_status") == "ready" else "warning"
        detail = _material_read_detail(read_material_payload)
        _emit(
            event_callback,
            "read",
            f"完成阅读：{name}",
            detail=detail,
            meta=f"Read {index}/{total}",
            body=_material_read_body(read_material_payload),
            status=status,
            index=index,
            total=total,
            material=read_material_payload,
        )
    return read


def select_target_materials(
    materials: list[dict[str, Any]],
    *,
    input_path: str | None = None,
    prompt: str = "",
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Select the materials that should be executed for this prompt."""
    usable = [item for item in materials if item.get("source_path")]
    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope in {"all", "all_materials", "project_materials", "library"}:
        return usable

    explicit_targets = _explicit_prompt_material_targets(usable, prompt)
    if explicit_targets:
        return explicit_targets

    transition_pair = _adjacent_transition_targets(usable, prompt=prompt, input_path=input_path, scope=scope)
    if transition_pair:
        return transition_pair

    if wants_all_materials(prompt, scope):
        return usable

    normalized_input = _normalized_path(input_path)
    if normalized_input:
        matched = [
            item for item in usable
            if _normalized_path(str(item.get("source_path") or "")) == normalized_input
        ]
        if matched:
            return matched[:1]

    selected = [item for item in usable if item.get("selected")]
    if selected:
        return selected[:1]
    return usable[:1]


def _explicit_prompt_material_targets(
    materials: list[dict[str, Any]],
    prompt: str,
) -> list[dict[str, Any]]:
    """Return materials explicitly named in the prompt, preserving prompt order."""
    text = str(prompt or "").lower()
    if not text:
        return []
    matched: list[tuple[int, int, dict[str, Any]]] = []
    for index, material in enumerate(materials):
        position = _explicit_prompt_material_position(material, text)
        if position is not None:
            matched.append((position, index, material))
    if not matched:
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, _, material in sorted(matched, key=lambda item: (item[0], item[1])):
        key = _material_identity(material)
        if key in seen:
            continue
        seen.add(key)
        result.append(material)
    return result


def _explicit_prompt_material_position(material: dict[str, Any], prompt_lower: str) -> int | None:
    positions: list[int] = []
    for identifier in _prompt_material_identifiers(material):
        needle = identifier.lower()
        if len(needle) < 4:
            continue
        start = 0
        while True:
            position = prompt_lower.find(needle, start)
            if position < 0:
                break
            start = position + len(needle)
            if _prompt_material_position_is_excluded(prompt_lower, position):
                continue
            positions.append(position)
    return min(positions) if positions else None


def _prompt_material_position_is_excluded(prompt_lower: str, position: int) -> bool:
    boundary = max(prompt_lower.rfind(mark, 0, position) for mark in ("。", "；", "，", ";", "\n"))
    context = prompt_lower[boundary + 1:position]
    return any(term in context for term in NEGATIVE_MATERIAL_CONTEXT_WORDS)


def _prompt_material_identifiers(material: dict[str, Any]) -> list[str]:
    identifiers: list[str] = []
    raw_values = (
        material.get("name"),
        material.get("source_path"),
        material.get("asset_id"),
        material.get("material_id"),
        material.get("clip_id"),
    )
    for raw in raw_values:
        value = str(raw or "").strip()
        if not value:
            continue
        identifiers.append(value)
        basename = Path(value).name
        if basename and basename != value:
            identifiers.append(basename)
        stem = Path(value).stem
        if len(stem) >= 8 and stem != basename:
            identifiers.append(stem)
    return list(dict.fromkeys(identifiers))


def _material_identity(material: dict[str, Any]) -> str:
    for key in ("clip_id", "material_id", "asset_id", "source_path"):
        value = str(material.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return f"object:{id(material)}"


def _prompt_wants_adjacent_transition(prompt: str) -> bool:
    text = str(prompt or "")
    lowered = text.lower()
    return any(term in text or term in lowered for term in ADJACENT_TRANSITION_WORDS)


def _prompt_requires_two_transition_targets(prompt: str, scope: str | None = None) -> bool:
    """Return whether the wording promises a two-clip transition contract.

    Vague requests like "加一个转场" should still reach the planner so it can
    ask once. Explicit pair language like "两段视频中间" must fail before
    execution if target selection found only one clip.
    """
    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope in {"all", "all_materials", "project_materials", "library"}:
        return False
    if not _prompt_wants_adjacent_transition(prompt):
        return False
    if _prompt_wants_timeline_broll_concat(prompt):
        return False
    text = str(prompt or "")
    lowered = text.lower()
    if any(term in text or term in lowered for term in ALL_SCOPE_WORDS):
        return False
    return any(term in text or term in lowered for term in TRANSITION_PAIR_REQUIRED_WORDS)


def _adjacent_transition_targets(
    materials: list[dict[str, Any]],
    *,
    prompt: str,
    input_path: str | None = None,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Return the ordered pair that should feed a two-input transition."""
    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope in {"current", "selected", "single"}:
        return []
    if not _prompt_wants_adjacent_transition(prompt):
        return []
    text = str(prompt or "")
    lowered = text.lower()
    if any(term in text or term in lowered for term in ALL_SCOPE_WORDS):
        return []
    if _prompt_wants_timeline_broll_concat(prompt):
        return []

    candidates = [
        item
        for item in materials
        if str(item.get("origin") or "").lower() == "timeline"
        and str(item.get("media_kind") or "video").lower() == "video"
        and item.get("source_path")
    ]
    if len(candidates) < 2:
        return []

    normalized_input = _normalized_path(input_path)
    pivot = next((idx for idx, item in enumerate(candidates) if item.get("selected")), None)
    if pivot is None and normalized_input:
        pivot = next(
            (
                idx
                for idx, item in enumerate(candidates)
                if _normalized_path(str(item.get("source_path") or "")) == normalized_input
            ),
            None,
        )
    if pivot is None:
        return candidates[:2]
    if pivot > 0:
        return [candidates[pivot - 1], candidates[pivot]]
    return candidates[:2]


def build_agent_context(
    *,
    prompt: str,
    scope: str | None,
    materials: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    time_references: list[dict[str, Any]] | None = None,
    prompt_only_creation: bool = False,
    creative_mode: str = "timeline_guided",
    reference_assets: list[dict[str, Any]] | None = None,
    layer_plan: dict[str, Any] | None = None,
    render_passes: list[dict[str, Any]] | None = None,
    review_notes: list[dict[str, Any]] | None = None,
    human_feedback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return compact context that the planner can reason over."""
    refs = time_references or []
    instructions = [
        "Read the provided material metadata before planning.",
        "Plan only after identifying the target material.",
        "Use timeline/project context when clip ids or asset ids are relevant.",
        "Treat user-selected time_references as high-priority temporal constraints for Gemini planning.",
    ]
    if _prompt_wants_adjacent_transition(prompt) and len(targets) >= 2:
        instructions.append(
            "For adjacent transition requests, treat the ordered targets as one multi-input operation: "
            "targets[0] is input_a and targets[1] is input_b; do not execute the transition separately per target."
        )
    else:
        instructions.append("When multiple targets are present, execute the same user intent independently for each target.")
    if prompt_only_creation:
        instructions = [
            "No source media was provided; treat this as prompt-only video creation on a blank canvas.",
            "Do not ask the user to upload media just because project materials are empty.",
            "Prefer generative, stock-media, ad-graphics, or other standalone creation primitives that do not require existing footage.",
            "Use $input as an empty/blank-canvas input only for primitives that explicitly support standalone rendering.",
        ]
    return {
        "mode": "prompt_only_creation" if prompt_only_creation else "think_read_plan_execute",
        "scope": scope or "auto",
        "request": prompt,
        "creative_mode": creative_mode,
        "prompt_only_creation": bool(prompt_only_creation),
        "material_count": len(materials),
        "target_count": len(targets),
        "reference_assets": [_reference_asset_summary(item) for item in (reference_assets or [])],
        "time_references": refs,
        "materials": [_prompt_material_summary(item) for item in materials],
        "targets": [_prompt_material_summary(item) for item in targets],
        "layer_plan": layer_plan or {},
        "render_passes": [_compact_render_pass(item) for item in (render_passes or [])],
        "review_notes": [_compact_review_note(item) for item in (review_notes or [])],
        "human_feedback": [_compact_human_feedback(item) for item in (human_feedback or [])],
        "instructions": instructions,
    }


def run_agent_workflow(
    orch: Any,
    *,
    prompt: str,
    input_path: str | None,
    answers: dict[str, str] | None = None,
    project_state: dict[str, Any] | None = None,
    account_id: str | None = None,
    scope: str | None = "auto",
    agent: str | None = None,
    event_callback: AgentEventCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> dict[str, Any]:
    """Run a Codex/Claude-Code-style loop: think, read, plan, execute."""
    _emit(
        event_callback,
        "think",
        "开始处理",
        detail=_short_text(prompt, 160),
        meta="Started agent loop",
        body="我先确认这次要处理什么素材、用户选了哪些时间位置，然后把素材读清楚再交给 Gemini 规划。",
    )
    materials = collect_materials(
        project_state,
        account_id=account_id,
        include_library=should_include_media_library(prompt, scope),
    )
    if not materials and input_path:
        materials = [
            _material_from_raw(
                {"source_path": input_path, "name": Path(input_path).name},
                origin="input",
                selected=True,
            )
        ]
    timeline_materials = [
        item
        for item in materials
        if str(item.get("origin") or "").lower() == "timeline" and item.get("source_path")
    ]
    if (
        _prompt_requires_current_timeline_materials(prompt)
        and not timeline_materials
        and not input_path
    ):
        return _needs_current_timeline_media_result(
            prompt=prompt,
            project_state=project_state,
            scope=scope,
            agent=agent,
            event_callback=event_callback,
        )
    prompt_only_creation = not materials
    reference_assets = _reference_assets_from_project_state(project_state)
    human_feedback = _human_feedback_from_project_state(project_state)
    creative_mode = _creative_mode_for_run(
        prompt_only_creation=prompt_only_creation,
        reference_assets=reference_assets,
    )
    layer_plan = _initial_layer_plan(
        prompt=prompt,
        creative_mode=creative_mode,
        reference_assets=reference_assets,
    )
    render_passes: list[dict[str, Any]] = []
    review_notes: list[dict[str, Any]] = []

    if prompt_only_creation:
        read_materials: list[dict[str, Any]] = []
        targets = [_prompt_only_material(prompt)]
        _emit(
            event_callback,
            "think",
            "进入空画布创作",
            detail="无需上传素材",
            status="succeeded",
            meta="Prompt-only creation",
            body="这轮没有项目素材，我会把它当作从 prompt 出发的创作任务：可以生成画面、抓取公开素材、写广告图文动效，或在空画布上渲染结果。",
            stats={"material_count": 0, "target_count": 1},
        )
    elif reference_assets:
        _emit(
            event_callback,
            "think",
            "参考资料就绪",
            detail=f"{len(reference_assets)} 个参考附件",
            status="succeeded",
            meta="Reference assets",
            body="我会把这些上传内容当作创作参考，而不是默认塞进时间轴；只有规划需要时才读取、生成、抓取或提升为正式素材。",
            stats={"reference_asset_count": len(reference_assets)},
            extra={"voice": "gemini"},
        )
    else:
        _emit(
            event_callback,
            "think",
            "收集素材完成",
            detail=f"发现 {len(materials)} 个素材",
            status="succeeded",
            meta="Analyzed materials",
            body=_materials_overview(materials),
            stats={"material_count": len(materials)},
        )
        _emit(
            event_callback,
            "read",
            "开始阅读素材",
            detail=f"{len(materials)} 个素材",
            meta=f"Read 0/{len(materials)}",
            body="我正在读取素材的类型、时长、分辨率、fps、音频和文件状态；后面的计划会基于这些可验证的信息生成。",
            stats={"material_count": len(materials)},
        )
        read_materials = read_all_materials(
            materials,
            event_callback=event_callback,
            should_cancel=should_cancel,
        )
        targets = select_target_materials(read_materials, input_path=input_path, prompt=prompt, scope=scope)
        if not targets:
            raise ValueError("没有找到可执行的目标素材")
        _enforce_target_gate(prompt=prompt, targets=targets, scope=scope)

        _emit(
            event_callback,
            "plan",
            "选择目标素材",
            detail=f"{len(targets)} / {len(read_materials)} 个素材进入执行",
            status="succeeded",
            meta="Planned target scope",
            body=_target_selection_body(targets, len(read_materials)),
            stats={"material_count": len(read_materials), "target_count": len(targets)},
            extra={"selected_targets": [_prompt_material_summary(item) for item in targets]},
        )
        timeline_broll_targets = _timeline_broll_targets(read_materials, targets, prompt)
        if timeline_broll_targets:
            return _run_timeline_broll_preview(
                orch,
                prompt=prompt,
                read_materials=read_materials,
                targets=timeline_broll_targets,
                creative_mode=creative_mode,
                reference_assets=reference_assets,
                layer_plan=layer_plan,
                review_notes=review_notes,
                human_feedback=human_feedback,
                event_callback=event_callback,
            )
    time_references = _extract_time_references(project_state)
    if time_references:
        _emit(
            event_callback,
            "plan",
            "锁定时间参考",
            detail=f"{len(time_references)} 个时间参考",
            status="succeeded",
            meta="Pinned time references",
            body=_time_references_body(time_references),
            stats={"time_reference_count": len(time_references)},
        )

    context = build_agent_context(
        prompt=prompt,
        scope=scope,
        materials=read_materials,
        targets=targets,
        time_references=time_references,
        prompt_only_creation=prompt_only_creation,
        creative_mode=creative_mode,
        reference_assets=reference_assets,
        layer_plan=layer_plan,
        render_passes=render_passes,
        review_notes=review_notes,
        human_feedback=human_feedback,
    )
    _emit(
        event_callback,
        "plan",
        "生成执行计划",
        detail=f"目标素材 {len(targets)} / 已阅读 {len(read_materials)}",
        meta="Planning execution",
        body="素材已经读完。现在进入 Gemini 规划阶段，它会用自然语言说明这轮理解和接下来的执行方式。",
        status="running",
        stats={"material_count": len(read_materials), "target_count": len(targets)},
    )

    outputs: list[str] = []
    artifact_outputs: list[str] = []
    child_tasks: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    execution_targets = _execution_targets_for_prompt(targets, prompt)
    grouped_multi_input_targets = execution_targets != targets
    total_targets = len(execution_targets)
    emitted_step_events: set[tuple[int, int, int, str]] = set()
    for index, target in enumerate(execution_targets, start=1):
        _check_cancel(should_cancel)
        name = str(target.get("name") or target.get("source_path") or "素材")
        output_path = str((orch.outputs_dir / f"ai_{uuid.uuid4().hex[:8]}_{index}.mp4").resolve())
        target_context = {
            **context,
            "current_target": _prompt_material_summary(target),
            "target_index": index,
            "target_total": len(targets) if grouped_multi_input_targets else total_targets,
            "execution_target_total": total_targets,
        }
        if grouped_multi_input_targets:
            target_context["multi_input_targets"] = [_prompt_material_summary(item) for item in targets]
            target_context["multi_input_operation"] = "adjacent_transition"
        enriched_project_state = _project_state_with_agent_context(project_state, target_context)
        _emit(
            event_callback,
            "plan",
            f"规划：{name}",
            detail=f"{index}/{total_targets}",
            meta=f"Planning material {index}/{total_targets}",
            body=_material_execution_body(target),
            index=index,
            total=total_targets,
            material=target,
        )
        target_input_path = str(target.get("source_path") or input_path or "")
        try:
            plan = orch.plan_from_primitives(
                prompt,
                input_path=target_input_path,
                output_path=output_path,
                answers=answers,
                agent=agent,
                project_state=enriched_project_state,
            )
        except PlanContractError as exc:
            if not prompt_only_creation or not stability_gate_enabled():
                raise
            plan = _safe_layer_preview_plan(
                prompt=prompt,
                input_path=target_input_path,
                output_path=output_path,
                blocked_functions=["plan_contract"],
                reason="plan_contract_error",
            )
            _emit(
                event_callback,
                "capability_call",
                "规划契约失败已降级为本地预览",
                detail=str(getattr(exc, "detail", "") or exc),
                meta="Plan contract fallback",
                body=(
                    "Gemini 这次给出的计划里还有未解析模板或契约字段。"
                    "我先切到确定可执行的本地图层小样，避免让空画布创作直接失败。"
                ),
                status="succeeded",
                index=index,
                total=total_targets,
                material=target,
                extra={
                    "voice": "gemini",
                    "capability": "stability_gate",
                    "fallback_reason": "PlanContractError",
                    "error_code": getattr(exc, "code", "E_PLAN_CONTRACT"),
                },
            )
        except Exception as exc:
            if not _allow_planning_layer_fallback(exc, prompt_only_creation=prompt_only_creation, prompt=prompt):
                raise
            plan = _safe_layer_preview_plan(
                prompt=prompt,
                input_path=target_input_path,
                output_path=output_path,
                blocked_functions=["planning_provider"],
                reason="planning_provider_unavailable",
            )
            _emit(
                event_callback,
                "capability_call",
                "规划失败已降级为本地预览",
                detail=exc.__class__.__name__,
                meta="Planning provider fallback",
                body=(
                    "外部规划或生成服务这次没有返回可用计划。"
                    "这轮是 prompt-only 空画布创作，我先切到确定可执行的本地图层小样，避免直接失败。"
                ),
                status="succeeded",
                index=index,
                total=total_targets,
                material=target,
                extra={
                    "voice": "gemini",
                    "capability": "stability_gate",
                    "fallback_reason": "planning_provider_unavailable",
                },
            )
        if plan.get("ask"):
            questions = plan.get("questions") if isinstance(plan.get("questions"), list) else []
            question_text = _questions_summary(questions)
            _emit(
                event_callback,
                "ask",
                "需要补充信息",
                detail=question_text,
                meta="Ask",
                body=_ask_body(questions),
                status="asking",
                index=index,
                total=total_targets,
                material=target,
                extra={"questions": questions},
            )
            return {
                "execution_mode": "agent_loop",
                "status": "needs_input",
                "ask": True,
                "goal": prompt,
                "outputs": outputs,
                "materials": [_prompt_material_summary(item) for item in read_materials],
                "targets": [_prompt_material_summary(item) for item in targets],
                "agent_plan": plans,
                "child_tasks": child_tasks,
                "creative_mode": creative_mode,
                "reference_assets": [_reference_asset_summary(item) for item in reference_assets],
                "layer_plan": layer_plan,
                "render_passes": render_passes,
                "review_notes": review_notes,
                "human_feedback": human_feedback,
                "pending_ask": {"questions": questions},
                "_pending_ask_session": {
                    "prompt": prompt,
                    "video": str(target.get("source_path") or input_path or ""),
                    "output_path": output_path,
                    "agent": agent,
                    "execution_scope": scope,
                    "project_state": enriched_project_state,
                },
            }
        gated_plan, gate = _apply_capability_gate(
            plan,
            prompt=prompt,
            input_path=str(target.get("source_path") or input_path or ""),
            output_path=output_path,
            prompt_only_creation=prompt_only_creation,
        )
        if gate:
            plan = gated_plan
            _emit(
                event_callback,
                "capability_call",
                "稳定闸门已切换到本地预览",
                detail=", ".join(gate.get("blocked_functions") or []),
                meta="Capability gate",
                body=(
                    "这轮计划里出现了默认不自动进入主路径的高风险能力。"
                    "我先改走本地图层小样，保留可看的预览；如果你明确说要 Veo、Blender、公共素材抓取或开发 brief，我再打开对应能力。"
                ),
                status="succeeded",
                index=index,
                total=total_targets,
                material=target,
                extra={
                    "voice": "gemini",
                    "capability": "stability_gate",
                    "blocked_capabilities": gate.get("blocked_capabilities") or [],
                    "blocked_functions": gate.get("blocked_functions") or [],
                },
            )
        plan = _apply_multi_input_target_contract(
            plan,
            targets=targets,
            grouped_multi_input_targets=grouped_multi_input_targets,
        )
        _validate_plan_capability_contract(
            plan,
            prompt=prompt,
            targets=targets,
            prompt_only_creation=prompt_only_creation,
        )
        plan.setdefault("input_path", str(target.get("source_path") or input_path or ""))
        plan.setdefault("output_path", output_path)
        plan.setdefault("goal", prompt)
        plan.setdefault("creative_mode", creative_mode)
        plans.append({"material": _prompt_material_summary(target), "plan": plan})
        _emit(
            event_callback,
            "plan",
            "Gemini 规划",
            detail=_plan_detail(plan),
            meta=f"Planned material {index}/{total_targets}",
            body=_plan_body(plan),
            status="succeeded",
            index=index,
            total=total_targets,
            material=target,
            stats={"step_count": len(plan.get("steps") if isinstance(plan.get("steps"), list) else [])},
            extra={"voice": "gemini" if _plan_assistant_message(plan) else "lumeri"},
        )

        _emit(
            event_callback,
            "execute",
            f"执行：{name}",
            detail=f"{index}/{total_targets}",
            meta=f"Executing material {index}/{total_targets}",
            body=_execute_start_body(plan, output_path),
            index=index,
            total=total_targets,
            material=target,
        )

        def on_step(current: int, total: int, function_name: str, *, target_index: int = index) -> None:
            _check_cancel(should_cancel)
            if progress_callback is not None:
                global_current = (target_index - 1) * max(total, 1) + current
                global_total = total_targets * max(total, 1)
                progress_callback(global_current, global_total, function_name)
            step_key = (target_index, current, total, function_name)
            if step_key not in emitted_step_events:
                emitted_step_events.add(step_key)
                step_message = _step_assistant_message(plan, current, function_name)
                _emit(
                    event_callback,
                    "execute",
                    f"执行：{_function_label(function_name)}",
                    detail=f"{current}/{total}",
                    meta=f"Ran {_function_label(function_name)}",
                    command=_function_label(function_name),
                    body=step_message or f"我正在执行这一步处理：素材 {target_index}/{total_targets}，步骤 {current}/{total}。",
                    index=target_index,
                    total=total_targets,
                    material=target,
                    extra={
                        "step_index": current,
                        "step_total": total,
                        "target_index": target_index,
                        "target_total": total_targets,
                        "voice": "gemini" if step_message else "lumeri",
                    },
                )
                _emit(
                    event_callback,
                    "capability_call",
                    f"调用能力：{_function_label(function_name)}",
                    detail=f"{current}/{total}",
                    meta=f"Capability {_function_label(function_name)}",
                    command=_function_label(function_name),
                    body=_capability_call_body(function_name, plan, current),
                    index=target_index,
                    total=total_targets,
                    material=target,
                    extra={
                        "step_index": current,
                        "step_total": total,
                        "target_index": target_index,
                        "target_total": total_targets,
                        "voice": "gemini",
                        "capability": _capability_family(function_name),
                        "call": _step_call_snapshot(plan, current, function_name),
                    },
                )

        try:
            child_task_id = orch.run_plan_dict(plan, progress_callback=on_step)
        except Exception as exc:
            if (
                not stability_gate_enabled()
                or plan.get("stability_gate")
                or not _allow_runtime_layer_fallback(plan, prompt_only_creation=prompt_only_creation)
            ):
                raise
            original_functions = _plan_functions(plan)
            plan = _safe_layer_preview_plan(
                prompt=prompt,
                input_path=str(target.get("source_path") or input_path or ""),
                output_path=output_path,
                blocked_functions=original_functions,
            )
            if plans:
                plans[-1]["plan"] = plan
            _emit(
                event_callback,
                "capability_call",
                "执行失败已降级为本地预览",
                detail="local layer fallback",
                meta="Runtime fallback",
                body=(
                    "刚才的图层或外部执行没有通过稳定检查。"
                    "我没有继续沿着错误路径跑，而是切到一个确定可回放的本地图层小样。"
                ),
                status="succeeded",
                index=index,
                total=total_targets,
                material=target,
                extra={
                    "voice": "gemini",
                    "capability": "stability_gate",
                    "fallback_reason": exc.__class__.__name__,
                    "blocked_functions": original_functions,
                },
            )
            child_task_id = orch.run_plan_dict(plan, progress_callback=on_step)
        child_task = orch.get_task(child_task_id)
        child_outputs_raw = _unique_paths(
            _output_paths(child_task.get("all_outputs") or child_task.get("outputs") or [])
            + _output_paths(child_task.get("artifact_outputs") or [])
        )
        child_outputs = _media_outputs(child_outputs_raw)
        child_artifacts = _artifact_outputs(child_outputs_raw)
        if not child_outputs and not child_artifacts:
            raise UserInputError(
                "这一步执行结束了，但没有产生可用媒体或文档产物。我不会把空结果标记为成功。",
                detail=f"child_task={child_task_id} outputs={child_outputs_raw}",
            )
        outputs.extend(child_outputs)
        artifact_outputs.extend(child_artifacts)
        child_render_passes = _render_passes_for_child(
            child_task_id=child_task_id,
            target=target,
            plan=plan,
            outputs=child_outputs_raw,
        )
        render_passes.extend(child_render_passes)
        layer_plan = _merge_layer_plan_snapshot(layer_plan, child_render_passes)
        child_review_notes = _review_notes_for_passes(child_render_passes, prompt=prompt)
        review_notes.extend(child_review_notes)
        child_tasks.append(
            {
                "task_id": child_task_id,
                "material_id": target.get("material_id"),
                "asset_id": target.get("asset_id"),
                "clip_id": target.get("clip_id"),
                "outputs": child_outputs,
                "artifact_outputs": child_artifacts,
                "all_outputs": child_outputs_raw,
                "render_pass_ids": [str(item.get("render_pass_id")) for item in child_render_passes],
            }
        )
        for render_pass in child_render_passes:
            preview_path = str(render_pass.get("preview_path") or "")
            if render_pass.get("status") == "succeeded" and preview_path:
                _emit(
                    event_callback,
                    "preview_ready",
                    "小样已生成",
                    detail=str(render_pass.get("kind") or "preview"),
                    meta="Preview ready",
                    body=_preview_ready_body(render_pass),
                    outputs=[preview_path],
                    status="succeeded",
                    index=index,
                    total=total_targets,
                    material=target,
                    extra={
                        "voice": "gemini",
                        "render_pass_id": render_pass.get("render_pass_id"),
                        "layer_ids": render_pass.get("layer_ids") or [],
                    },
                )
            if render_pass.get("dev_brief_path"):
                _emit(
                    event_callback,
                    "dev_brief",
                    "开发补丁 brief",
                    detail=str(render_pass.get("dev_brief_path")),
                    meta="Development brief",
                    body=_dev_brief_body(render_pass),
                    outputs=[str(render_pass.get("dev_brief_path"))],
                    status="succeeded",
                    index=index,
                    total=total_targets,
                    material=target,
                    extra={"voice": "gemini", "render_pass_id": render_pass.get("render_pass_id")},
                )
        for note in child_review_notes:
            _emit(
                event_callback,
                "self_review",
                "自审",
                detail=str(note.get("verdict") or "reviewed"),
                meta="Self review",
                body=str(note.get("note") or ""),
                status="succeeded" if note.get("verdict") != "needs_work" else "warning",
                index=index,
                total=total_targets,
                material=target,
                extra={
                    "voice": "gemini",
                    "review_note_id": note.get("review_note_id"),
                    "render_pass_id": note.get("render_pass_id"),
                },
            )
        _emit(
            event_callback,
            "result",
            f"完成：{name}",
            detail=f"媒体 {len(child_outputs)} 个，文档 {len(child_artifacts)} 个",
            meta=f"Completed material {index}/{total_targets}",
            body=_material_result_body(child_outputs, child_artifacts),
            outputs=child_outputs,
            status="succeeded",
            index=index,
            total=total_targets,
            material=target,
        )

    _emit(
        event_callback,
        "result",
        "完成汇报",
        detail=f"生成 {len(outputs)} 个媒体输出，{len(artifact_outputs)} 个文档产物",
        meta="Completed agent loop",
        body=_final_report_body(
            prompt=prompt,
            targets=targets,
            outputs=outputs,
            child_tasks=child_tasks,
            artifact_outputs=artifact_outputs,
        ),
        outputs=outputs,
        status="succeeded",
        stats={
            "target_count": len(targets),
            "output_count": len(outputs),
            "artifact_count": len(artifact_outputs),
            "child_task_count": len(child_tasks),
        },
    )

    return {
        "execution_mode": "agent_loop",
        "goal": prompt,
        "outputs": outputs,
        "artifact_outputs": _unique_paths(artifact_outputs),
        "all_outputs": _unique_paths(outputs + artifact_outputs),
        "materials": [_prompt_material_summary(item) for item in read_materials],
        "targets": [_prompt_material_summary(item) for item in targets],
        "agent_plan": plans,
        "child_tasks": child_tasks,
        "creative_mode": creative_mode,
        "reference_assets": [_reference_asset_summary(item) for item in reference_assets],
        "layer_plan": layer_plan,
        "render_passes": render_passes,
        "review_notes": review_notes,
        "human_feedback": human_feedback,
    }


def _needs_current_timeline_media_result(
    *,
    prompt: str,
    project_state: dict[str, Any] | None,
    scope: str | None,
    agent: str | None,
    event_callback: AgentEventCallback | None,
) -> dict[str, Any]:
    questions = [
        {
            "id": "add_timeline_media",
            "text": "当前时间线没有媒体。请先通过媒体池 + 或导入媒体添加至少一段素材到时间线后再运行。",
            "input_type": "media",
        }
    ]
    _emit(
        event_callback,
        "ask",
        "需要先添加时间线媒体",
        detail="当前时间线为空",
        meta="Ask",
        body="当前时间线没有可执行媒体，并且这次请求限定使用当前时间线；我不会从媒体库、历史会话或旧会话替代素材生成 MP4。",
        status="asking",
        extra={"questions": questions},
    )
    return {
        "execution_mode": "agent_loop",
        "status": "needs_input",
        "ask": True,
        "goal": prompt,
        "outputs": [],
        "artifact_outputs": [],
        "all_outputs": [],
        "materials": [],
        "targets": [],
        "agent_plan": [],
        "child_tasks": [],
        "creative_mode": "timeline",
        "reference_assets": [],
        "layer_plan": {},
        "render_passes": [],
        "review_notes": [],
        "human_feedback": {},
        "pending_ask": {"questions": questions},
        "_pending_ask_session": {
            "prompt": prompt,
            "video": "",
            "output_path": "",
            "agent": agent,
            "execution_scope": scope,
            "project_state": project_state if isinstance(project_state, dict) else {},
        },
    }


def _apply_capability_gate(
    plan: dict[str, Any],
    *,
    prompt: str,
    input_path: str,
    output_path: str,
    prompt_only_creation: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Downgrade risky unrequested capabilities to a local layer preview."""
    if not stability_gate_enabled():
        return plan, None
    blocked = _blocked_plan_capabilities(plan, prompt)
    if not blocked:
        return plan, None
    blocked_functions = _plan_functions(plan)
    if not prompt_only_creation:
        raise UserInputError(
            "这次 Gemini 选择了默认不自动执行的高风险能力，所以我没有继续硬跑，也不会把它伪装成本地小样成功。",
            detail=f"blocked_capabilities={blocked}; blocked_functions={blocked_functions}",
        )
    fallback = _safe_layer_preview_plan(
        prompt=prompt,
        input_path=input_path,
        output_path=output_path,
        blocked_functions=blocked_functions,
    )
    return fallback, {
        "blocked_capabilities": blocked,
        "blocked_functions": blocked_functions,
    }


def _allow_runtime_layer_fallback(plan: dict[str, Any], *, prompt_only_creation: bool) -> bool:
    """Only fallback after runtime failure for blank-canvas creative drafts.

    Core editing primitives such as timeline edits, color, and transitions must
    fail visibly when their inputs are wrong. Otherwise Lumeri can turn a real
    primitive failure into a fake successful layer preview, which is worse than
    a clear failure for editing work.
    """
    if not prompt_only_creation:
        return False
    functions = _plan_functions(plan)
    if not functions:
        return False
    return all("gemia.video.layer_flow.render_layer_workflow" in function for function in functions)


def _allow_planning_layer_fallback(
    exc: BaseException,
    *,
    prompt_only_creation: bool,
    prompt: str,
) -> bool:
    if not prompt_only_creation or not stability_gate_enabled():
        return False
    if _prompt_explicitly_allows(
        prompt,
        ("veo", "openrouter", "外部生成", "外部模型", "external generation", "external provider"),
    ):
        return False
    text = str(exc).lower()
    provider_failure_terms = (
        "openrouter",
        "veo",
        "external",
        "provider",
        "empty response",
        "returned empty",
        "timeout",
        "timed out",
        "外部生成",
        "外部服务",
    )
    return any(term in text for term in provider_failure_terms)


def _enforce_target_gate(*, prompt: str, targets: list[dict[str, Any]], scope: str | None) -> None:
    """Fail early when a request shape cannot satisfy the primitive contract."""
    video_targets = [
        item for item in targets
        if str(item.get("media_kind") or "video").lower() == "video"
    ]
    if (
        _prompt_requires_two_transition_targets(prompt, scope)
        and len(video_targets) < 2
    ):
        raise UserInputError(
            "这个转场需要两段相邻的可播放视频。当前只找到一段目标素材，所以我不会生成假预览；请把第二段素材放到时间线相邻位置后重试。",
            detail=f"transition target gate failed: target_count={len(video_targets)} prompt={prompt}",
        )


def _execution_targets_for_prompt(targets: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
    """Collapse ordered multi-input transition targets into one planner run."""
    if (
        len(targets) >= 2
        and _prompt_wants_adjacent_transition(prompt)
        and not _prompt_wants_timeline_broll_concat(prompt)
    ):
        selected = next((item for item in targets if item.get("selected")), None)
        return [selected or targets[min(1, len(targets) - 1)]]
    return targets


def _apply_multi_input_target_contract(
    plan: dict[str, Any],
    *,
    targets: list[dict[str, Any]],
    grouped_multi_input_targets: bool,
) -> dict[str, Any]:
    """Pin two-input transition steps to the selected adjacent target pair."""
    if not grouped_multi_input_targets or len(targets) < 2:
        return plan
    input_paths = [
        str(item.get("source_path") or "").strip()
        for item in targets[:2]
        if str(item.get("source_path") or "").strip()
        and str(item.get("media_kind") or "video").lower() == "video"
    ]
    if len(input_paths) < 2:
        return plan
    updated = False
    next_plan = deepcopy(plan)
    for step in next_plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if not _is_two_input_transition_function(str(step.get("function") or "")):
            continue
        if _step_input_count(step.get("input")) >= 2:
            continue
        step["input"] = input_paths[:2]
        updated = True
    if updated:
        next_plan.setdefault("plan_contract", {})
        contract = next_plan.get("plan_contract")
        if isinstance(contract, dict):
            warnings = contract.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append({"kind": "pinned_adjacent_transition_inputs", "input_count": 2})
    return next_plan


def _validate_plan_capability_contract(
    plan: dict[str, Any],
    *,
    prompt: str,
    targets: list[dict[str, Any]],
    prompt_only_creation: bool,
) -> None:
    """Fail before execution when a plan cannot satisfy known primitive arity."""
    if prompt_only_creation:
        return
    video_targets = [
        item for item in targets
        if str(item.get("media_kind") or "video").lower() == "video" and item.get("source_path")
    ]
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        function_name = str(step.get("function") or "")
        if not _is_two_input_transition_function(function_name):
            continue
        if len(video_targets) < 2 and _step_input_count(step.get("input")) < 2:
            raise UserInputError(
                "这个转场计划缺少第二段视频输入。我不会继续执行，也不会用文字/图层小样冒充成功。",
                detail=(
                    f"transition capability contract failed: function={function_name} "
                    f"target_count={len(video_targets)} input={step.get('input')!r} prompt={prompt}"
                ),
            )


def _is_two_input_transition_function(function_name: str) -> bool:
    lowered = str(function_name or "").lower()
    return (
        ".transitions.transition_" in lowered
        or lowered.endswith(".transition_dissolve")
        or lowered.endswith(".transition_wipe")
        or lowered.endswith(".transition_push")
        or lowered.endswith(".transition_custom")
        or lowered.endswith(".transition_shutter")
    )


def _step_input_count(value: Any) -> int:
    if isinstance(value, list):
        return len([item for item in value if str(item or "").strip()])
    if str(value or "").strip():
        return 1
    return 0


def _timeline_broll_targets(
    materials: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    prompt: str,
) -> list[dict[str, Any]]:
    if not _prompt_wants_timeline_broll_concat(prompt):
        return []
    candidates = [
        item
        for item in materials
        if str(item.get("media_kind") or "").lower() == "video"
        and str(item.get("origin") or "").lower() == "timeline"
        and item.get("source_path")
    ]
    if len(candidates) < 2:
        candidates = [
            item
            for item in targets
            if str(item.get("media_kind") or "").lower() == "video" and item.get("source_path")
        ]
    explicit_candidates = _explicit_prompt_material_targets(candidates, prompt)
    if len(explicit_candidates) < 2:
        fallback_candidates = [
            item
            for item in targets
            if str(item.get("media_kind") or "").lower() == "video" and item.get("source_path")
        ]
        explicit_candidates = _explicit_prompt_material_targets(fallback_candidates, prompt)
    if len(explicit_candidates) >= 2:
        return explicit_candidates
    requested_count = _requested_timeline_clip_count(prompt)
    if requested_count:
        candidates = candidates[:requested_count]
    return candidates if len(candidates) >= 2 else []


def _requested_timeline_clip_count(prompt: str) -> int | None:
    text = str(prompt or "")
    lowered = text.lower()
    explicit_counts: tuple[tuple[int, tuple[str, ...]], ...] = (
        (4, ("四段", "四个", "4段", "4 段", "4个", "4 个", "four clips")),
        (3, ("三段", "三个", "3段", "3 段", "3个", "3 个", "three clips")),
        (2, ("两段", "两个", "2段", "2 段", "2个", "2 个", "two clips", "both clips")),
    )
    for count, terms in explicit_counts:
        if any(term in text or term in lowered for term in terms):
            return count
    return None


def _prompt_wants_timeline_broll_concat(prompt: str) -> bool:
    text = str(prompt or "")
    lowered = text.lower()
    has_timeline = _prompt_wants_multi_timeline_materials(text) or "时间线" in text or "timeline" in lowered
    has_concat = any(term in text or term in lowered for term in TIMELINE_CONCAT_WORDS)
    has_short = bool(_requested_total_duration_sec(text, default=0.0))
    return has_timeline and has_concat and has_short


def _run_timeline_broll_preview(
    orch: Any,
    *,
    prompt: str,
    read_materials: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    creative_mode: str,
    reference_assets: list[dict[str, Any]],
    layer_plan: dict[str, Any],
    review_notes: list[dict[str, Any]],
    human_feedback: list[dict[str, Any]],
    event_callback: AgentEventCallback | None,
) -> dict[str, Any]:
    output_path = str((orch.outputs_dir / f"ai_{uuid.uuid4().hex[:8]}_1.mp4").resolve())
    duration_sec = _requested_total_duration_sec(prompt, default=3.0)
    hard_cut = _prompt_requests_hard_cut(prompt)
    transition_sec = 0.0 if hard_cut else min(0.35, max(0.2, duration_sec * 0.1))
    transition_body = "硬切" if hard_cut else "叠化"
    transition_detail = "硬切无重叠" if hard_cut else "柔和叠化"
    _emit(
        event_callback,
        "plan",
        "规划时间线拼接",
        detail=f"{len(targets)} 段 -> {duration_sec:.1f}s",
        meta="Timeline concat plan",
        body=(
            f"这次请求明确使用当前时间线里的多段视频，并要求短 B-roll、{transition_body}和固定时长。"
            f"我会绕开单素材规划，直接按时间线分段裁切、{transition_body}并做轻微暖色调色。"
        ),
        status="succeeded",
        stats={"material_count": len(read_materials), "target_count": len(targets)},
        extra={"voice": "lumeri", "capability": "timeline"},
    )
    _emit(
        event_callback,
        "execute",
        "执行：时间线拼接",
        detail="timeline_broll_concat",
        meta="Running timeline concat",
        command="timeline_broll_concat",
        body=f"我正在把时间线 {len(targets)} 段素材各裁出短片段，段落之间做{transition_detail}，并输出一个本地 MP4 小样。",
        status="running",
        index=1,
        total=1,
        extra={"voice": "lumeri", "step_index": 1, "step_total": 1},
    )
    _emit(
        event_callback,
        "capability_call",
        "调用能力：timeline_broll_concat",
        detail=f"{len(targets)} clips",
        meta="Capability timeline_broll_concat",
        command="timeline_broll_concat",
        body="这一步使用本地 FFmpeg 时间线拼接路径，不会添加标题、字幕或稳定闸门说明文字。",
        status="running",
        extra={"voice": "lumeri", "capability": "timeline"},
    )
    render_meta = _render_timeline_broll_preview(
        targets,
        output_path=output_path,
        duration_sec=duration_sec,
        transition_sec=transition_sec,
        prompt=prompt,
    )
    render_pass = {
        "render_pass_id": f"pass_{uuid.uuid4().hex[:12]}",
        "kind": "timeline_broll_preview",
        "status": "succeeded",
        "preview_path": output_path,
        "output_path": output_path,
        "capabilities": ["timeline", "transition", "color"],
        "step_functions": ["gemia.agent_workflow.timeline_broll_concat"],
        "source_materials": [_prompt_material_summary(item) for item in targets],
        "duration_sec": duration_sec,
        "transition_sec": transition_sec,
        "metadata": render_meta,
        "created_at": _utc_now(),
    }
    render_passes = [render_pass]
    layer_plan = _merge_layer_plan_snapshot(layer_plan, render_passes)
    review_notes = review_notes + _review_notes_for_passes(render_passes, prompt=prompt)
    outputs = [output_path]
    child_tasks: list[dict[str, Any]] = []
    _emit(
        event_callback,
        "preview_ready",
        "小样已生成",
        detail="timeline_broll_preview",
        meta="Preview ready",
        body=_preview_ready_body(render_pass),
        outputs=outputs,
        status="succeeded",
        index=1,
        total=1,
        extra={"voice": "lumeri", "render_pass_id": render_pass["render_pass_id"]},
    )
    for note in review_notes[-1:]:
        _emit(
            event_callback,
            "self_review",
            "自审",
            detail=str(note.get("verdict") or "reviewed"),
            meta="Self review",
            body=str(note.get("note") or ""),
            status="succeeded" if note.get("verdict") != "needs_work" else "warning",
            index=1,
            total=1,
            extra={"voice": "lumeri", "render_pass_id": render_pass["render_pass_id"]},
        )
    _emit(
        event_callback,
        "result",
        "完成：时间线拼接",
        detail="媒体 1 个，文档 0 个",
        meta="Completed timeline concat",
        body=_material_result_body(outputs, []),
        outputs=outputs,
        status="succeeded",
        index=1,
        total=1,
    )
    _emit(
        event_callback,
        "result",
        "完成汇报",
        detail="生成 1 个媒体输出，0 个文档产物",
        meta="Completed agent loop",
        body=_final_report_body(
            prompt=prompt,
            targets=targets,
            outputs=outputs,
            child_tasks=child_tasks,
            artifact_outputs=[],
        ),
        outputs=outputs,
        status="succeeded",
        stats={
            "target_count": len(targets),
            "output_count": len(outputs),
            "artifact_count": 0,
            "child_task_count": 0,
        },
    )
    return {
        "execution_mode": "agent_loop",
        "goal": prompt,
        "outputs": outputs,
        "artifact_outputs": [],
        "all_outputs": outputs,
        "materials": [_prompt_material_summary(item) for item in read_materials],
        "targets": [_prompt_material_summary(item) for item in targets],
        "agent_plan": [
            {
                "material": [_prompt_material_summary(item) for item in targets],
                "plan": {
                    "version": "2.0",
                    "goal": prompt,
                    "assistant_message": "我会直接按时间线双素材短片路径裁切、叠化和调色。",
                    "steps": [
                        {
                            "id": "timeline_broll_concat",
                            "function": "gemia.agent_workflow.timeline_broll_concat",
                    "args": {
                        "duration_sec": duration_sec,
                        "transition_sec": transition_sec,
                        "clip_count": len(targets),
                        "no_text_overlay": True,
                    },
                        }
                    ],
                },
            }
        ],
        "child_tasks": child_tasks,
        "creative_mode": creative_mode,
        "reference_assets": [_reference_asset_summary(item) for item in reference_assets],
        "layer_plan": layer_plan,
        "render_passes": render_passes,
        "review_notes": review_notes,
        "human_feedback": human_feedback,
    }


def run_timeline_kept_clip_merge(
    orch: Any,
    *,
    clips: list[dict[str, Any]],
    project_state: dict[str, Any] | None = None,
    account_id: str | None = None,
    event_callback: AgentEventCallback | None = None,
) -> dict[str, Any]:
    """Render the frontend timeline's kept clips into one local MP4."""
    kept_clips = [dict(item) for item in clips if isinstance(item, dict) and item.get("keep") is not False]
    if not kept_clips:
        raise ValueError("没有可合并的保留片段")

    merge_state = deepcopy(project_state) if isinstance(project_state, dict) else {}
    if is_canonical_project(merge_state):
        timeline = merge_state.get("timeline") if isinstance(merge_state.get("timeline"), dict) else {}
        merge_state["timeline"] = {**timeline, "clips": kept_clips}
    else:
        merge_state["clips"] = kept_clips

    materials = collect_materials(merge_state, account_id=account_id, include_library=False)
    read_materials = read_all_materials(materials, event_callback=event_callback)
    targets = [
        item for item in read_materials
        if str(item.get("media_kind") or "video") == "video" and item.get("source_path")
    ]
    if not targets:
        raise ValueError("保留片段里没有可合并的视频素材")

    output_path = str((orch.outputs_dir / f"merge_{uuid.uuid4().hex[:8]}.mp4").resolve())
    total_duration = sum(_merge_clip_duration(item) for item in targets)
    _emit(
        event_callback,
        "plan",
        "规划保留片段合并",
        detail=f"{len(targets)} 段 -> {total_duration:.1f}s",
        meta="Timeline merge plan",
        body="我会按当前时间线顺序把标记为保留的片段逐段裁出并合并成一个本地 MP4，不添加字幕、标题或说明文字。",
        status="succeeded",
        stats={"material_count": len(read_materials), "target_count": len(targets)},
        extra={"voice": "lumeri", "capability": "timeline"},
    )
    _emit(
        event_callback,
        "execute",
        "执行：合并保留片段",
        detail="timeline_merge_kept",
        meta="Running kept clip merge",
        command="timeline_merge_kept",
        body=f"正在把 {len(targets)} 个保留片段按时间线顺序合并成一个可预览 MP4。",
        status="running",
        index=1,
        total=1,
        extra={"voice": "lumeri", "step_index": 1, "step_total": 1},
    )
    render_meta = _render_timeline_kept_clip_merge(targets, output_path=output_path)
    render_pass = {
        "render_pass_id": f"pass_{uuid.uuid4().hex[:12]}",
        "kind": "timeline_merge_kept",
        "status": "succeeded",
        "preview_path": output_path,
        "output_path": output_path,
        "capabilities": ["timeline", "concat"],
        "step_functions": ["gemia.agent_workflow.timeline_merge_kept"],
        "source_materials": [_prompt_material_summary(item) for item in targets],
        "duration_sec": render_meta.get("duration_sec"),
        "metadata": render_meta,
        "created_at": _utc_now(),
    }
    render_passes = [render_pass]
    layer_plan = _merge_layer_plan_snapshot({}, render_passes)
    review_notes = _review_notes_for_passes(render_passes, prompt="合并保留片段")
    outputs = [output_path]
    _emit(
        event_callback,
        "preview_ready",
        "合并小样已生成",
        detail="timeline_merge_kept",
        meta="Preview ready",
        body=_preview_ready_body(render_pass),
        outputs=outputs,
        status="succeeded",
        index=1,
        total=1,
        extra={"voice": "lumeri", "render_pass_id": render_pass["render_pass_id"]},
    )
    _emit(
        event_callback,
        "result",
        "完成：合并保留片段",
        detail="媒体 1 个，文档 0 个",
        meta="Completed kept clip merge",
        body=_material_result_body(outputs, []),
        outputs=outputs,
        status="succeeded",
        index=1,
        total=1,
    )
    return {
        "execution_mode": "agent_loop",
        "goal": "合并保留片段",
        "outputs": outputs,
        "artifact_outputs": [],
        "all_outputs": outputs,
        "materials": [_prompt_material_summary(item) for item in read_materials],
        "targets": [_prompt_material_summary(item) for item in targets],
        "agent_plan": [
            {
                "material": [_prompt_material_summary(item) for item in targets],
                "plan": {
                    "version": "2.0",
                    "goal": "合并保留片段",
                    "assistant_message": "我会直接合并当前时间线里标记为保留的片段。",
                    "steps": [
                        {
                            "id": "timeline_merge_kept",
                            "function": "gemia.agent_workflow.timeline_merge_kept",
                            "args": {"clip_count": len(targets), "no_text_overlay": True},
                        }
                    ],
                },
            }
        ],
        "child_tasks": [],
        "creative_mode": "timeline_guided",
        "reference_assets": [],
        "layer_plan": layer_plan,
        "render_passes": render_passes,
        "review_notes": review_notes,
        "human_feedback": [],
    }


def _requested_total_duration_sec(prompt: str, *, default: float) -> float:
    text = str(prompt or "")
    requested = _explicit_target_duration_sec(text)
    if requested is not None:
        return requested
    values: list[float] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*秒", text):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue
    if not values:
        return default
    return max(0.5, min(max(values), 30.0))


def _explicit_target_duration_sec(prompt: str) -> float | None:
    text = str(prompt or "")
    patterns = (
        r"(?:严格|总长|总时长|目标时长|目标|时长|压到|压成|缩到|控制在|剪成|做成|生成|导出|输出)[^。；，,.!?！？\n]{0,24}?(\d+(?:\.\d+)?)\s*秒",
        r"(\d+(?:\.\d+)?)\s*秒[^。；，,.!?！？\n]{0,18}?(?:横版|竖版|小样|b-roll|broll|mp4|MP4|成片|预览)",
    )
    matches: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                matches.append(float(match.group(1)))
            except (IndexError, ValueError):
                continue
    if not matches:
        return None
    return max(0.5, min(matches[-1], 30.0))


def _requested_canvas_size(prompt: str, *, default: tuple[int, int] = (1280, 720)) -> tuple[int, int]:
    text = str(prompt or "")
    matches: list[tuple[int, int]] = []
    for match in re.finditer(r"(\d{3,5})\s*(?:x|X|×|\*)\s*(\d{3,5})", text):
        try:
            width = int(match.group(1))
            height = int(match.group(2))
        except (IndexError, ValueError):
            continue
        if 128 <= width <= 8192 and 128 <= height <= 8192:
            matches.append((width, height))
    if matches:
        return matches[-1]
    return default


def _requested_segment_durations_sec(prompt: str, *, count: int, total_sec: float) -> list[float] | None:
    text = str(prompt or "")
    if count <= 0:
        return None
    aliases_by_index = _segment_duration_aliases(count)
    exact: dict[int, float] = {}
    minimum: dict[int, float] = {}
    maximum: dict[int, float] = {}
    if count >= 2:
        for match in re.finditer(
            r"(?:^|[：:，,、；;。！？!\s])前\s*(\d+(?:\.\d+)?)\s*秒(?!\s*(?:时|处|位置|附近|的时候))",
            text,
        ):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if value > 0:
                exact.setdefault(0, value)
        for match in re.finditer(
            r"(?:^|[：:，,、；;。！？!\s])后\s*(\d+(?:\.\d+)?)\s*秒(?!\s*(?:时|处|位置|附近|的时候))",
            text,
        ):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if value > 0:
                exact.setdefault(count - 1, value)
        for match in re.finditer(
            r"(\d+(?:\.\d+)?)\s*秒后\s*(?:硬切|直接切|切到|切换|hard cut|straight cut|cut)",
            text,
            re.IGNORECASE,
        ):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if value > 0:
                exact.setdefault(0, value)
    for index, aliases in enumerate(aliases_by_index):
        for alias in aliases:
            for match in re.finditer(re.escape(alias) + r"[^。；，!?！？\n]{0,96}?(\d+(?:\.\d+)?)\s*秒", text):
                try:
                    value = float(match.group(1))
                except ValueError:
                    continue
                if value <= 0:
                    continue
                phrase = match.group(0)
                is_maximum = re.search(r"(?:最多|至多|不超过|不要超过|上限|以内)", phrase)
                is_minimum = re.search(r"(?:至少|最少|不少于|不低于|以上)", phrase)
                following = text[match.end() : match.end() + 6]
                if not (is_maximum or is_minimum) and re.match(r"\s*(?:时|处|位置|附近|的时候)", following):
                    continue
                if is_maximum:
                    maximum[index] = value
                elif is_minimum:
                    minimum[index] = value
                else:
                    exact[index] = value
    if len(exact) == count:
        values = [exact[index] for index in range(count)]
        total = sum(values)
        if total <= 0:
            return None
        if abs(total - total_sec) > max(0.2, total_sec * 0.12):
            scale = total_sec / total
            values = [value * scale for value in values]
        return values
    if not (exact or minimum or maximum):
        return None
    return _fit_requested_segment_constraints(
        count=count,
        total_sec=total_sec,
        exact=exact,
        minimum=minimum,
        maximum=maximum,
    )


def _prompt_requests_hard_cut(prompt: str) -> bool:
    text = str(prompt or "").lower()
    positive_terms = (
        "硬切",
        "直接切",
        "切到",
        "hard cut",
        "hard-cut",
        "straight cut",
        "cut only",
    )
    negative_transition_terms = (
        "不要叠化",
        "不叠化",
        "不要交叉淡化",
        "不要交叉溶解",
        "不要溶解",
        "不要淡入淡出",
        "不要叠加残影",
        "不要残影",
        "无叠化",
        "无交叉淡化",
        "no crossfade",
        "no cross fade",
        "no dissolve",
        "no overlap",
        "without crossfade",
        "without dissolve",
        "without overlap",
    )
    return any(term in text for term in positive_terms) or any(term in text for term in negative_transition_terms)


def _requested_timeline_broll_clip_transforms(prompt: str, *, count: int) -> list[dict[str, Any]]:
    transforms: list[dict[str, Any]] = [
        {"clip_index": index, "zoom": 1.0, "crop": False, "anchor": "center", "crop_offset_x": 0.0, "crop_offset_y": 0.0}
        for index in range(max(count, 0))
    ]
    if count <= 0:
        return transforms

    text = str(prompt or "")
    lowered = text.lower()
    if not re.search(r"(?:放大|裁切|裁剪|zoom|crop|scale)", lowered):
        return transforms

    zoom_matches: list[tuple[float, int, int]] = []
    percent_patterns = (
        r"(?:放大(?:裁切)?|缩放|裁切|裁剪|zoom|scale)[^。；，,.!?！？\n]{0,10}?(?:到|至|为|成|设为|设置为|提高到|提高至|提升到|提升至|改到|调整到|调到)?\s*(\d+(?:\.\d+)?)\s*(?:%|％)",
        r"(\d+(?:\.\d+)?)\s*(?:%|％)[^。；，,.!?！？\n]{0,16}?(?:放大|裁切|裁剪|zoom|crop|scale)",
    )
    for pattern in percent_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                zoom_matches.append((float(match.group(1)) / 100.0, match.start(), match.end()))
            except ValueError:
                continue
    for match in re.finditer(
        r"(?:放大(?:裁切)?|缩放|裁切|裁剪|zoom|scale)[^。；，,.!?！？\n]{0,10}?(?:到|至|为|成|设为|设置为|提高到|提高至|提升到|提升至|改到|调整到|调到)?\s*(\d+(?:\.\d+)?)\s*(?:x|倍)",
        text,
        re.IGNORECASE,
    ):
        try:
            zoom_matches.append((float(match.group(1)), match.start(), match.end()))
        except ValueError:
            continue
    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(?:x|倍)[^。；，,.!?！？\n]{0,16}?(?:放大|裁切|裁剪|zoom|crop|scale)",
        text,
        re.IGNORECASE,
    ):
        try:
            zoom_matches.append((float(match.group(1)), match.start(), match.end()))
        except ValueError:
            continue
    for match in re.finditer(
        r"(?:zoom|缩放)[^。；，,.!?！？\n]{0,10}?(?:=|:|：|为|到|至|设为|设置为)\s*(\d+(?:\.\d+)?)\b",
        text,
        re.IGNORECASE,
    ):
        try:
            zoom_matches.append((float(match.group(1)), match.start(), match.end()))
        except ValueError:
            continue
    for match in re.finditer(
        r"(?:zoom|scale|缩放)\s+(\d+(?:\.\d+)?)\b",
        text,
        re.IGNORECASE,
    ):
        try:
            zoom_matches.append((float(match.group(1)), match.start(), match.end()))
        except ValueError:
            continue

    for zoom, start, end in zoom_matches:
        if _timeline_broll_transform_zoom_is_view_context(text, start, end):
            continue
        zoom = max(1.0, min(zoom, 2.5))
        if zoom <= 1.001:
            continue
        context = text[max(0, start - 80) : min(len(text), end + 80)]
        target_indexes = _nearest_timeline_broll_transform_target_indexes(text, start, count)
        if not target_indexes:
            target_indexes = _timeline_broll_transform_target_indexes(context, count)
        if not target_indexes:
            target_indexes = _timeline_broll_transform_target_indexes(text, count)
        if not target_indexes:
            target_indexes = list(range(count)) if re.search(r"(?:整体|全部|所有|全片|all|entire)", lowered) else [count - 1]
        for index in target_indexes:
            transforms[index]["zoom"] = max(float(transforms[index]["zoom"]), zoom)
            transforms[index]["crop"] = True
    _apply_timeline_broll_crop_offset_feedback(text, transforms)
    return transforms


def _apply_timeline_broll_crop_offset_feedback(text: str, transforms: list[dict[str, Any]]) -> None:
    count = len(transforms)
    if count <= 0:
        return
    explicit_offset_x_patterns: tuple[tuple[str, float], ...] = (
        (
            r"(?:crop_offset_x|offset_x|horizontal offset|水平偏移|横向偏移)[^。；，,.!?！？\n]{0,16}?(?:到|至|为|成|设为|设置为|=|:|：)?\s*(-?\d+(?:\.\d+)?)\s*(%|％)?",
            0.0,
        ),
        (
            r"(?:取景向右|向右平移|右移偏移|取景右移|pan right|move right|shift right)[^。；，,.!?！？\n]{0,16}?(-?\d+(?:\.\d+)?)\s*(%|％)?",
            1.0,
        ),
        (
            r"(?:取景向左|向左平移|左移偏移|取景左移|pan left|move left|shift left)[^。；，,.!?！？\n]{0,16}?(-?\d+(?:\.\d+)?)\s*(%|％)?",
            -1.0,
        ),
    )
    for pattern, direction in explicit_offset_x_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                offset_x = float(match.group(1))
            except ValueError:
                continue
            if match.lastindex and match.lastindex >= 2 and match.group(2):
                offset_x /= 100.0
            elif abs(offset_x) > 1.0:
                offset_x /= 100.0
            if direction < 0 and offset_x > 0:
                offset_x = -offset_x
            elif direction > 0 and offset_x < 0:
                offset_x = abs(offset_x)
            offset_x = max(-0.45, min(0.45, offset_x))
            if abs(offset_x) <= 0.001:
                continue
            context = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
            target_indexes = _nearest_timeline_broll_transform_target_indexes(text, match.start(), count)
            if not target_indexes:
                target_indexes = _timeline_broll_transform_target_indexes(context, count)
            if not target_indexes:
                target_indexes = [count - 1]
            for index in target_indexes:
                transforms[index]["crop"] = True
                transforms[index]["crop_offset_x"] = offset_x
    explicit_offset_patterns: tuple[str, ...] = (
        r"(?:crop_offset_y|offset_y|vertical offset|垂直偏移|取景下移|下移偏移)[^。；，,.!?！？\n]{0,16}?(?:到|至|为|成|设为|设置为|=|:|：)?\s*(-?\d+(?:\.\d+)?)\s*(%|％)?",
        r"(?:下移|向下|pan down|move down)[^。；，,.!?！？\n]{0,16}?(-?\d+(?:\.\d+)?)\s*(%|％)",
    )
    for pattern in explicit_offset_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                offset_y = float(match.group(1))
            except ValueError:
                continue
            if match.lastindex and match.lastindex >= 2 and match.group(2):
                offset_y /= 100.0
            elif abs(offset_y) > 1.0:
                offset_y /= 100.0
            offset_y = max(-0.45, min(0.45, offset_y))
            if abs(offset_y) <= 0.001:
                continue
            context = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
            target_indexes = _nearest_timeline_broll_transform_target_indexes(text, match.start(), count)
            if not target_indexes:
                target_indexes = _timeline_broll_transform_target_indexes(context, count)
            if not target_indexes:
                target_indexes = [count - 1]
            anchor = "lower" if offset_y > 0 else "upper"
            for index in target_indexes:
                transforms[index]["crop"] = True
                transforms[index]["anchor"] = anchor
                transforms[index]["crop_offset_y"] = offset_y
    strong_down_pattern = (
        r"(?:继续|再|进一步|更|更加|明显|强|更多)[^。；，,.!?！？\n]{0,12}(?:下移|向下|下方|down)"
        r"|(?:下移|向下|下方|down)[^。；，,.!?！？\n]{0,12}(?:继续|再|进一步|更|更加|明显|强|更多)"
    )
    offset_patterns: tuple[tuple[str, float, str], ...] = (
        (
            strong_down_pattern,
            0.45,
            "lower",
        ),
        (
            r"(?:避开|避掉|去掉|裁掉|减少)[^。；，,.!?！？\n]{0,16}(?:顶部|上方|上边)[^。；，,.!?！？\n]{0,16}(?:手指|手|遮挡)"
            r"|(?:取景|画面|crop|pan)[^。；，,.!?！？\n]{0,12}(?:下移|向下|下方|down)",
            0.30,
            "lower",
        ),
    )
    for pattern, offset_y, anchor in offset_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            context = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
            target_indexes = _nearest_timeline_broll_transform_target_indexes(text, match.start(), count)
            if not target_indexes:
                target_indexes = _timeline_broll_transform_target_indexes(context, count)
            if not target_indexes:
                target_indexes = [count - 1]
            for index in target_indexes:
                transforms[index]["crop"] = True
                transforms[index]["anchor"] = anchor
                transforms[index]["crop_offset_y"] = max(float(transforms[index].get("crop_offset_y") or 0.0), offset_y)
    horizontal_offset_patterns: tuple[tuple[str, float], ...] = (
        (
            r"(?:取景|画面|crop|pan)[^。；，,.!?！？\n]{0,16}(?:向右|右移|右侧|right)"
            r"|(?:向右|右移|右侧|right)[^。；，,.!?！？\n]{0,16}(?:取景|画面|crop|pan)",
            0.25,
        ),
        (
            r"(?:取景|画面|crop|pan)[^。；，,.!?！？\n]{0,16}(?:向左|左移|左侧|left)"
            r"|(?:向左|左移|左侧|left)[^。；，,.!?！？\n]{0,16}(?:取景|画面|crop|pan)",
            -0.25,
        ),
    )
    for pattern, offset_x in horizontal_offset_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            context = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
            target_indexes = _nearest_timeline_broll_transform_target_indexes(text, match.start(), count)
            if not target_indexes:
                target_indexes = _timeline_broll_transform_target_indexes(context, count)
            if not target_indexes:
                target_indexes = [count - 1]
            for index in target_indexes:
                transforms[index]["crop"] = True
                current = float(transforms[index].get("crop_offset_x") or 0.0)
                if abs(offset_x) > abs(current):
                    transforms[index]["crop_offset_x"] = offset_x
    for match in re.finditer(r"(?:center\s+crop|crop\s+center|居中裁切|中心裁切)", text, re.IGNORECASE):
        context = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
        target_indexes = _nearest_timeline_broll_transform_target_indexes(text, match.start(), count)
        if not target_indexes:
            target_indexes = _timeline_broll_transform_target_indexes(context, count)
        if not target_indexes:
            target_indexes = [count - 1]
        for index in target_indexes:
            transforms[index]["crop"] = True
            transforms[index]["anchor"] = "center"
            transforms[index]["crop_offset_x"] = 0.0
            transforms[index]["crop_offset_y"] = 0.0


def _timeline_broll_transform_zoom_is_view_context(text: str, start: int, end: int) -> bool:
    local = text[max(0, start - 8) : min(len(text), end + 30)].lower()
    view_terms = (
        "时间线视图",
        "时间轴视图",
        "视图缩放",
        "视图百分比",
        "界面缩放",
        "ui 缩放",
        "ui zoom",
        "timeline view",
        "timeline zoom",
        "view zoom",
        "zoom control",
        "zoom controls",
    )
    if any(term in local for term in view_terms):
        return True
    return bool(re.search(r"(?:时间线|时间轴|界面|ui)[^。；，,.!?！？\n]{0,12}?(?:缩放|zoom)", local, re.IGNORECASE))


def _nearest_timeline_broll_transform_target_indexes(text: str, position: int, count: int) -> list[int]:
    aliases_by_index = _segment_duration_aliases(count)
    best: tuple[int, int] | None = None
    for index, aliases in enumerate(aliases_by_index):
        for alias in aliases:
            for match in re.finditer(re.escape(alias), text):
                if match.start() > position:
                    continue
                distance = position - match.start()
                if distance > 120:
                    continue
                if best is None or distance < best[1]:
                    best = (index, distance)
    return [best[0]] if best is not None else []


def _timeline_broll_transform_target_indexes(text: str, count: int) -> list[int]:
    if count <= 0:
        return []
    lowered = str(text or "").lower()
    if re.search(r"(?:整体|全部|所有|全片|all|entire)", lowered):
        return list(range(count))
    indexes: list[int] = []
    aliases_by_index = _segment_duration_aliases(count)
    for index, aliases in enumerate(aliases_by_index):
        if any(alias in text for alias in aliases):
            indexes.append(index)
    if count >= 2 and not indexes and re.search(r"(?:后段|后一段|后半段|末段|结尾段|机械|主体|main subject|mechanical)", lowered):
        indexes.append(count - 1)
    return sorted(set(indexes))


def _segment_duration_aliases(count: int) -> list[tuple[str, ...]]:
    aliases: list[list[str]] = []
    chinese_ordinals = ("一", "二", "三", "四", "五", "六")
    for index in range(count):
        ordinal = chinese_ordinals[index] if index < len(chinese_ordinals) else str(index + 1)
        aliases.append(
            [
                f"第{ordinal}段",
                f"第{index + 1}段",
                f"第{ordinal}个片段",
                f"第{index + 1}个片段",
                f"第{ordinal}个素材",
                f"第{index + 1}个素材",
            ]
        )
    if aliases:
        aliases[0].extend(["前段", "前一段", "前半段", "开头段", "前面段"])
        aliases[-1].extend(["后段", "后一段", "后半段", "末段", "结尾段", "后面段"])
    return [tuple(items) for items in aliases]


def _fit_requested_segment_constraints(
    *,
    count: int,
    total_sec: float,
    exact: dict[int, float],
    minimum: dict[int, float],
    maximum: dict[int, float],
) -> list[float] | None:
    floor = 0.1
    values: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    flexible: list[int] = []
    for index in range(count):
        if index in exact:
            value = exact[index]
            values.append(value)
            lower.append(value)
            upper.append(value)
        elif index in minimum:
            value = minimum[index]
            values.append(value)
            lower.append(value)
            upper.append(float("inf"))
            flexible.append(index)
        elif index in maximum:
            value = maximum[index]
            values.append(value)
            lower.append(floor)
            upper.append(value)
        else:
            values.append(floor)
            lower.append(floor)
            upper.append(float("inf"))
            flexible.append(index)

    if any(value <= 0 for value in values) or total_sec <= 0:
        return None
    current = sum(values)
    if current < total_sec:
        remaining = total_sec - current
        if not flexible:
            return _scale_segment_values(values, total_sec)
        for index in flexible:
            values[index] += remaining / len(flexible)
    elif current > total_sec:
        excess = current - total_sec
        reducible = [index for index, value in enumerate(values) if value > lower[index]]
        if not reducible:
            return _scale_segment_values(values, total_sec)
        for index in reducible:
            if excess <= 0:
                break
            amount = min(excess, values[index] - lower[index])
            values[index] -= amount
            excess -= amount
        if excess > 0.001:
            return _scale_segment_values(values, total_sec)

    drift = total_sec - sum(values)
    if abs(drift) > 0.001:
        for index in flexible:
            candidate = values[index] + drift
            if candidate >= lower[index] and candidate <= upper[index]:
                values[index] = candidate
                break
    if any(value <= 0 for value in values):
        return None
    return values


def _scale_segment_values(values: list[float], total_sec: float) -> list[float] | None:
    total = sum(values)
    if total <= 0:
        return None
    scale = total_sec / total
    scaled = [value * scale for value in values]
    if any(value <= 0 for value in scaled):
        return None
    return scaled


def _merge_clip_duration(target: dict[str, Any]) -> float:
    source_in = _float_or(target.get("source_in") or target.get("inPoint"), 0.0)
    source_out = _float_or(target.get("source_out") or target.get("outPoint"), 0.0)
    if source_out > source_in:
        return max(0.1, source_out - source_in)
    duration = _float_or(target.get("duration"), 0.0)
    metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
    if duration <= 0:
        duration = _float_or(metadata.get("duration") or metadata.get("source_duration"), 0.0)
    return max(0.1, duration)


def _render_timeline_kept_clip_merge(
    targets: list[dict[str, Any]],
    *,
    output_path: str,
    min_clip_duration_sec: float | None = None,
) -> dict[str, Any]:
    if not targets:
        raise ValueError("timeline kept merge requires at least one video target")
    sources = [str(item.get("source_path") or "") for item in targets]
    if any(not source for source in sources):
        raise ValueError("timeline kept merge requires source paths")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    filters: list[str] = []
    source_ranges: list[dict[str, float]] = []
    min_duration = max(0.0, float(min_clip_duration_sec or 0.0))
    for index, target in enumerate(targets):
        source_in = _float_or(target.get("source_in") or target.get("inPoint"), 0.0)
        duration = _merge_clip_duration(target)
        metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
        source_duration = _float_or(metadata.get("source_duration") or metadata.get("file_duration") or metadata.get("duration"), 0.0)
        extended_short_clip = False
        if min_duration > 0 and 0 < duration < min_duration:
            if source_duration <= 0 or source_in + min_duration <= source_duration:
                duration = min_duration
                extended_short_clip = True
            else:
                extended = max(duration, source_duration - source_in)
                extended_short_clip = extended > duration
                duration = extended
        if source_duration > 0 and source_in + duration > source_duration:
            duration = max(0.1, source_duration - source_in)
        source_ranges.append(
            {
                "source_in": source_in,
                "source_out": source_in + duration,
                "duration": duration,
                "extended_short_clip": extended_short_clip,
            }
        )
        filters.append(
            f"[{index}:v]trim=start={source_in:.3f}:duration={duration:.3f},setpts=PTS-STARTPTS,"
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
            f"[v{index}]"
        )

    if len(targets) == 1:
        filters.append("[v0]format=yuv420p[v]")
    else:
        filters.append("".join(f"[v{index}]" for index in range(len(targets))) + f"concat=n={len(targets)}:v=1:a=0[v]")
    duration_sec = sum(item["duration"] for item in source_ranges)
    cmd = ["ffmpeg", "-y"]
    for source in sources:
        cmd.extend(["-i", source])
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-t",
            f"{duration_sec:.3f}",
            "-r",
            "30",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"timeline kept merge failed: {proc.stderr}")
    sidecar = output.with_suffix(".timeline-merge.json")
    payload = {
        "kind": "timeline_merge_kept",
        "status": "succeeded",
        "inputs": sources,
        "output": str(output),
        "duration_sec": duration_sec,
        "clip_count": len(targets),
        "min_clip_duration_sec": min_duration,
        "source_ranges": source_ranges,
        "created_at": _utc_now(),
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _render_timeline_broll_preview(
    targets: list[dict[str, Any]],
    *,
    output_path: str,
    duration_sec: float,
    transition_sec: float,
    grade_filter: str | None = None,
    prompt: str = "",
) -> dict[str, Any]:
    if len(targets) < 2:
        raise ValueError("timeline b-roll preview requires at least two video targets")
    sources = [str(item.get("source_path") or "") for item in targets]
    if any(not source for source in sources):
        raise ValueError("timeline b-roll preview requires source paths")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    clip_count = len(targets)
    requested_transition = max(0.0, float(transition_sec))
    transition = 0.0 if requested_transition <= 0.001 else min(
        requested_transition,
        max(0.05, float(duration_sec) / max(clip_count * 3.0, 1.0)),
    )
    requested_segments = _requested_segment_durations_sec(prompt, count=clip_count, total_sec=float(duration_sec))
    visible_durations = requested_segments or [float(duration_sec) / clip_count for _ in targets]
    input_durations = [
        max(0.1, visible + (transition if index < clip_count - 1 else 0.0))
        for index, visible in enumerate(visible_durations)
    ]
    grade = grade_filter or "eq=saturation=1.12:contrast=1.04:brightness=0.012"
    clip_transforms = _requested_timeline_broll_clip_transforms(prompt, count=clip_count)
    filters: list[str] = []
    source_ranges: list[dict[str, float]] = []
    for index, (target, clip_duration) in enumerate(zip(targets, input_durations, strict=False)):
        source_in = _float_or(target.get("source_in") or target.get("inPoint"), 0.0)
        source_out = _float_or(target.get("source_out") or target.get("outPoint"), 0.0)
        metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
        source_duration = _float_or(metadata.get("source_duration") or metadata.get("file_duration"), 0.0)
        available = source_out - source_in if source_out > source_in else 0.0
        extended_short_clip = False
        if 0 < available < 0.25 and source_duration > source_in + min(clip_duration, 0.25):
            available = max(available, min(clip_duration, source_duration - source_in))
            extended_short_clip = True
        duration = min(clip_duration, available) if available > 0 else clip_duration
        duration = max(0.1, duration)
        source_ranges.append(
            {
                "source_in": source_in,
                "source_out": source_in + duration,
                "duration": duration,
                "extended_short_clip": extended_short_clip,
            }
        )
        transform = clip_transforms[index] if index < len(clip_transforms) else {"zoom": 1.0}
        zoom = _float_or(transform.get("zoom") if isinstance(transform, dict) else None, 1.0)
        crop_offset_x = _float_or(transform.get("crop_offset_x") if isinstance(transform, dict) else None, 0.0)
        crop_offset_y = _float_or(transform.get("crop_offset_y") if isinstance(transform, dict) else None, 0.0)
        crop_offset_x = max(-0.45, min(0.45, crop_offset_x))
        crop_offset_y = max(-0.45, min(0.45, crop_offset_y))
        filter_parts = [
            f"trim=start={source_in:.3f}:duration={duration:.3f}",
            "setpts=PTS-STARTPTS",
            "scale=1280:720:force_original_aspect_ratio=decrease",
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        ]
        if zoom > 1.001:
            filter_parts.extend(
                [
                    f"scale=trunc(iw*{zoom:.6f}/2)*2:trunc(ih*{zoom:.6f}/2)*2",
                    f"crop=1280:720:(iw-1280)*{0.5 + crop_offset_x:.6f}:(ih-720)*{0.5 + crop_offset_y:.6f}",
                ]
            )
        filter_parts.extend(["setsar=1", "fps=30", "format=yuv420p", grade])
        filters.append(f"[{index}:v]{','.join(filter_parts)}[v{index}]")
    if transition <= 0.001:
        concat_inputs = "".join(f"[v{index}]" for index in range(clip_count))
        filters.append(f"{concat_inputs}concat=n={clip_count}:v=1:a=0,format=yuv420p[v]")
    else:
        chain_label = "v0"
        chain_duration = source_ranges[0]["duration"]
        for index in range(1, clip_count):
            next_duration = source_ranges[index]["duration"]
            offset_sec = max(0.0, chain_duration - transition)
            out_label = "v" if index == clip_count - 1 else f"x{index}"
            filters.append(
                f"[{chain_label}][v{index}]xfade=transition=dissolve:duration={transition:.3f}:"
                f"offset={offset_sec:.3f},format=yuv420p[{out_label}]"
            )
            chain_label = out_label
            chain_duration = chain_duration + next_duration - transition
    filter_complex = ";".join(filters)
    cmd = ["ffmpeg", "-y"]
    for source in sources:
        cmd.extend(["-i", source])
    cmd.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-t",
        f"{duration_sec:.3f}",
        "-r",
        "30",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ])
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"timeline b-roll concat failed: {proc.stderr}")
    sidecar = output.with_suffix(".timeline-broll.json")
    payload = {
        "kind": "timeline_broll_preview",
        "status": "succeeded",
        "inputs": sources,
        "output": str(output),
        "duration_sec": duration_sec,
        "transition_sec": transition,
        "grade_filter": grade,
        "clip_count": clip_count,
        "clip_durations": input_durations,
        "clip_transforms": clip_transforms,
        "source_ranges": source_ranges,
        "created_at": _utc_now(),
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _blocked_plan_capabilities(plan: dict[str, Any], prompt: str) -> list[str]:
    functions = _plan_functions(plan)
    blocked: list[str] = []
    for capability, needles, explicit_terms in _RISKY_CAPABILITY_RULES:
        if _prompt_explicitly_allows(prompt, explicit_terms):
            continue
        if any(any(needle.lower() in fn.lower() for needle in needles) for fn in functions):
            blocked.append(capability)
    return blocked


def _plan_functions(plan: dict[str, Any]) -> list[str]:
    functions: list[str] = []
    for step in plan.get("steps") or []:
        if isinstance(step, dict):
            fn = str(step.get("function") or "").strip()
            if fn:
                functions.append(fn)
    return functions


def _prompt_explicitly_allows(prompt: str, terms: tuple[str, ...]) -> bool:
    text = str(prompt or "").lower()
    if "视频生成" in terms and "生成" in text and "视频" in text:
        return True
    return any(term.lower() in text for term in terms)


def _safe_layer_preview_plan(
    *,
    prompt: str,
    input_path: str,
    output_path: str,
    blocked_functions: list[str],
    reason: str = "capability_gate",
) -> dict[str, Any]:
    source = str(input_path or "").strip()
    scripted_args = _scripted_motion_graphics_fallback_args(prompt, source=source)
    if scripted_args is not None:
        assistant_message = (
            "Gemini 的计划没有通过执行契约，我先改用本地脚本化图层小样："
            "直接用文字层、运动图形和时间轴关键帧渲染出可审阅版本。"
            if reason == "plan_contract_error"
            else (
                "外部规划服务这次不可用，我先改用本地脚本化图层小样："
                "直接用文字层、运动小球和时间轴关键帧渲染出可审阅版本。"
                if reason == "planning_provider_unavailable"
                else (
                    "我先把高风险外部视频生成降级为本地脚本化图层小样："
                    "直接用文字层、运动小球和时间轴关键帧渲染出可审阅版本。"
                )
            )
        )
        return {
            "version": "2.0",
            "goal": prompt,
            "assistant_message": assistant_message,
            "stability_gate": {
                "mode": "local_scripted_layer_preview",
                "blocked_functions": blocked_functions,
                "reason": reason,
            },
            "input_path": source,
            "output_path": output_path,
            "steps": [
                {
                    "id": "step_1",
                    "function": "gemia.video.layer_flow.render_layer_workflow",
                    "input": "$input",
                    "output": "$output",
                    "args": scripted_args,
                    "assistant_message": "正在用本地图层脚本渲染小球依次弹过字符的 MG 小样。",
                }
            ],
        }

    fallback_layers: list[dict[str, Any]] = []
    if not source:
        fallback_layers.append(
            {
                "id": "fallback_background",
                "type": "solid",
                "color": [0.035, 0.043, 0.052, 1.0],
                "position": [0, 0],
                "size": [1280, 720],
                "duration": 90,
                "z_index": 0,
            }
        )
    assistant_message = (
        "Gemini 的计划没有通过执行契约，我先改用本地图层渲染一个可审阅小样。"
        if reason == "plan_contract_error"
        else (
            "外部规划服务这次不可用，我先改用本地图层渲染一个可审阅小样。"
            if reason == "planning_provider_unavailable"
            else (
                "我先不自动调用高风险外部或开发能力，改用本地图层渲染一个可审阅小样。"
                "明确要求对应能力时，我会再打开它。"
            )
        )
    )
    return {
        "version": "2.0",
        "goal": prompt,
        "assistant_message": assistant_message,
        "stability_gate": {
            "mode": "local_preview_fallback",
            "blocked_functions": blocked_functions,
            "reason": reason,
        },
        "input_path": source,
        "output_path": output_path,
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.layer_flow.render_layer_workflow",
                "input": "$input",
                "output": "$output",
                "args": {
                    "include_source": bool(source),
                    "frame_step": 3,
                    "max_long_edge": 540,
                    "canvas": {"width": 1280, "height": 720, "fps": 30, "total_frames": 90},
                    "overlay_layers": fallback_layers,
                },
            }
        ],
    }


def _scripted_motion_graphics_fallback_args(prompt: str, *, source: str = "") -> dict[str, Any] | None:
    text = str(prompt or "")
    lowered = text.lower()
    wants_ball = any(term in lowered for term in ("ball", "bounce", "bouncy")) or any(term in text for term in ("小球", "圆球", "圆点", "球"))
    wants_bounce = any(term in lowered for term in ("bounce", "jump")) or any(term in text for term in ("弹", "跳", "跃"))
    if not (wants_ball and wants_bounce):
        scripted_title_args = _scripted_title_motion_graphics_fallback_args(text, source=source)
        if scripted_title_args is not None:
            return scripted_title_args
        return None
    wants_sparks = any(term in lowered for term in ("spark", "sparks", "firework")) or any(
        term in text for term in ("火花", "星火", "闪光")
    )
    wants_yellow_ball = any(term in lowered for term in ("yellow", "gold")) or any(
        term in text for term in ("黄色", "亮黄", "金色")
    )
    wants_final_stop = any(term in lowered for term in ("stop", "stops", "settle", "settles", "rest")) or any(
        term in text for term in ("停在", "停到", "停止", "最后停")
    )
    wants_slide_left = any(term in lowered for term in ("slide in", "from left", "slide from left")) or any(
        term in text for term in ("从左滑入", "左滑入", "左侧滑入", "从左进入")
    )
    ball_color = [1.0, 0.84, 0.12, 1.0] if wants_yellow_ball else [0.37, 0.84, 1.0, 1.0]

    word = _extract_word_for_bounce(text)
    if not word:
        return None

    width, height = _requested_canvas_size(text)
    fps = 30
    default_total_frames = max(132, min(180, 84 + len(word) * 16))
    duration_sec = _requested_total_duration_sec(text, default=default_total_frames / fps)
    total_frames = max(15, int(round(duration_sec * fps)))
    letter_size = 112 if len(word) <= 7 else 96
    letter_advance = max(int(letter_size * 0.58), min(80, int(width * 0.36 / max(len(word), 1))))
    text_width = letter_advance * (len(word) - 1) + int(letter_size * 0.68)
    start_x = int((width - text_width) / 2)
    letter_y = int(height * 0.50)
    ball_size = 58
    contact_y = letter_y - ball_size + 12
    apex_y = contact_y - int(height * 0.18)
    shadow_y = letter_y + int(letter_size * 0.74)
    slide_frames = min(20, max(8, int(round(total_frames * 0.2)))) if wants_slide_left else 0
    first_contact = min(max(14, slide_frames + 6), max(total_frames - 2, 0))
    last_contact = max(first_contact, total_frames - 22)
    interval = (last_contact - first_contact) / max(len(word) - 1, 1)
    contact_frames = [int(round(first_contact + index * interval)) for index in range(len(word))]

    layers: list[dict[str, Any]] = [
        {
            "id": "scripted_bg",
            "type": "solid",
            "color": [0.012, 0.018, 0.026, 1.0],
            "position": [0, 0],
            "size": [width, height],
            "duration": total_frames,
            "z_index": 0,
        },
        {
            "id": "stage_floor",
            "type": "solid",
            "color": [0.018, 0.036, 0.048, 0.88],
            "position": [0, shadow_y + 40],
            "size": [width, height - shadow_y - 40],
            "duration": total_frames,
            "z_index": 1,
        },
        {
            "id": "baseline_glow",
            "type": "solid",
            "color": [0.22, 0.75, 0.92, 0.46],
            "position": [start_x - 34, shadow_y + 22],
            "size": [text_width + 94, 3],
            "opacity": 0.56,
            "duration": total_frames,
            "z_index": 2,
        },
    ]

    word_base_layer: dict[str, Any] = {
        "id": "word_base",
        "type": "text",
        "text": word,
        "position": [start_x, letter_y],
        "font_config": {
            "size": letter_size,
            "padding": 8,
            "color": [0.86, 0.94, 1.0, 1.0],
        },
        "duration": total_frames,
        "z_index": 10,
    }
    if wants_slide_left:
        word_base_layer["opacity"] = 0.0
        word_base_layer["keyframes"] = {
            "position": {
                "points": [
                    {"frame": 0, "value": [-text_width - 80, letter_y], "easing": "ease_out"},
                    {"frame": slide_frames, "value": [start_x, letter_y], "easing": "ease_out"},
                    {"frame": total_frames - 1, "value": [start_x, letter_y], "easing": "linear"},
                ]
            },
            "opacity": {
                "0": 0.0,
                str(max(1, int(round(slide_frames * 0.45)))): 0.72,
                str(slide_frames): 1.0,
            },
        }
    layers.append(word_base_layer)

    for index, char in enumerate(word):
        x = start_x + index * letter_advance
        contact = contact_frames[index]
        if char.strip():
            layers.append(
                {
                    "id": f"letter_hit_{index}_{char}",
                    "type": "text",
                    "text": char,
                    "position": [x, letter_y],
                    "font_config": {
                        "size": letter_size,
                        "padding": 8,
                        "color": [0.44, 0.91, 1.0, 1.0],
                    },
                    "duration": total_frames,
                    "z_index": 11,
                    "keyframes": {
                        "opacity": {
                            str(max(contact - 10, 0)): 0.0,
                            str(contact): 0.88,
                            str(min(contact + 14, total_frames - 1)): 0.0,
                        },
                        "scale": {
                            str(max(contact - 8, 0)): 1.0,
                            str(contact): 1.12,
                            str(min(contact + 12, total_frames - 1)): 1.0,
                        },
                    },
                }
            )
        layers.append(
            {
                "id": f"contact_shadow_{index}",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/shadow.png",
                "position": [x + int(letter_size * 0.06), shadow_y],
                "size": [int(letter_size * 0.78), int(letter_size * 0.18)],
                "duration": total_frames,
                "z_index": 8,
                "opacity": 0.0,
                "keyframes": {
                    "opacity": {
                        str(max(contact - 12, 0)): 0.0,
                        str(contact): 0.72,
                        str(min(contact + 18, total_frames - 1)): 0.0,
                    },
                    "scale": {
                        str(max(contact - 10, 0)): 0.62,
                        str(contact): 1.08,
                        str(min(contact + 16, total_frames - 1)): 0.7,
                    },
                },
            }
        )
        if wants_sparks:
            spark_specs = (
                (-24, -10, 18, 4, -28),
                (6, -24, 14, 4, 18),
                (34, -8, 16, 4, 38),
            )
            for spark_index, (dx, dy, spark_width, spark_height, angle) in enumerate(spark_specs):
                layers.append(
                    {
                        "id": f"spark_{index}_{spark_index}",
                        "type": "solid",
                        "color": [1.0, 0.74, 0.16, 0.94],
                        "position": [x + int(letter_size * 0.22) + dx, shadow_y + dy],
                        "size": [spark_width, spark_height],
                        "rotation_deg": angle,
                        "duration": total_frames,
                        "z_index": 16,
                        "opacity": 0.0,
                        "keyframes": {
                            "opacity": {
                                str(max(contact - 4, 0)): 0.0,
                                str(min(contact + 2, total_frames - 1)): 0.92,
                                str(min(contact + 12, total_frames - 1)): 0.0,
                            },
                            "scale": {
                                str(max(contact - 4, 0)): 0.45,
                                str(min(contact + 2, total_frames - 1)): 1.16,
                                str(min(contact + 12, total_frames - 1)): 0.38,
                            },
                            "rotation_deg": {
                                str(max(contact - 4, 0)): angle,
                                str(min(contact + 12, total_frames - 1)): angle + (18 if angle >= 0 else -18),
                            },
                        },
                    }
                )

    position_points: list[dict[str, Any]] = []
    lead_x = start_x - int(letter_size * 0.55)
    first_x = start_x + int(letter_size * 0.18)
    position_points.append({"frame": 0, "value": [lead_x, apex_y + 18], "easing": "ease_in_out"})
    for index, _char in enumerate(word):
        x = start_x + index * letter_advance + int(letter_size * 0.18)
        arrival = contact_frames[index]
        position_points.append({"frame": arrival, "value": [x, contact_y], "easing": "ease_in_out"})
        if index < len(word) - 1:
            next_x = start_x + (index + 1) * letter_advance + int(letter_size * 0.18)
            apex = int(round((arrival + contact_frames[index + 1]) / 2))
            position_points.append({"frame": apex, "value": [int((x + next_x) / 2), apex_y], "easing": "ease_out"})
    last_letter_contact_x = start_x + (len(word) - 1) * letter_advance + int(letter_size * 0.18)
    exit_x = last_letter_contact_x if wants_final_stop else start_x + (len(word) - 1) * letter_advance + int(letter_size * 0.88)
    position_points.append({"frame": total_frames - 1, "value": [exit_x, apex_y + 10], "easing": "ease_in_out"})

    def _shift_points(points: list[dict[str, Any]], delay: int) -> list[dict[str, Any]]:
        return [
            {
                **point,
                "frame": min(max(int(point["frame"]) + delay, 0), total_frames - 1),
            }
            for point in points
        ]

    for trail_index, (delay, opacity, scale) in enumerate(((4, 0.22, 0.78), (8, 0.12, 0.62)), start=1):
        layers.append(
            {
                "id": f"ball_trail_{trail_index}",
                "type": "image",
                "source": "https://lumeri.ai/assets/primitives/circle.png",
                "color": ball_color,
                "position": [first_x, contact_y],
                "size": [ball_size, ball_size],
                "duration": total_frames,
                "z_index": 24 - trail_index,
                "opacity": opacity,
                "scale": scale,
                "keyframes": {"position": {"points": _shift_points(position_points, delay)}},
            }
        )

    ball_scale_items: dict[int, float] = {}
    for contact in contact_frames:
        ball_scale_items[max(contact - 7, 0)] = 1.06
        ball_scale_items[contact] = 0.88
        ball_scale_items[min(contact + 8, total_frames - 1)] = 1.08
    ball_scale_keyframes = {
        str(frame): value
        for frame, value in sorted(ball_scale_items.items(), key=lambda item: item[0])
    }

    layers.append(
        {
            "id": "bouncing_ball",
            "type": "image",
            "source": "https://lumeri.ai/assets/primitives/ball.png",
            "color": ball_color,
            "position": position_points[0]["value"],
            "size": [ball_size, ball_size],
            "duration": total_frames,
            "z_index": 30,
            "keyframes": {
                "position": {"points": position_points},
                "scale": ball_scale_keyframes,
            },
        }
    )

    return {
        "include_source": bool(source),
        "frame_step": 1,
        "max_long_edge": max(width, height),
        "canvas": {"width": width, "height": height, "fps": fps, "total_frames": total_frames},
        "overlay_layers": layers,
    }


def _scripted_title_motion_graphics_fallback_args(prompt: str, *, source: str = "") -> dict[str, Any] | None:
    text = str(prompt or "")
    lowered = text.lower()
    wants_title_mg = any(term in lowered for term in ("mg", "motion graphics", "title", "neon", "wipe", "grid")) or any(
        term in text for term in ("标题", "片头", "文字", "霓虹", "擦除", "网格", "方块")
    )
    if not wants_title_mg:
        return None

    title = _extract_title_for_motion_graphics(text)
    if not title:
        return None

    width, height, fps, total_frames = 1280, 720, 30, 90
    title_size = 104 if len(title.replace(" ", "")) <= 8 else 88
    estimated_title_width = max(360, min(740, int(len(title) * title_size * 0.56)))
    title_x = int((width - estimated_title_width) / 2)
    title_y = int(height * 0.43)
    final_square_x = min(width - 96, title_x + estimated_title_width + 28)
    final_square_y = title_y + int(title_size * 0.22)

    layers: list[dict[str, Any]] = [
        {
            "id": "scripted_neon_bg",
            "type": "solid",
            "color": [0.01, 0.014, 0.025, 1.0],
            "position": [0, 0],
            "size": [width, height],
            "duration": total_frames,
            "z_index": 0,
        },
        {
            "id": "cyan_floor_glow",
            "type": "solid",
            "color": [0.0, 0.72, 0.92, 0.18],
            "position": [0, int(height * 0.67)],
            "size": [width, 3],
            "opacity": 0.62,
            "duration": total_frames,
            "z_index": 2,
        },
    ]

    for index, x in enumerate(range(120, width, 160)):
        layers.append(
            {
                "id": f"grid_v_{index}",
                "type": "solid",
                "color": [0.05, 0.42, 0.58, 0.24],
                "position": [x, 120],
                "size": [2, 440],
                "opacity": 0.0,
                "duration": total_frames,
                "z_index": 1,
                "keyframes": {"opacity": {"0": 0.0, "12": 0.42, "89": 0.18}},
            }
        )
    for index, y in enumerate(range(150, 560, 80)):
        layers.append(
            {
                "id": f"grid_h_{index}",
                "type": "solid",
                "color": [0.05, 0.42, 0.58, 0.22],
                "position": [96, y],
                "size": [1088, 2],
                "opacity": 0.0,
                "duration": total_frames,
                "z_index": 1,
                "keyframes": {"opacity": {"0": 0.0, "12": 0.38, "89": 0.16}},
            }
        )

    title_points = [
        {"frame": 0, "value": [-estimated_title_width - 80, title_y], "easing": "ease_out"},
        {"frame": 20, "value": [title_x, title_y], "easing": "ease_out"},
        {"frame": 89, "value": [title_x, title_y], "easing": "linear"},
    ]
    layers.extend(
        [
            {
                "id": "title_shadow",
                "type": "text",
                "text": title,
                "position": [title_x + 8, title_y + 8],
                "font_config": {"size": title_size, "padding": 10, "color": [0.82, 0.05, 0.88, 0.42]},
                "opacity": 0.0,
                "duration": total_frames,
                "z_index": 8,
                "keyframes": {
                    "position": {"points": [{"frame": item["frame"], "value": [item["value"][0] + 8, item["value"][1] + 8], "easing": item["easing"]} for item in title_points]},
                    "opacity": {"0": 0.0, "18": 0.68, "89": 0.34},
                },
            },
            {
                "id": "title_main",
                "type": "text",
                "text": title,
                "position": [title_x, title_y],
                "font_config": {"size": title_size, "padding": 10, "color": [0.92, 0.97, 1.0, 1.0]},
                "opacity": 0.0,
                "duration": total_frames,
                "z_index": 12,
                "keyframes": {
                    "position": {"points": title_points},
                    "opacity": {"0": 0.0, "9": 0.45, "20": 1.0, "89": 1.0},
                },
            },
            {
                "id": "cyan_wipe_line",
                "type": "solid",
                "color": [0.0, 0.86, 0.96, 0.92],
                "position": [title_x - 26, title_y - 18],
                "size": [8, title_size + 42],
                "opacity": 0.0,
                "duration": total_frames,
                "z_index": 18,
                "keyframes": {
                    "position": {
                        "points": [
                            {"frame": 15, "value": [title_x - 26, title_y - 18], "easing": "ease_in_out"},
                            {"frame": 38, "value": [title_x + estimated_title_width + 22, title_y - 18], "easing": "ease_in_out"},
                            {"frame": 52, "value": [title_x + estimated_title_width + 22, title_y - 18], "easing": "linear"},
                        ]
                    },
                    "opacity": {"0": 0.0, "15": 1.0, "42": 0.9, "52": 0.0},
                },
            },
            {
                "id": "magenta_orbit_square",
                "type": "solid",
                "color": [1.0, 0.08, 0.74, 0.96],
                "position": [title_x - 46, final_square_y],
                "size": [34, 34],
                "opacity": 0.0,
                "rotation_deg": 0,
                "duration": total_frames,
                "z_index": 20,
                "keyframes": {
                    "position": {
                        "points": [
                            {"frame": 24, "value": [title_x - 46, final_square_y], "easing": "ease_in_out"},
                            {"frame": 38, "value": [title_x + int(estimated_title_width * 0.5), title_y - 70], "easing": "ease_out"},
                            {"frame": 54, "value": [title_x + estimated_title_width + 44, final_square_y], "easing": "ease_in_out"},
                            {"frame": 70, "value": [title_x + int(estimated_title_width * 0.5), title_y + title_size + 30], "easing": "ease_in_out"},
                            {"frame": 84, "value": [final_square_x, final_square_y], "easing": "ease_out"},
                            {"frame": 89, "value": [final_square_x, final_square_y], "easing": "linear"},
                        ]
                    },
                    "opacity": {"0": 0.0, "24": 0.95, "89": 0.95},
                    "rotation_deg": {"24": 0, "54": 180, "84": 360, "89": 360},
                },
            },
        ]
    )

    return {
        "include_source": bool(source),
        "frame_step": 1,
        "max_long_edge": 720,
        "canvas": {"width": width, "height": height, "fps": fps, "total_frames": total_frames},
        "overlay_layers": layers,
    }


def _extract_title_for_motion_graphics(prompt: str) -> str:
    patterns = [
        r"(?:中心大字|大字|主标题|标题文字|文字|标题|title)\s*[:：为是]?\s*['\"“”‘’]?([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+){0,3})",
        r"['\"“”‘’]([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+){0,3})['\"“”‘’]",
    ]
    banned_words = {"AI", "MG", "MP4", "VEO", "FPS", "HD", "RGB", "CSS"}
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if not match:
            continue
        words = [word for word in re.split(r"\s+", match.group(1).strip()) if word]
        if not words or all(word.upper() in banned_words for word in words):
            continue
        cleaned = " ".join(words[:4]).strip()
        if len(cleaned) >= 2:
            return cleaned[:28]
    for match in re.finditer(r"\b([A-Z][A-Z0-9]{1,12}(?:\s+[A-Z0-9]{2,12}){0,3})\b", prompt):
        words = [word for word in re.split(r"\s+", match.group(1).strip()) if word]
        if not words or all(word.upper() in banned_words for word in words):
            continue
        cleaned = " ".join(words[:4]).strip()
        if len(cleaned) >= 2:
            return cleaned[:28]
    return ""


def _extract_word_for_bounce(prompt: str) -> str:
    patterns = [
        r"(?:弹过|跳过|越过|经过)\s*['\"“”‘’]?([A-Za-z]{2,16})",
        r"['\"“”‘’]([A-Za-z]{2,16})['\"“”‘’]",
    ]
    banned_words = {"video", "script", "ball", "bounce", "jump", "prompt", "only", "title", "canvas"}
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if not match:
            continue
        word = match.group(1).strip()
        if word.lower() in banned_words:
            continue
        word = re.sub(r"\d+$", "", word)
        if len(word) < 2:
            continue
        return word[:12]
    title = _extract_title_for_motion_graphics(prompt)
    if title:
        return title[:12]
    for match in re.finditer(r"\b([A-Za-z][A-Za-z0-9]{2,15})\b", prompt):
        word = match.group(1).strip()
        if word.lower() in banned_words:
            continue
        word = re.sub(r"\d+$", "", word)
        if len(word) < 2:
            continue
        return word[:12]
    return ""


def _emit(
    callback: AgentEventCallback | None,
    phase: str,
    label: str,
    *,
    detail: str = "",
    body: str = "",
    meta: str = "",
    command: str = "",
    output: str = "",
    outputs: list[str] | None = None,
    stats: dict[str, Any] | None = None,
    status: str = "running",
    index: int | None = None,
    total: int | None = None,
    material: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compact_body = _compact_event_body(phase=phase, label=label, body=body, status=status)
    compact_meta = _compact_event_meta(
        phase=phase,
        label=label,
        status=status,
        meta=meta,
        command=command,
        index=index,
        total=total,
        stats=stats,
        extra=extra,
        material=material,
    )
    event: dict[str, Any] = {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "phase": phase,
        "label": label,
        "detail": detail,
        "status": status,
        "created_at": _utc_now(),
    }
    if compact_body:
        event["body"] = compact_body
    if compact_meta:
        event["meta"] = compact_meta
    if command:
        event["command"] = command
    if output:
        event["output"] = output
    if outputs is not None:
        event["outputs"] = outputs
    if stats is not None:
        event["stats"] = stats
    if index is not None:
        event["index"] = index
    if total is not None:
        event["total"] = total
    if material:
        event["material_id"] = material.get("material_id")
        event["asset_id"] = material.get("asset_id")
        event["clip_id"] = material.get("clip_id")
        event["material_name"] = material.get("name")
        event["media_kind"] = material.get("media_kind")
    if extra:
        event.update(extra)
    if callback is not None:
        callback(event)
    return event


def _compact_event_body(*, phase: str, label: str, body: str, status: str) -> str:
    """Keep machine detail trimmed while preserving a readable transcript."""
    if phase == "result" and label == "完成汇报":
        return body
    if phase in {"ask", "error"} or status in {"asking", "failed", "cancelled"}:
        return body
    return _trim_report_body(body)


def _trim_report_body(body: str, *, max_chars: int = 520, max_lines: int = 7) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"...已省略 {len(lines) - max_lines} 行"]
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _compact_event_meta(
    *,
    phase: str,
    label: str,
    status: str,
    meta: str,
    command: str,
    index: int | None,
    total: int | None,
    stats: dict[str, Any] | None,
    extra: dict[str, Any] | None,
    material: dict[str, Any] | None,
) -> str:
    """Return a short Chinese header line for the event card."""
    if phase == "execute" and command:
        step_index = (extra or {}).get("step_index")
        step_total = (extra or {}).get("step_total")
        bits = [f"执行了 {command}"]
        if step_index and step_total:
            bits.append(f"步骤 {step_index}/{step_total}")
        if index and total:
            bits.append(f"素材 {index}/{total}")
        return " · ".join(bits)
    if phase == "execute":
        if index and total:
            return f"开始执行素材 {index}/{total}"
        return "开始执行"
    if phase == "read":
        material_count = (stats or {}).get("material_count")
        if material_count and not index:
            return f"准备读取 {material_count} 个素材"
        if index and total:
            verb = "已读取" if status == "succeeded" else "正在读取"
            return f"{verb}素材 {index}/{total}"
        return "已读取素材" if status == "succeeded" else "正在读取素材"
    if phase == "plan":
        target_count = (stats or {}).get("target_count")
        time_reference_count = (stats or {}).get("time_reference_count")
        if label == "选择目标素材" and target_count:
            return f"已选择 {target_count} 个目标"
        if label == "锁定时间参考" and time_reference_count:
            return f"已锁定 {time_reference_count} 个时间参考"
        if label == "生成执行计划":
            return "正在生成计划"
        step_count = (stats or {}).get("step_count")
        if step_count:
            return f"已规划 {step_count} 个步骤"
        if index and total:
            verb = "已规划" if status == "succeeded" else "正在规划"
            return f"{verb}素材 {index}/{total}"
        return "已规划执行" if status == "succeeded" else "正在规划执行"
    if phase == "think":
        material_count = (stats or {}).get("material_count")
        if material_count:
            return f"已收集 {material_count} 个素材"
        return "正在准备" if status != "succeeded" else "准备完成"
    if phase == "ask":
        return "等待补充信息"
    if phase == "result":
        if index and total:
            return f"完成了素材 {index}/{total}"
        return "最终汇报"
    if phase == "error":
        return "执行异常"
    return meta or (str(material.get("name")) if material and material.get("name") else "进度")


def _short_text(value: str, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "..."


def _materials_overview(materials: list[dict[str, Any]]) -> str:
    lines = [f"已收集 {len(materials)} 个素材，来源包含时间轴、项目资产和账号媒体库。"]
    for item in materials[:6]:
        lines.append(f"- {_material_line(item)}")
    if len(materials) > 6:
        lines.append(f"- 还有 {len(materials) - 6} 个素材待阅读")
    return "\n".join(lines)


def _target_selection_body(targets: list[dict[str, Any]], material_count: int) -> str:
    lines = [f"从 {material_count} 个已读素材中选择 {len(targets)} 个执行目标。"]
    for item in targets[:6]:
        lines.append(f"- {_material_line(item)}")
    if len(targets) > 6:
        lines.append(f"- 还有 {len(targets) - 6} 个目标会继续逐个执行")
    return "\n".join(lines)


def _material_line(material: dict[str, Any]) -> str:
    metadata = material.get("metadata") if isinstance(material.get("metadata"), dict) else {}
    name = str(material.get("name") or Path(str(material.get("source_path") or "")).name or "素材")
    kind = str(material.get("media_kind") or "media")
    duration = _float_or(material.get("duration") or metadata.get("duration"), 0.0)
    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)
    origin = str(material.get("origin") or "")
    bits = [name, kind]
    if duration:
        bits.append(f"{duration:.2f}s")
    if width and height:
        bits.append(f"{width}x{height}")
    if origin:
        bits.append(origin)
    return " · ".join(bits)


def _material_read_body(material: dict[str, Any]) -> str:
    status = str(material.get("read_status") or "unknown")
    detail = _material_read_detail(material)
    error = str(material.get("read_error") or "")
    lines = [f"读取状态：{status}", f"素材信息：{detail}"]
    metadata = material.get("metadata") if isinstance(material.get("metadata"), dict) else {}
    if metadata.get("has_audio"):
        lines.append("音频：有")
    elif material.get("media_kind") != "image":
        lines.append("音频：未检测到")
    if error:
        lines.append(f"备注：{error}")
    return "\n".join(lines)


def _material_execution_body(material: dict[str, Any]) -> str:
    source = str(material.get("source_path") or "")
    lines = [f"目标素材：{_material_line(material)}"]
    if source:
        lines.append(f"源文件：{Path(source).name}")
    return "\n".join(lines)


def _plan_body(plan: dict[str, Any]) -> str:
    assistant_message = _plan_assistant_message(plan)
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    if not steps:
        return assistant_message or "我已经拿到请求，但这次没有生成需要执行的媒体步骤。"
    names: list[str] = []
    for index, step in enumerate(steps[:8], start=1):
        if not isinstance(step, dict):
            continue
        function_name = _function_label(str(step.get("function") or step.get("type") or step.get("id") or "step"))
        names.append(function_name)
    if assistant_message:
        lines = [assistant_message]
        if names:
            lines.append(f"接下来会调用：{'、'.join(names)}。")
        if len(steps) > len(names):
            lines.append(f"另外还有 {len(steps) - len(names)} 个底层动作会串起来执行。")
        return "\n".join(lines)
    lines = [f"我会按 {len(steps)} 个动作执行："]
    for index, function_name in enumerate(names, start=1):
        lines.append(f"{index}. {function_name}")
    if len(steps) > 8:
        lines.append(f"...还有 {len(steps) - 8} 个步骤")
    return "\n".join(lines)


def _plan_assistant_message(plan: dict[str, Any]) -> str:
    for key in ("assistant_message", "assistant_text", "message", "summary"):
        value = plan.get(key)
        if isinstance(value, str) and value.strip():
            return _trim_report_body(value, max_chars=360, max_lines=3)
    return ""


def _step_assistant_message(plan: dict[str, Any], current: int, function_name: str) -> str:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    candidates: list[dict[str, Any]] = []
    if 1 <= current <= len(steps) and isinstance(steps[current - 1], dict):
        candidates.append(steps[current - 1])
    fn = str(function_name or "")
    candidates.extend(
        step for step in steps
        if isinstance(step, dict) and str(step.get("function") or "") == fn
    )
    for step in candidates:
        for key in ("assistant_message", "assistant_text", "message", "summary"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                return _trim_report_body(value, max_chars=260, max_lines=2)
    return ""


def _questions_summary(questions: list[Any]) -> str:
    texts = []
    for item in questions[:3]:
        if isinstance(item, dict):
            texts.append(str(item.get("text") or item.get("label") or item.get("id") or "").strip())
        else:
            texts.append(str(item).strip())
    return " / ".join(text for text in texts if text) or "AI planner requested clarification"


def _ask_body(questions: list[Any]) -> str:
    lines = ["Planner 需要你补齐这些信息，我会拿到答案后继续生成可执行计划。"]
    for item in questions[:6]:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("label") or item.get("id") or "").strip()
        else:
            text = str(item).strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def _execute_start_body(plan: dict[str, Any], output_path: str) -> str:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    output_name = Path(output_path).name
    if steps:
        return f"Gemini 的计划已经就绪。我会按它给出的 {len(steps)} 个动作执行，结果写入 {output_name}。"
    return f"Gemini 没有给出需要串联的底层动作；我会直接确认输出目标 {output_name}。"


def _unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        text = str(path or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _material_result_body(outputs: list[str], artifact_paths: list[str] | None = None) -> str:
    artifacts = artifact_paths or []
    if not outputs and not artifacts:
        return "该素材执行完成，但没有登记新的输出文件。"
    lines: list[str] = []
    if outputs:
        lines.append(f"该素材生成 {len(outputs)} 个媒体输出：")
        for output_path in outputs[:4]:
            lines.append(f"- {Path(output_path).name}")
        if len(outputs) > 4:
            lines.append(f"- 还有 {len(outputs) - 4} 个媒体输出")
    if artifacts:
        lines.append(f"同时留下 {len(artifacts)} 个文档产物，不会作为视频预览打开：")
        for artifact_path in artifacts[:4]:
            lines.append(f"- {Path(artifact_path).name}")
        if len(artifacts) > 4:
            lines.append(f"- 还有 {len(artifacts) - 4} 个文档产物")
    return "\n".join(lines)


def _final_report_body(
    *,
    prompt: str,
    targets: list[dict[str, Any]],
    outputs: list[str],
    child_tasks: list[dict[str, Any]],
    artifact_outputs: list[str] | None = None,
) -> str:
    artifacts = artifact_outputs or []
    lines = [
        "最终汇报",
        f"本轮任务：{_short_text(prompt, 180)}",
        f"处理了：{len(targets)} 个素材",
        f"生成了：{len(outputs)} 个媒体输出",
    ]
    if artifacts:
        lines.append(f"留下了：{len(artifacts)} 个文档产物")
    if child_tasks:
        lines.append(f"子任务：{len(child_tasks)} 个")
    if outputs:
        lines.append("输出文件：")
        for output_path in outputs[:6]:
            lines.append(f"- {Path(output_path).name}")
        if len(outputs) > 6:
            lines.append(f"- 还有 {len(outputs) - 6} 个输出")
    if artifacts:
        lines.append("文档产物：")
        for artifact_path in artifacts[:6]:
            lines.append(f"- {Path(artifact_path).name}")
        if len(artifacts) > 6:
            lines.append(f"- 还有 {len(artifacts) - 6} 个文档产物")
    return "\n".join(lines)


def _function_label(function_name: str) -> str:
    text = str(function_name or "step").strip()
    if not text:
        return "step"
    return text.split(".")[-1]


def _material_from_raw(
    raw: dict[str, Any],
    *,
    origin: str,
    clip_id: str | None = None,
    selected: bool = False,
) -> dict[str, Any]:
    source_path = str(raw.get("source_path") or raw.get("serverPath") or raw.get("server_path") or raw.get("path") or "")
    name = str(raw.get("name") or Path(source_path).name or "media")
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    mime_type = str(raw.get("mime_type") or raw.get("mimeType") or metadata.get("mime_type") or mimetypes.guess_type(name)[0] or "")
    try:
        media_kind = str(raw.get("media_kind") or raw.get("mediaKind") or metadata.get("media_kind") or media_kind_for_path(source_path or name))
    except Exception:
        media_kind = "video"
    if media_kind not in {"video", "image", "audio"}:
        media_kind = "video"
    duration = _float_or(raw.get("duration") or metadata.get("duration"), IMAGE_DURATION if media_kind == "image" else 0.0)
    asset_id = str(raw.get("asset_id") or raw.get("assetId") or raw.get("id") or "")
    material_id = asset_id or f"material_{uuid.uuid5(uuid.NAMESPACE_URL, source_path or name).hex[:16]}"
    source_in = _float_or(raw.get("source_in") or raw.get("sourceIn") or raw.get("inPoint"), 0.0)
    source_out = _float_or(raw.get("source_out") or raw.get("sourceOut") or raw.get("outPoint"), 0.0)
    if source_out <= source_in and duration > 0:
        source_out = source_in + duration
    if source_out > source_in and (bool(raw.get("trimmed")) or source_in > 0.01):
        duration = max(0.1, source_out - source_in)
    return {
        "material_id": material_id,
        "asset_id": asset_id,
        "clip_id": clip_id or str(raw.get("clip_id") or ""),
        "name": name,
        "source_path": source_path,
        "preview_src": raw.get("preview_src") or raw.get("previewSrc") or "",
        "media_kind": media_kind,
        "mime_type": mime_type,
        "duration": duration,
        "metadata": metadata,
        "source_in": source_in,
        "source_out": source_out,
        "timeline_start": _float_or(raw.get("timeline_start") or raw.get("start"), 0.0),
        "trimmed": bool(raw.get("trimmed")) or source_in > 0.01,
        "effects": raw.get("effects") if isinstance(raw.get("effects"), dict) else {},
        "transition_after": raw.get("transition_after") if isinstance(raw.get("transition_after"), dict) else raw.get("transitionAfter"),
        "origin": origin,
        "selected": selected,
        "read_status": "pending",
    }


def _prompt_only_material(prompt: str) -> dict[str, Any]:
    """Synthetic target used when the user wants creation without media upload."""
    return {
        "material_id": f"prompt_canvas_{uuid.uuid5(uuid.NAMESPACE_URL, prompt or 'lumeri').hex[:12]}",
        "asset_id": "",
        "clip_id": "",
        "name": "Prompt-only canvas",
        "source_path": "",
        "preview_src": "",
        "media_kind": "video",
        "mime_type": "video/mp4",
        "duration": 3.0,
        "metadata": {
            "media_kind": "video",
            "mime_type": "video/mp4",
            "duration": 3.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "has_audio": False,
            "prompt_only_creation": True,
        },
        "origin": "prompt",
        "selected": True,
        "read_status": "virtual",
        "read_error": "",
    }


def _asset_fields_from_timeline_clip(clip: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": clip.get("asset_id"),
        "id": clip.get("asset_id"),
        "name": clip.get("name"),
        "media_kind": clip.get("media_kind"),
        "duration": clip.get("duration"),
        "source_in": clip.get("source_in"),
        "source_out": clip.get("source_out"),
        "timeline_start": clip.get("start"),
        "trimmed": bool(_float_or(clip.get("source_in"), 0.0) > 0.01),
        "effects": clip.get("effects") if isinstance(clip.get("effects"), dict) else {},
        "transition_after": clip.get("transition_after") if isinstance(clip.get("transition_after"), dict) else None,
        "metadata": clip.get("metadata") if isinstance(clip.get("metadata"), dict) else {},
        "thumbnails": clip.get("thumbnails") if isinstance(clip.get("thumbnails"), list) else [],
        "waveform_peaks": clip.get("waveform_peaks") if isinstance(clip.get("waveform_peaks"), list) else [],
    }


def _normalize_material_metadata(material: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    media_kind = str(metadata.get("media_kind") or material.get("media_kind") or "video")
    if media_kind not in {"video", "image", "audio"}:
        media_kind = "video"
    probed_duration = _float_or(metadata.get("duration"), 0.0)
    material_duration = _float_or(material.get("duration"), 0.0)
    is_timeline_clip = str(material.get("origin") or "") == "timeline"
    has_source_bounds = _float_or(material.get("source_out"), 0.0) > _float_or(material.get("source_in"), 0.0)
    if media_kind == "image":
        duration = IMAGE_DURATION
    elif is_timeline_clip and has_source_bounds:
        duration = max(0.1, _float_or(material.get("source_out"), 0.0) - _float_or(material.get("source_in"), 0.0))
    elif is_timeline_clip and material_duration > 0:
        duration = material_duration
    else:
        duration = _float_or(metadata.get("duration") or material.get("duration"), 0.0)
    material["media_kind"] = media_kind
    material["mime_type"] = str(metadata.get("mime_type") or material.get("mime_type") or "")
    material["duration"] = duration
    if probed_duration and is_timeline_clip:
        metadata.setdefault("source_duration", probed_duration)
    material["metadata"] = {
        **metadata,
        "media_kind": media_kind,
        "mime_type": material["mime_type"],
        "duration": duration,
    }
    return material


def _material_key(material: dict[str, Any]) -> str:
    asset_id = str(material.get("asset_id") or material.get("material_id") or "")
    if asset_id:
        return f"asset:{asset_id}"
    return f"path:{_normalized_path(str(material.get('source_path') or ''))}"


def _material_dedupe_key(material: dict[str, Any]) -> str:
    if str(material.get("origin") or "") == "timeline":
        clip_id = str(material.get("clip_id") or "")
        if clip_id:
            return f"clip:{clip_id}"
        source_in = _float_or(material.get("source_in"), 0.0)
        source_out = _float_or(material.get("source_out"), 0.0)
        return f"timeline:{_material_key(material)}:{source_in:.6f}:{source_out:.6f}"
    return _material_key(material)


def _normalized_path(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return str(value)


def _prompt_material_summary(material: dict[str, Any]) -> dict[str, Any]:
    metadata = material.get("metadata") if isinstance(material.get("metadata"), dict) else {}
    return {
        "material_id": material.get("material_id"),
        "asset_id": material.get("asset_id"),
        "clip_id": material.get("clip_id"),
        "name": material.get("name"),
        "source_path": material.get("source_path"),
        "media_kind": material.get("media_kind"),
        "mime_type": material.get("mime_type"),
        "duration": material.get("duration"),
        "width": metadata.get("width"),
        "height": metadata.get("height"),
        "fps": metadata.get("fps"),
        "has_audio": metadata.get("has_audio"),
        "source_in": material.get("source_in"),
        "source_out": material.get("source_out"),
        "timeline_start": material.get("timeline_start"),
        "trimmed": bool(material.get("trimmed")),
        "origin": material.get("origin"),
        "selected": bool(material.get("selected")),
        "read_status": material.get("read_status"),
        "read_error": material.get("read_error") or "",
    }


def _reference_assets_from_project_state(project_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(project_state, dict):
        return []
    raw = project_state.get("reference_assets")
    if raw is None:
        raw = project_state.get("referenceAssets")
    if raw is None:
        draft = project_state.get("draft") if isinstance(project_state.get("draft"), dict) else {}
        raw = draft.get("reference_assets") or draft.get("referenceAssets")
    if not isinstance(raw, list):
        return []
    assets: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        assets.append(
            {
                "reference_asset_id": str(item.get("reference_asset_id") or item.get("id") or f"ref_{index}"),
                "name": str(item.get("name") or item.get("filename") or item.get("file_name") or f"reference-{index}"),
                "path": str(item.get("path") or item.get("source_path") or item.get("uri") or ""),
                "mime": str(item.get("mime") or item.get("mime_type") or item.get("mimeType") or ""),
                "kind": str(item.get("kind") or item.get("media_kind") or item.get("mediaKind") or ""),
                "preview": str(item.get("preview") or item.get("preview_src") or item.get("previewSrc") or ""),
                "promoted_asset_id": str(item.get("promoted_asset_id") or item.get("promotedAssetId") or ""),
                "created_at": str(item.get("created_at") or item.get("createdAt") or ""),
            }
        )
    return assets


def _human_feedback_from_project_state(project_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(project_state, dict):
        return []
    raw = project_state.get("human_feedback") or project_state.get("humanFeedback") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "feedback_id": str(item.get("feedback_id") or item.get("id") or f"feedback_{index}"),
                "feedback": str(item.get("feedback") or item.get("text") or item.get("body") or ""),
                "render_pass_id": str(item.get("render_pass_id") or item.get("renderPassId") or ""),
                "layer_id": str(item.get("layer_id") or item.get("layerId") or ""),
                "time_range": item.get("time_range") or item.get("timeRange") or None,
                "created_at": str(item.get("created_at") or item.get("createdAt") or ""),
            }
        )
    return out


def _creative_mode_for_run(*, prompt_only_creation: bool, reference_assets: list[dict[str, Any]]) -> str:
    if reference_assets:
        return "reference_guided"
    if prompt_only_creation:
        return "prompt_only"
    return "timeline_guided"


def _initial_layer_plan(
    *,
    prompt: str,
    creative_mode: str,
    reference_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "creative_mode": creative_mode,
        "prompt": _short_text(prompt, 240),
        "layers": [],
        "render_pass_ids": [],
        "reference_asset_ids": [str(item.get("reference_asset_id")) for item in reference_assets],
        "updated_at": _utc_now(),
    }


def _reference_asset_summary(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "reference_asset_id": asset.get("reference_asset_id"),
        "name": asset.get("name"),
        "path": asset.get("path"),
        "mime": asset.get("mime"),
        "kind": asset.get("kind"),
        "preview": asset.get("preview"),
        "promoted_asset_id": asset.get("promoted_asset_id") or "",
        "created_at": asset.get("created_at") or "",
    }


def _compact_render_pass(render_pass: dict[str, Any]) -> dict[str, Any]:
    return {
        "render_pass_id": render_pass.get("render_pass_id"),
        "kind": render_pass.get("kind"),
        "output_path": render_pass.get("output_path"),
        "preview_path": render_pass.get("preview_path"),
        "capabilities": render_pass.get("capabilities") or [],
        "layer_ids": render_pass.get("layer_ids") or [],
        "manifest_paths": render_pass.get("manifest_paths") or [],
        "status": render_pass.get("status") or "succeeded",
    }


def _compact_review_note(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_note_id": note.get("review_note_id"),
        "render_pass_id": note.get("render_pass_id"),
        "verdict": note.get("verdict"),
        "note": note.get("note"),
        "created_at": note.get("created_at"),
    }


def _compact_human_feedback(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "feedback_id": item.get("feedback_id"),
        "feedback": item.get("feedback"),
        "render_pass_id": item.get("render_pass_id") or "",
        "layer_id": item.get("layer_id") or "",
        "time_range": item.get("time_range"),
        "created_at": item.get("created_at") or "",
    }


def _render_passes_for_child(
    *,
    child_task_id: str,
    target: dict[str, Any],
    plan: dict[str, Any],
    outputs: list[str],
) -> list[dict[str, Any]]:
    steps = [step for step in plan.get("steps", []) if isinstance(step, dict)]
    functions = [str(step.get("function") or "") for step in steps if step.get("function")]
    capabilities = sorted({_capability_family(function) for function in functions if function})
    passes: list[dict[str, Any]] = []
    for index, output in enumerate(outputs, start=1):
        output_path = str(output)
        sidecars = _sidecar_paths_for_output(output_path)
        sidecar_payloads = {str(path): _load_json_file(path) for path in sidecars if path.suffix == ".json"}
        layers = _layers_from_sidecars(sidecar_payloads)
        dev_brief = next((str(path) for path in sidecars if path.name.endswith(".lumeri-dev-brief.json")), "")
        fallback_payload = _veo_fallback_payload(sidecar_payloads)
        is_media = is_media_output(output_path)
        output_exists = Path(output_path).exists()
        status = "succeeded" if is_media and output_exists else "missing_output"
        if not is_media:
            status = "artifact_ready" if output_exists else "missing_artifact"
        kind = _render_pass_kind(functions, sidecar_payloads)
        if not is_media:
            kind = "development_brief" if dev_brief or output_path.endswith(".lumeri-dev-brief.md") else "document_artifact"
        render_pass_id = f"pass_{uuid.uuid5(uuid.NAMESPACE_URL, child_task_id + ':' + output_path).hex[:12]}"
        passes.append(
            {
                "schema_version": 1,
                "render_pass_id": render_pass_id,
                "kind": kind,
                "status": status,
                "created_at": _utc_now(),
                "child_task_id": child_task_id,
                "target_material_id": target.get("material_id"),
                "asset_id": target.get("asset_id"),
                "clip_id": target.get("clip_id"),
                "output_path": output_path,
                "preview_path": output_path if is_media else "",
                "artifact_path": "" if is_media else output_path,
                "manifest_paths": [str(path) for path in sidecars],
                "capabilities": capabilities,
                "step_functions": functions,
                "layer_ids": [str(layer.get("id")) for layer in layers if layer.get("id")],
                "layers": layers,
                "fallback": bool(fallback_payload),
                "fallback_reason": str(fallback_payload.get("user_message") or "") if fallback_payload else "",
                "provider_status": str(fallback_payload.get("status") or "") if fallback_payload else "",
                "provenance": {
                    "generated_by": "lumeri_agent_workflow",
                    "plan_goal": plan.get("goal") or "",
                    "output_index": index,
                },
                "dev_brief_path": dev_brief or (output_path if output_path.endswith(".lumeri-dev-brief.md") else ""),
            }
        )
    return passes


def _sidecar_paths_for_output(output_path: str) -> list[Path]:
    output = Path(output_path).expanduser().resolve()
    suffixes = (
        ".preview.json",
        ".layer-flow.json",
        ".ad_graphics.json",
        ".ad_composition.html",
        ".html_graphics.json",
        ".mg.json",
        ".motion.json",
        ".face_tracking.json",
        ".blenderlink.json",
        ".veo-fallback.json",
        ".real-media-review.json",
        ".lumeri-dev-brief.json",
        ".lumeri-dev-brief.md",
    )
    paths: list[Path] = []
    for suffix in suffixes:
        path = output.with_suffix(suffix)
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


def _veo_fallback_payload(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for payload in payloads.values():
        kind = str(payload.get("kind") or payload.get("effect") or "").lower()
        if "veo_fallback" in kind or "veo_local_fallback" in kind:
            return payload
        status = str(payload.get("status") or "").lower()
        provider = str(payload.get("provider") or "").lower()
        if status == "fallback" and "veo" in provider:
            return payload
    return {}


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _layers_from_sidecars(payloads: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    for path, payload in payloads.items():
        authored = payload.get("authored_plan") if isinstance(payload.get("authored_plan"), dict) else {}
        materialized = payload.get("materialized_plan") if isinstance(payload.get("materialized_plan"), dict) else {}
        for source, plan in (("authored", authored), ("materialized", materialized)):
            for layer in plan.get("layers", []) if isinstance(plan.get("layers"), list) else []:
                if not isinstance(layer, dict):
                    continue
                layers.append(_layer_summary(layer, manifest_path=path, source=source))
        composition = payload.get("composition") if isinstance(payload.get("composition"), dict) else {}
        for block in composition.get("blocks", []) if isinstance(composition.get("blocks"), list) else []:
            if not isinstance(block, dict):
                continue
            layer_id = str(block.get("id") or f"block_{len(layers) + 1}")
            layers.append(
                {
                    "id": layer_id,
                    "type": str(block.get("kind") or "ad_graphic"),
                    "text": str(block.get("text") or block.get("role") or ""),
                    "z_index": block.get("data-track-index"),
                    "timing": {
                        "start": block.get("data-start", 0),
                        "duration": block.get("data-duration", composition.get("duration")),
                    },
                    "source_manifest": path,
                    "source": "ad_composition",
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for layer in layers:
        key = (str(layer.get("id") or ""), str(layer.get("source_manifest") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(layer)
    return deduped


def _layer_summary(layer: dict[str, Any], *, manifest_path: str, source: str) -> dict[str, Any]:
    return {
        "id": str(layer.get("id") or f"layer_{uuid.uuid4().hex[:8]}"),
        "type": str(layer.get("type") or "layer"),
        "text": str(layer.get("text") or ""),
        "source": layer.get("source"),
        "timing": {
            "start_frame": layer.get("start_frame"),
            "end_frame": layer.get("end_frame"),
            "duration": layer.get("duration"),
        },
        "z_index": layer.get("z_index"),
        "transform": {
            "position": layer.get("position"),
            "scale": layer.get("scale"),
            "rotation_deg": layer.get("rotation_deg"),
        },
        "opacity": layer.get("opacity"),
        "style": layer.get("font_config") if isinstance(layer.get("font_config"), dict) else {},
        "source_manifest": manifest_path,
        "generated_by": source,
    }


def _render_pass_kind(functions: list[str], sidecar_payloads: dict[str, dict[str, Any]]) -> str:
    effects = [
        str(payload.get("effect") or payload.get("kind") or "")
        for payload in sidecar_payloads.values()
        if payload.get("effect") or payload.get("kind")
    ]
    if effects:
        return effects[0]
    if any("ad_graphics" in function for function in functions):
        return "ad_graphics_preview"
    if any("layer_flow" in function for function in functions):
        return "layer_preview"
    if any("generative" in function for function in functions):
        return "generative_preview"
    return "render_preview"


def _merge_layer_plan_snapshot(layer_plan: dict[str, Any], passes: list[dict[str, Any]]) -> dict[str, Any]:
    merged = deepcopy(layer_plan) if isinstance(layer_plan, dict) else _initial_layer_plan(prompt="", creative_mode="timeline_guided", reference_assets=[])
    layers = list(merged.get("layers") if isinstance(merged.get("layers"), list) else [])
    seen = {(str(layer.get("id") or ""), str(layer.get("source_manifest") or "")) for layer in layers if isinstance(layer, dict)}
    for render_pass in passes:
        pass_id = str(render_pass.get("render_pass_id") or "")
        if pass_id:
            ids = merged.setdefault("render_pass_ids", [])
            if isinstance(ids, list) and pass_id not in ids:
                ids.append(pass_id)
        for layer in render_pass.get("layers") or []:
            if not isinstance(layer, dict):
                continue
            key = (str(layer.get("id") or ""), str(layer.get("source_manifest") or ""))
            if key in seen:
                continue
            seen.add(key)
            layers.append(layer)
    merged["layers"] = layers
    merged["updated_at"] = _utc_now()
    return merged


def _review_notes_for_passes(passes: list[dict[str, Any]], *, prompt: str) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for render_pass in passes:
        pass_id = str(render_pass.get("render_pass_id") or "")
        status = str(render_pass.get("status") or "")
        has_dev_brief = bool(render_pass.get("dev_brief_path"))
        if has_dev_brief and status in {"succeeded", "artifact_ready"}:
            verdict = "needs_dev"
            note = "这里暴露出一个底层能力缺口：我已经写出开发补丁 brief，普通运行不会直接改核心源码，下一步应由开发者/代理补齐能力后再重试这一处。"
        elif status == "artifact_ready":
            verdict = "artifact"
            note = "这一轮留下的是文档或 manifest 产物，不是可播放小样。我会把它作为参考记录，不会把它塞进视频播放器。"
        elif status != "succeeded":
            verdict = "needs_work"
            note = "这个小样还没有可靠产出媒体文件。我会把它标记为需要重做，只修这一段对应的图层或能力调用。"
        elif render_pass.get("fallback"):
            verdict = "fallback"
            note = (
                str(render_pass.get("fallback_reason") or "").strip()
                or "Veo 外部生成暂时不可用；我先保留一个本地低清预览，让这一轮创作可以继续审、继续改。"
            )
        else:
            layer_count = len(render_pass.get("layer_ids") or [])
            capability_text = "、".join(render_pass.get("capabilities") or []) or "本地渲染"
            if layer_count:
                note = f"这个小样已经产出，我看到它包含 {layer_count} 个可回溯图层，主要调用了 {capability_text}。下一轮如果你指出文字、节奏或画面问题，我会只改对应 render pass。"
            else:
                note = f"这个小样已经产出，主要调用了 {capability_text}。我会把它作为当前阶段的预览结果，后续反馈只局部修正。"
            verdict = "pass"
        notes.append(
            {
                "review_note_id": f"review_{uuid.uuid5(uuid.NAMESPACE_URL, pass_id + ':' + prompt).hex[:12]}",
                "render_pass_id": pass_id,
                "verdict": verdict,
                "note": note,
                "created_at": _utc_now(),
            }
        )
    return notes


def _capability_call_body(function_name: str, plan: dict[str, Any], step_index: int) -> str:
    step_message = _step_assistant_message(plan, step_index, function_name)
    if step_message:
        return step_message
    family = _capability_family(function_name)
    return f"我正在调用 {family} 能力处理这一小步；完成后会留下可回看的输出、manifest 或小样记录。"


def _step_call_snapshot(plan: dict[str, Any], step_index: int, function_name: str) -> dict[str, Any]:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    step: dict[str, Any] | None = None
    if 1 <= step_index <= len(steps) and isinstance(steps[step_index - 1], dict):
        step = steps[step_index - 1]
    if step is None:
        step = next(
            (
                item for item in steps
                if isinstance(item, dict) and str(item.get("function") or "") == str(function_name or "")
            ),
            None,
        )
    if not isinstance(step, dict):
        return {"function": str(function_name or ""), "args": {}}
    return {
        "id": str(step.get("id") or ""),
        "function": str(step.get("function") or function_name or ""),
        "input": deepcopy(step.get("input")) if "input" in step else plan.get("input_path", ""),
        "output": deepcopy(step.get("output")) if "output" in step else plan.get("output_path", ""),
        "args": deepcopy(step.get("args") if isinstance(step.get("args"), dict) else {}),
    }


def _capability_family(function_name: str) -> str:
    fqn = str(function_name or "")
    lowered = fqn.lower()
    if "ad_graphics" in lowered:
        return "ad-graphics"
    if "layer_flow" in lowered or "html_graphics" in lowered or "motion_graphics" in lowered:
        return "layer-render"
    if ".picture.generative" in lowered:
        return "nano-banana"
    if ".video.generative" in lowered:
        return "veo"
    if "stock_media" in lowered:
        return "stock-media"
    if "blender" in lowered:
        return "lumerilink-blender"
    if "creative_runtime.write_development_patch_brief" in lowered:
        return "dev-brief"
    if "transition" in lowered:
        return "transition"
    if "timeline" in lowered or "trim" in lowered or "concat" in lowered:
        return "timeline"
    if "color" in lowered or "grade" in lowered:
        return "color"
    return _function_label(fqn)


def _preview_ready_body(render_pass: dict[str, Any]) -> str:
    output = str(render_pass.get("preview_path") or render_pass.get("output_path") or "")
    kind = str(render_pass.get("kind") or "preview")
    layers = render_pass.get("layer_ids") or []
    if layers:
        return f"这一小段效果已经渲染成可看的小样：{Path(output).name}。它不是最终定稿，而是一个 {kind} 预览；里面的图层可以继续单独改。"
    return f"这一小段效果已经渲染成可看的小样：{Path(output).name}。我会先用它做阶段确认，再决定是否继续推进或局部重做。"


def _dev_brief_body(render_pass: dict[str, Any]) -> str:
    path = str(render_pass.get("dev_brief_path") or "")
    return f"这个想法需要新的底层能力。我没有在普通运行中直接改源码，而是写好了开发补丁 brief：{Path(path).name}。"


def _extract_time_references(project_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(project_state, dict):
        return []
    candidates: Any = project_state.get("agent_time_references") or project_state.get("timeReferences")
    if not candidates:
        metadata = project_state.get("metadata") if isinstance(project_state.get("metadata"), dict) else {}
        candidates = metadata.get("agent_time_references") or metadata.get("time_references")
    if not isinstance(candidates, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "point")
        start = _float_or(item.get("start"), 0.0)
        end = _float_or(item.get("end"), start)
        left = max(0.0, min(start, end))
        right = max(0.0, max(start, end))
        if kind != "range" or right - left < 0.05:
            kind = "point"
            right = left
        refs.append(
            {
                "id": str(item.get("id") or f"time_ref_{len(refs) + 1}"),
                "kind": kind,
                "start": round(left, 3),
                "end": round(right, 3),
                "confirmed": item.get("confirmed") is not False,
                "label": _time_reference_label(kind, left, right),
            }
        )
    return refs


def _time_reference_label(kind: str, start: float, end: float) -> str:
    if kind == "range":
        return f"{_fmt_time(start)}-{_fmt_time(end)}"
    return _fmt_time(start)


def _time_references_body(refs: list[dict[str, Any]]) -> str:
    lines = ["用户在时间轴上选择了以下位置，规划时优先围绕这些时间点/区间执行："]
    for ref in refs[:8]:
        status = "confirmed" if ref.get("confirmed") else "draft"
        lines.append(f"- {ref.get('label')} · {ref.get('kind')} · {status}")
    if len(refs) > 8:
        lines.append(f"- 还有 {len(refs) - 8} 个时间参考")
    return "\n".join(lines)


def _fmt_time(value: float) -> str:
    seconds = max(0.0, float(value))
    total_tenths = int(round(seconds * 10))
    minutes = total_tenths // 600
    whole = (total_tenths // 10) % 60
    tenths = total_tenths % 10
    base = f"{minutes:02d}:{whole:02d}"
    return f"{base}.{tenths}" if tenths else base


def _project_state_with_agent_context(project_state: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(project_state) if isinstance(project_state, dict) else {}
    payload["agent_context"] = context
    if context.get("time_references"):
        payload["agent_time_references"] = context.get("time_references")
    return payload


def _material_read_detail(material: dict[str, Any]) -> str:
    metadata = material.get("metadata") if isinstance(material.get("metadata"), dict) else {}
    bits = [str(material.get("media_kind") or "media")]
    duration = _float_or(material.get("duration"), 0.0)
    if duration:
        bits.append(f"{duration:.2f}s")
    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)
    if width and height:
        bits.append(f"{width}x{height}")
    if material.get("read_status") not in {"ready", "pending"}:
        bits.append(str(material.get("read_status")))
    return " · ".join(bits)


def _plan_detail(plan: dict[str, Any]) -> str:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    names: list[str] = []
    for step in steps[:4]:
        if not isinstance(step, dict):
            continue
        name = str(step.get("function") or step.get("type") or step.get("id") or "")
        if name:
            names.append(name.split(".")[-1])
    if not steps:
        return "0 steps"
    suffix = "..." if len(steps) > len(names) else ""
    joined = ", ".join(names) if names else "steps"
    return f"{len(steps)} steps · {joined}{suffix}"


def _float_or(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def media_kind_for_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return "video"


def probe_media(path: str) -> dict[str, Any]:
    media = Path(path)
    if not media.exists():
        raise FileNotFoundError(path)
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=width,height,r_frame_rate,codec_name,codec_type",
            "-of",
            "json",
            str(media),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    payload = json.loads(proc.stdout or "{}")
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    fps = 0.0
    fps_text = str(video.get("r_frame_rate") or "0/1")
    try:
        num, den = fps_text.split("/")
        fps = float(num) / max(float(den), 1.0)
    except Exception:
        fps = 0.0
    return {
        "duration": max(float(fmt.get("duration") or 0.0), 0.0),
        "media_kind": media_kind_for_path(media),
        "mime_type": mimetypes.guess_type(str(media))[0] or "application/octet-stream",
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audio.get("codec_name") or ""),
        "has_audio": bool(audio),
        "file_size_bytes": int(fmt.get("size") or media.stat().st_size),
    }


def _check_cancel(callback: CancelCallback | None) -> None:
    if callback is not None:
        callback()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
