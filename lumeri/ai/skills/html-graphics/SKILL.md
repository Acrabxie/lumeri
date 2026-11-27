---
id: html-graphics
description: |
  用于字幕、标题卡、文字、lower third、HTML/Lottie overlay、MG 公式揭示和信息面板。何时不用我：只改时间长短用 timeline-ops；素材间过渡用 transition；Blender 空间三维效果用 blender-link。
triggers:
  primary: [字幕, 标题, 文字, 标题卡, lower third, lottie, html graphics, caption, subtitle, title]
  secondary: [动效, mg, motion graphics, 下三分之一, 信息面板, 文案, text, overlay]
primitives:
  - lumeri.video.subtitles.add_subtitle_track
  - lumeri.video.subtitles.add_text
  - lumeri.video.subtitles.auto_subtitle
  - lumeri.video.subtitles.add_lower_third
  - lumeri.video.animated_subtitles.render_ai_animated_subtitles_plan
  - lumeri.video.html_graphics.render_html_graphics_plan
  - lumeri.video.motion_graphics.render_mg_title_card
  - lumeri.video.motion_graphics.render_mg_formula_reveal
  - lumeri.video.motion_graphics.render_mg_process_diagram
est_tokens: 620
---

# html-graphics

## 何时使用
用户需要屏幕文字、字幕、标题动画、数据卡片、Lottie 或 HTML overlay 时使用。

## 参数说明
核心参数是 `text`、`style`、`position`、`duration`、`html_source`、`lottie_source`、`overlay_layers`。

## 典型 plan 片段
```json
{"id":"step_1","function":"lumeri.video.html_graphics.render_html_graphics_plan","args":{"text":"Lumeri","style":"lower_third"},"input":"$input","output":"$output"}
```

## 边界 / fallback
字幕文案缺失但音频可转写时用 `auto_subtitle`；完全不清楚字幕内容且无法转写时 ask。
