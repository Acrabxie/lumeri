import lumerai as lm

clip = lm.clip_load("demo.mp4")
trimmed = lm.clip_trim(clip, start=0.2, end=1.2)
lm.timeline_insert(trimmed, at=0.0)
