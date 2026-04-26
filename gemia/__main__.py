from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .orchestrator import GemiaOrchestrator, get_assets, get_task, run_plan, run_skill
from .bridge import (
    BridgeDaemon,
    BridgePaths,
    BridgeTask,
    ClaudeCodeAdapter,
    ControllerAdapter,
    MasterBridgeController,
    QueueBridgeAdapter,
)
from .video.layers import render_layer_plan
from .video.preview import render_shadow_preview
from .video.review import review_real_media_artifact
from .video.intellisearch import index_real_media, search_media_index


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m gemia")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── New primary command: run ───────────────────────────────────────
    p_run = sub.add_parser("run", help="AI-driven execution via primitive functions")
    p_run.add_argument("--video", required=True, help="Input video path")
    p_run.add_argument("--prompt", required=True, help="Natural language instruction")
    p_run.add_argument("--output", default=None, help="Output path (auto-generated if omitted)")

    # ── Skill commands ────────────────────────────────────────────────
    p_save_skill = sub.add_parser("save-skill", help="Save last run as a reusable skill")
    p_save_skill.add_argument("--name", required=True, help="Skill name (e.g. '赛博朋克调色')")
    p_save_skill.add_argument("--from-last-run", action="store_true", default=True,
                              help="Use most recent succeeded task (default)")
    p_save_skill.add_argument("--from-task", default=None, help="Specific task ID to save from")
    p_save_skill.add_argument("--description", default=None, help="Optional description")

    p_list_skills = sub.add_parser("list-skills", help="List all saved v2 skills")

    p_run_skill_v2 = sub.add_parser("run-skill-v2", help="Run a saved v2 skill")
    p_run_skill_v2.add_argument("skill_name", help="Skill name")
    p_run_skill_v2.add_argument("--video", required=True, help="Input video path")
    p_run_skill_v2.add_argument("--output", default=None, help="Output path")

    # ── Legacy commands ────────────────────────────────────────────────
    p_run_plan = sub.add_parser("run-plan")
    p_run_plan.add_argument("plan_path")

    p_run_skill = sub.add_parser("run-skill", help="Run a saved skill (v2 or legacy)")
    p_run_skill.add_argument("skill_id")
    p_run_skill.add_argument("--video", required=True)
    p_run_skill.add_argument("--style", default=None, help="Style (legacy only)")
    p_run_skill.add_argument("--output", default=None, help="Output path (v2 only)")

    p_get_task = sub.add_parser("get-task")
    p_get_task.add_argument("task_id")

    p_get_assets = sub.add_parser("get-assets")
    p_get_assets.add_argument("task_id")

    p_render_layer_plan = sub.add_parser("render-layer-plan", help="Render a layer-plan JSON into a preview video")
    p_render_layer_plan.add_argument("plan_path", help="Path to a layer-plan JSON file")
    p_render_layer_plan.add_argument("--output", required=True, help="Output video path")
    p_render_layer_plan.add_argument("--step", type=int, default=1, help="Render every Nth frame for low-fi previews")

    p_shadow_preview = sub.add_parser("render-shadow-preview", help="Render a low-fi shadow preview from a layer-plan JSON")
    p_shadow_preview.add_argument("plan_path", help="Path to a layer-plan JSON file")
    p_shadow_preview.add_argument("--output", required=True, help="Output preview video path")
    p_shadow_preview.add_argument("--step", type=int, default=2, help="Render every Nth frame")
    p_shadow_preview.add_argument("--max-long-edge", type=int, default=540, help="Scale preview canvas so its longest edge is this size")
    p_shadow_preview.add_argument("--proxy-resolution", type=int, default=540, help="Resolution used for generated video proxies")
    p_shadow_preview.add_argument("--proxy-root", default=None, help="Directory for generated proxy assets")
    p_shadow_preview.add_argument("--backend", default=None, help="Render backend target: auto or software")

    p_real_media_review = sub.add_parser("review-real-media", help="Write a real-media review artifact for an output")
    p_real_media_review.add_argument("--source", required=True, help="Real source video path")
    p_real_media_review.add_argument("--output", required=True, help="Rendered output video path")
    p_real_media_review.add_argument("--report", default=None, help="Review report JSON path")
    p_real_media_review.add_argument("--preview-manifest", default=None, help="Preview manifest JSON path")
    p_real_media_review.add_argument("--layer-flow-manifest", default=None, help="Layer-flow manifest JSON path")
    p_real_media_review.add_argument("--stock-catalog", default=None, help="Stock catalog JSON for real-footage evidence")
    p_real_media_review.add_argument("--min-output-frames", type=int, default=1, help="Minimum accepted output frame count")
    p_real_media_review.add_argument("--allow-unconfirmed-source", action="store_true", help="Do not require stock catalog real-footage evidence")

    p_intellisearch_index = sub.add_parser("intellisearch-index", help="Build a searchable real-media semantic index")
    p_intellisearch_index.add_argument("--media", action="append", required=True, help="Real video path to index")
    p_intellisearch_index.add_argument("--review", action="append", default=[], help="Real-media review report JSON")
    p_intellisearch_index.add_argument("--stock-catalog", default=None, help="Stock catalog JSON for semantic labels")
    p_intellisearch_index.add_argument("--label", action="append", default=[], help="Extra label to attach to every indexed clip")
    p_intellisearch_index.add_argument("--output", required=True, help="Output index JSON path")
    p_intellisearch_index.add_argument("--max-samples", type=int, default=6, help="Maximum video sample frames per clip")

    p_intellisearch_search = sub.add_parser("intellisearch-search", help="Search a real-media semantic index")
    p_intellisearch_search.add_argument("--index", required=True, help="IntelliSearch index JSON path")
    p_intellisearch_search.add_argument("--query", required=True, help="Search query")
    p_intellisearch_search.add_argument("--output", default=None, help="Optional output result JSON path")
    p_intellisearch_search.add_argument("--limit", type=int, default=5, help="Maximum matches")

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("request")
    p_plan.add_argument("--video", required=True)

    p_revise = sub.add_parser("revise-task", help="Apply feedback revision to a completed task")
    p_revise.add_argument("task_id", help="Original task ID")
    p_revise.add_argument("--feedback", required=True, help="Revision instruction / style feedback")

    p_server = sub.add_parser("server", help="Start the web server")
    p_server.add_argument("--host", default="127.0.0.1")
    p_server.add_argument("--port", type=int, default=7788)

    p_bridge_init = sub.add_parser("bridge-init", help="Create bridge inbox/outbox directories")
    p_bridge_init.add_argument("--root", default="~/.gemia/bridge")

    p_bridge_submit = sub.add_parser("bridge-submit", help="Submit a file-based bridge task")
    p_bridge_submit.add_argument("--root", default="~/.gemia/bridge")
    p_bridge_submit.add_argument("--source", default="antigravity")
    p_bridge_submit.add_argument("--intent", required=True)
    p_bridge_submit.add_argument("--prompt", required=True)
    p_bridge_submit.add_argument("--asset", action="append", default=[])
    p_bridge_submit.add_argument(
        "--task-class",
        default=None,
        help="Routing class: architecture | review | frontend",
    )
    p_bridge_submit.add_argument("--agent", default=None, help="Preferred sub-agent (e.g. claude_code, antigravity)")
    p_bridge_submit.add_argument("--allow-agent", action="append", default=[], help="Restrict routing to these agents")
    p_bridge_submit.add_argument("--cwd", default=None)
    p_bridge_submit.add_argument("--context-json", default=None)
    p_bridge_submit.add_argument("--permissions-json", default=None)

    p_bridge_once = sub.add_parser("bridge-run-once", help="Process all pending bridge tasks once")
    p_bridge_once.add_argument("--root", default="~/.gemia/bridge")
    p_bridge_once.add_argument("--claude-bin", default="claude")
    p_bridge_once.add_argument("--antigravity-root", default=None, help="Queue root for delegated Antigravity tasks")
    p_bridge_once.add_argument("--timeout-sec", type=int, default=600)
    p_bridge_once.add_argument("--cwd", default=None)

    p_bridge_daemon = sub.add_parser("bridge-daemon", help="Run the file-based bridge daemon")
    p_bridge_daemon.add_argument("--root", default="~/.gemia/bridge")
    p_bridge_daemon.add_argument("--claude-bin", default="claude")
    p_bridge_daemon.add_argument("--antigravity-root", default=None, help="Queue root for delegated Antigravity tasks")
    p_bridge_daemon.add_argument("--timeout-sec", type=int, default=600)
    p_bridge_daemon.add_argument("--poll-interval", type=float, default=1.0)
    p_bridge_daemon.add_argument("--heartbeat-interval-sec", type=int, default=7200)
    p_bridge_daemon.add_argument("--heartbeat-source", default="codex")
    p_bridge_daemon.add_argument("--heartbeat-instructions", default=None, help="Path to HEARTBEAT.md-style instructions file")
    p_bridge_daemon.add_argument("--cwd", default=None)

    p_bridge_heartbeat = sub.add_parser("bridge-heartbeat-once", help="Submit and process one local heartbeat poll")
    p_bridge_heartbeat.add_argument("--root", default="~/.gemia/bridge")
    p_bridge_heartbeat.add_argument("--source", default="codex")
    p_bridge_heartbeat.add_argument("--instructions", default=None, help="Path to HEARTBEAT.md-style instructions file")
    p_bridge_heartbeat.add_argument("--min-interval-sec", type=int, default=0)
    p_bridge_heartbeat.add_argument("--claude-bin", default="claude")
    p_bridge_heartbeat.add_argument("--antigravity-root", default=None, help="Queue root for delegated Antigravity tasks")
    p_bridge_heartbeat.add_argument("--timeout-sec", type=int, default=600)
    p_bridge_heartbeat.add_argument("--cwd", default=None)

    args = parser.parse_args()

    if args.command == "run":
        _cmd_run(args)
    elif args.command == "save-skill":
        _cmd_save_skill(args)
    elif args.command == "list-skills":
        _cmd_list_skills()
    elif args.command == "run-skill-v2":
        _cmd_run_skill_v2(args)
    elif args.command == "run-plan":
        task_id = run_plan(args.plan_path)
        print(task_id)
    elif args.command == "run-skill":
        _cmd_run_skill(args)
    elif args.command == "get-task":
        print(json.dumps(get_task(args.task_id), ensure_ascii=False, indent=2))
    elif args.command == "get-assets":
        print(json.dumps(get_assets(args.task_id), ensure_ascii=False, indent=2))
    elif args.command == "render-layer-plan":
        _cmd_render_layer_plan(args)
    elif args.command == "render-shadow-preview":
        _cmd_render_shadow_preview(args)
    elif args.command == "review-real-media":
        _cmd_review_real_media(args)
    elif args.command == "intellisearch-index":
        _cmd_intellisearch_index(args)
    elif args.command == "intellisearch-search":
        _cmd_intellisearch_search(args)
    elif args.command == "revise-task":
        _cmd_revise_task(args)
    elif args.command == "plan":
        print(json.dumps(GemiaOrchestrator().plan_from_prompt(args.request, input_path=args.video), ensure_ascii=False, indent=2))
    elif args.command == "server":
        import importlib.util, pathlib
        _spec = importlib.util.spec_from_file_location(
            "gemia_server",
            pathlib.Path(__file__).parent.parent / "server.py",
        )
        _srv = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_srv)
        _srv.main(host=args.host, port=args.port)
    elif args.command == "bridge-init":
        _cmd_bridge_init(args)
    elif args.command == "bridge-submit":
        _cmd_bridge_submit(args)
    elif args.command == "bridge-run-once":
        _cmd_bridge_run_once(args)
    elif args.command == "bridge-daemon":
        _cmd_bridge_daemon(args)
    elif args.command == "bridge-heartbeat-once":
        _cmd_bridge_heartbeat_once(args)


