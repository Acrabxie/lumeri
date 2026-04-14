"""gemia.picture — Image primitive operations.

All functions accept float32 [0, 1] BGR ndarrays and return the same.
Every function supports batch input via the @batchable decorator:
pass a single image or a list of images.
"""
from gemia.picture.geometry import resize, crop, rotate, perspective_transform
from gemia.picture.pixel import blur, sharpen, denoise, add_grain, convolve
from gemia.picture.color import color_grade, adjust_exposure, adjust_temperature, apply_lut, colorslice_grade
from gemia.picture.analysis import histogram, dominant_colors, edge_detect
from gemia.picture.composite import create_mask, blend, composite
from gemia.picture.generative import generate_image, edit_image, style_transfer, blend_images
from gemia.picture.enhance import super_scale, match_color, skin_tone_protect, hdr_grade, film_grain_organic, defocus_background, relight, motion_blur, color_balance, image_flip, image_rotate, image_crop, image_resize_to_fit, image_add_border, image_grayscale, image_invert, image_posterize, image_solarize, image_pixelate, image_emboss, image_find_edges, image_smooth, image_auto_enhance, image_tint, image_watermark_text, image_rounded_corners, image_composite_alpha, image_adjust_hsl, image_resize_canvas, image_collage, image_sketch, image_oil_paint, image_cartoon, image_sepia, image_hdr_simulate, image_lens_blur, image_cross_process, image_halftone, image_noise, image_dither, image_clahe, image_palette_swap, image_channel_split, image_channel_merge, image_blend_overlay, image_blend_multiply, image_blend_screen, image_pixelate_region, image_text_overlay, image_draw_rect, image_histogram_equalize, image_mosaic, image_perspective_warp, image_normalize_brightness

__all__ = [
    # geometry
    "resize", "crop", "rotate", "perspective_transform",
    # pixel
    "blur", "sharpen", "denoise", "add_grain", "convolve",
    # color
    "color_grade", "adjust_exposure", "adjust_temperature", "apply_lut", "colorslice_grade",
    # analysis
    "histogram", "dominant_colors", "edge_detect",
    # composite
    "create_mask", "blend", "composite",
    # generative (Nano Banana)
    "generate_image", "edit_image", "style_transfer", "blend_images",
    # enhance
    "super_scale", "match_color", "skin_tone_protect", "hdr_grade", "film_grain_organic",
    "defocus_background", "relight", "motion_blur", "color_balance", "image_flip", "image_rotate", "image_crop",
    "image_resize_to_fit", "image_add_border", "image_grayscale", "image_invert", "image_posterize",
    "image_solarize", "image_pixelate", "image_emboss", "image_find_edges", "image_smooth",
    "image_auto_enhance", "image_tint", "image_watermark_text", "image_rounded_corners",
    "image_composite_alpha", "image_adjust_hsl", "image_resize_canvas", "image_collage",
    "image_sketch", "image_oil_paint",
    "image_cartoon", "image_sepia",
    "image_hdr_simulate", "image_lens_blur",
    "image_cross_process", "image_halftone",
    "image_noise", "image_dither",
    "image_clahe", "image_palette_swap",
    "image_channel_split", "image_channel_merge",
    "image_blend_overlay", "image_blend_multiply",
    "image_blend_screen", "image_pixelate_region",
    "image_text_overlay", "image_draw_rect",
    "image_histogram_equalize", "image_mosaic",
    "image_perspective_warp", "image_normalize_brightness",
]
