import lumerai as lm

state = lm.timeline_state()
target = state["timeline"]["clips"][0]
clip = lm.clip_load(target["id"])
graded = lm.clip_color_grade(clip, preset="warm", strength=1.0)
lm.timeline_replace(target["id"], graded)