def _cmd_run(args: argparse.Namespace) -> None:
    """AI-driven execution: prompt → plan v2 → engine → output."""
    from pathlib import Path

    from .ai.ai_client import AIClient
    from .engine import PlanEngine

    engine = PlanEngine()
    output_path = args.output or str(
        (engine.outputs_dir / f"gemia_out_{__import__('uuid').uuid4().hex[:8]}.mp4").resolve()
    )

    print(f"Input:  {args.video}")
    print(f"Prompt: {args.prompt}")
    print(f"Output: {output_path}")
    print()

    # Step 1: Ask AI for a plan
    print("Asking AI for a plan...")
    client = AIClient()
    plan = asyncio.run(client.plan_from_primitives(
        args.prompt,
        input_path=str(Path(args.video).resolve()),
        output_path=output_path,
    ))

    while plan.get("ask"):
        print("\nAI needs more info:")
        answers: dict[str, str] = {}
        for q in plan.get("questions", []):
            print(f"  {q}")
            try:
                ans = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return
            answers[str(q.get("id", len(answers)))] = ans
        print()
        print("Re-planning with your answers...")
        plan = asyncio.run(client.plan_from_primitives(
            args.prompt,
            input_path=str(Path(args.video).resolve()),
            output_path=output_path,
            answers=answers,
        ))

    # Step 2: Show the plan
    steps = plan.get("steps", [])
    print(f"Plan: {plan.get('goal', '?')} ({len(steps)} step{'s' if len(steps) != 1 else ''})")
    for s in steps:
        print(f"  {s['id']}: {s['function']}({s.get('args', {})})")
    print()

    # Step 3: Execute
    print("Executing...")
    task_id = engine.run_with_task(plan, str(Path(args.video).resolve()), output_path)
    print(f"\nDone! task_id={task_id}")
    print(f"Output: {output_path}")


