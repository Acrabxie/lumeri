from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .common import (
    agent_log_path,
    append_agent_log,
    append_human_needed,
    automation_env,
    bridge_root,
    choose_stock_root,
    heartbeat_state_path,
    human_needed_path,
    now_utc_iso,
    read_json,
    repo_root,
    rollover_queue_dir,
    runtime_logs_dir,
    runtime_state_path,
    safe_slug,
    stock_catalog_path,
    stock_manifest_path,
    write_json,
)
from .gemini_media import GeminiMediaClient
from .stock_sources import (
    ExternalStockClient,
    LocalStockClient,
    MissingStockSourceCredentials,
    StockAsset,
    StockSourceError,
)

HEARTBEAT_INTERVAL_SEC = 2 * 60 * 60
ROLLOVER_INTERVAL_SEC = 5 * 60 * 60
IMAGE_TARGET = 1500
VIDEO_TARGET = 150
IMAGE_PROMPT_BATCH = 4
DEFAULT_STOCK_PREFERRED_FREE_GIB = 20
DEFAULT_LOCAL_FALLBACK_MAX_VIDEOS = 3
DEFAULT_LOCAL_FALLBACK_MAX_IMAGES = 12

IMAGE_SUBJECTS = [
    "street market", "fashion portrait", "mountain lake", "desert highway", "glass tower skyline",
    "coastal village", "forest trail", "industrial warehouse", "bookstore corner", "night diner",
    "subway platform", "rainy alley", "tea house interior", "rooftop garden", "concert stage",
]
IMAGE_STYLES = ["editorial", "cinematic", "documentary", "commercial", "travel", "moody", "clean"]
IMAGE_LIGHTS = ["golden hour", "blue hour", "soft studio light", "rain reflections", "hard noon light"]
VIDEO_ACTIONS = [
    "camera pushes in slowly", "handheld walk-through", "drone reveal", "locked-off observation",
    "gentle orbit move", "slow pan left", "slow pan right", "tilt up from detail to wide",
]
VIDEO_MOODS = ["uplifting", "tense", "dreamy", "clean commercial", "gritty", "luxury", "playful"]


def _preferred_stock_free_bytes() -> int:
    raw = os.environ.get("GEMIA_STOCK_PREFERRED_FREE_GIB", str(DEFAULT_STOCK_PREFERRED_FREE_GIB)).strip()
    try:
        gib = max(float(raw), 2.0)
    except ValueError:
        gib = float(DEFAULT_STOCK_PREFERRED_FREE_GIB)
    return int(gib * 1024**3)


def _local_fallback_max_videos() -> int:
    raw = os.environ.get("GEMIA_LOCAL_FALLBACK_MAX_VIDEOS", str(DEFAULT_LOCAL_FALLBACK_MAX_VIDEOS)).strip()
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_LOCAL_FALLBACK_MAX_VIDEOS


def _local_fallback_max_images() -> int:
    raw = os.environ.get("GEMIA_LOCAL_FALLBACK_MAX_IMAGES", str(DEFAULT_LOCAL_FALLBACK_MAX_IMAGES)).strip()
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_LOCAL_FALLBACK_MAX_IMAGES


def _runtime_state() -> dict[str, Any]:
    state = read_json(runtime_state_path(), {})
    if state:
        return state
    stock_root = str(choose_stock_root(min_free_bytes=_preferred_stock_free_bytes()))
    now = datetime.now(timezone.utc)
    state = {
        "started_at": now_utc_iso(),
        "ends_at": (now + timedelta(days=5)).replace(microsecond=0).isoformat(),
        "stock_root": stock_root,
        "catalog_path": str(stock_catalog_path()),
        "last_heartbeat_at": "",
        "last_rollover_at": "",
        "last_stock_fill_at": "",
        "heartbeat_count": 0,
        "rollover_count": 0,
        "failure_counts": {},
    }
    write_json(runtime_state_path(), state)
    return state


def _save_state(state: dict[str, Any]) -> None:
    write_json(runtime_state_path(), state)


def _record_failure(state: dict[str, Any], issue_key: str, details: str) -> None:
    counts = state.setdefault("failure_counts", {})
    counts[issue_key] = int(counts.get(issue_key, 0)) + 1
    _save_state(state)
    if counts[issue_key] >= 3:
        append_human_needed(
            f"{issue_key} failed three times",
            details + f"\n\nagent_log: {agent_log_path()}\nstock_catalog: {stock_catalog_path()}",
        )


