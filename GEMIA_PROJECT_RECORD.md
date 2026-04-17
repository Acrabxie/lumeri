# Gemia 项目全记录

> 本文件为本机权威记录，跨会话/跨 Agent 均应以此为准。
> 最后更新：2026-04-17

---

## 一、项目定位

**Gemia = 把 AI 真正整合进视频/图片/音频工作流的工作台。**

不是剪辑软件——底层是可编程 Primitive API，AI（Gemini）读取 prompt → 生成执行计划 → 调用 primitive 函数 → 输出成品。

核心命题：Claude Code 让程序员的工作流被 AI 接管；Gemia 要对视频/图片/音频做同样的事，让每个人都能做出专业级内容。

---

## 二、仓库信息

| 项目 | 信息 |
|------|------|
| 本地路径 | `/Users/xiehaibo/Code/gemia/` |
| GitHub | https://github.com/Acrabxie/gemia（public, MIT） |
| 首次 push | 2026-04-04，commit `329db4f` |
| 当前最新 commit | Wave 105（`6bd1d16`） |
| 默认分支 | `main` |

---

## 三、架构（v2，当前）

```
User prompt
    ↓
Gemini 2.5 Flash（via OpenRouter）
    ↓
Plan v2 JSON（步骤列表）
    ↓
PlanEngine（gemia/engine.py）
    ↓
Primitives（picture / audio / video 函数库）
    ↓
Output 文件
```

### 核心模块

| 模块 | 职责 |
|------|------|
| `gemia/registry.py` | 自动发现所有 primitive，生成 AI catalog |
| `gemia/engine.py` | PlanEngine，执行 v2 plan，auto-bridge picture↔video，追踪 models_used |
| `gemia/ai/gemini_adapter.py` | OpenRouter → Gemini，含 Ask 机制（模糊 prompt 时 AI 主动提问） |
| `gemia/ai/generative_client.py` | Gemini 图像生成双后端（native + OpenRouter） |
| `gemia/ai/veo_client.py` | Veo 3.1 视频生成 via OpenRouter |
| `gemia/skill_store.py` | Skills v2，存档时记录 models_used + parameters |
| `gemia/__main__.py` | CLI：run / save-skill / list-skills / run-skill |
| `server.py` | Web server，GET/POST /config（API key 管理） |
| `gemia_server_entry.py` | PyInstaller 入口，读 ~/.gemia/config.json，patch 路径 |
| `tauri-app/` | Tauri v2 桌面端，打包 macOS DMG |

---

## 四、Primitive 函数库

每个 primitive 是一个纯 Python 函数，接受文件路径，返回 None（写出文件）。  
picture 函数用 PIL + numpy；audio/video 函数用 ffmpeg（带 fallback）。

### 当前规模（2026-04-17，Wave 105 后）

| 模块 | 文件 | 行数 | 函数数（估算） |
|------|------|------|----------------|
| `gemia/picture/enhance.py` | + 5 个子模块 | 4552 | ~170+ |
| `gemia/audio/effects.py` | + 5 个子模块 | 4039 | ~150+ |
| `gemia/video/effects.py` | + 7 个子模块 | 7521 | ~220+ |

> Wave 100 里程碑：三模块合计突破 500 函数。

### 波次（Wave）开发规则

每个 Wave 实现 **5 个函数**：2 picture + 1 audio + 2 video。

流程：
1. 在对应 `.py` 文件末尾追加函数体
2. 更新 `__init__.py`：import 行 + `__all__` 列表
3. 2× PASS 验证（`python3 -c "import inspect; ..."` 确认签名 + `__all__` 成员）
4. `git commit`（含 Co-Authored-By trailer）
5. 更新 `agent_log.md` 记录时间戳

---

## 五、已完成里程碑