def _cmd_render_layer_plan(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan_path).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    output = render_layer_plan(plan, args.output, step=max(int(args.step), 1))
    print(output)


def _cmd_render_shadow_preview(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan_path).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result = render_shadow_preview(
        plan,
        args.output,
        frame_step=max(int(args.step), 1),
        max_long_edge=int(args.max_long_edge),
        proxy_resolution=int(args.proxy_resolution),
        proxy_root=args.proxy_root,
        backend=args.backend,
    )
    print(json.dumps(
        {
            "output_path": result.output_path,
            "manifest_path": result.manifest_path,
            "proxy_map": result.proxy_map,
            "render_backend": result.render_backend,
        },
        ensure_ascii=False,
        indent=2,
    ))


def _cmd_review_real_media(args: argparse.Namespace) -> None:
    result = review_real_media_artifact(
        args.source,
        args.output,
        report_path=args.report,
        preview_manifest_path=args.preview_manifest,
        layer_flow_manifest_path=args.layer_flow_manifest,
        stock_catalog_path=args.stock_catalog,
        min_output_frames=max(int(args.min_output_frames), 1),
        require_real_source=not bool(args.allow_unconfirmed_source),
    )
    print(json.dumps(
        {
            "report_path": result.report_path,
            "status": result.status,
            "artifact_paths": result.artifact_paths,
            "findings": result.findings,
        },
        ensure_ascii=False,
        indent=2,
    ))


