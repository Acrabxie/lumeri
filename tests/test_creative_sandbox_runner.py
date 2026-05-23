from __future__ import annotations

import shutil
import sys
from pathlib import Path

from gemia.creative_sandbox_runner import CreativeSandboxRunner, run_creative_command


def test_creative_runner_allows_python_command_and_captures_metadata(tmp_path: Path) -> None:
    runner = CreativeSandboxRunner(tmp_path, session_id="sess_ok")
    result = runner.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path\nPath('hello.txt').write_text('hi', encoding='utf-8')\nprint('done')\n",
        ],
        timeout_sec=5,
    )

    payload = result.to_dict()
    assert payload["status"] == "succeeded"
    assert payload["command_id"].startswith("cmd_")
    assert payload["exit_code"] == 0
    assert payload["stdout_tail"].strip() == "done"
    assert "Traceback" not in payload["stderr_tail"]
    assert payload["safe_args"][0] == sys.executable
    assert payload["artifacts"][0]["rel_path"] == "hello.txt"
    assert (tmp_path / "workspaces" / "sess_ok" / "hello.txt").read_text(encoding="utf-8") == "hi"


def test_creative_runner_blocks_dangerous_network_command(tmp_path: Path) -> None:
    result = run_creative_command(
        ["curl", "https://example.com"],
        root_dir=tmp_path,
        session_id="sess_block",
    )

    assert result["status"] == "blocked"
    assert result["error"]["code"] == "blocked_command"
    assert result["exit_code"] is None
    assert "Traceback" not in result["stderr_tail"]


def test_creative_runner_timeout_returns_structured_result(tmp_path: Path) -> None:
    runner = CreativeSandboxRunner(tmp_path, session_id="sess_timeout")
    result = runner.run(
        [sys.executable, "-c", "import time\ntime.sleep(2)\n"],
        timeout_sec=0.2,
    ).to_dict()

    assert result["status"] == "timeout"
    assert result["timed_out"] is True
    assert result["exit_code"] == 124
    assert result["error"]["code"] == "timeout"
    assert "Traceback" not in result["stderr_tail"]


def test_creative_runner_strips_raw_traceback_from_stderr_tail(tmp_path: Path) -> None:
    runner = CreativeSandboxRunner(tmp_path, session_id="sess_traceback")
    result = runner.run(
        [sys.executable, "-c", "1 / 0\n"],
    ).to_dict()

    assert result["status"] == "failed"
    assert result["exit_code"] != 0
    assert "ZeroDivisionError" in result["stderr_tail"]
    assert "Traceback" not in result["stderr_tail"]
    assert 'File "' not in result["stderr_tail"]


def test_creative_runner_rejects_cwd_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    runner = CreativeSandboxRunner(tmp_path, session_id="sess_cwd")
    result = runner.run(
        [sys.executable, "-c", "print('nope')"],
        cwd=outside,
    ).to_dict()

    assert result["status"] == "blocked"
    assert result["error"]["code"] == "cwd_outside_workspace"


def test_creative_runner_rejects_python_write_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    runner = CreativeSandboxRunner(tmp_path, session_id="sess_write")
    result = runner.run(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path\nPath({str(outside)!r}).write_text('nope', encoding='utf-8')\n",
        ],
    ).to_dict()

    assert result["status"] == "blocked"
    assert result["error"]["code"] == "python_write_outside_workspace"
    assert not outside.exists()


def test_creative_runner_discovers_workspace_and_declared_artifacts(tmp_path: Path) -> None:
    declared = tmp_path / "declared" / "copy.txt"
    runner = CreativeSandboxRunner(tmp_path, session_id="sess_artifacts")
    result = runner.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path\n"
                "Path('frames').mkdir()\n"
                "Path('frames/out.txt').write_text('frame', encoding='utf-8')\n"
                f"Path({str(declared)!r}).write_text('copy', encoding='utf-8')\n"
            ),
        ],
        declared_artifact_paths=[declared],
        timeout_sec=5,
    ).to_dict()

    assert result["status"] == "succeeded"
    artifacts = {item["rel_path"]: item for item in result["artifacts"]}
    assert "frames/out.txt" in artifacts
    declared_items = [item for item in result["artifacts"] if item["declared"]]
    assert len(declared_items) == 1
    assert declared_items[0]["path"] == str(declared.resolve())
    assert declared.read_text(encoding="utf-8") == "copy"


def test_creative_runner_lumerai_script_can_write_declared_preview(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        return

    runner = CreativeSandboxRunner(tmp_path, session_id="sess_lumerai")
    script_path = runner.workspace_dir / "scripts" / "runtime" / "script.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import lumerai as lm",
                "",
                "clip = lm.hyperframes_render(",
                "    '<section class=\"title\">LUMERI</section>',",
                "    css='.title{width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0b0f14;color:#f8fafc;font-size:32px;font-weight:800}',",
                "    duration=0.25,",
                "    width=160,",
                "    height=90,",
                "    fps=24,",
                "    name='sandbox-preview',",
                ")",
                "Path('previews').mkdir(exist_ok=True)",
                "Path('previews/runtime-preview.mp4').write_bytes(Path(clip['path']).read_bytes())",
                "print(clip['metadata']['generated_by'])",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.run(
        [sys.executable, str(script_path)],
        declared_artifact_paths=["previews/runtime-preview.mp4"],
        timeout_sec=30,
    ).to_dict()

    assert result["status"] == "succeeded", result["stderr_tail"]
    assert result["stdout_tail"].strip() in {"hyperframes", "hyperframes_fallback"}
    assert result["safe_args"][1] == str(script_path)
    assert result["artifacts"]
    declared = [item for item in result["artifacts"] if item["declared"]]
    assert len(declared) == 1
    assert declared[0]["rel_path"] == "previews/runtime-preview.mp4"
    assert Path(declared[0]["path"]).stat().st_size > 0
