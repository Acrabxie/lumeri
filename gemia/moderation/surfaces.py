"""Which tool arguments get screened, and why that list stops where it does.

The line drawn here is between *generating* media and *typesetting* text.

Screened — the tool turns free text into a newly synthesized image, video, or
audio track. This is the AI-generated content the AUP makes promises about, and
the reason a payment processor asks about it at all.

Not screened — the tool renders text the user already wrote onto a frame
(subtitles, title cards, captions). That is closer to typing in a word
processor than to asking a model to produce something, and screening it would
misfire on exactly the serious material Lumeri should be good at: a subtitle
track for a film about the Holocaust or about racial violence contains the
words those films are about. Refusing to typeset a line the user wrote is not a
content-safety win.

If a new tool synthesizes media from a prompt, add it here. The consistency
test in ``tests/test_moderation.py`` fails when a tool declares a required
``prompt`` argument and is missing from this table, so the omission surfaces as
a red test rather than as a silent hole in enforcement.
"""
from __future__ import annotations

SCREENED_TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "generate_image": ("prompt",),
    "generate_video": ("prompt",),
    "generate_audio": ("prompt",),
    "narrate": ("text",),
}

__all__ = ["SCREENED_TOOL_ARGS"]
