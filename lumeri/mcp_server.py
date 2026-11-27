"""Compatibility shim: ``lumeri.mcp_server`` re-exports the Direction-A MCP
server implementation that lives in :mod:`lumeri.mcp.server`.

The canonical layout (docs/mcp-interface-plan.md §5) puts the server under the
``lumeri/mcp/`` package. This module exists so ``python -c "import
lumeri.mcp_server"`` and any caller expecting a flat ``mcp_server`` module keeps
working. Nothing here imports the third-party ``mcp`` SDK at import time — the
SDK import is deferred inside the serve entrypoints in :mod:`lumeri.mcp.server`.
"""
from __future__ import annotations

from lumeri.mcp.server import (
    SERVER_NAME,
    SERVER_VERSION,
    build_server,
    run_stdio,
    serve_stdio,
)

__all__ = [
    "build_server",
    "serve_stdio",
    "run_stdio",
    "SERVER_NAME",
    "SERVER_VERSION",
]
