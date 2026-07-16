"""远程(公网演示)会话的高危工具限制。

护主机：当会话带 X-Lumeri-Remote 标记时，能触达宿主机 shell / 任意文件读写 /
任意网络出网的工具必须从模型工具面剔除；创作类工具保持完整。
"""
from gemia.agent_loop_v3 import _REMOTE_DENY_TOOLS, _strip_remote_denied
from gemia.tools import DISPATCHER


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def test_deny_set_covers_host_reaching_tools():
    for name in [
        "run_shell", "build",
        "file_read", "read_file", "file_list", "list_dir",
        "file_write", "write_file", "file_copy", "copy_in",
        "file_move", "move_file", "file_delete", "organize_files",
        "fetch", "web_search", "web_open",
    ]:
        assert name in _REMOTE_DENY_TOOLS, name


def test_strip_removes_denied_keeps_creative():
    names = [
        "run_shell", "file_read", "list_dir", "fetch", "web_search", "web_open",
        "generate_video", "edit_video", "lumen_add_layer", "vector_motion",
        "timeline_split_clip", "probe_media",
    ]
    kept = {s["function"]["name"] for s in _strip_remote_denied([_schema(n) for n in names])}
    # host-reaching tools gone
    for gone in ("run_shell", "file_read", "list_dir", "fetch", "web_search", "web_open"):
        assert gone not in kept, gone
    # creative tools untouched
    for stay in ("generate_video", "edit_video", "lumen_add_layer",
                 "vector_motion", "timeline_split_clip", "probe_media"):
        assert stay in kept, stay


def test_deny_names_are_real_registered_tools():
    # Drift guard: a rename of any host tool must fail here, not silently
    # leave a hole in the remote restriction.
    for name in _REMOTE_DENY_TOOLS:
        assert name in DISPATCHER, f"{name!r} not registered — deny list drifted"


def test_empty_and_noninteractive_inputs():
    assert _strip_remote_denied([]) == []
    # schema missing function/name must not crash (fail-open-safe: kept, but
    # such entries never carry a dangerous dispatch name anyway)
    assert _strip_remote_denied([{"type": "function"}]) == [{"type": "function"}]
