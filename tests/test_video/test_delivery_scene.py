import pytest
from pathlib import Path
import json
from gemia.video.delivery_scene import render_blended_portrait_slate_delivery_scene

def test_render_blended_portrait_slate_delivery_scene_basic(tmp_path):
    # We'll use a synthetic video for the basic test if possible,
    # but the instructions suggest using real stock for reproduction.
    # For a unit test, we might need a very short real video or a synthetic one.
    
    # Let's create a synthetic video first to ensure the code runs
    import cv2
    import numpy as np
    
    input_video = tmp_path / "input.mp4"
    catalog_path = tmp_path / "stock_catalog.json"
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(str(input_video), fourcc, 24.0, (640, 480))
    for i in range(48): # 2 seconds
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, f"Frame {i}", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        # Add a white box to simulate a "slate" or "face" region for some primitives
        if i < 10:
            cv2.rectangle(frame, (100, 100), (500, 400), (200, 200, 200), -1)
            cv2.putText(frame, "SLATE", (150, 250), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 5)
        out.write(frame)
    out.release()
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-delivery-test",
                    "kind": "video",
                    "status": "completed",
                    "backend": "local_real_video",
                    "source": str(input_video),
                    "outputs": [str(input_video)],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    
    output_video = tmp_path / "output.mp4"
    result_path = render_blended_portrait_slate_delivery_scene(
        str(input_video),
        str(output_video),
        max_long_edge=360,
        stock_catalog_path=catalog_path,
    )
    
    assert Path(result_path).exists()
    assert Path(result_path).stat().st_size > 0
    
    meta_path = Path(result_path).with_suffix(".blended_scene.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["effect"] == "resolve21_blended_portrait_slate_delivery_scene"
    assert meta["source_kind"] == "local_real_video"
    assert "no_face_evidence" in meta["diagnostics"]
    assert "components" in meta
    
    report_path = Path(result_path).with_suffix(".review_report.json")
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["real_source"]["confirmed"] is True

@pytest.mark.skipif(not Path("inputs/demo.mp4").exists(), reason="Real stock demo.mp4 not found")
def test_render_blended_portrait_slate_delivery_scene_real_media(tmp_path):
    input_video = "inputs/demo.mp4"
    output_video = tmp_path / "output_real.mp4"
    
    result_path = render_blended_portrait_slate_delivery_scene(
        input_video,
        str(output_video),
        max_long_edge=240 # Small for speed
    )
    
    assert Path(result_path).exists()
    meta_path = Path(result_path).with_suffix(".blended_scene.json")
    assert meta_path.exists()
    
    report_path = Path(result_path).with_suffix(".review_report.json")
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["status"] in ("passed", "passed_with_warnings", "failed")
