from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
)

DEFAULT_TOOL_PATHS = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    root = Path.home() / ".gemia" / "automation"
    root.mkdir(parents=True, exist_ok=True)
    return root


def runtime_logs_dir() -> Path:
    path = runtime_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def rollover_queue_dir() -> Path:
    path = runtime_root() / "rollovers" / "pending"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_state_path() -> Path:
    return runtime_root() / "loop_state.json"


def heartbeat_state_path() -> Path:
    return runtime_root() / "heartbeat_state.json"


def stock_catalog_path() -> Path:
    return runtime_root() / "stock_catalog.json"


def stock_manifest_path() -> Path:
    return runtime_root() / "stock_manifest.json"


def bridge_root() -> Path:
    return Path.home() / ".gemia" / "bridge"


def config_path() -> Path:
    return Path.home() / ".gemia" / "config.json"


def agent_log_path() -> Path:
    return repo_root() / "agent_log.md"


def human_needed_path() -> Path:
    return repo_root() / "HUMAN_NEEDED.md"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_user_config() -> dict[str, Any]:
    return read_json(config_path(), {})


def get_config_value(field: str, env_name: str | None = None) -> str:
    if env_name:
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            return env_value
    value = str(read_user_config().get(field, "") or "").strip()
    return value


def append_agent_log(message: str) -> None:
    path = agent_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def append_human_needed(title: str, details: str) -> None:
    path = human_needed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {title}\n")
        fh.write(details.rstrip() + "\n")


def safe_slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "asset"


def proxyless_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def automation_tool_path(path_value: str | None = None) -> str:
    """Return a launchd-safe PATH for Homebrew, Node, uv, and system tools."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (*DEFAULT_TOOL_PATHS, *(path_value or "").split(os.pathsep)):
        item = raw.strip()
        if not item or item in seen:
            continue
        if Path(item).exists():
            parts.append(item)
            seen.add(item)
    return os.pathsep.join(parts)


def automation_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(extra or {})
    env = proxyless_env(env)
    env["PATH"] = automation_tool_path(env.get("PATH", ""))
    return env


def resolve_tool(name: str, env_name: str | None = None) -> str:
    configured = os.environ.get(env_name, "").strip() if env_name else ""
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.exists():
            return str(configured_path)
    resolved = shutil.which(name, path=automation_tool_path(os.environ.get("PATH", "")))
    if resolved:
        return resolved
    raise FileNotFoundError(f"Required tool not found on automation PATH: {name}")


@contextmanager
def cleared_proxy_env() -> Iterator[None]:
    removed: dict[str, str] = {}
    for key in PROXY_ENV_KEYS:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        yield
    finally:
        for key in ("NO_PROXY", "no_proxy"):
            os.environ.pop(key, None)
        os.environ.update(removed)


def candidate_stock_roots() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("GEMIA_STOCK_ROOT", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    volumes_dir = Path("/Volumes")
    if volumes_dir.exists():
        for child in sorted(volumes_dir.iterdir()):
            if child.name == "My Mac" or child.is_symlink():
                continue
            candidates.append(child / "gemia-stock")
    candidates.append(repo_root() / "temp" / "gemia-stock")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def choose_stock_root(min_free_bytes: int = 3 * 1024**3) -> Path:
    viable: list[tuple[int, int, Path]] = []
    for root in candidate_stock_roots():
        mount = root if root.exists() else root.parent
        try:
            usage = shutil.disk_usage(mount)
        except FileNotFoundError:
            continue
        if usage.free < min_free_bytes:
            continue
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError:
            continue
        external_rank = 1 if str(root).startswith("/Volumes/") else 0
        viable.append((external_rank, usage.free, root))
    if not viable:
        raise RuntimeError(
            "No writable stock-media root has enough free space. "
            "Set GEMIA_STOCK_ROOT or free more disk."
        )
    return max(viable, key=lambda item: (item[0], item[1]))[2]
