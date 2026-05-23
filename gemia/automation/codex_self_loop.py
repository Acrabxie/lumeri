from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_OUTPUT_ROOT = Path("workspaces/lumeri-codex-self-loop")
SHARED_ROOT = Path.home() / ".agents" / "shared-agent-loop"
SECRET_PATTERN = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|bearer\s+[a-z0-9._-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IterationPaths:
    run_dir: Path
    iteration_dir: Path
    prompt: Path
    context: Path
    manifest: Path
    result_report: Path
    workspace_diagnostics: Path


def safe_slug(value: str, *, max_len: int = 64) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return (slug or "lumeri-coding-environment")[:max_len].strip("-")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_text_if_exists(path: Path, *, limit: int = 8000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


def redact(value: str) -> str:
    lines: list[str] = []
    for line in value.splitlines():
        if SECRET_PATTERN.search(line):
            lines.append("[redacted secret-like line]")
        else:
            lines.append(line)
    return "\n".join(lines)


def run_command(args: list[str], cwd: Path, *, timeout: int = 30) -> dict[str, object]:
    try:
        proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    except Exception as exc:  # pragma: no cover - defensive diagnostics
        return {"args": args, "ok": False, "error": str(exc)}
    return {
        "args": args,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": redact(proc.stdout)[-4000:],
        "stderr": redact(proc.stderr)[-4000:],
    }


def git_status(cwd: Path) -> dict[str, object]:
    result = run_command(["git", "status", "--short"], cwd, timeout=10)
    stdout = str(result.get("stdout") or "")
    result["changed_paths"] = [line[3:] if len(line) > 3 else line for line in stdout.splitlines()]
    return result


def collect_project_snapshot(cwd: Path) -> dict[str, object]:
    key_paths = list(expected_workspace_files())
    files = {path: (cwd / path).exists() for path in key_paths}
    static_next = cwd / "static" / "next.html"
    runtime_vnext = cwd / "gemia" / "runtime_vnext.py"
    return {
        "cwd": str(cwd),
        "files": files,
        "has_vnext_ui": static_next.exists(),
        "has_runtime_vnext_source": runtime_vnext.exists(),
        "git_status": git_status(cwd),
    }


def expected_workspace_files() -> dict[str, dict[str, str]]:
    return {
        "server.py": {
            "capability": "HTTP runtime/API entrypoint",
            "restore_hint": "Keep server routes available before adding vNext integration tests.",
        },
        "static/next.html": {
            "capability": "vNext Codex-style browser workspace",
            "restore_hint": "Restore or rebuild /next before live browser smoke tests.",
        },
        "gemia/runtime_vnext.py": {
            "capability": "Runtime Kernel natural-language execution path",
            "restore_hint": "Restore Runtime Kernel before task hydration or async result work.",
        },
        "gemia/creative_sandbox.py": {
            "capability": "Creative Dev Sandbox script execution",
            "restore_hint": "Restore sandbox APIs before claiming script execution support.",
        },
        "gemia/automation/codex_self_loop.py": {
            "capability": "Codex self-improvement loop",
            "restore_hint": "Keep this runner compiling so automation can continue.",
        },
        "scripts/codex_lumeri_self_loop.py": {
            "capability": "Automation CLI entrypoint",
            "restore_hint": "Keep this command path stable for recurring automation.",
        },
        "tests/test_codex_self_loop.py": {
            "capability": "Self-loop artifact tests",
            "restore_hint": "Use focused tests to prevent report/manifest regressions.",
        },
        "tests/test_server_static_web.py": {
            "capability": "Server and /next static contract tests",
            "restore_hint": "Restore when /next returns so browser-workspace contracts are testable.",
        },
        "tests/test_lumerai_runtime_kernel.py": {
            "capability": "Runtime Kernel behavior tests",
            "restore_hint": "Restore with Runtime Kernel source before modifying execution behavior.",
        },
    }


def build_workspace_diagnostics(snapshot: dict[str, object]) -> dict[str, object]:
    files = snapshot.get("files", {})
    if not isinstance(files, dict):
        files = {}
    expected = expected_workspace_files()
    gaps: list[dict[str, str]] = []
    for path, meta in expected.items():
        if files.get(path) is True:
            continue
        gaps.append(
            {
                "path": path,
                "capability": meta["capability"],
                "restore_hint": meta["restore_hint"],
            }
        )
    next_focus = "All tracked coding-environment capability files are present."
    if gaps:
        first = gaps[0]
        next_focus = f"Restore `{first['path']}` to recover {first['capability']}."
    return {
        "status": "ready" if not gaps else "needs_recovery",
        "gap_count": len(gaps),
        "gaps": gaps,
        "next_focus": next_focus,
    }


def collect_shared_context(today: datetime | None = None) -> dict[str, str]:
    today = today or datetime.now()
    daily_path = SHARED_ROOT / "daily" / f"{today.date().isoformat()}.md"
    return {
        "roles": redact(read_text_if_exists(SHARED_ROOT / "ROLES.md", limit=5000)),
        "queue": redact(read_text_if_exists(SHARED_ROOT / "QUEUE.md", limit=9000)),
        "memory": redact(read_text_if_exists(SHARED_ROOT / "MEMORY.md", limit=9000)),
        "daily": redact(read_text_if_exists(daily_path, limit=9000)),
    }


def build_prompt(goal: str, snapshot: dict[str, object]) -> str:
    files = snapshot.get("files", {})
    visible_files = "\n".join(f"- {path}: {'present' if present else 'missing'}" for path, present in files.items())
    return (
        "# Lumeri Codex Self-Loop Iteration\n\n"
        f"Goal: {goal}\n\n"
        "Treat this as one small coding-environment improvement for video creation. "
        "Prefer vNext, Runtime Kernel, Creative Dev Sandbox, TimelinePatch, preview rendering, "
        "artifact/report quality, testability, observability, and developer ergonomics. "
        "Do not use OpenClaw and do not expand the frozen old 7788 default UI.\n\n"
        "## Current Workspace Signals\n"
        f"{visible_files}\n\n"
        "## Required Report\n"
        "Write a concise `codex-result.md` with what changed, verification, blockers, and next follow-up.\n"
    )


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_paths(output_root: Path, goal: str, index: int) -> IterationPaths:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_root / f"{stamp}-{safe_slug(goal)}"
    iteration_dir = run_dir / f"iteration-{index:02d}"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    return IterationPaths(
        run_dir=run_dir,
        iteration_dir=iteration_dir,
        prompt=iteration_dir / "codex-prompt.md",
        context=iteration_dir / "context.json",
        manifest=iteration_dir / "manifest.json",
        result_report=iteration_dir / "codex-result.md",
        workspace_diagnostics=iteration_dir / "workspace-diagnostics.json",
    )


def write_result_report(
    paths: IterationPaths,
    goal: str,
    snapshot: dict[str, object],
    diagnostics: dict[str, object],
) -> None:
    gaps = diagnostics.get("gaps", [])
    missing = [str(gap.get("path")) for gap in gaps if isinstance(gap, dict)]
    status = "成功" if not missing else "成功，带诊断"
    body = [
        "# Lumeri 自循环结果",
        "",
        f"- 状态: {status}",
        f"- 时间: {utc_now()}",
        f"- 目标: {goal}",
        "- 本轮最小能力: 生成可追踪的 coding-environment 迭代目录，包含 prompt、context、manifest 和结果报告。",
        "- 对视频工作区的价值: 后续代理可以像读取代码项目一样读取当前能力、缺口、执行结果和下一步，而不是只看到一次聊天回复。",
        "",
        "## 诊断",
        "",
    ]
    if missing:
        body.append("当前 checkout 缺少这些 vNext/self-loop 预期文件，需要后续迭代补齐或恢复：")
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            body.append(
                f"- `{gap.get('path')}`: {gap.get('capability')}；建议: {gap.get('restore_hint')}"
            )
    else:
        body.append("关键 vNext/self-loop 文件均存在。")
    body.extend(
        [
            "",
            "## 验证",
            "",
            "- 结果报告已写入本 iteration 目录。",
            "- `manifest.json` 记录 report metadata 和 workspace snapshot。",
            "- `workspace-diagnostics.json` 记录可机器读取的 capability gap 和下一步恢复焦点。",
            "",
            "## 下一步",
            "",
            str(diagnostics.get("next_focus") or "恢复缺失的 vNext Runtime Kernel 源文件后再做 live smoke。"),
            "",
        ]
    )
    paths.result_report.write_text("\n".join(body), encoding="utf-8")


def run_iteration(cwd: Path, output_root: Path, goal: str, index: int) -> dict[str, object]:
    paths = make_paths(output_root, goal, index)
    snapshot = collect_project_snapshot(cwd)
    context = {
        "created_at": utc_now(),
        "goal": goal,
        "project": snapshot,
        "shared": collect_shared_context(),
    }
    paths.prompt.write_text(build_prompt(goal, snapshot), encoding="utf-8")
    write_json(paths.context, context)
    diagnostics = build_workspace_diagnostics(snapshot)
    write_json(paths.workspace_diagnostics, diagnostics)
    write_result_report(paths, goal, snapshot, diagnostics)

    result_report_meta = {
        "path": str(paths.result_report),
        "bytes": paths.result_report.stat().st_size,
        "missing": not paths.result_report.exists(),
    }
    manifest = {
        "status": "succeeded" if paths.result_report.exists() else "failed",
        "created_at": utc_now(),
        "goal": goal,
        "iteration": index,
        "run_dir": str(paths.run_dir),
        "iteration_dir": str(paths.iteration_dir),
        "prompt": str(paths.prompt),
        "context": str(paths.context),
        "workspace_diagnostics": {
            "path": str(paths.workspace_diagnostics),
            "status": diagnostics["status"],
            "gap_count": diagnostics["gap_count"],
            "next_focus": diagnostics["next_focus"],
        },
        "result_report": result_report_meta,
        "project_snapshot": snapshot,
    }
    if not paths.result_report.exists():
        manifest["failure_reason"] = "missing_codex_result"
    write_json(paths.manifest, manifest)
    return manifest


def run_self_loop(cwd: Path, output_root: Path, goal: str, iterations: int) -> list[dict[str, object]]:
    output_root.mkdir(parents=True, exist_ok=True)
    return [run_iteration(cwd, output_root, goal, index) for index in range(1, iterations + 1)]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one or more small Lumeri Codex self-loop iterations.")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.iterations < 1:
        raise SystemExit("--iterations must be >= 1")
    cwd = args.cwd.resolve()
    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = cwd / output_root
    manifests = run_self_loop(cwd, output_root, args.goal, args.iterations)
    print(json.dumps({"status": "succeeded", "iterations": manifests}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
