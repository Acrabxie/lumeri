#!/usr/bin/env python3
"""Run the Lumeri Runtime Kernel golden command workflow.

This is a developer smoke test for the experimental script runtime. It does
not touch the default 7788 / Plan-v2 product path.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="Absolute or relative MP4 input path")
    parser.add_argument("--project-id", default="smoke_kernel")
    parser.add_argument("--workdir", default=None, help="Directory for smoke artifacts")
    parser.add_argument("--repo-root", default=None, help="Override repo root")
    parser.add_argument("--include-agent", action="store_true", help="Also run lumerai-agent with a canned script")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    video_path = Path(args.video).expanduser()
    if not video_path.is_absolute():
        video_path = (Path.cwd() / video_path).resolve()
    if not video_path.exists():
        return _print_failure("video_not_found", f"Input video does not exist: {video_path}")

    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir(repo_root)
    smoke_root = workdir / "repo"
    workdir.mkdir(parents=True, exist_ok=True)
    smoke_root.mkdir(parents=True, exist_ok=True)

    try:
        duration = _ffprobe_duration(video_path)
        seed_path = workdir / "seed.json"
        _write_seed(seed_path, video_path, duration)
        env = _runtime_env(repo_root)
        script_path = repo_root / "tests" / "scripts" / "lumerai_trim_grade_insert.py"

        first = _run_json(
            [
                sys.executable, "-m", "gemia", "lumerai-script",
                "--script", str(script_path),
                "--project-id", args.project_id,
                "--project-init-from", str(seed_path),
                "--session-id", "smoke_1",
                "--root", str(smoke_root),
            ],
            repo_root,
            env,
            "first_run",
        )
        inspect_1 = _run_json(
            [
                sys.executable, "-m", "gemia", "lumerai-inspect",
                "--project-id", args.project_id,
                "--history", "1",
                "--root", str(smoke_root),
            ],
            repo_root,
            _python_env(repo_root),
            "inspect_after_first_run",
        )
        render_1 = _run_json(
            [
                sys.executable, "-m", "gemia", "lumerai-render",
                "--project-id", args.project_id,
                "--label", "first",
                "--root", str(smoke_root),
            ],
            repo_root,
            env,
            "render_after_first_run",
        )
        undo = _run_json(
            [
                sys.executable, "-m", "gemia", "lumerai-undo",
                "--project-id", args.project_id,
                "--to-seq", "0",
                "--root", str(smoke_root),
            ],
            repo_root,
            env,
            "undo_to_zero",
        )
        rerun = _run_json(
            [
                sys.executable, "-m", "gemia", "lumerai-script",
                "--script", str(script_path),
                "--project-id", args.project_id,
                "--session-id", "smoke_2",
                "--root", str(smoke_root),
            ],
            repo_root,
            env,
            "rerun",
        )
        render_2 = _run_json(
            [
                sys.executable, "-m", "gemia", "lumerai-render",
                "--project-id", args.project_id,
                "--label", "rerun",
                "--root", str(smoke_root),
            ],
            repo_root,
            env,
            "render_after_rerun",
        )
        inspect_2_text = _run_text(
            [
                sys.executable, "-m", "gemia", "lumerai-inspect",
                "--project-id", args.project_id,
                "--format", "text",
                "--history", "1",
                "--root", str(smoke_root),
            ],
            repo_root,
            _python_env(repo_root),
            "final_inspect_text",
        )

        agent = None
        if args.include_agent:
            agent_script = workdir / "agent_script.py"
            agent_script.write_text(
                "import lumerai as lm\n"
                "clip = lm.clip_load('demo.mp4')\n"
                "trimmed = lm.clip_trim(clip, start=0.0, end=0.8)\n"
                "lm.timeline_insert(trimmed, at=2.2)\n"
                "# DONE\n",
                encoding="utf-8",
            )
            agent = _run_json(
                [
                    sys.executable, "-m", "gemia", "lumerai-agent",
                    "--project-id", args.project_id,
                    "--session-id", "smoke_agent",
                    "--goal", "insert a short trim",
                    "--max-turns", "3",
                    "--canned-script", str(agent_script),
                    "--root", str(smoke_root),
                ],
                repo_root,
                env,
                "canned_agent",
            )

        final_clip_count = _extract_clip_count(inspect_2_text["stdout"])
        payload: dict[str, Any] = {
            "status": "succeeded",
            "project_id": args.project_id,
            "workdir": str(workdir),
            "smoke_root": str(smoke_root),
            "seed_path": str(seed_path),
            "steps": {
                "first_run": first,
                "inspect_after_first_run": inspect_1,
                "render_after_first_run": render_1,
                "undo_to_zero": undo,
                "rerun": rerun,
                "render_after_rerun": render_2,
                "final_inspect_text": inspect_2_text,
            },
            "final": {
                "patch_seq": rerun.get("patch_seq_end"),
                "clip_count": final_clip_count,
                "preview_path": render_2.get("preview_path"),
                "render_manifest_path": render_2.get("manifest_path"),
            },
        }
        if agent is not None:
            payload["steps"]["canned_agent"] = agent
            payload["final"]["agent_status"] = agent.get("status")
            payload["final"]["agent_clip_count"] = agent.get("final_clip_count")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except SmokeError as exc:
        print(json.dumps(exc.payload, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:  # pragma: no cover - defensive command boundary
        return _print_failure("smoke_failed", f"{type(exc).__name__}: {exc}")


class SmokeError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(json.dumps(payload, ensure_ascii=False))
        self.payload = payload


def _default_workdir(repo_root: Path) -> Path:
    external = Path("/Volumes/Extreme SSD/GemiaTemp")
    base = external if external.exists() else repo_root / "temp"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return base / "lumeri-runtime-kernel-smoke" / stamp


def _ffprobe_duration(video_path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        raise SmokeError(_failure_payload("ffprobe_failed", proc.stderr.strip() or "ffprobe failed"))
    data = json.loads(proc.stdout or "{}")
    return max(float((data.get("format") or {}).get("duration") or 0.1), 0.1)


def _write_seed(seed_path: Path, video_path: Path, duration: float) -> None:
    from gemia.project_model import empty_project

    project = empty_project(title="Runtime Kernel Smoke")
    asset = {
        "id": "asset_demo",
        "asset_id": "asset_demo",
        "name": "demo.mp4",
        "media_kind": "video",
        "mime_type": "video/mp4",
        "source_path": str(video_path),
        "duration": duration,
        "metadata": {"duration": duration},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    clip = {
        "id": "clip_demo",
        "asset_id": "asset_demo",
        "track_id": "V1",
        "name": "demo.mp4",
        "media_kind": "video",
        "start": 0.0,
        "duration": duration,
        "source_in": 0.0,
        "source_out": duration,
        "enabled": True,
        "effects": {
            "rotation": 0,
            "mirrored": False,
            "muted": False,
            "audioDetached": False,
            "speed": 1,
        },
    }
    project["assets"] = [asset]
    project["timeline"]["clips"] = [clip]
    project["timeline"]["duration"] = duration
    seed_path.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _runtime_env(repo_root: Path) -> dict[str, str]:
    env = _python_env(repo_root)
    env["LUMERAI_SCRIPT_MODE"] = "1"
    return env


def _python_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in [str(repo_root), existing] if part)
    return env


def _run_json(cmd: list[str], cwd: Path, env: dict[str, str], step: str) -> dict[str, Any]:
    proc = _run(cmd, cwd, env)
    if proc.returncode != 0:
        raise SmokeError(_command_failure_payload(step, proc))
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError(_failure_payload("invalid_json", f"{step} did not return JSON: {exc}"))
    if isinstance(payload, dict) and payload.get("status") == "failed":
        raise SmokeError({"status": "failed", "step": step, "command": cmd, "result": payload})
    return payload


def _run_text(cmd: list[str], cwd: Path, env: dict[str, str], step: str) -> dict[str, Any]:
    proc = _run(cmd, cwd, env)
    if proc.returncode != 0:
        raise SmokeError(_command_failure_payload(step, proc))
    return {"stdout": proc.stdout, "stderr": proc.stderr}


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _extract_clip_count(text: str) -> int | None:
    marker = "clips="
    idx = text.find(marker)
    if idx < 0:
        return None
    rest = text[idx + len(marker):]
    digits = []
    for ch in rest:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    return int("".join(digits)) if digits else None


def _command_failure_payload(step: str, proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "status": "failed",
        "step": step,
        "error": {
            "code": "command_failed",
            "message": f"{step} failed with exit code {proc.returncode}",
        },
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def _failure_payload(code: str, message: str) -> dict[str, Any]:
    return {"status": "failed", "error": {"code": code, "message": message}}


def _print_failure(code: str, message: str) -> int:
    print(json.dumps(_failure_payload(code, message), ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