def _image_prompt(index: int) -> str:
    subject = IMAGE_SUBJECTS[index % len(IMAGE_SUBJECTS)]
    style = IMAGE_STYLES[(index // len(IMAGE_SUBJECTS)) % len(IMAGE_STYLES)]
    light = IMAGE_LIGHTS[(index // (len(IMAGE_SUBJECTS) * len(IMAGE_STYLES))) % len(IMAGE_LIGHTS)]
    return f"{subject}, {style} visual treatment, {light}, sharp composition, real-world clip test stock."


def _video_prompt(index: int) -> str:
    subject = IMAGE_SUBJECTS[index % len(IMAGE_SUBJECTS)]
    action = VIDEO_ACTIONS[(index // len(IMAGE_SUBJECTS)) % len(VIDEO_ACTIONS)]
    mood = VIDEO_MOODS[(index // (len(IMAGE_SUBJECTS) * len(VIDEO_ACTIONS))) % len(VIDEO_MOODS)]
    return f"{subject}, {action}, {mood} mood, natural motion, real clip-test b-roll."


def _build_catalog() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index in range(math.ceil(IMAGE_TARGET / IMAGE_PROMPT_BATCH)):
        requested = min(IMAGE_PROMPT_BATCH, IMAGE_TARGET - index * IMAGE_PROMPT_BATCH)
        task_id = f"image-{index + 1:04d}"
        tasks.append(
            {
                "id": task_id,
                "kind": "image",
                "prompt": _image_prompt(index),
                "count": requested,
                "aspect_ratio": "16:9",
                "image_size": "1K",
                "status": "pending",
                "attempts": 0,
                "outputs": [],
            }
        )
    for index in range(VIDEO_TARGET):
        task_id = f"video-{index + 1:04d}"
        tasks.append(
            {
                "id": task_id,
                "kind": "video",
                "prompt": _video_prompt(index),
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "status": "pending",
                "attempts": 0,
                "outputs": [],
            }
        )
    return tasks


def ensure_catalog() -> list[dict[str, Any]]:
    path = stock_catalog_path()
    catalog = read_json(path, [])
    if catalog:
        return catalog
    catalog = _build_catalog()
    write_json(path, catalog)
    append_agent_log(
        f"initialized Gemini-native stock catalog: {VIDEO_TARGET} videos + {IMAGE_TARGET} images at {path}"
    )
    return catalog


def _catalog_progress(catalog: list[dict[str, Any]]) -> dict[str, int]:
    generated_images = sum(len(task.get("outputs", [])) for task in catalog if task["kind"] == "image")
    generated_videos = sum(len(task.get("outputs", [])) for task in catalog if task["kind"] == "video")
    return {
        "images_done": generated_images,
        "videos_done": generated_videos,
        "images_target": IMAGE_TARGET,
        "videos_target": VIDEO_TARGET,
    }


def _path_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _stock_root_allows_bulk(root: Path) -> bool:
    explicit = os.environ.get("GEMIA_STOCK_ROOT", "").strip()
    if explicit and root.expanduser().resolve(strict=False) == Path(explicit).expanduser().resolve(strict=False):
        return True
    return str(root).startswith("/Volumes/")


def _catalog_counts_for_root(catalog: list[dict[str, Any]], root: Path) -> dict[str, int]:
    videos = 0
    images = 0
    for task in catalog:
        kind = task.get("kind")
        for output in task.get("outputs", []) or []:
            if not _path_under(Path(str(output)).expanduser(), root):
                continue
            if kind == "video":
                videos += 1
            elif kind == "image":
                images += 1
    return {"videos": videos, "images": images}


def _catalog_backend_counts(catalog: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in catalog:
        backend = str(task.get("backend") or "pending")
        counts[backend] = counts.get(backend, 0) + len(task.get("outputs", []) or [])
    return counts


def _write_stock_manifest(
    state: dict[str, Any],
    catalog: list[dict[str, Any]],
    root: Path,
    *,
    storage_note: str = "",
) -> None:
    root_counts = _catalog_counts_for_root(catalog, root)
    bulk_root = _stock_root_allows_bulk(root)
    manifest = {
        "updated_at": now_utc_iso(),
        "stock_root": str(root),
        "catalog_path": str(stock_catalog_path()),
        "bulk_root": bulk_root,
        "root_class": "external_or_explicit" if bulk_root else "local_workspace_fallback",
        "storage_note": storage_note,
        "progress": _catalog_progress(catalog),
        "root_outputs": root_counts,
        "backend_counts": _catalog_backend_counts(catalog),
        "local_fallback_caps": {
            "videos": _local_fallback_max_videos(),
            "images": _local_fallback_max_images(),
        },
        "paused_reason": state.get("stock_paused_reason", ""),
        "external_storage_needed": not bulk_root
        and (
            root_counts["videos"] >= _local_fallback_max_videos()
            or root_counts["images"] >= _local_fallback_max_images()
        ),
    }
    write_json(stock_manifest_path(), manifest)


def _pick_tasks(catalog: list[dict[str, Any]], *, kind: str, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    picked: list[dict[str, Any]] = []
    for task in catalog:
        if task["kind"] != kind or task.get("status") == "completed":
            continue
        if int(task.get("attempts", 0)) >= 3:
            continue
        picked.append(task)
        if len(picked) >= limit:
            break
    return picked


def _pick_fallback_tasks(catalog: list[dict[str, Any]], *, kind: str, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    picked: list[dict[str, Any]] = []
    for task in catalog:
        if task["kind"] != kind or task.get("status") == "completed":
            continue
        if int(task.get("fallback_attempts", 0)) >= 3:
            continue
        picked.append(task)
        if len(picked) >= limit:
            break
    return picked


def _can_write_stock_root(root: Path) -> bool:
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _stock_root(state: dict[str, Any]) -> Path:
    configured = str(state.get("stock_root", "")).strip()
    root = Path(configured).expanduser() if configured else choose_stock_root()
    preferred_free = _preferred_stock_free_bytes()
    try:
        usage = shutil.disk_usage(root if root.exists() else root.parent)
    except FileNotFoundError:
        usage = None
    root_writable = usage is not None and _can_write_stock_root(root)
    if (
        not str(root).startswith("/Volumes/")
        or usage is None
        or usage.free < preferred_free
        or not root_writable
    ):
        previous = root
        try:
            root = choose_stock_root(min_free_bytes=preferred_free)
        except RuntimeError:
            try:
                root = choose_stock_root()
            except RuntimeError:
                if not root_writable:
                    raise
                root = previous
        if root != previous:
            append_agent_log(
                f"stock root moved from {previous} to {root} for more free disk"
            )
    if state.get("stock_root") != str(root):
        state["stock_root"] = str(root)
        _save_state(state)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _limit_local_fallback_fill(
    state: dict[str, Any],
    catalog: list[dict[str, Any]],
    root: Path,
    *,
    image_limit: int,
    video_limit: int,
) -> tuple[int, int, str]:
    if _stock_root_allows_bulk(root):
        state.pop("stock_local_fallback_paused_reason", None)
        return image_limit, video_limit, ""
    root_counts = _catalog_counts_for_root(catalog, root)
    remaining_videos = max(_local_fallback_max_videos() - root_counts["videos"], 0)
    remaining_images = max(_local_fallback_max_images() - root_counts["images"], 0)
    safe_video_limit = min(video_limit, remaining_videos)
    safe_image_limit = min(image_limit, remaining_images // IMAGE_PROMPT_BATCH)
    if safe_video_limit == video_limit and safe_image_limit == image_limit:
        state.pop("stock_local_fallback_paused_reason", None)
        return image_limit, video_limit, ""
    note = (
        "external_storage_needed: local workspace fallback root is capped at "
        f"{_local_fallback_max_videos()} videos and {_local_fallback_max_images()} images; "
        "set GEMIA_STOCK_ROOT to writable external storage or mount a writable /Volumes root for bulk stock fill."
    )
    state["stock_local_fallback_paused_reason"] = "external_storage_needed"
    if not state.get("stock_external_storage_needed_logged"):
        state["stock_external_storage_needed_logged"] = True
        append_human_needed("External stock storage needed", note)
    return safe_image_limit, safe_video_limit, note


def _all_source_paths(catalog: list[dict[str, Any]]) -> set[str]:
    sources: set[str] = set()
    for task in catalog:
        source = str(task.get("source", "")).strip()
        if source.startswith("/"):
            sources.add(source)
    return sources


def _apply_stock_asset(task: dict[str, Any], asset: StockAsset) -> None:
    task["outputs"] = asset.outputs
    task["status"] = "completed"
    task["completed_at"] = now_utc_iso()
    task["backend"] = asset.backend
    task["source"] = asset.source
    if asset.attribution:
        task["attribution"] = asset.attribution


def _record_stock_fallback_config_needed(state: dict[str, Any], details: str) -> None:
    if state.get("stock_external_key_needed_logged"):
        return
    state["stock_external_key_needed_logged"] = True
    _save_state(state)
    append_human_needed("Pexels/Pixabay stock API key missing", details)


def _fallback_stock_fill_once(
    state: dict[str, Any],
    catalog: list[dict[str, Any]],
    root: Path,
    *,
    image_limit: int,
    video_limit: int,
) -> dict[str, Any]:
    external = ExternalStockClient()
    local = LocalStockClient()
    completed: list[str] = []
    errors: list[str] = []
    missing_credentials = False

    for task in _pick_fallback_tasks(catalog, kind="video", limit=video_limit):
        task["fallback_attempts"] = int(task.get("fallback_attempts", 0)) + 1
        task["status"] = "running"
        write_json(stock_catalog_path(), catalog)
        output_path = root / "videos" / f"{task['id']}-{safe_slug(task['prompt'])[:48]}.mp4"
        try:
            try:
                asset = external.download_video(prompt=str(task["prompt"]), output_path=output_path)
            except MissingStockSourceCredentials:
                missing_credentials = True
                asset = local.copy_video(
                    prompt=str(task["prompt"]),
                    output_path=output_path,
                    used_sources=_all_source_paths(catalog),
                )
            except StockSourceError as exc:
                errors.append(str(exc))
                asset = local.copy_video(
                    prompt=str(task["prompt"]),
                    output_path=output_path,
                    used_sources=_all_source_paths(catalog),
                )
            _apply_stock_asset(task, asset)
            completed.append(task["id"])
            append_agent_log(f"stock fallback generated {task['id']} ({task['kind']}) via {asset.backend}")
        except Exception as exc:
            task["status"] = "failed"
            task["fallback_error"] = str(exc)
            detail = f"{task['id']}: {exc}"
            errors.append(detail)
            _record_failure(state, f"stock-fallback:{task['id']}", detail)
        finally:
            write_json(stock_catalog_path(), catalog)

    for task in _pick_fallback_tasks(catalog, kind="image", limit=image_limit):
        task["fallback_attempts"] = int(task.get("fallback_attempts", 0)) + 1
        task["status"] = "running"
        write_json(stock_catalog_path(), catalog)
        output_dir = root / "images" / task["id"]
        try:
            try:
                asset = external.download_images(
                    prompt=str(task["prompt"]),
                    output_dir=output_dir,
                    task_id=str(task["id"]),
                    count=int(task.get("count", 1)),
                )
            except MissingStockSourceCredentials:
                missing_credentials = True
                asset = local.extract_images(
                    prompt=str(task["prompt"]),
                    output_dir=output_dir,
                    task_id=str(task["id"]),
                    count=int(task.get("count", 1)),
                    used_sources=_all_source_paths(catalog),
                )
            except StockSourceError as exc:
                errors.append(str(exc))
                asset = local.extract_images(
                    prompt=str(task["prompt"]),
                    output_dir=output_dir,
                    task_id=str(task["id"]),
                    count=int(task.get("count", 1)),
                    used_sources=_all_source_paths(catalog),
                )
            _apply_stock_asset(task, asset)
            completed.append(task["id"])
            append_agent_log(f"stock fallback generated {task['id']} ({task['kind']}) via {asset.backend}")
        except Exception as exc:
            task["status"] = "failed"
            task["fallback_error"] = str(exc)
            detail = f"{task['id']}: {exc}"
            errors.append(detail)
            _record_failure(state, f"stock-fallback:{task['id']}", detail)
        finally:
            write_json(stock_catalog_path(), catalog)

    if missing_credentials:
        _record_stock_fallback_config_needed(
            state,
            "Gemini native media is paused by location restrictions and no PEXELS_API_KEY or PIXABAY_API_KEY is configured. "
            "The controller used local real videos where possible; add one stock API key to resume Pexels/Pixabay sourcing.",
        )
    state["last_stock_fill_at"] = now_utc_iso()
    state["last_stock_fallback_at"] = state["last_stock_fill_at"]
    state["last_stock_fallback_errors"] = errors[-5:]
    _save_state(state)
    _write_stock_manifest(state, catalog, root, storage_note="")
    return {
        "completed": completed,
        **_catalog_progress(catalog),
        "stock_root": str(root),
        "fallback": True,
        "errors": errors[-5:],
    }


def stock_fill_once(*, image_limit: int = 2, video_limit: int = 1) -> dict[str, Any]:
    state = _runtime_state()
    catalog = ensure_catalog()
    paused_reason = str(state.get("stock_paused_reason", "")).strip()
    if paused_reason and os.environ.get("GEMIA_FORCE_STOCK_FILL", "0") != "1":
        if paused_reason == "gemini_location_unsupported":
            root = _stock_root(state)
            image_limit, video_limit, storage_note = _limit_local_fallback_fill(
                state,
                catalog,
                root,
                image_limit=image_limit,
                video_limit=video_limit,
            )
            if image_limit <= 0 and video_limit <= 0:
                state["last_stock_fill_at"] = now_utc_iso()
                _save_state(state)
                _write_stock_manifest(state, catalog, root, storage_note=storage_note)
                return {
                    "completed": [],
                    **_catalog_progress(catalog),
                    "paused": paused_reason,
                    "fallback": True,
                    "stock_root": str(root),
                    "storage_paused": "external_storage_needed",
                    "storage_note": storage_note,
                }
            result = _fallback_stock_fill_once(
                state,
                catalog,
                root,
                image_limit=image_limit,
                video_limit=video_limit,
            )
            result["paused"] = paused_reason
            if storage_note:
                result["storage_note"] = storage_note
                _write_stock_manifest(state, catalog, root, storage_note=storage_note)
            return result
        root = Path(str(state.get("stock_root", ""))).expanduser()
        _write_stock_manifest(state, catalog, root, storage_note=f"paused:{paused_reason}")
        return {"completed": [], **_catalog_progress(catalog), "paused": paused_reason, "stock_root": state.get("stock_root", "")}
    root = _stock_root(state)
    usage = shutil.disk_usage(root)
    if usage.free < 2 * 1024**3:
        detail = f"Stock root {root} is below 2 GiB free ({usage.free} bytes)."
        _record_failure(state, "stock:disk-space", detail)
        append_agent_log(f"stock fill paused for low disk: {detail}")
        _write_stock_manifest(state, catalog, root, storage_note="paused:low_disk")
        return {"completed": [], **_catalog_progress(catalog), "stock_root": str(root), "paused": "low_disk"}
    client = GeminiMediaClient()
    completed: list[str] = []

    for task in _pick_tasks(catalog, kind="video", limit=video_limit) + _pick_tasks(catalog, kind="image", limit=image_limit):
        task["attempts"] = int(task.get("attempts", 0)) + 1
        task["status"] = "running"
        write_json(stock_catalog_path(), catalog)
        try:
            if task["kind"] == "image":
                outputs = client.generate_images(
                    prompt=task["prompt"],
                    output_dir=root / "images" / task["id"],
                    task_id=task["id"],
                    count=int(task.get("count", 1)),
                    aspect_ratio=str(task.get("aspect_ratio", "16:9")),
                    image_size=str(task.get("image_size", "1K")),
                )
            else:
                slug = safe_slug(task["prompt"])[:48]
                outputs = [
                    client.generate_video(
                        prompt=task["prompt"],
                        output_path=root / "videos" / f"{task['id']}-{slug}.mp4",
                        duration_seconds=int(task.get("duration_seconds", 5)),
                        aspect_ratio=str(task.get("aspect_ratio", "16:9")),
                        resolution=str(task.get("resolution", "720p")),
                    )
                ]
            task["outputs"] = outputs
            task["status"] = "completed"
            task["completed_at"] = now_utc_iso()
            task["backend"] = "gemini_native"
            completed.append(task["id"])
            append_agent_log(f"Gemini stock generated {task['id']} ({task['kind']}) -> {len(outputs)} output(s)")
        except Exception as exc:
            task["status"] = "failed"
            task["error"] = str(exc)
            if "User location is not supported" in str(exc):
                state["stock_paused_reason"] = "gemini_location_unsupported"
                state["stock_paused_at"] = now_utc_iso()
                append_agent_log("stock fill paused: Gemini API user location is unsupported from this network path")
            _record_failure(state, f"stock:{task['id']}", str(exc))
            append_agent_log(f"Gemini stock generation failed for {task['id']}: {exc}")
            if state.get("stock_paused_reason"):
                _save_state(state)
                break
        finally:
            write_json(stock_catalog_path(), catalog)

    if state.get("stock_paused_reason") == "gemini_location_unsupported":
        image_limit, video_limit, storage_note = _limit_local_fallback_fill(
            state,
            catalog,
            root,
            image_limit=image_limit,
            video_limit=video_limit,
        )
        if image_limit <= 0 and video_limit <= 0:
            state["last_stock_fill_at"] = now_utc_iso()
            _save_state(state)
            _write_stock_manifest(state, catalog, root, storage_note=storage_note)
            return {
                "completed": completed,
                **_catalog_progress(catalog),
                "stock_root": str(root),
                "paused": "gemini_location_unsupported",
                "fallback": True,
                "storage_paused": "external_storage_needed",
                "storage_note": storage_note,
            }
        fallback = _fallback_stock_fill_once(
            state,
            catalog,
            root,
            image_limit=image_limit,
            video_limit=video_limit,
        )
        completed.extend(fallback["completed"])
        progress = _catalog_progress(catalog)
        if storage_note:
            _write_stock_manifest(state, catalog, root, storage_note=storage_note)
        return {
            "completed": completed,
            **progress,
            "stock_root": str(root),
            "paused": "gemini_location_unsupported",
            "fallback": True,
            "errors": fallback.get("errors", []),
            "storage_note": storage_note,
        }

    state["last_stock_fill_at"] = now_utc_iso()
    _save_state(state)
    progress = _catalog_progress(catalog)
    _write_stock_manifest(state, catalog, root, storage_note="")
    return {"completed": completed, **progress, "stock_root": str(root)}


def heartbeat_once() -> dict[str, Any]:
    state = _runtime_state()
    catalog = ensure_catalog()
    stock_root = _stock_root(state)
    low_level_status = {"returncode": None}
    try:
        from gemia.bridge import (
            BridgeDaemon,
            BridgePaths,
            BridgeTask,
            ClaudeCodeAdapter,
            ControllerAdapter,
            MasterBridgeController,
            QueueBridgeAdapter,
        )

        paths = BridgePaths.from_root(bridge_root())
        controller = MasterBridgeController(
            {
                "claude_code": ClaudeCodeAdapter(default_cwd=str(repo_root())),
                "antigravity": QueueBridgeAdapter("antigravity", paths.root / "agents" / "antigravity"),
            },
            default_agent="claude_code",
        )
        daemon = BridgeDaemon(paths, ControllerAdapter(controller))
        task = BridgeTask.new(
            source="codex",
            intent="heartbeat",
            prompt="Automatic Gemia controller heartbeat.",
            metadata={"task_class": "heartbeat", "heartbeat": True},
            context={"heartbeat_action": "poll", "min_interval_sec": 0},
            cwd=str(repo_root()),
        )
        daemon.submit_task(task)
        daemon.process_once()
        result_path = daemon.paths.outbox / f"{task.task_id}.json"
        low_level_status = {"returncode": 0, "stdout": result_path.read_text(encoding="utf-8").strip(), "stderr": ""}
    except Exception as exc:
        low_level_status = {"returncode": -1, "stderr": str(exc)}
        _record_failure(state, "heartbeat-bridge", str(exc))

    usage = shutil.disk_usage(stock_root)
    summary = {
        "checked_at": now_utc_iso(),
        "stock_root": str(stock_root),
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
        "progress": _catalog_progress(catalog),
        "last_rollover_at": state.get("last_rollover_at", ""),
        "last_stock_fill_at": state.get("last_stock_fill_at", ""),
        "rollovers": _rollover_queue_snapshot(state),
        "bridge": low_level_status,
    }
    write_json(heartbeat_state_path(), summary)
    state["last_heartbeat_at"] = summary["checked_at"]
    state["heartbeat_count"] = int(state.get("heartbeat_count", 0)) + 1
    _save_state(state)
    progress = summary["progress"]
    append_agent_log(
        "controller heartbeat: "
        f"videos {progress['videos_done']}/{progress['videos_target']}, "
        f"images {progress['images_done']}/{progress['images_target']}, "
        f"free {(usage.free / 1024**3):.2f} GiB"
    )
    _write_stock_manifest(
        state,
        catalog,
        stock_root,
        storage_note=str(state.get("stock_local_fallback_paused_reason", "")),
    )
    return summary


def build_rollover_prompt(state: dict[str, Any], progress: dict[str, int]) -> str:
    return f"""[GEMIA FIVE DAY LOOP ROLLOVER]
Repository: {repo_root()}
Shared queue: {Path.home() / '.agents' / 'shared-agent-loop' / 'QUEUE.md'}
Shared daily log: {Path.home() / '.agents' / 'shared-agent-loop' / 'daily' / datetime.now().strftime('%Y-%m-%d.md')}
Runtime state: {runtime_state_path()}
Stock catalog: {stock_catalog_path()}
Stock root: {state.get('stock_root')}
Human-needed file: {human_needed_path()}

Requirements for this session:
1. Read the shared role/queue/memory layer first.
2. Continue the five-day Gemia loop using Gemini-native generation via GEMINI_API_KEY from ~/.gemia/config.json.
3. Maintain three lanes every session: architecture GitHub scouting, feature completion, and Antigravity review.
4. Keep progressing toward at least {VIDEO_TARGET} videos and {IMAGE_TARGET} images for clip testing.
5. Follow circuit breakers: same issue fails 3 times -> HUMAN_NEEDED.md, each feature reproduced twice, every 5 completed features commit once, every 10 features Antigravity debug, every 30 features Codex token/efficiency pass.
6. Update agent_log.md and shared queue/daily notes before exiting.

Current progress:
- Videos: {progress['videos_done']}/{progress['videos_target']}
- Images: {progress['images_done']}/{progress['images_target']}
- Last heartbeat: {state.get('last_heartbeat_at', '')}
- Last rollover: {state.get('last_rollover_at', '')}
- Last stock fill: {state.get('last_stock_fill_at', '')}
- Stock pause reason: {state.get('stock_paused_reason', 'active')}
"""


def _rollover_queue_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    pending = sorted(rollover_queue_dir().glob("*.json"))
    return {
        "pending": len(pending),
        "last_status": state.get("last_rollover_status", ""),
        "last_fallback_path": state.get("last_rollover_fallback_path", ""),
        "oldest_pending": str(pending[0]) if pending else "",
    }


def _persist_rollover_fallback(
    state: dict[str, Any],
    prompt: str,
    progress: dict[str, int],
    *,
    reason: str,
    log_path: Path | None = None,
    returncode: int | None = None,
) -> Path:
    created_at = now_utc_iso()
    task_id = f"rollover-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    fallback_path = rollover_queue_dir() / f"{task_id}.json"
    claude_queue_path = bridge_root() / "agents" / "claude_code" / "inbox" / f"{task_id}.json"
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "status": "pending",
        "created_at": created_at,
        "reason": reason,
        "returncode": returncode,
        "repo": str(repo_root()),
        "runtime_state": str(runtime_state_path()),
        "stock_catalog": str(stock_catalog_path()),
        "source_log": str(log_path) if log_path else "",
        "progress": progress,
        "next_agent": "claude_code",
        "review_agent": "antigravity",
        "prompt": prompt,
        "instructions": [
            "Read the shared-agent-loop memory first.",
            "Continue the Gemia five-day loop from this persisted rollover prompt.",
            "Delegate real-media editing/review tasks to Antigravity and record the result.",
            "Update agent_log.md, the checklist, and shared queue/daily notes before exiting.",
        ],
    }
    bridge_payload = {
        "task_id": task_id,
        "source": "gemia_rollover_fallback",
        "intent": "automation",
        "prompt": prompt,
        "assets": [str(fallback_path)],
        "context": {
            "rollover_fallback_path": str(fallback_path),
            "progress": progress,
            "reason": reason,
        },
        "permissions": {},
        "preferred_agent": None,
        "allowed_agents": [],
        "cwd": str(repo_root()),
        "created_at": created_at,
        "metadata": {
            "task_class": "rollover",
            "automation_id": "automation",
            "source_log": str(log_path) if log_path else "",
        },
    }
    write_json(fallback_path, payload)
    write_json(claude_queue_path, bridge_payload)
    payload["claude_code_queue"] = str(claude_queue_path)
    write_json(fallback_path, payload)
    state["last_rollover_status"] = "queued_offline"
    state["last_rollover_fallback_path"] = str(fallback_path)
    state["last_rollover_fallback_at"] = created_at
    _save_state(state)
    return fallback_path


def rollover_once(force: bool = False) -> dict[str, Any]:
    state = _runtime_state()
    catalog = ensure_catalog()
    last_rollover_raw = str(state.get("last_rollover_at", "")).strip()
    if last_rollover_raw and not force:
        last_rollover = datetime.fromisoformat(last_rollover_raw)
        if (datetime.now(timezone.utc) - last_rollover).total_seconds() < ROLLOVER_INTERVAL_SEC:
            return {"skipped": True, "reason": "not_due"}
    progress = _catalog_progress(catalog)
    prompt = build_rollover_prompt(state, progress)
    log_path = runtime_logs_dir() / f"rollover-{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GEMIA_ROLLOVER_OFFLINE_ONLY", "0") == "1":
        log_path.write_text(
            "returncode=offline-only\n\nSTDOUT:\n\n\nSTDERR:\nGEMIA_ROLLOVER_OFFLINE_ONLY requested local queue fallback.\n",
            encoding="utf-8",
        )
        fallback_path = _persist_rollover_fallback(
            state,
            prompt,
            progress,
            reason="offline_only_requested",
            log_path=log_path,
        )
        append_agent_log(f"five-hour rollover queued locally for Claude Code recovery: {fallback_path}")
        result_code = None
    else:
        cmd = [str(repo_root() / "acpx-codex.sh"), "--format", "json", "--json-strict", "codex", "exec", prompt]
        result = subprocess.run(
            cmd,
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            env=automation_env(),
            check=False,
        )
        log_path.write_text(
            f"returncode={result.returncode}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n",
            encoding="utf-8",
        )
        result_code = result.returncode
        if result.returncode != 0:
            try:
                fallback_path = _persist_rollover_fallback(
                    state,
                    prompt,
                    progress,
                    reason="codex_acp_failed",
                    log_path=log_path,
                    returncode=result.returncode,
                )
                append_agent_log(
                    f"five-hour rollover queued locally after Codex ACP failure: {fallback_path}"
                )
            except Exception:
                _record_failure(state, "rollover-codex", log_path.read_text(encoding="utf-8"))
                append_agent_log(f"five-hour rollover failed: {log_path}")
                raise
        else:
            state["last_rollover_status"] = "launched"
            append_agent_log(f"five-hour rollover launched successfully: {log_path}")
    state["last_rollover_at"] = now_utc_iso()
    state["rollover_count"] = int(state.get("rollover_count", 0)) + 1
    _save_state(state)
    response: dict[str, Any] = {"returncode": result_code, "log_path": str(log_path)}
    if state.get("last_rollover_status") == "queued_offline":
        response["queued"] = True
        response["queue_path"] = state.get("last_rollover_fallback_path", "")
    return response


def tick_once(force_rollover: bool = False) -> dict[str, Any]:
    state = _runtime_state()
    catalog = ensure_catalog()
    progress = _catalog_progress(catalog)
    actions: dict[str, Any] = {"progress_before": progress}
    last_heartbeat_raw = str(state.get("last_heartbeat_at", "")).strip()
    due_heartbeat = True
    if last_heartbeat_raw:
        due_heartbeat = (
            datetime.now(timezone.utc) - datetime.fromisoformat(last_heartbeat_raw)
        ).total_seconds() >= HEARTBEAT_INTERVAL_SEC
    if due_heartbeat:
        actions["heartbeat"] = heartbeat_once()
    actions["stock_fill"] = stock_fill_once()
    actions["rollover"] = rollover_once(force=force_rollover)
    return actions


def run_supervisor(duration_days: float, poll_sec: int, force_rollover: bool) -> None:
    state = _runtime_state()
    if duration_days > 0:
        state["ends_at"] = (
            datetime.now(timezone.utc) + timedelta(days=duration_days)
        ).replace(microsecond=0).isoformat()
        _save_state(state)
    append_agent_log("started five-day Gemia supervisor loop")
    while True:
        end_at = datetime.fromisoformat(str(_runtime_state()["ends_at"]))
        if datetime.now(timezone.utc) >= end_at:
            append_agent_log("five-day Gemia supervisor loop finished by schedule")
            break
        tick_once(force_rollover=force_rollover)
        time.sleep(max(int(poll_sec), 30))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m gemia.automation.loop_controller")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("tick-once")
    p_heartbeat = sub.add_parser("heartbeat-once")
    p_heartbeat.add_argument("--force", action="store_true")
    p_rollover = sub.add_parser("rollover-once")
    p_rollover.add_argument("--force", action="store_true")
    p_stock = sub.add_parser("stock-fill-once")
    p_stock.add_argument("--image-limit", type=int, default=2)
    p_stock.add_argument("--video-limit", type=int, default=1)
    p_supervisor = sub.add_parser("run-supervisor")
    p_supervisor.add_argument("--duration-days", type=float, default=5.0)
    p_supervisor.add_argument("--poll-sec", type=int, default=300)
    p_supervisor.add_argument("--force-rollover", action="store_true")

    args = parser.parse_args()
    if args.command == "tick-once":
        print(tick_once())
    elif args.command == "heartbeat-once":
        print(heartbeat_once())
    elif args.command == "rollover-once":
        print(rollover_once(force=bool(args.force)))
    elif args.command == "stock-fill-once":
        print(stock_fill_once(image_limit=int(args.image_limit), video_limit=int(args.video_limit)))
    elif args.command == "run-supervisor":
        run_supervisor(
            duration_days=float(args.duration_days),
            poll_sec=int(args.poll_sec),
            force_rollover=bool(args.force_rollover),
        )


if __name__ == "__main__":
    main()
