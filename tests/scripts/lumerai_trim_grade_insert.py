import lumerai as lm

clip = lm.clip_load("demo.mp4")
trimmed = lm.clip_trim(clip, start=0.2, end=1.2)
graded = lm.clip_color_grade(trimmed, preset="warm", strength=0.7)
lm.timeline_insert(graded, at=0.0)
