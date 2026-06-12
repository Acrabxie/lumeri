"""Lumeri v3 verb schemas.

Pure data. Defines the creative-action tools the model can call. Each
schema follows the OpenAI function-calling shape OpenRouter accepts for
Gemini 3.1 Pro:

    {
        "type": "function",
        "function": {
            "name": "edit_video",
            "description": "...",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
    }

Asset references are always ``asset_id`` strings (e.g. ``v_001``,
``img_003``). Never file paths. The host owns id allocation and path
resolution.
"""
from __future__ import annotations

from typing import Any


def _tool(
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_ASSET_ID = {
    "type": "string",
    "description": "An asset_id from the session registry (e.g. v_001, img_003).",
}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool(
        "generate_image",
        "Create a new still image from a text prompt. Returns a new asset_id.",
        {
            "prompt": {"type": "string", "description": "What the image should depict."},
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                "description": "Frame aspect ratio.",
            },
            "style": {"type": "string", "description": "Optional aesthetic hint (e.g. 'cinematic, soft light')."},
            "reference_asset_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional reference assets for image-to-image guidance.",
            },
        },
        ["prompt"],
    ),
    _tool(
        "generate_video",
        "Create a new video clip from a text prompt using Vertex AI Veo. This is an expensive slow tool: it submits a Vertex long-running operation, waits for completion, writes the MP4 to the session workspace, and returns a video asset_id. Prefer search_library or local editing when existing footage can satisfy the task.",
        {
            "prompt": {"type": "string", "description": "What the clip should show."},
            "duration_sec": {"type": "number", "description": "Target duration in seconds (max 8)."},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
            "reference_asset_id": {"type": "string", "description": "Optional starting image for image-to-video."},
            "camera": {"type": "string", "description": "Optional motion hint (e.g. 'slow dolly in')."},
            "max_wait_sec": {
                "type": "number",
                "description": "Optional maximum time to wait for the Vertex LRO. Default 300, clamped to 30..900.",
            },
        },
        ["prompt"],
    ),
    _tool(
        "generate_audio",
        "Create new music or sound from a text prompt using Vertex AI Lyria. Returns an audio asset_id after writing the generated WAV to the session workspace. Lyria currently returns a fixed short clip; use edit/mix tools afterward for timeline placement.",
        {
            "prompt": {"type": "string", "description": "What the audio should sound like."},
            "duration_sec": {"type": "number", "description": "Requested duration hint. Vertex Lyria may ignore this and return its fixed clip length."},
            "mood": {"type": "string", "description": "Optional mood (e.g. 'tense, low')."},
            "bpm": {"type": "number", "description": "Optional tempo target."},
        },
        ["prompt"],
    ),
    _tool(
        "edit_image",
        "Transform an existing image. Returns a new asset_id.",
        {
            "asset_id": _ASSET_ID,
            "operation": {
                "type": "string",
                "enum": ["crop", "rotate", "resize", "blur", "denoise", "remove_background"],
            },
            "params": {
                "type": "object",
                "description": "Operation-specific parameters (e.g. {angle_deg: 90} for rotate).",
            },
        },
        ["asset_id", "operation"],
    ),
    _tool(
        "edit_video",
        "Transform an existing video. Operations: trim (cut a segment), concat (join clips end-to-end), reverse (play backward), speed (change playback speed). Returns a new asset_id.",
        {
            "asset_id": _ASSET_ID,
            "operation": {
                "type": "string",
                "enum": ["trim", "concat", "reverse", "speed"],
            },
            "trim": {
                "type": "object",
                "description": "For trim. {start_sec, end_sec}. end_sec=null means 'to end of clip'.",
                "properties": {
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": ["number", "null"]},
                },
            },
            "concat_with": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For concat. Additional asset_ids to append after asset_id, in order.",
            },
            "speed_factor": {
                "type": "number",
                "description": "For speed. >1 faster, <1 slower.",
            },
        },
        ["asset_id", "operation"],
    ),
    _tool(
        "composite",
        "Combine two visual layers into one. Returns a new asset_id.",
        {
            "base_asset_id": _ASSET_ID,
            "overlay_asset_id": _ASSET_ID,
            "mode": {"type": "string", "enum": ["alpha", "blend", "screen", "multiply"]},
            "opacity": {"type": "number", "description": "0..1"},
            "position": {
                "type": "object",
                "description": "Optional pixel offset {x, y} of the overlay's top-left.",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            },
            "scale": {"type": "number", "description": "Optional overlay scale factor."},
        },
        ["base_asset_id", "overlay_asset_id", "mode"],
    ),
    _tool(
        "color_grade",
        "Apply a color look to an image or video. Returns a new asset_id.",
        {
            "asset_id": _ASSET_ID,
            "look": {
                "type": "string",
                "description": "Named look (warm, cool, vintage, cinematic, teal_orange, neutral) or a free-form description.",
            },
            "intensity": {
                "type": "number",
                "description": "0..1, default 1.0. Lower values blend the graded look with the original.",
            },
        },
        ["asset_id", "look"],
    ),
    _tool(
        "add_overlay",
        "Burn a text caption, image overlay, or subtitle onto a video. Returns a new asset_id.",
        {
            "asset_id": _ASSET_ID,
            "kind": {"type": "string", "enum": ["text", "image", "subtitle"]},
            "text": {"type": "string", "description": "For kind=text or kind=subtitle."},
            "overlay_asset_id": {"type": "string", "description": "For kind=image. The asset to overlay."},
            "position": {
                "type": "string",
                "enum": [
                    "top_left", "top_center", "top_right",
                    "center_left", "center", "center_right",
                    "bottom_left", "bottom_center", "bottom_right",
                ],
                "description": "Anchor position on the frame.",
            },
            "start_sec": {"type": "number", "description": "When the overlay appears."},
            "end_sec": {"type": "number", "description": "When the overlay disappears. Omit for whole clip."},
            "font_size": {"type": "integer", "description": "For kind=text. Default 32."},
            "font_color": {"type": "string", "description": "For kind=text. e.g. 'white', '#ffcc00'."},
        },
        ["asset_id", "kind"],
    ),
    _tool(
        "arrange_timeline",
        "Sequence multiple clips in order, with optional transitions. Returns a new asset_id (the assembled timeline).",
        {
            "asset_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Clips to place on the timeline, in playback order.",
            },
            "transitions": {
                "type": "array",
                "description": "Optional transitions between adjacent clips.",
                "items": {
                    "type": "object",
                    "properties": {
                        "between_index": {"type": "integer", "description": "0-based index of the cut (between clip i and i+1)."},
                        "kind": {"type": "string", "enum": ["cut", "dissolve", "wipe", "fade"]},
                        "duration_sec": {"type": "number"},
                    },
                },
            },
        },
        ["asset_ids"],
    ),
    _tool(
        "mix_audio",
        "Combine multiple audio tracks. Returns a new asset_id.",
        {
            "asset_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Audio assets to combine.",
            },
            "mode": {"type": "string", "enum": ["concat", "mix", "duck"]},
            "levels_db": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Optional per-track gain in dB. Same length as asset_ids.",
            },
        },
        ["asset_ids", "mode"],
    ),
    _tool(
        "transform_geometry",
        "Geometric operations on an image or video frame (crop, rotate, scale, perspective warp). Returns a new asset_id.",
        {
            "asset_id": _ASSET_ID,
            "operation": {
                "type": "string",
                "enum": ["crop", "rotate", "scale", "perspective"],
            },
            "params": {
                "type": "object",
                "description": "Operation-specific parameters (e.g. {angle_deg: 15} or {x, y, w, h} for crop).",
            },
        },
        ["asset_id", "operation"],
    ),
    _tool(
        "extract_frame",
        "Pull a single still frame from a video at a given timestamp. Returns a new image asset_id.",
        {
            "asset_id": _ASSET_ID,
            "time_sec": {"type": "number", "description": "Timestamp to extract from."},
        },
        ["asset_id", "time_sec"],
    ),
    _tool(
        "analyze_media",
        "Examine an asset and get a short text description and a thumbnail back. Use this when you want to actually see what an earlier action produced — for self-review or to decide a next step. Costs tokens; do not call as a default pre-flight.",
        {
            "asset_id": _ASSET_ID,
            "focus": {
                "type": "string",
                "description": "Optional aspect to focus on (e.g. 'color palette', 'subject motion', 'visible text').",
            },
        },
        ["asset_id"],
    ),
    _tool(
        "search_library",
        "Search the user's existing asset library by text query. Returns a list of matching asset_ids with brief descriptions.",
        {
            "query": {"type": "string", "description": "Free-text search."},
            "kind": {"type": "string", "enum": ["image", "video", "audio", "any"]},
            "limit": {"type": "integer", "description": "Max results. Default 10."},
        },
        ["query"],
    ),
    _tool(
        "export",
        "Final encode of an asset at requested quality and format. Returns the path to the exported file as a new asset_id.",
        {
            "asset_id": _ASSET_ID,
            "format": {"type": "string", "enum": ["mp4", "mov", "webm", "gif"]},
            "quality": {
                "type": "string",
                "enum": ["4k", "1080p", "720p", "480p", "draft"],
            },
            "platform": {
                "type": "string",
                "enum": ["youtube", "instagram", "tiktok", "twitter", "prores", "generic"],
                "description": "Optional platform preset for additional tuning.",
            },
        },
        ["asset_id", "format", "quality"],
    ),
    _tool(
        "web_search",
        "Search the public web from the host side and return compact result titles, URLs, snippets, and a saved JSON path. Use this to discover current sources before opening a page or fetching a file. The sandbox remains network-denied; raw HTML is not returned.",
        {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'current YouTube Shorts safe zone dimensions'.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return. Default 5; clamped to 1..10.",
            },
        },
        ["query"],
    ),
    _tool(
        "web_open",
        "Read an https:// web page from the host side and return cleaned text, title, source, and a saved JSON path. Use fetch instead for binary/media downloads. The sandbox remains network-denied; raw HTML is not returned.",
        {
            "url": {
                "type": "string",
                "description": "https:// page URL to read as text.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum cleaned text characters to return. Default 6000; clamped to 500..12000.",
            },
        },
        ["url"],
    ),
    _tool(
        "fetch",
        "Download a file from an https:// URL into the session workspace. Returns asset_id (if media), path, size_bytes, content_type, summary. Enforces https-only; blocks http://, file://, and other schemes. Network access is restricted to the host; the sandbox has no internet. Supports optional filename customization.",
        {
            "url": {
                "type": "string",
                "description": "https:// URL to download from. Must be https (no http, file://, etc.).",
            },
            "dest_name": {
                "type": "string",
                "description": "Optional: target filename in the workspace (default: url basename). Sanitized to prevent path traversal.",
            },
        },
        ["url"],
    ),
    _tool(
        "run_shell",
        "Execute a bash command in an isolated sandbox. Workspace directory is fully readable/writable. Outside workspace: files can only be created, not modified/deleted. Credentials (~/.ssh, ~/.config/gcloud, ~/.gemia/config.json) are not readable. Network access denied. Wraps command with sandbox-exec for M1 isolation. Returns exit_code, stdout_tail, stderr_tail, timed_out, sandbox_enforced, workspace_dir.",
        {
            "command": {
                "type": "string",
                "description": "Bash command string to execute.",
            },
            "timeout_sec": {
                "type": "number",
                "description": "Timeout in seconds (default 30, max 120). Command killed if it exceeds this duration.",
            },
        },
        ["command"],
    ),
]


TOOL_NAMES: list[str] = [t["function"]["name"] for t in TOOL_SCHEMAS]


__all__ = ["TOOL_SCHEMAS", "TOOL_NAMES"]