1. **Primitive API** — 纯 Python 函数，120 个测试全绿
2. **Registry** — 自动发现 + `catalog_for_prompt()` 生成 Gemini system prompt
3. **PlanEngine** — v2 plan 执行，auto-bridging，models_used 追踪
4. **Ask 机制** — 模糊 prompt 触发 AI 提问，不猜直接问
5. **Skills 系统 v2** — save-skill / list-skills / run-skill，含 models_used + parameters
6. **CLI** — `python3 -m gemia run/save-skill/list-skills/run-skill`
7. **Nano Banana** — Gemini 图像生成：generate_image / edit_image / style_transfer / blend_images
8. **Veo 3.1** — 视频生成：generate_video / generate_video_from_image / extend_video
9. **GitHub 上线** — README + Roadmap
10. **Tauri macOS DMG** — `Gemia_0.1.0_aarch64.dmg`（138MB）
11. **Wave 100 里程碑** — 三模块 500+ primitive 函数
12. **Wave 105（当前）** — 持续扩库中

---

## 六、Wave 历史记录（最近 20 波）

| Wave | 日期时间 | 函数（picture / audio / video） |
|------|----------|---------------------------------|
| 86 | 2026-04-17 | image_pencil_color, image_selective_blur / audio_radio_effect / video_shake_cam, video_color_temperature |
| 87 | 2026-04-17 00:15 | image_light_leak, image_pixelate_grid / audio_crowd_ambience / video_zoom_blur, video_flash_cut |
| 88 | 2026-04-17 00:30 | image_frost, image_color_halftone / audio_pitch_octave_down / video_invert_colors, video_strobe |
| 89 | 2026-04-17 02:45 | image_relief, image_rainbow_gradient / audio_granular_freeze / video_pixelate_faces, video_speed_echo |
| 90 | 2026-04-17 02:58 | image_tilt_shift, image_diffuse_glow / audio_reverb_room / video_zoom_in_center, video_frame_hold |
| 91 | 2026-04-17 04:50 | image_stipple, image_color_burn_blend / audio_distortion / video_vhs_glitch, video_letterbox_blur |
| 92 | 2026-04-17 05:00 | image_noise_stipple, image_gradient_map / audio_chorus_stereo / video_split_tone, video_zoom_out_center |
| 93 | 2026-04-17 06:50 | image_cross_process, image_lomo / audio_wah_effect / video_push_transition, video_fade_to_white |
| 94 | 2026-04-17 07:05 | image_pixel_sort, image_mosaic_portrait / audio_pitch_vibrato / video_mirror_vertical, video_chromatic_shift |
| 95 | 2026-04-17 08:50 | image_watermark_logo, image_orton_effect / audio_tape_saturation / video_zoom_pulse, video_color_grade_lut |
| 96 | 2026-04-17 09:05 | image_scanline_art, image_color_overlay / audio_vinyl_pop / video_split_quad, video_text_lower_third |
| 97 | 2026-04-17 10:50 | image_warp_swirl, image_sketch_color / audio_flanger_jet / video_burn_in_timecode, video_slow_in_fast_out |
| 98 | 2026-04-17 11:05 | image_neon_outline, image_texture_overlay / audio_binaural_pan / video_color_crush, video_fast_in_slow_out |
| 99 | 2026-04-17 12:50 | image_color_shift_channels, image_glamour_glow / audio_sidechain_pump / video_dreamy_blur, video_rgb_parade |
| **100** | 2026-04-17 13:05 | image_kaleidoscope, image_vintage_photo / audio_stereo_imager / video_cinematic_bars, video_epic_slowmo — **500 函数里程碑** |
| 101 | 2026-04-17 14:50 | image_paint_strokes, image_morning_haze / audio_spatial_reverb / video_freeze_zoom, video_duotone |
| 102 | 2026-04-17 15:05 | image_color_relief, image_glitter / audio_pitch_glide / video_infrared, video_retrowave |
| 103 | 2026-04-17 15:20 | image_watercolor_light, image_solarize_color / audio_granular_pitch / video_color_fade_out, video_zoom_letters |
| 104 | 2026-04-17 15:35 | image_pixel_wave, image_crystallize / audio_vinyl_hiss / video_glitch_rgb, video_vignette_focus |
| 105 | 2026-04-17 15:50 | image_comic_dots, image_thermal / audio_cb_radio / video_mirror_time, video_color_shift_time |

完整历史见 `agent_log.md`。

---

## 七、环境与依赖