def _cmd_intellisearch_index(args: argparse.Namespace) -> None:
    result = index_real_media(
        list(args.media or []),
        args.output,
        review_report_paths=list(args.review or []),
        stock_catalog_path=args.stock_catalog,
        extra_labels=list(args.label or []),
        max_samples=max(int(args.max_samples), 1),
    )
    print(json.dumps(
        {
            "index_path": result.index_path,
            "clip_count": result.clip_count,
            "label_count": result.label_count,
        },
        ensure_ascii=False,
        indent=2,
    ))


def _cmd_intellisearch_search(args: argparse.Namespace) -> None:
    result = search_media_index(
        args.index,
        args.query,
        output_path=args.output,
        limit=max(int(args.limit), 1),
    )
    print(json.dumps(
        {
            "query": result.query,
            "index_path": result.index_path,
            "output_path": result.output_path,
            "match_count": result.match_count,
            "matches": result.matches,
        },
        ensure_ascii=False,
        indent=2,
    ))


def _cmd_run_skill(args: argparse.Namespace) -> None:
    """Run a skill — tries v2 first, falls back to legacy."""
    from .skill_store import SkillStore

    store = SkillStore()
    try:
        skill = store.load(args.skill_id)
        # Found a v2 skill — delegate to v2 runner
        args.skill_name = args.skill_id
        _cmd_run_skill_v2(args)
        return
    except FileNotFoundError:
        pass

    # Legacy fallback
    if not args.style:
        raise SystemExit(f"Skill '{args.skill_id}' not found in v2 store, and --style is required for legacy skills.")
    task_id = run_skill(args.skill_id, {"video": args.video, "style": args.style})
    print(task_id)


