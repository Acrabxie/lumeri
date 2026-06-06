"""fetch — pull material from an https URL into the workspace.

Host-side networking only (sandbox denies network entirely). Verifies https-only,
applies proxy from environment / config, uses urllib with certifi, and writes to
the session output directory.

Dispatcher signature: async def dispatch(args: dict, ctx: ToolContext) -> dict.
Returns {asset_id?, path, size_bytes, content_type, summary}.
"""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import certifi

from gemia.tools._context import ToolContext


def _read_config(key: str) -> str | None:
    """Read a single key from ~/.gemia/config.json if it exists."""
    config_path = Path.home() / ".gemia" / "config.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path) as f:
            data = json.load(f)
            return data.get(key)
    except Exception:
        return None


def _sanitize_filename(name: str) -> str:
    """Remove path traversal and ensure a safe basename.

    Rejects any path component (containing / or ..), and absolute paths.
    """
    # Reject if name contains ".." anywhere (path traversal)
    if ".." in name:
        raise ValueError(f"unsafe filename: contains path traversal (..) — {name}")
    # Reject absolute paths
    if name.startswith("/"):
        raise ValueError(f"unsafe filename: absolute path — {name}")
    # If name contains /, take only the basename (last component)
    basename = name.split("/")[-1]
    # If empty after cleanup, use a default
    if not basename:
        return "downloaded_file"
    return basename


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Fetch a file from an https URL into ctx.output_dir.

    Args:
        url: required, must be https (no file://, no http://)
        dest_name: optional, filename to save as (default: basename from url)

    Returns:
        {asset_id?, path, size_bytes, content_type, summary}
        asset_id present only if the file is a recognized media type (video/image/audio).
    """
    url = str(args.get("url") or "").strip()
    if not url:
        raise ValueError("fetch requires a non-empty 'url' argument")

    # https-only: block file://, http://, and other schemes
    if not url.startswith("https://"):
        raise ValueError(
            f"fetch requires https:// (blocking http, file://, and other schemes). "
            f"Got: {url[:50]}"
        )

    # Validate and sanitize dest_name BEFORE attempting fetch (fail fast)
    dest_name = args.get("dest_name")
    if dest_name:
        dest_name_str = str(dest_name)
        # Sanitization will raise ValueError on ".." or "/" paths
        dest_name = _sanitize_filename(dest_name_str)
    else:
        # Use url basename
        url_basename = url.split("?")[0].split("#")[0].split("/")[-1]
        if url_basename:
            dest_name = _sanitize_filename(url_basename)
        else:
            dest_name = "downloaded_file"

    # Build output path
    output_path = ctx.output_dir / dest_name

    # Proxy configuration: from env or config
    proxy = os.environ.get("OPENROUTER_PROXY") or _read_config("proxy")

    # Build urllib opener with proxy and SSL (mirrors google_genai_client.py:336-350)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    https_handler = urllib.request.HTTPSHandler(context=ssl_context)

    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": proxy, "http": proxy}),
            https_handler,
        )
    else:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            https_handler,
        )

    # Fetch in a thread to avoid blocking the event loop
    def _fetch_blocking() -> tuple[bytes, str]:
        req = urllib.request.Request(url, headers={"User-Agent": "Lumeri/4.0"})
        try:
            resp = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"fetch HTTP {exc.code} from {url[:50]}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"fetch transport error: {exc.reason}") from exc

        with resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")

        return data, content_type

    # Size limit: 100 MB
    MAX_SIZE = 100 * 1024 * 1024

    try:
        data, content_type = await asyncio.to_thread(_fetch_blocking)
    except Exception as exc:
        raise ValueError(f"fetch failed for {url[:50]}: {exc}") from exc

    if len(data) > MAX_SIZE:
        raise ValueError(
            f"fetch exceeded size limit: {len(data) / 1024 / 1024:.1f} MB > "
            f"{MAX_SIZE / 1024 / 1024:.0f} MB"
        )

    # Write to disk
    try:
        output_path.write_bytes(data)
    except OSError as exc:
        raise ValueError(f"fetch failed to write to {output_path}: {exc}") from exc

    # Try to register as asset (skip if non-media, like .zip/.json/.py)
    asset_id = None
    try:
        record = ctx.registry.add_external(
            output_path,
            summary=f"fetched from {url[:60]}",
        )
        asset_id = record.asset_id
    except ValueError:
        # Not a recognized media type (video/image/audio) — skip registration
        pass

    # Build result
    result: dict[str, Any] = {
        "path": str(output_path.relative_to(ctx.output_dir)),
        "size_bytes": len(data),
        "content_type": content_type,
        "summary": f"downloaded {len(data) / 1024 / 1024:.1f} MB from URL",
    }
    if asset_id:
        result["asset_id"] = asset_id

    return result


__all__ = ["dispatch"]
