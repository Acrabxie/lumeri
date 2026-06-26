#!/usr/bin/env python3
"""M1.1 demo: Real media rendering + effects chain + adjustment layers."""
from __future__ import annotations

import sys
sys.path.insert(0, '.')

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import compile_to_layer_stack
import numpy as np
from PIL import Image as PILImage
from pathlib import Path
import cv2


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def main():
    # Create temporary test assets
    tmpdir = Path("/Volumes/Extreme SSD/GemiaTemp/lumenframe-demo2")
    tmpdir.mkdir(exist_ok=True)

    # 1. Create a test image (red square)
    img_path = tmpdir / "test_image.png"
    img = PILImage.new("RGBA", (100, 100), color=(255, 0, 0, 255))
    img.save(img_path)
    print(f"Created test image: {img_path}")

    # 2. Create a test video (green frames)
    video_path = tmpdir / "test_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(video_path), fourcc, 10.0, (100, 100))
    for _ in range(10):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :] = [0, 255, 0]  # green
        out.write(frame)
    out.release()
    print(f"Created test video: {video_path}")

    # 3. Build a lumenframe document with real media + effects
    doc = empty_doc(width=320, height=240, fps=10)
    doc["title"] = "M1.1 Demo: Real Media + Effects"

    # Add the image asset
    doc["assets"].append({"id": "img1", "path": str(img_path)})

    # Add the video asset
    doc["assets"].append({"id": "vid1", "path": str(video_path)})

    # Layer 1: Background (solid black)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "bg", "type": "solid",
        "color": "#000000", "duration": 2.0
    }))

    # Layer 2: Image (red square)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "img_layer", "type": "image",
        "asset_id": "img1", "duration": 1.0
    }))

    # Layer 3: Video (green frames)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "vid_layer", "type": "video",
        "asset_id": "vid1", "start": 1.0, "duration": 1.0
    }))

    # Layer 4: Text
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "txt_layer", "type": "text",
        "start": 0.5, "duration": 1.5,
        "props": {
            "text": "M1.1 Fidelity",
            "color": "#FFFF00",
            "font": {"size": 48}
        }
    }))

    # Add effect to image: blur + color grade
    doc = apply_layer_patch(doc, patch({
        "op": "add_effect", "layer_id": "img_layer",
        "effect": {
            "type": "gaussian_blur",
            "params": {"radius": 2.0},
            "enabled": True
        }
    }))

    # Add adjustment layer to darken everything below it
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "adj_darken", "type": "adjustment",
        "start": 1.5, "duration": 0.5,
        "effects": [{
            "type": "brightness",
            "params": {"value": -0.2},
            "enabled": True
        }]
    }))

    print("\nDocument structure:")
    print(f"  Canvas: {doc['canvas']['width']}x{doc['canvas']['height']} @ {doc['canvas']['fps']}fps")
    print(f"  Assets: {len(doc['assets'])}")
    print(f"  Root children: {len(doc['root'].get('children', []))}")

    # Compile and render
    print("\nCompiling to LayerStack...")
    stack = compile_to_layer_stack(doc)
    print(f"  Stack: {stack.width}x{stack.height}, {stack.total_frames} frames")

    # Render first frame
    print("Rendering frame 0...")
    frame0 = stack.render_frame(0)
    print(f"  Frame shape: {frame0.shape}, dtype: {frame0.dtype}")

    # Render frame at 0.5s (text present)
    print("Rendering frame 5 (text visible)...")
    frame5 = stack.render_frame(5)

    # Render frame at 1.0s (video present, image has blur)
    print("Rendering frame 10 (video + blur)...")
    frame10 = stack.render_frame(10)

    # Render to video file
    output_video = tmpdir / "demo.mp4"
    print(f"\nRendering full video to {output_video}...")
    stack.render_to_video(str(output_video), codec="mp4v")
    if output_video.exists():
        print(f"  Output: {output_video.stat().st_size} bytes")
    else:
        print(f"  Warning: Video file not found")

    # Save individual frame images for inspection
    frame0_u8 = (np.clip(frame0, 0, 1) * 255).astype(np.uint8)
    frame0_img = PILImage.fromarray(frame0_u8, "RGBA")
    frame0_path = tmpdir / "frame_0.png"
    frame0_img.save(frame0_path)
    print(f"\nSaved frame 0: {frame0_path}")

    frame5_u8 = (np.clip(frame5, 0, 1) * 255).astype(np.uint8)
    frame5_img = PILImage.fromarray(frame5_u8, "RGBA")
    frame5_path = tmpdir / "frame_5_text.png"
    frame5_img.save(frame5_path)
    print(f"Saved frame 5 (text): {frame5_path}")

    frame10_u8 = (np.clip(frame10, 0, 1) * 255).astype(np.uint8)
    frame10_img = PILImage.fromarray(frame10_u8, "RGBA")
    frame10_path = tmpdir / "frame_10_video_blur.png"
    frame10_img.save(frame10_path)
    print(f"Saved frame 10 (video+blur): {frame10_path}")

    # Verify content
    print("\nFrame content verification:")
    print(f"  Frame 0 pixel [120,160]: {frame0[120, 160]}")
    print(f"  Frame 5 (text region) max alpha: {frame5[..., 3].max():.3f}")
    print(f"  Frame 10 (video) green channel: {frame10[120, 160, 1]:.3f}")

    print("\n✅ M1.1 demo complete!")
    print(f"Demo output in: {tmpdir}")


if __name__ == "__main__":
    main()
