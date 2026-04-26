"""gemia.video — Video primitive operations."""
from gemia.video.frames import (
    extract_frames, frames_to_video, apply_picture_op_to_video, speed_curve,
    optical_flow_retime, ai_stabilize, denoise_temporal,
)
from gemia.video.timeline import (
    cut, concat, nest_clips, speed, reverse,
    slip_edit, slide_edit, ripple_trim, roll_edit, freeze_frame,
    timeline_from_script, speed_ramp,
)
from gemia.video.compositing import (
    overlay, add_audio_track, object_remove, background_replace, stereo_3d_align,
    depth_mask, picture_in_picture,
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
    waveform_monitor, keyframe_extract, aspect_ratio_pad, video_denoise_spatial,
    video_vignette, video_mirror, video_brightness_contrast,
    video_rotate, video_thumbnail_grid,
    video_frame_rate_convert, video_letterbox,
    video_extract_frames_range, video_color_temp,
    video_split, video_subtitle_extract,
    video_mute, video_to_audio,
    video_delogo, video_zoom_in,
    video_aspect_ratio_change, video_ken_burns,
    video_hue_rotate, video_slow_motion,
    video_fast_forward, video_timelapse,
    video_color_invert, video_frame_blend,
    video_pixelate, video_edge_detect,
    video_colorize, video_glitch,
    video_denoise, video_sharpen,
    video_blur, video_zoom_out,
    video_fade, video_shake,
    video_crop_center, video_transition_fade_black,
    video_overlay_image, video_audio_visualizer,
    video_chapters_from_timestamps, video_countdown,
    video_stabilize_simple, video_loop,
    video_rotate_90, video_add_watermark_text,
    video_extract_audio_segment, video_trim_silence,
    video_freeze_at, video_concat_with_transition,
    video_flip_horizontal, video_flip_vertical,
    video_scale_to_width, video_scale_to_height,
    video_set_fps, video_crop,
    video_pad, video_thumbnail,
    video_add_silent_audio, video_remove_audio,
    video_info, video_concat_list,
    video_replace_audio, video_segment_export,
    video_grayscale, video_sepia,
    video_normalize, video_speed_audio_sync,
    video_slow_zoom, video_color_boost,
    video_adjust_brightness, video_adjust_contrast,
    video_adjust_gamma, video_split_to_frames,
    video_frames_to_video, video_denoise_hqdn3d,
    video_add_timestamp, video_hstack,
    video_vstack, video_draw_box,
    video_xfade, video_motion_blur,
    video_color_lut_apply, video_reverse_audio,
    video_audio_normalize, video_subtitle_burn_style,
    video_extract_i_frames, video_fade_audio,
    video_concat_crossfade, video_add_chapters,
    video_boomerang, video_color_splash,
    video_zoom_crop_safe, video_time_remap,
    video_aspect_letterbox, video_gif_export,
    video_stabilize_crop, video_multi_speed,
    video_luma_key, video_audio_waveform_overlay,
    video_highlight_region, video_frame_interpolate,
    video_rolling_shutter, video_color_correct,
    video_text_caption, video_posterize,
    video_speed_ramp_ease, video_split_screen,
    video_freeze_frame_at, video_wipe_transition,
    video_zoom_punch, video_color_shift,
    video_rgb_split, video_scanlines,
    video_night_vision, video_old_film,
    video_tilt_shift, video_mirror_flip,
    video_ken_burns_auto, video_color_pop,
    video_shake_cam, video_color_temperature,
    video_zoom_blur, video_flash_cut,
    video_invert_colors, video_strobe,
    video_pixelate_faces, video_speed_echo,
    video_zoom_in_center, video_frame_hold,
    video_vhs_glitch, video_letterbox_blur,
    video_split_tone, video_zoom_out_center,
    video_push_transition, video_fade_to_white,
    video_mirror_vertical, video_chromatic_shift,
    video_zoom_pulse, video_color_grade_lut,
    video_split_quad, video_text_lower_third,
    video_burn_in_timecode, video_slow_in_fast_out,
    video_color_crush, video_fast_in_slow_out,
    video_dreamy_blur, video_rgb_parade,
    video_cinematic_bars, video_epic_slowmo,
    video_freeze_zoom, video_duotone,
    video_infrared, video_retrowave,
    video_color_fade_out, video_zoom_letters,
    video_glitch_rgb, video_vignette_focus,
    video_mirror_time, video_color_shift_time,
    video_bleach_bypass, video_frame_trails,
    video_heatwave, video_shadow_highlight,
    video_film_flicker, video_cross_zoom,
    video_parallax_drift, video_sunset_grade,
    video_iris_pulse, video_moonlight_grade,
    video_crystal_shimmer, video_dusk_fade,
)
from gemia.video.subtitles import (
    make_srt, make_vtt,
    transcribe_to_srt, transcribe_to_vtt,
    burn_subtitles, mux_subtitle_track, extract_subtitle_track,
    auto_subtitle, add_lower_third,
    add_text, add_subtitle_track,
    animated_text,
)
from gemia.video.layers import (
    Layer, LayerStack, execute_layer_plan, render_layer_plan,
    make_video_layer, make_image_layer, make_text_layer, make_solid_layer,
)
from gemia.video.layer_validation import (
    LayerPlanValidationError, validate_layer_plan, validate_layer_stack_preview,
)
from gemia.video.compositing_graph import (
    CompositingEdge, CompositingGraph, CompositingNode,
    CompiledCompositingPlan, CompiledNodeStep,
    NeutralGraphBackend, NodeOutputRef,
    build_compositing_graph, build_compositing_graph_from_layer_plan,
    build_compositing_graph_from_layer_stack, compile_compositing_graph,
)
from gemia.video.preview import ShadowPreviewResult, render_shadow_preview
from gemia.video.review import RealMediaReviewResult, review_real_media_artifact
from gemia.video.layer_flow import render_layer_workflow
from gemia.video.intellisearch import (
    IntelliSearchIndexResult, IntelliSearchQueryResult,
    index_real_media, search_media_index,
)
from gemia.video.proxy import ProxyAsset, ProxyManager
from gemia.video.backends import (
    BackendDecision, RenderBackend, RenderProfile, RenderResult,
    SoftwareGraphBackend, SoftwareRenderBackend, choose_render_backend,
)

