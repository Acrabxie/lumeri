"""gemia.video — Video primitive operations."""
from gemia.video.frames import (
    extract_frames, frames_to_video, apply_picture_op_to_video, speed_curve,
)
from gemia.video.timeline import (
    cut, concat, nest_clips, speed, reverse,
    slip_edit, slide_edit, ripple_trim, roll_edit, freeze_frame,
)
from gemia.video.compositing import overlay, add_audio_track, object_remove, background_replace
from gemia.video.transitions import (
    transition_dissolve, transition_wipe, transition_push, transition_custom,
)
from gemia.video.analysis import detect_scenes, get_metadata, multicam_sync, scene_detect, auto_highlight
from gemia.video.generative import (
    generate_video, generate_video_from_image, extend_video,
    generative_extend, ai_color_grade, generate_broll,
)
from gemia.video.export import export_preset, proxy_generate, batch_export
from gemia.video.subtitles import (
    make_srt, make_vtt,
    transcribe_to_srt, transcribe_to_vtt,
    burn_subtitles, mux_subtitle_track, extract_subtitle_track,
    auto_subtitle, add_lower_third,
    add_text, add_subtitle_track,
    animated_text,
)

__all__ = [
    # frames
    "extract_frames", "frames_to_video", "apply_picture_op_to_video", "speed_curve",
    # timeline
    "cut", "concat", "nest_clips", "speed", "reverse",
    "slip_edit", "slide_edit", "ripple_trim", "roll_edit", "freeze_frame",
    # compositing
    "overlay", "add_audio_track", "object_remove", "background_replace",
    # transitions
    "transition_dissolve", "transition_wipe", "transition_push", "transition_custom",
    # analysis
    "detect_scenes", "get_metadata", "multicam_sync", "scene_detect", "auto_highlight",
    # generative
    "generate_video", "generate_video_from_image", "extend_video",
    "generative_extend", "ai_color_grade", "generate_broll",
    # subtitles / text
    "make_srt", "make_vtt", "transcribe_to_srt", "transcribe_to_vtt",
    "burn_subtitles", "mux_subtitle_track", "extract_subtitle_track",
    "auto_subtitle", "add_lower_third", "add_text", "add_subtitle_track", "animated_text",
    # export
    "export_preset", "proxy_generate", "batch_export",
]
