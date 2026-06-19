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
        "Submit a Vertex AI Veo video generation job. Returns a job_id immediately (non-blocking). Use check_job(job_id) to poll or wait_for_job(job_id) to block until the video asset is ready. Expensive — prefer search_library or local editing when existing footage can satisfy the task.",
        {
            "prompt": {"type": "string", "description": "What the clip should show."},
            "duration_sec": {"type": "number", "description": "Target duration in seconds (max 8)."},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
            "reference_asset_id": {"type": "string", "description": "Optional starting image for image-to-video."},
            "camera": {"type": "string", "description": "Optional motion hint (e.g. 'slow dolly in')."},
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
                "enum": ["crop", "rotate", "resize", "blur", "denoise"],
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
        "Apply one of the named color looks to a video. Returns a new asset_id.",
        {
            "asset_id": _ASSET_ID,
            "look": {
                "type": "string",
                "enum": ["warm", "cool", "vintage", "cinematic", "teal_orange", "neutral"],
                "description": "The color look to apply. color_grade does color looks only — there is no grayscale/black-and-white and no mirror/flip.",
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
                "description": "Max results to return. Defaults to a full result page (10); clamped to 1..10.",
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
    _tool(
        "build",
        "Async submit Python code to a sandboxed subprocess. Executes immediately in a new process group with workspace full r/w and network denied. Returns job_id immediately; use check_job or wait_for_job to poll status. Perfect for long-running code, iteration loops (see→modify→rerun), and skill development.",
        {
            "code": {
                "type": "string",
                "description": "Python source code to execute in sandbox.",
            },
            "filename": {
                "type": "string",
                "description": "Output script filename (default 'script.py'). Must be a simple name without path separators.",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command-line arguments to pass to the script.",
            },
            "timeout_sec": {
                "type": "number",
                "description": "Timeout in seconds (default 120, clamped to (0, 600]). Process killed if exceeded.",
            },
            "note": {
                "type": "string",
                "description": "Optional human-readable description for the job.",
            },
        },
        ["code"],
    ),
    _tool(
        "check_job",
        "Poll a pending job by job_id. Works for build jobs (status + log tails) and Veo video jobs (status → asset_id when done). Inexpensive. Use to inspect job state without blocking.",
        {
            "job_id": {
                "type": "string",
                "description": "Job identifier returned by build or generate_video.",
            },
        },
        ["job_id"],
    ),
    _tool(
        "wait_for_job",
        "Block until a job completes or max_wait_sec is exceeded. Works for build jobs (polls every 1s) and Veo video jobs (polls every 10s). Returns same shape as check_job plus waited_sec and timed_out flag.",
        {
            "job_id": {
                "type": "string",
                "description": "Job identifier returned by build or generate_video.",
            },
            "max_wait_sec": {
                "type": "number",
                "description": "Maximum seconds to wait (default 60, clamped to (0, 300]). For Veo jobs use 300.",
            },
        },
        ["job_id"],
    ),
    _tool(
        "save_skill",
        "Persist a workspace artifact (e.g., debugged script, utility library) as a reusable skill for future sessions. Validates path containment, slugifies name, writes metadata JSON. Host-side only (no sandbox). Use after iterating/debugging code via build+check_job.",
        {
            "source": {
                "type": "string",
                "description": "Workspace-relative path to source file (e.g. 'builds/build_abc/script.py').",
            },
            "name": {
                "type": "string",
                "description": "Human-readable skill name (slugified to lowercase/hyphens).",
            },
            "description": {
                "type": "string",
                "description": "Optional skill description.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "If true, replace existing skill of same name. Default false.",
            },
        },
        ["source", "name"],
    ),
    # ── timeline document verbs (timeline v1, 2026-06-13 design) ──────
    # The session owns ONE persistent timeline (tracks + clips). These verbs
    # are fine-grained on purpose: each call = one logged, undoable patch.
    # Times are seconds. clip_ids come from the Timeline section / get_timeline.
    _tool(
        "get_timeline",
        "Inspect the session timeline document: tracks, clips (id, track, start/end, source range, effects), duration, and optionally recent patch history. Use when the prompt's Timeline section is not detailed enough.",
        {
            "history": {
                "type": "integer",
                "description": "How many recent patches to include (0-20). Default 0.",
            },
        },
        [],
    ),
    _tool(
        "timeline_insert_clip",
        "Place a clip on the timeline. Video assets go on video tracks (V1...); image assets and text titles go on overlay tracks (OV1..., auto-created); audio assets go on audio tracks (A1..., auto-created). Default position appends to the end of the track. Returns the new clip_id plus the updated timeline.",
        {
            "asset_id": {
                "type": "string",
                "description": "Asset to place (video, image, or audio). Omit when inserting text.",
            },
            "text": {
                "type": "object",
                "description": "Text title/caption clip instead of an asset. {content (required), font_size, color '#rrggbb', position {x,y} px top-left (default centered), align}.",
                "properties": {
                    "content": {"type": "string"},
                    "font_size": {"type": "number"},
                    "color": {"type": "string"},
                    "position": {"type": "object"},
                    "align": {"type": "string", "enum": ["left", "center", "right"]},
                },
            },
            "track_id": {
                "type": "string",
                "description": "Target track (e.g. V1, OV1). Default: V1 for video, first overlay track for image/text.",
            },
            "at_time": {
                "type": "number",
                "description": "Place at this timeline second. Overlap on the track is an error unless ripple=true (then later clips shift right).",
            },
            "at_index": {
                "type": "integer",
                "description": "Insert before the Nth clip of the track (0-based); later clips shift right. Mutually exclusive with at_time.",
            },
            "source_in": {"type": "number", "description": "Video/audio only: source trim-in second. Default 0."},
            "source_out": {"type": "number", "description": "Video/audio only: source trim-out second. Default full asset."},
            "duration": {"type": "number", "description": "Image/text only: how long it stays on screen. Default 3s."},
            "ripple": {"type": "boolean", "description": "Default false: never shifts other clips unless you set true."},
        },
        [],
    ),
    _tool(
        "timeline_delete_clip",
        "Remove a clip from the timeline. ripple=true closes the gap by shifting later clips on the same track left.",
        {
            "clip_id": {"type": "string", "description": "Clip to delete."},
            "ripple": {"type": "boolean", "description": "Default false (leaves a gap)."},
        },
        ["clip_id"],
    ),
    _tool(
        "timeline_move_clip",
        "Move a clip to a new start time and/or another compatible track. The destination must be free (no overlap). ripple=true closes the gap left at the origin.",
        {
            "clip_id": {"type": "string"},
            "start": {"type": "number", "description": "New timeline start second."},
            "track_id": {"type": "string", "description": "Optional destination track of the same kind."},
            "ripple": {"type": "boolean", "description": "Default false."},
        },
        ["clip_id"],
    ),
    _tool(
        "timeline_trim_clip",
        "Change which part of the source media a video/image clip shows (source_in/source_out, seconds). Clip duration follows the new range. ripple=true shifts later clips on the track by the duration change.",
        {
            "clip_id": {"type": "string"},
            "source_in": {"type": "number"},
            "source_out": {"type": "number"},
            "ripple": {"type": "boolean", "description": "Default false: extending into a neighbour is an error instead."},
        },
        ["clip_id"],
    ),
    _tool(
        "timeline_split_clip",
        "Cut a clip in two at a timeline second strictly inside it. The first half keeps the clip_id; the new second-half clip_id is returned. Both halves keep the same asset.",
        {
            "clip_id": {"type": "string"},
            "at_time": {"type": "number", "description": "Timeline second to cut at (inside the clip)."},
        },
        ["clip_id", "at_time"],
    ),
    _tool(
        "timeline_set_clip_time",
        "Set a clip's start (any clip) and/or on-screen duration (image/text clips only — trim video clips with timeline_trim_clip instead).",
        {
            "clip_id": {"type": "string"},
            "start": {"type": "number"},
            "duration": {"type": "number", "description": "Image/text clips only."},
            "ripple": {"type": "boolean", "description": "Default false."},
        },
        ["clip_id"],
    ),
    _tool(
        "timeline_add_transition",
        "Set the transition from a clip into the NEXT adjacent clip on the same track. kind=cut removes any transition. Non-cut kinds need the two clips to touch (no gap) — align them first.",
        {
            "clip_id": {"type": "string", "description": "The earlier clip of the pair."},
            "kind": {"type": "string", "enum": ["cut", "dissolve", "wipe", "fade"]},
            "duration_sec": {"type": "number", "description": "Transition length, default 0.5. Must fit inside both clips."},
        },
        ["clip_id", "kind"],
    ),
    _tool(
        "timeline_set_clip_effects",
        "Merge effects onto one clip. Visual keys: rotation (0/90/180/270), mirrored, blur_radius, opacity (0-1), x, y (px, overlay placement), scale, speed (reserved). Audio keys: gain_db (volume change in dB, +/-), fade_in, fade_out (seconds), muted (drop this clip's audio). Set a key to null to clear it.",
        {
            "clip_id": {"type": "string"},
            "effects": {
                "type": "object",
                "description": "Subset of allowed effect keys to merge.",
            },
        },
        ["clip_id", "effects"],
    ),
    _tool(
        "timeline_add_track",
        "Add a video, overlay, or audio track. track_id defaults to the next free V<n>/OV<n>/A<n>.",
        {
            "kind": {"type": "string", "enum": ["video", "overlay", "audio"]},
            "track_id": {"type": "string"},
            "name": {"type": "string"},
        },
        ["kind"],
    ),
    _tool(
        "timeline_set_track",
        "Set track-level options. Ducking: pass duck_under=<audio track id> to make this audio track a music bed that automatically ducks (lowers) whenever the trigger track (e.g. a voiceover track) is loud; pass duck_under=null to clear it. Both tracks must be audio; no self-reference or cycles.",
        {
            "track_id": {"type": "string", "description": "The audio track to configure."},
            "duck_under": {
                "type": ["string", "null"],
                "description": "Audio track id whose loudness ducks this track (sidechain trigger). null clears the relationship.",
            },
        },
        ["track_id"],
    ),
    _tool(
        "timeline_undo",
        "Rewind the last N timeline changes (each timeline_* call is one step). Discarded patches are kept for audit.",
        {
            "steps": {"type": "integer", "description": "1-10, default 1."},
        },
        [],
    ),
    _tool(
        "render_preview",
        "Render the current timeline document into a low-res proxy MP4 and register it as a new video asset. Use after a batch of timeline edits, then analyze_media to look at it. Final quality comes from export.",
        {
            "label": {"type": "string", "description": "Short label for the render manifest (default 'preview')."},
        },
        [],
    ),
    _tool(
        "project_export",
        "Export the full project timeline as a final-quality MP4. Composes video track clips in timeline order and overlays image/text clips on top. Returns a registered asset_id for the output file.",
        {
            "quality": {
                "type": "string",
                "enum": ["4k", "1080p", "720p", "480p", "draft"],
                "description": "Output quality preset. Default '1080p'.",
            },
            "label": {"type": "string", "description": "Short label for the export manifest (default 'export')."},
        },
        [],
    ),
    _tool(
        "project_export_otio",
        "Export the current project timeline as an OpenTimelineIO (.otio) file for use in DaVinci Resolve, Final Cut Pro, Premiere Pro, and other NLEs. Returns the path to the written .otio file.",
        {
            "label": {"type": "string", "description": "Base filename label (without extension). Default 'project'."},
        },
        [],
    ),
    _tool(
        "project_import_otio",
        "Import an OpenTimelineIO (.otio) file and replace the current session project's timeline. The prior state can be restored with timeline_undo.",
        {
            "otio_path": {"type": "string", "description": "Absolute path to the .otio file to import."},
        },
        ["otio_path"],
    ),
]


TOOL_NAMES: list[str] = [t["function"]["name"] for t in TOOL_SCHEMAS]


__all__ = ["TOOL_SCHEMAS", "TOOL_NAMES"]