```
Python 3.12+
ffmpeg（必须在 PATH 中）
PIL / Pillow
numpy
OPENROUTER_API_KEY（必填）
GEMINI_API_KEY（可选，用于 native 图像生成）
默认模型：google/gemini-2.5-flash
PyInstaller 6.19.0（打包用）
Tauri v2 / Rust 1.93.1（桌面端）
```

---

## 八、关键 Bug 修复记录

| Bug | 原因 | 修复 |
|-----|------|------|
| 系统代理劫持 | WebView fetch() 被 localhost:7890 拦截 | 改为 Rust invoke + reqwest no_proxy |
| 端口冲突崩溃 | 重启时旧 sidecar 占用 7788 | gemia_server_entry.py 先检测端口，占用则 exit(0) |
| workspace 路径错误 | frozen binary __file__ 指向 /tmp/_MEI | 加载 server 模块后 patch 所有路径到 ~/.gemia/workspace/ |
| 401 auth error | 调试时测试 config key="test" 跳过 modal | config_exists() 加校验，启动时自动读 config |
| 首次 modal 存 key 后仍 401 | sidecar 启动时 env 为空，modal save 只写文件 | save_config 改为 async，写文件后 POST /config 同步到 sidecar |

---

## 九、Tauri 桌面端架构

```
sidecar:  gemia_server_entry.py → PyInstaller → binaries/gemia-server-aarch64-apple-darwin
前端通信: JS invoke() → Rust api_call/upload_video/fetch_video_b64 → reqwest no_proxy → Python :7788
配置:     ~/.gemia/config.json（API keys）
workspace: ~/.gemia/workspace/
DMG:      tauri-app/src-tauri/target/release/bundle/dmg/Gemia_0.1.0_aarch64.dmg（138MB）
```

---

## 十、Roadmap（待完成）

- [ ] Skills UI — Web 界面可视化 Skill 浏览器
- [ ] Tauri 桌面端完善 — 视频预览流畅 + 进度条
- [ ] Nano Banana E2E 验证 — 真实 style_transfer on video
- [ ] Primitive 函数库持续扩张（当前 Wave 105，目标 200+ Wave）
- [ ] Registry 自动同步最新函数到 AI catalog

---

## 十一、Agent 分工（2026-04-17 起）

| Agent | 职责 |
|-------|------|
| **Codex** | Primitive 函数批量实现（Wave 开发主力），每波 5 函数，2×PASS 验证，git commit，写 agent_log.md |
| **Claude（本机）** | 核心架构设计、引擎逻辑、AI 集成、复杂 bug 修复、Roadmap 决策 |

### Codex 执行规范

Codex 每次执行一个 Wave，流程如下：

1. 检查 `agent_log.md` 最后时间戳，确认间隔 > 1.5h（或被明确触发）
2. 查 TaskList 找 in_progress / pending 任务；如无则创建 Wave N+1 的 5 个 Task
3. 在对应文件末尾追加函数实现：
   - picture → `gemia/picture/enhance.py`
   - audio → `gemia/audio/effects.py`
   - video → `gemia/video/effects.py`
4. 更新三个 `__init__.py`（import 行追加名称 + `__all__` 追加字符串）
5. 2× PASS 验证：`python3 -c "import inspect; from gemia.X import fn; assert fn.__name__ in mod.__all__; print('PASSn', inspect.signature(fn))"`
6. `git add` 涉及的 6 个文件，`git commit`（含 Co-Authored-By: Claude Sonnet 4.6）
7. 标记所有 Task 为 completed
8. `echo "时间戳 Wave N complete: ..." >> agent_log.md`
9. 立即开始下一个 Wave，不等待

### 函数实现规范

- **picture 函数**：只用 PIL + numpy，无 OpenCV 硬依赖
- **audio 函数**：ffmpeg subprocess，returncode != 0 时有 fallback
- **video 函数**：ffmpeg subprocess，returncode != 0 时有 fallback（更简单的滤镜或 PIL 逐帧）
- 所有函数签名：`(input_path: "str", output_path: "str", *, param: "type" = default) -> "None"`
- 不重复实现已有函数（查 `__all__` 确认）

---

*本文件由 Claude 生成并维护，Codex 可直接读取作为上下文。*
