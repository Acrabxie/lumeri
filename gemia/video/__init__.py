"""gemia.video — Video primitive operations."""
from gemia.video.frames import (
    extract_frames, frames_to_video, apply_picture_op_to_video, speed_curve,
    optical_flow_retime, ai_stabilize, denoise_temporal,
)
from gemia.video.timeline import (
    cut, concat, nest_clips, speed, reverse,
    slip_edit, slide_edit, ripple_trim, roll_edit, freeze_frame,
    timeline_from_script,
)
from gemia.video.compositing import (
    overlay, add_audio_track, object_remove, background_replace, stereo_3d_align,
    depth_mask,
)
from gemia.video.transitions import (
    transition_dissolve, transition_wipe, transition_push, transition_custom,
)
from gemia.video.analysis import detect_scenes, get_metadata, multicam_sync, scene_detect, auto_highlight, smart_multicam
from gemia.video.generative import (
    generate_video, generate_video_from_image, extend_video,
    generative_extend, ai_color_grade, generate_broll, hdr_tone_map,
    film_look_creator, intellitrack_zone,
)
from gemia.video.export import export_preset, proxy_generate, batch_export
from gemia.video.effects import (
    lut_apply, chroma_aberration, vhs_effect, color_wheels, zoom_pan, chroma_warp,
    particle_emitter, planar_tracker, curves_warp, light_wrap,
    ai_cinematic_haze, hdr_vivid,
    deep_composite, rhythm_cut,
    timecode_burn, auto_reframe, color_space_convert, deinterlace, spatial_video_render,
)
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
    "optical_flow_retime", "ai_stabilize", "denoise_temporal",
    # timeline
    "cut", "concat", "nest_clips", "speed", "reverse",
    "slip_edit", "slide_edit", "ripple_trim", "roll_edit", "freeze_frame",
    "timeline_from_script",
    # compositing
    "overlay", "add_audio_track", "object_remove", "background_replace", "stereo_3d_align",
    "depth_mask",
    "lut_apply", "vhs_effect", "chroma_aberration", "zoom_pan", "color_wheels",
    # transitions
    "transition_dissolve", "transition_wipe", "transition_push", "transition_custom",
    # analysis
    "detect_scenes", "get_metadata", "multicam_sync", "scene_detect", "auto_highlight", "smart_multicam",
    # generative
    "generate_video", "generate_video_from_image", "extend_video",
    "generative_extend", "ai_color_grade", "generate_broll", "hdr_tone_map",
    "film_look_creator", "intellitrack_zone",
    # subtitles / text
    "make_srt", "make_vtt", "transcribe_to_srt", "transcribe_to_vtt",
    "burn_subtitles", "mux_subtitle_track", "extract_subtitle_track",
    "auto_subtitle", "add_lower_third", "add_text", "add_subtitle_track", "animated_text",
    # export
    "export_preset", "proxy_generate", "batch_export",
    # effects
    "lut_apply", "chroma_aberration", "vhs_effect", "color_wheels", "zoom_pan", "chroma_warp",
    "particle_emitter", "planar_tracker", "curves_warp", "light_wrap",
    "ai_cinematic_haze", "hdr_vivid",
    "deep_composite", "rhythm_cut",
    "timecode_burn", "auto_reframe", "color_space_convert", "deinterlace", "spatial_video_render",
]
