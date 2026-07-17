import io
import json
import asyncio
from pathlib import Path

from gemia import v3_routes
from gemia.agent_loop_v3 import AgentLoopV3


ROOT = Path(__file__).resolve().parents[1]


class Handler:
    def __init__(self, payload: dict | None = None) -> None:
        raw = json.dumps(payload or {}).encode()
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, *_args) -> None:
        pass

    def end_headers(self) -> None:
        pass


class Runner:
    session_id = "v3-control"

    def __init__(self, *, active: bool = True) -> None:
        self.active = active
        self.guidance = []

    def steer_turn(self, message: str) -> bool:
        if not self.active:
            return False
        self.guidance.append(message)
        return True

    def stop_turn(self) -> bool:
        if not self.active:
            return False
        self.active = False
        return True


def test_steer_and_stop_routes_accept_only_active_turns() -> None:
    runner = Runner()
    steer = Handler({"message": "节奏再快一点"})
    assert v3_routes._steer_turn(steer, runner) is True
    assert steer.status == 202
    assert runner.guidance == ["节奏再快一点"]

    stop = Handler()
    assert v3_routes._stop_turn(stop, runner) is True
    assert stop.status == 202

    late = Handler({"message": "继续"})
    assert v3_routes._steer_turn(late, runner) is True
    assert late.status == 409


def test_web_composer_exposes_stop_and_midturn_guidance() -> None:
    # M3 restyle: the dedicated #session-stop-btn was removed. Mid-turn, the
    # voice-input button morphs into the stop control (pause icon, label
    # "停止当前执行" -> stopCurrentTurn), and the send button relabels to
    # "引导当前执行" so submissions route to steerTurn instead of submitTurn.
    html = (ROOT / "static/v3/index.html").read_text(encoding="utf-8")
    source = (ROOT / "static/v3/v3.js").read_text(encoding="utf-8")

    assert 'id="voice-input-btn"' in html

    # Stop affordance: the composer button cancels the active turn via /stop.
    assert '/stop`' in source
    assert 'if (state.turnInProgress) stopCurrentTurn();' in source
    assert '"停止当前执行"' in source

    # Mid-turn guidance: submissions during a turn steer it via /steer.
    assert '/steer`' in source
    assert 'state.turnInProgress ? steerTurn(msg) : submitTurn(msg)' in source
    assert '"引导当前执行"' in source

    # Server events for both flows are still handled.
    assert 'turn_guidance_applied:' in source
    assert 'turn_cancelled:' in source


class SteeringClient:
    def __init__(self) -> None:
        self.calls = []

    async def stream_turn(self, messages, *, tools):
        self.calls.append(messages)
        if len(self.calls) == 1:
            yield {"kind": "text_delta", "text": "原方向"}
            await asyncio.sleep(0.03)
        else:
            yield {"kind": "text_delta", "text": "已经按新方向调整"}
        yield {"kind": "finish", "reason": "stop"}


def test_guidance_is_applied_before_accepting_a_text_only_finish(tmp_path: Path) -> None:
    async def scenario():
        events = []
        client = SteeringClient()
        agent = AgentLoopV3(
            session_id="steer-safe-boundary",
            output_dir=tmp_path,
            gemini_client=client,
            emit_event=events.append,
        )
        task = asyncio.create_task(agent.run_turn("你好"))
        await asyncio.sleep(0.01)
        agent.queue_turn_guidance("改成冰蓝色，节奏更快")
        await task
        return client, events

    client, events = asyncio.run(scenario())
    assert len(client.calls) == 2
    assert "改成冰蓝色，节奏更快" in json.dumps(client.calls[1], ensure_ascii=False)
    assert any(event.get("kind") == "turn_guidance_applied" for event in events)
    assert any(event.get("kind") == "turn_complete" for event in events)
