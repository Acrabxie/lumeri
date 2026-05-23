"""Micro-benchmarks for hot-path primitives optimised in this pass.

Run::

    python3 bench/profile_primitives.py

Prints best-of-N timings for LUT application, colorslice grading,
waveform monitor, and audio compressor. Useful for verifying that an
optimization actually moved the needle (compare baseline JSON against the
post-optimization run).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

# Allow running this script directly from the repo root without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gemia.audio.dynamics import compress  # noqa: E402
from gemia.picture.analysis import waveform_monitor  # noqa: E402
from gemia.picture.color import apply_lut, colorslice_grade  # noqa: E402


def best_of(fn, n: int = 5) -> float:
    timings = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        timings.append(time.perf_counter() - t0)
    return min(timings)


def main() -> None:
    results: dict[str, float] = {}

    rng = np.random.default_rng(42)
    img_4k = rng.random((2160, 3840, 3), dtype=np.float32)
    img_1080 = rng.random((1080, 1920, 3), dtype=np.float32)
    audio = rng.standard_normal(44100 * 5).astype(np.float32) * 0.3

    lut = np.linspace(0, 1, 256, dtype=np.float32)
    results["apply_lut_4k_ms"] = best_of(lambda: apply_lut(img_4k, lut=lut)) * 1000
    results["colorslice_grade_1080_ms"] = (
        best_of(
            lambda: colorslice_grade(
                img_1080,
                hue_adjustments={"red": (5.0, 1.2, 0.05), "blue": (-3.0, 0.9, 0.0)},
            )
        )
        * 1000
    )
    results["waveform_monitor_1080_ms"] = (
        best_of(lambda: waveform_monitor(img_1080)) * 1000
    )
    results["compress_5s_audio_ms"] = (
        best_of(lambda: compress(audio, sr=44100), n=3) * 1000
    )

    out_path = Path(__file__).parent / "last_run.json"
    out_path.write_text(json.dumps(results, indent=2))
    for name, ms in results.items():
        print(f"  {name:32s} {ms:8.2f} ms")
    print(f"\nWritten: {out_path}")


if __name__ == "__main__":
    main()
