# ML model weights

These ONNX weights power `gemia.picture.matting` (the `edit_image`
`remove_background` op — real subject/portrait matting). They are **not** committed
(too large; see `.gitignore`). Fetch them once:

```bash
# U2Net human segmentation (portrait/person cutout) — ~176 MB
curl -L -o models/u2net_human_seg.onnx \
  https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx
```

Resolution order (first hit wins): `$LUMERI_MATTING_MODEL`,
`models/u2net_human_seg.onnx`, `models/u2net.onnx`,
`~/.cache/lumeri/u2net_human_seg.onnx`, `~/.u2net/u2net_human_seg.onnx`.

If no model is present, matting automatically falls back to a GrabCut +
face-anchored pipeline (lower quality on busy backgrounds, but fully offline and
never a hard failure). `matting.describe_backend()` reports which tier is live.

Optional env:
- `LUMERI_MATTING_COREML=1` — try the CoreML execution provider (Apple Silicon).
- `LUMERI_MATTING_THREADS=N` — cap onnxruntime intra-op threads.
