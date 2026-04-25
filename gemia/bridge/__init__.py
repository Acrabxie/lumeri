"""File-based bridge for external AI clients such as Antigravity and Claude Code."""

from .daemon import (
    BridgeDaemon,
    BridgePaths,
    BridgeResult,
    BridgeTask,
    ClaudeCodeAdapter,
    ControllerAdapter,
    MasterBridgeController,
    QueueBridgeAdapter,
)

__all__ = [
    "BridgeDaemon",
    "BridgePaths",
    "BridgeResult",
    "BridgeTask",
    "ClaudeCodeAdapter",
    "ControllerAdapter",
    "MasterBridgeController",
    "QueueBridgeAdapter",
]