__all__ = [
    # frames
    "extract_frames", "frames_to_video", "apply_picture_op_to_video", "speed_curve",
    "optical_flow_retime", "ai_stabilize", "denoise_temporal",
    # timeline
    "cut", "concat", "nest_clips", "speed", "reverse",
    "slip_edit", "slide_edit", "ripple_trim", "roll_edit", "freeze_frame",
    "timeline_from_script", "speed_ramp",
    # compositing
    "overlay", "add_audio_track", "object_remove", "background_replace", "stereo_3d_align",
    "depth_mask", "picture_in_picture",
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
    # layers
    "Layer", "LayerStack", "execute_layer_plan", "render_layer_plan",
    "make_video_layer", "make_image_layer", "make_text_layer", "make_solid_layer",
    "LayerPlanValidationError", "validate_layer_plan", "validate_layer_stack_preview",
    "CompositingEdge", "CompositingGraph", "CompositingNode",
    "CompiledCompositingPlan", "CompiledNodeStep",
    "NeutralGraphBackend", "NodeOutputRef",
    "build_compositing_graph", "build_compositing_graph_from_layer_plan",
    "build_compositing_graph_from_layer_stack", "compile_compositing_graph",
    "ShadowPreviewResult", "render_shadow_preview", "RealMediaReviewResult",
    "review_real_media_artifact", "render_layer_workflow",
    "IntelliSearchIndexResult", "IntelliSearchQueryResult",
    "index_real_media", "search_media_index",
    "ProxyAsset", "ProxyManager",
    "BackendDecision", "RenderBackend", "RenderProfile", "RenderResult",
    "SoftwareGraphBackend", "SoftwareRenderBackend", "choose_render_backend",
    # export
    "export_preset", "proxy_generate", "batch_export",
    # effects
    "lut_apply", "chroma_aberration", "vhs_effect", "color_wheels", "zoom_pan", "chroma_warp",
    "particle_emitter", "planar_tracker", "curves_warp", "light_wrap",
    "ai_cinematic_haze", "hdr_vivid",
    "deep_composite", "rhythm_cut",
    "timecode_burn", "auto_reframe", "color_space_convert", "deinterlace", "spatial_video_render",
    "waveform_monitor", "keyframe_extract", "aspect_ratio_pad", "video_denoise_spatial",
    "video_vignette", "video_mirror", "video_brightness_contrast",
    "video_rotate", "video_thumbnail_grid",
    "video_frame_rate_convert", "video_letterbox",
    "video_extract_frames_range", "video_color_temp",
    "video_split", "video_subtitle_extract",
    "video_mute", "video_to_audio",
    "video_delogo", "video_zoom_in",
    "video_aspect_ratio_change", "video_ken_burns",
    "video_hue_rotate", "video_slow_motion",
    "video_fast_forward", "video_timelapse",
    "video_color_invert", "video_frame_blend",
    "video_pixelate", "video_edge_detect",
    "video_colorize", "video_glitch",
    "video_denoise", "video_sharpen",
    "video_blur", "video_zoom_out",
    "video_fade", "video_shake",
    "video_crop_center", "video_transition_fade_black",
    "video_overlay_image", "video_audio_visualizer",
    "video_chapters_from_timestamps", "video_countdown",
    "video_stabilize_simple", "video_loop",
    "video_rotate_90", "video_add_watermark_text",
    "video_extract_audio_segment", "video_trim_silence",
    "video_freeze_at", "video_concat_with_transition",
    "video_flip_horizontal", "video_flip_vertical",
    "video_scale_to_width", "video_scale_to_height",
    "video_set_fps", "video_crop",
    "video_pad", "video_thumbnail",
    "video_add_silent_audio", "video_remove_audio",
    "video_info", "video_concat_list",
    "video_replace_audio", "video_segment_export",
    "video_grayscale", "video_sepia",
    "video_normalize", "video_speed_audio_sync",
    "video_slow_zoom", "video_color_boost",
    "video_adjust_brightness", "video_adjust_contrast",
    "video_adjust_gamma", "video_split_to_frames",
    "video_frames_to_video", "video_denoise_hqdn3d",
    "video_add_timestamp", "video_hstack",
    "video_vstack", "video_draw_box",
    "video_xfade", "video_motion_blur",
    "video_color_lut_apply", "video_reverse_audio",
    "video_audio_normalize", "video_subtitle_burn_style",
    "video_extract_i_frames", "video_fade_audio",
    "video_concat_crossfade", "video_add_chapters",
    "video_boomerang", "video_color_splash",
    "video_zoom_crop_safe", "video_time_remap",
    "video_aspect_letterbox", "video_gif_export",
    "video_stabilize_crop", "video_multi_speed",
    "video_luma_key", "video_audio_waveform_overlay",
    "video_highlight_region", "video_frame_interpolate",
    "video_rolling_shutter", "video_color_correct",
    "video_text_caption", "video_posterize",
    "video_speed_ramp_ease", "video_split_screen",
    "video_freeze_frame_at", "video_wipe_transition",
    "video_zoom_punch", "video_color_shift",
    "video_rgb_split", "video_scanlines",
    "video_night_vision", "video_old_film",
    "video_tilt_shift", "video_mirror_flip",
    "video_ken_burns_auto", "video_color_pop",
    "video_shake_cam", "video_color_temperature",
    "video_zoom_blur", "video_flash_cut",
    "video_invert_colors", "video_strobe",
    "video_pixelate_faces", "video_speed_echo",
    "video_zoom_in_center", "video_frame_hold",
    "video_vhs_glitch", "video_letterbox_blur",
    "video_split_tone", "video_zoom_out_center",
    "video_push_transition", "video_fade_to_white",
    "video_mirror_vertical", "video_chromatic_shift",
    "video_zoom_pulse", "video_color_grade_lut",
    "video_split_quad", "video_text_lower_third",
    "video_burn_in_timecode", "video_slow_in_fast_out",
    "video_color_crush", "video_fast_in_slow_out",
    "video_dreamy_blur", "video_rgb_parade",
    "video_cinematic_bars", "video_epic_slowmo",
    "video_freeze_zoom", "video_duotone",
    "video_infrared", "video_retrowave",
    "video_color_fade_out", "video_zoom_letters",
    "video_glitch_rgb", "video_vignette_focus",
    "video_mirror_time", "video_color_shift_time",
    "video_bleach_bypass", "video_frame_trails",
    "video_heatwave", "video_shadow_highlight",
    "video_film_flicker", "video_cross_zoom",
    "video_parallax_drift", "video_sunset_grade",
    "video_iris_pulse", "video_moonlight_grade",
    "video_crystal_shimmer", "video_dusk_fade",
]

from gemia.video.effects import video_prism_echo, video_midnight_bloom

__all__.extend([
    "video_prism_echo",
    "video_midnight_bloom",
])

from gemia.video.effects import video_silver_halation, video_horizon_glow

__all__.extend([
    "video_silver_halation",
    "video_horizon_glow",
])

from gemia.video.effects import video_mirage_trail, video_aurora_grade

__all__.extend([
    "video_mirage_trail",
    "video_aurora_grade",
])

from gemia.video.effects import video_rainbow_streak, video_velvet_fade

__all__.extend([
    "video_rainbow_streak",
    "video_velvet_fade",
])

from gemia.video.effects import video_afterglow_pulse, video_mercury_smear

__all__.extend([
    "video_afterglow_pulse",
    "video_mercury_smear",
])

from gemia.video.effects import video_polar_sheen, video_lantern_drift

__all__.extend([
    "video_polar_sheen",
    "video_lantern_drift",
])
