"""gemia.audio — Audio primitive operations.

All functions work with float32 ndarrays [-1, 1].
"""
from gemia.audio.basics import load, save, trim, concat, mix
from gemia.audio.dynamics import normalize, compress, adjust_gain, lufs_normalize
from gemia.audio.frequency import eq, highpass, lowpass
from gemia.audio.time_pitch import time_stretch, pitch_shift, detect_bpm
from gemia.audio.analysis import silence_detect, beat_detect, music_extend, stem_separate, loudness_meter, audio_visualizer
from gemia.audio.effects import (
    voice_convert, auto_mix, ducker, voice_isolate, pitch_correction,
    dynamic_eq_match, level_matcher, spectral_denoise,
    remove_silence, speaker_separate, create_adr_cues,
    noise_gate, audio_compressor, stereo_widener, audio_reverb, audio_fade,
    audio_trim_silence, text_to_speech,
    audio_normalize_loudness, audio_pitch_shift_semitones,
    audio_mix_to_mono, audio_concat, audio_ducking, audio_speed,
    audio_volume, audio_equalizer,
)

__all__ = [
    "load", "save", "trim", "concat", "mix",
    "normalize", "compress", "adjust_gain",
    "eq", "highpass", "lowpass",
    "time_stretch", "pitch_shift", "detect_bpm",
    "silence_detect",
    "beat_detect", "music_extend", "stem_separate", "loudness_meter", "audio_visualizer",
    "voice_convert", "auto_mix", "ducker",
    "lufs_normalize",
    "voice_isolate",
    "pitch_correction",
    "dynamic_eq_match",
    "level_matcher",
    "spectral_denoise",
    "remove_silence", "speaker_separate", "create_adr_cues",
    "noise_gate", "audio_compressor", "stereo_widener", "audio_reverb", "audio_fade",
    "audio_trim_silence", "text_to_speech",
    "audio_normalize_loudness", "audio_pitch_shift_semitones",
    "audio_mix_to_mono", "audio_concat", "audio_ducking", "audio_speed",
    "audio_volume", "audio_equalizer",
]