def _cmd_save_skill(args: argparse.Namespace) -> None:
    """Save a completed task as a reusable v2 skill."""
    from .skill_store import SkillStore

    store = SkillStore()
    task_id = args.from_task  # None means "last succeeded"
    path = store.save_from_task(name=args.name, task_id=task_id, description=args.description)
    skill = json.loads(path.read_text())
    steps = skill.get("plan", {}).get("steps", [])
    print(f"Skill saved: {args.name}")
    print(f"  File: {path}")
    print(f"  Steps: {len(steps)}")
    print(f"  Origin task: {skill.get('origin_task_id')}")


def _cmd_list_skills() -> None:
    """List all saved v2 skills."""
    from .skill_store import SkillStore

    store = SkillStore()
    skills = store.list_skills()
    if not skills:
        print("No v2 skills saved yet.")
        return
    for s in skills:
        print(f"  {s['name']}  ({s['step_count']} steps)  — {s['description'][:60]}")


def _cmd_run_skill_v2(args: argparse.Namespace) -> None:
    """Run a saved v2 skill on a video."""
    from pathlib import Path

    from .engine import PlanEngine
    from .skill_store import SkillStore

    store = SkillStore()
    engine = PlanEngine()

    skill = store.load(args.skill_name)
    plan = skill["plan"]

    output_path = args.output or str(
        (engine.outputs_dir / f"gemia_out_{__import__('uuid').uuid4().hex[:8]}.mp4").resolve()
    )

    print(f"Skill:  {skill['name']}")
    print(f"Input:  {args.video}")
    print(f"Output: {output_path}")
    steps = plan.get("steps", [])
    print(f"Plan: {len(steps)} step{'s' if len(steps) != 1 else ''}")
    for s in steps:
        print(f"  {s['id']}: {s['function']}({s.get('args', {})})")
    print()

    print("Executing...")
    task_id = engine.run_with_task(plan, str(Path(args.video).resolve()), output_path)
    print(f"\nDone! task_id={task_id}")
    print(f"Output: {output_path}")


def _cmd_revise_task(args: argparse.Namespace) -> None:
    """Apply a feedback revision to a completed task (mirrors /revise-task/<id>)."""
    from pathlib import Path

    from .orchestrator import run_skill, get_task

    plans_dir = Path(__file__).resolve().parent.parent / "plans"
    plan_file = plans_dir / f"{args.task_id}_plan.json"
    if not plan_file.exists():
        raise SystemExit(f"Plan not found for task: {args.task_id}")

    plan = json.loads(plan_file.read_text())
    skill_id = plan.get("skill_id")
    input_path = plan.get("input_path") or (plan.get("inputs") or {}).get("video")
    if not skill_id or not input_path:
        raise SystemExit("Original plan is missing skill_id or input_path — cannot revise.")

    print(f"Revising task {args.task_id} with: {args.feedback}")
    revision_task_id = run_skill(skill_id, {"video": input_path, "style": args.feedback})
    task = get_task(revision_task_id)
    print(f"Done! revision_task_id={revision_task_id}")
    outputs = task.get("outputs", [])
    if outputs:
        print(f"Output: {outputs[0]}")


def _bridge_paths(root: str) -> BridgePaths:
    return BridgePaths.from_root(root)


def _make_bridge_daemon(args: argparse.Namespace) -> BridgeDaemon:
    paths = _bridge_paths(args.root)
    claude_adapter = ClaudeCodeAdapter(
        claude_bin=args.claude_bin,
        timeout_sec=args.timeout_sec,
        default_cwd=args.cwd,
    )
    antigravity_root = args.antigravity_root or (paths.root / "agents" / "antigravity")
    antigravity_adapter = QueueBridgeAdapter("antigravity", antigravity_root)
    controller = MasterBridgeController(
        {
            "claude_code": claude_adapter,
            "antigravity": antigravity_adapter,
        },
        default_agent="claude_code",
    )
    adapter = ControllerAdapter(controller)
    heartbeat_interval_sec = int(getattr(args, "heartbeat_interval_sec", 7200))
    heartbeat_source = str(getattr(args, "heartbeat_source", "codex"))
    heartbeat_instructions = getattr(args, "heartbeat_instructions", None)
    if heartbeat_instructions:
        heartbeat_instructions = str(Path(heartbeat_instructions).expanduser().resolve())
    return BridgeDaemon(
        paths,
        adapter,
        auto_heartbeat_interval_sec=heartbeat_interval_sec,
        auto_heartbeat_source=heartbeat_source,
        auto_heartbeat_instructions_path=heartbeat_instructions,
    )


def _cmd_bridge_init(args: argparse.Namespace) -> None:
    paths = _bridge_paths(args.root)
    paths.ensure()
    print(json.dumps({
        "root": str(paths.root),
        "inbox": str(paths.inbox),
        "processing": str(paths.processing),
        "outbox": str(paths.outbox),
        "failed": str(paths.failed),
        "logs": str(paths.logs),
        "leases": str(paths.leases),
        "heartbeat_state": str(paths.heartbeat_state),
    }, ensure_ascii=False, indent=2))


def _read_json_arg(raw: str | None) -> dict:
    if not raw:
        return {}
    path = Path(raw).expanduser()
    if path.exists():
        return json.loads(path.read_text())
    return json.loads(raw)


def _cmd_bridge_submit(args: argparse.Namespace) -> None:
    daemon = BridgeDaemon(_bridge_paths(args.root), adapter=ClaudeCodeAdapter())
    metadata = {"submitted_via": "gemia_cli"}
    if args.task_class:
        metadata["task_class"] = args.task_class
    task = BridgeTask.new(
        source=args.source,
        intent=args.intent,
        prompt=args.prompt,
        assets=list(args.asset or []),
        context=_read_json_arg(args.context_json),
        permissions=_read_json_arg(args.permissions_json),
        metadata=metadata,
        cwd=args.cwd,
    )
    task.preferred_agent = args.agent
    task.allowed_agents = list(args.allow_agent or [])
    payload_path = daemon.submit_task(task)
    print(json.dumps({
        "task_id": task.task_id,
        "payload": str(payload_path),
    }, ensure_ascii=False, indent=2))


def _cmd_bridge_run_once(args: argparse.Namespace) -> None:
    daemon = _make_bridge_daemon(args)
    processed = daemon.process_once()
    print(json.dumps({
        "processed": processed,
        "root": str(daemon.paths.root),
    }, ensure_ascii=False, indent=2))


def _cmd_bridge_daemon(args: argparse.Namespace) -> None:
    daemon = _make_bridge_daemon(args)
    print(f"Bridge daemon watching {daemon.paths.inbox}")
    daemon.serve_forever(poll_interval=args.poll_interval)


def _cmd_bridge_heartbeat_once(args: argparse.Namespace) -> None:
    daemon = _make_bridge_daemon(args)
    metadata = {
        "task_class": "heartbeat",
        "heartbeat": True,
    }
    if args.instructions:
        metadata["instructions_path"] = str(Path(args.instructions).expanduser().resolve())
    task = BridgeTask.new(
        source=args.source,
        intent="heartbeat",
        prompt="Read HEARTBEAT.md if it exists. Follow it strictly. Reply HEARTBEAT_OK when nothing needs attention.",
        metadata=metadata,
        context={"heartbeat_action": "poll", "min_interval_sec": args.min_interval_sec},
        cwd=args.cwd,
    )
    daemon.submit_task(task)
    daemon.process_once()
    result_path = daemon.paths.outbox / f"{task.task_id}.json"
    if not result_path.exists():
        raise SystemExit(f"Heartbeat result missing: {result_path}")
    print(result_path.read_text().strip())


if __name__ == "__main__":
    main()
