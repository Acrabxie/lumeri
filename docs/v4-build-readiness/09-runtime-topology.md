# 09 — Lumeri 运行环境拓扑

> Date: 2026-05-30
> 只读诊断。每条结论附实际命令输出或文件路径。
> 查不准的标注 "需实地运行确认"。

---

## TL;DR

**当前生产形态**:macOS LaunchAgent → bash 启动脚本 → 系统 Python 3.14
→ `python3 -m gemia server --host 0.0.0.0 --port 7788` → BaseHTTPServer
监听 7788 → 用户在浏览器打开 `http://127.0.0.1:7788`。**没有 Tauri 在跑,
没有任何隔离**,Python 进程以 Acrab 自己的用户身份拥有完整文件系统/网络
权限。ffmpeg 由 Python `subprocess.run` 直接 spawn 为子进程,通过 PATH
查找。

**未来 Tauri build 形态**(binary 已存在但未投产):PyInstaller 把
server 打包成 140MB `gemia-server` 单文件二进制,Tauri Rust `setup()` 用
`app.shell().sidecar()` spawn 它,webview 走 `api_call` Tauri command 转发
到 127.0.0.1:7788。**当前 `gemia-server` binary 时间是 2026-04-05,7 周
前,不含批次 1 verb 也不含 generate_image。**

**对 v4 build 的关键判断**:今天模型写的代码如果直接 `exec` 跑,**就是
跑在 server.py 同一个 Python 进程里,Acrab 的真实 macOS,真实文件系统,
真实权限**。`creative_sandbox_runner.py` + `lumerai/sandbox.py` 的隔离
代码 **存在但在 v3 路径上未被使用** — 仅旧的 `/run-skill` 端点和被废弃
的 `/next` runtime_vnext UI 调用。

---

## 1. server.py 怎么启动的

**不是手动**,**不是 Tauri spawn**,是 **LaunchAgent**。

### 1.1 LaunchAgent plist

```
$ ls ~/Library/LaunchAgents/ | grep gemia
com.gemia.five-day-loop.plist
com.gemia.sidecar.plist
com.gemia.sidecar-watchdog.plist
```

`com.gemia.sidecar` 是主服务:

```
$ plutil -p ~/Library/LaunchAgents/com.gemia.sidecar.plist
{
  "EnvironmentVariables" => {
    "no_proxy" => "*"
    "NO_PROXY" => "*"
    "PATH" => "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    "PYTHONPATH" => "/Volumes/Extreme SSD/gemia"
  }
  "KeepAlive" => true
  "Label" => "com.gemia.sidecar"
  "ProgramArguments" => [
    0 => "/bin/bash"
    1 => "/Users/xiehaibo/.gemia/server/bin/run-7788.sh"
  ]
  "RunAtLoad" => true
  "StandardErrorPath" => "/Users/xiehaibo/.gemia/server/logs/sidecar.err.log"
  "StandardOutPath" => "/Users/xiehaibo/.gemia/server/logs/sidecar.out.log"
  "WorkingDirectory" => "/Users/xiehaibo"
}
```

关键字段:
- `KeepAlive: true` + `RunAtLoad: true` → 开机/登录后自动起,死了 launchd 复活
- `PYTHONPATH=/Volumes/Extreme SSD/gemia` → 直接指向外置硬盘源码,不是安装的包
- `PATH` 不含 user 自己的 ~/.local 等;只 homebrew + 系统 → ffmpeg 走 `/opt/homebrew/bin/ffmpeg`

### 1.2 启动脚本(实际"./server.py 怎么跑起来")

```
$ cat /Users/xiehaibo/.gemia/server/bin/run-7788.sh
#!/bin/bash
set -euo pipefail
GEMIA_DIR="/Volumes/Extreme SSD/gemia"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PYTHONPATH="$GEMIA_DIR${PYTHONPATH:+:$PYTHONPATH}"
export GEMIA_INPUT_TXT_LOG="1"
export GEMIA_INPUT_TXT_DIR="$HOME/Desktop/Lumeri Gemini Inputs"
export NO_PROXY="*"
export no_proxy="*"
export HTTP_PROXY=""
export HTTPS_PROXY=""
export ALL_PROXY=""
export http_proxy=""
export https_proxy=""
export all_proxy=""
export GEMIA_HOST="${GEMIA_HOST:-0.0.0.0}"
export GEMIA_PORT="${GEMIA_PORT:-7788}"
ulimit -n 4096 2>/dev/null || true
cd "$GEMIA_DIR"
exec /opt/homebrew/bin/python3 -m gemia server --host "$GEMIA_HOST" --port "$GEMIA_PORT"
```

**关键观察:**
1. **proxy env 被显式清空**(`HTTP_PROXY=""` 等 + `NO_PROXY="*"`)。任何依赖 `HTTPS_PROXY` 环境变量做 outbound HTTP 的库都拿不到代理。
2. **`ulimit -n 4096`** — 提高 fd 上限(默认 256,看到的注释说 browser reload 会撑爆)
3. **`exec`** 替换进程,无中间 bash 进程 → `ps` 看到的 PID 就是 Python 本身
4. **`-m gemia server`** 是 entrypoint(不是直接 `python3 server.py`)

### 1.3 `python3 -m gemia server` → server.py

`gemia/__main__.py:325-335` 路由:
```python
elif args.command == "server":
    import importlib.util, pathlib
    _spec = importlib.util.spec_from_file_location(
        "gemia_server",
        pathlib.Path(__file__).parent.parent / "server.py",
    )
    _srv = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_srv)
    _srv.main(host=args.host, port=args.port)
```

→ 用 `importlib` 动态加载 `server.py`(因为 server.py 在 repo root,不是
package 内),调 `server.main(host, port)`(`server.py:5289`)。

### 1.4 Watchdog

```
$ plutil -p ~/Library/LaunchAgents/com.gemia.sidecar-watchdog.plist
{
  ...
  "ProgramArguments" => ["/bin/bash", "/Users/xiehaibo/.gemia/server/bin/ensure-7788.sh"]
  "StartInterval" => 60
}
```

每 60s 跑一次 `ensure-7788.sh`(`/Users/xiehaibo/.gemia/server/bin/ensure-7788.sh`):
- `curl --max-time 3 http://127.0.0.1:7788/` + `/config` → 都返回 2xx 算健康
- 不健康 → `launchctl kickstart -k gui/<uid>/com.gemia.sidecar` 重启
- 10 次 2s sleep retry,仍不健康 → 退出 1 留日志

**意思:即使 server 崩了,60s 内会被打捞回来。** 对 v3-A 持续可用有真实
保护。

### 1.5 第三个 LaunchAgent(不属于 server 范畴但要知道)

`com.gemia.five-day-loop` — 跑 `/Users/xiehaibo/.gemia/automation/bin/run_gemia_controller.sh run-supervisor --duration-days 5 --poll-sec 300`。

这是独立的 5 天自动化循环(MEMORY.md 提过 `loop_controller.py`),**与 v3
server 无关,不会同 server 抢端口**,但也是 Acrab 机器上长期占资源的进程。

---

## 2. 实际运行时进程树

server 已在跑,**PID 95183**,从周三 12 PM 起 ~2.5 天没死:

```
$ ps auxww | grep "gemia server" | grep -v grep
xiehaibo  95183  Wed12PM  0:31.55  /opt/homebrew/Cellar/python@3.14/3.14.3_1/...Resources/Python.app/Contents/MacOS/Python -m gemia server --host 0.0.0.0 --port 7788

$ lsof -iTCP:7788 -sTCP:LISTEN
COMMAND   PID     USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
Python  95183 xiehaibo    3u  IPv4 ...                    0t0  TCP *:7788 (LISTEN)
```

**ffmpeg 子进程:** 探针时没有 active turn,所以 95183 当前无子进程。
模式上(从代码确认 `gemia/tools/_ffmpeg.py:118` 用 `subprocess.Popen`):
**每次 dispatcher 跑 ffmpeg = `subprocess.Popen(["ffmpeg", ...])` spawn
一个子进程,parent PID = 95183**。需实地运行确认完整子进程树:可在
turn 进行时跑 `ps -o pid,ppid,command -A | awk '$2==95183'`。

**ffmpeg 子进程死的方式:** ffmpeg 自然退出 / 失败 / Python 显式 kill
(`agent_loop_v3.py` 的 cancel path 没有显式 kill 全部 ffmpeg child)。
没看到 `Popen.kill()` 调用 — turn cancel 后已 spawn 的 ffmpeg 继续跑到完。
**这是一个小风险:批次 1 verb 跑长任务时 turn 被取消,ffmpeg 不停。**
需实地运行确认。

---

## 3. Python 环境

```
$ which python3
/opt/homebrew/bin/python3

$ python3 -c "import sys; print(sys.prefix); print(sys.executable); print(sys.version)"
/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14
/opt/homebrew/opt/python@3.14/bin/python3.14
3.14.3 (main, Feb  3 2026, 15:32:20) [Clang 17.0.0 (clang-1700.6.3.2)]

$ python3 -c "import sys; print('venv:', sys.prefix != sys.base_prefix)"
venv: False
```

**裸 Homebrew Python 3.14.3,无 venv,无 conda。**

`site-packages` 在 `/opt/homebrew/lib/python3.14/site-packages/`(188 entries):
```
$ ls /opt/homebrew/lib/python3.14/site-packages/ | wc -l
188

# 关键依赖实测:
numpy:    2.4.2
httpx:    0.28.1
requests: 2.32.5
certifi:  2026.01.04

# 报错的(未装):pillow, opencv-python, google-generativeai, openai
# 注:Pillow 装了但 import 名是 PIL;cv2 同理 —— 上面 probe 命令的
# import 名映射有误。需实地运行确认 cv2/PIL 是否真装。
```

---

## 4. 依赖装在哪 + 怎么定义的

**Manifest:** `pyproject.toml`(repo root,setuptools-based):

```toml
[project]
name = "lumeri"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "numpy>=2.0",
    "Pillow>=11.0",
    "opencv-python>=4.9",
    "scikit-image>=0.22",
    "librosa>=0.10",
    "pydub>=0.25",
    "soundfile>=0.12",
    "scipy>=1.12",
    "certifi",
    "PyYAML>=6",
]

[project.scripts]
lumeri-skill-stats = "gemia.ai.skill_telemetry:main"
```

**装在哪:** 跑 server 的 Python 是 `/opt/homebrew/opt/python@3.14`,
所有依赖装在 `/opt/homebrew/lib/python3.14/site-packages/`。**没有
`requirements.txt`,没有 `pip install -e .` 锁定 — 是 ad-hoc `pip
install <name>` 的结果**(否则会有 lockfile)。

**意思:**
- 任何 `pip install` 都直接写进系统 Python 的 site-packages
- 没有 venv 隔离,其他项目装了 conflicting numpy/scipy 会污染 server
- 升级 macOS / Homebrew 时如果 python@3.14 被替换成 3.15,所有依赖要重装

---

## 5. ffmpeg 哪来、版本、怎么被调用

```
$ which ffmpeg
/opt/homebrew/bin/ffmpeg

$ ffmpeg -version | head -1
ffmpeg version 8.1.1 Copyright (c) 2000-2026 the FFmpeg developers

# 关键 build flags(列出仅相关编解码):
configuration: --enable-gpl --enable-libaom --enable-libdav1d --enable-libharfbuzz
  --enable-libmp3lame --enable-libopus --enable-libsnappy --enable-libsvtav1
  --enable-libtheora --enable-libvorbis --enable-libvpx --enable-libx264
  --enable-libx265 --enable-libfontconfig --enable-libfreetype --enable-frei0r
  --enable-libass --enable-demuxer=dash --enable-neon --enable-opencl
  --enable-audiotoolbox --enable-videotoolbox
```

Homebrew 装的 ffmpeg 8.1.1,**含 GPL+x264/x265+VideoToolbox 硬解硬编**。

**怎么调用:** 全部走 PATH,**不写死路径**。

```
$ grep -rn "subprocess.*\"ffmpeg\"" gemia/tools/_ffmpeg.py
# (empty — 字符串拼接形式不会被这条正则命中)
```

实际看 `gemia/tools/_ffmpeg.py:118`:
```python
proc = await loop.run_in_executor(
    None,
    lambda: subprocess.Popen(
        full,                         # full[0] == "ffmpeg" — 裸字符串
        ...
    ),
)
```

`full` 是 dispatcher 构造的 argv,`full[0] == "ffmpeg"` 是裸字符串。
**`subprocess.Popen` 用 PATH 查找。** PATH 由 LaunchAgent (`run-7788.sh`)
设成 `/opt/homebrew/bin:...`,所以解析到 `/opt/homebrew/bin/ffmpeg` 8.1.1。

**对 v4 build 的影响:**
- 如果 Acrab 把 ffmpeg 升级到 9.x 或换路径,行为可能变(filter 兼容性)
- 如果给 sandbox-exec 写 `(allow process-exec ...)` 必须明确路径,**不能用 PATH 模糊解析**(claude-02 已提到)
- PyInstaller bundle 是否能把 ffmpeg 一起打进去?**没有 — 看 .spec 没把 ffmpeg 拉进去**,所以 Tauri 部署到没装 Homebrew 的用户机上会立刻 `ffmpeg: command not found`

---

## 6. 还有哪些系统级外部依赖

```
$ grep -rn "subprocess\.\(Popen\|run\|check_output\)" gemia/ server.py | grep -v test | wc -l
# 数十处 subprocess 调用

$ grep -rEoh "subprocess[^(]*\([^)]*\"(curl|git|wget|brew|node|npm|sh|bash|open|otool|file|sandbox-exec|sips|pngcrush|exiftool|ImageMagick|convert)" gemia/ server.py | sort -u
# (empty — 没有匹配)

$ grep -rn "shell=True\|os\.system\|os\.popen" gemia/ server.py | grep -v test
# (empty)
```

**结论:**
- **仅 ffmpeg / ffprobe**(两个都是 Homebrew 同包)
- **没有 `shell=True`** — 全部 argv list 调用,没有 shell injection 面
- **没有 `os.system` / `os.popen`** — 一处都没
- **没有调 curl / git / wget / brew / node / npm / sh / bash 等**(从 gemia/ + server.py 路径下)

外部 Python 库做的网络调用(`urllib.request.urlopen`):见 claude-01 §6
列了 4 个文件(fonts/stock_media/generative/layer_flow)。

**注意:** v3 routes 还引用 `git`?需实地运行确认 — 但当前 grep 没找到。

---

## 7. Tauri ↔ Python 通信(规划 vs 现实)

### 7.1 Tauri 配置(`tauri-app/src-tauri/tauri.conf.json`)

```json
{
  "productName": "Gemia",
  "identifier": "ai.gemia.app",
  "build": {
    "frontendDist": "../dist",
    "devUrl": "http://localhost:1420"
  },
  "app": {
    "withGlobalTauri": true,
    "security": {
      "csp": "...connect-src 'self' http://localhost:7788 http://127.0.0.1:7788..."
    }
  },
  "bundle": {
    "active": true,
    "targets": "dmg",
    "macOS": {"minimumSystemVersion": "13.0"},
    "externalBin": ["binaries/gemia-server"]
  }
}
```

**`externalBin: ["binaries/gemia-server"]`** — Tauri 把这个 binary 当
sidecar 打进 .app bundle。

### 7.2 Tauri Rust 怎么 spawn sidecar(`tauri-app/src-tauri/src/lib.rs`)

```rust
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        ...
        .setup(|app| {
            match app.shell().sidecar("gemia-server") {
                Ok(cmd) => match cmd.spawn() {
                    Ok((_rx, child)) => {
                        *state.0.lock().unwrap() = Some(child);
                        println!("gemia-server sidecar started on :7788");
                    }
                    ...
                }
            }
            Ok(())
        })
        ...
}
```

**Tauri 启动时 spawn `gemia-server` binary,持有 child handle,负责生命周期。** Webview 用 `api_call` Tauri command 转 HTTP 到 `http://127.0.0.1:7788`。

通信形态:
- **HTTP-only,基于 reqwest 客户端**(`reqwest::Client::builder().no_proxy()`)
- **proxy 同样被 Tauri 显式禁掉**(`.no_proxy()` 调用)— 与 LaunchAgent 一致
- 所有功能(api_call / upload_media / fetch_video_b64 / save_config / get_config / reveal_in_finder / open_url)都是 thin HTTP 转发或 OS 调用
- **配置 key 字段是 `openrouter_api_key` 和 `gemini_api_key`** — Tauri 的 `save_config` 命令只写这两个,**没有 `gemini_studio_api_key`**(批次 2.1 新引入的字段)。Tauri 一旦投产,UI 需要新增字段。

### 7.3 当前真实运行状态:Tauri **没有在跑**

```
$ ps auxww | grep -E "tauri|webkit|Gemia\.app" | grep -v grep
# (no matches related to Gemia)
```

`gemia-server` binary 时间:

```
$ ls -la dist/gemia-server tauri-app/src-tauri/binaries/gemia-server-aarch64-apple-darwin
-rwx------  1 xiehaibo  staff  140956128 Apr  5 08:04 dist/gemia-server
-rwx------  1 xiehaibo  staff  140956128 Apr  5 08:05 tauri-app/src-tauri/binaries/gemia-server-aarch64-apple-darwin
```

**两份都是 2026-04-05,7 周前。** PyInstaller spec (`gemia-server.spec`)
里 `hiddenimports` 列的是 v2 模块(`gemia.ai.gemini_adapter`, `gemia.ai.ai_client`,
`gemia.ai.generative_client`, `gemia.ai.veo_client`, `gemia.engine`, `gemia.skill_store`,
`gemia.orchestrator`)— **完全没列 v3 模块**(`gemia.agent_loop_v3`,
`gemia.tools.*`,`gemia.session_telemetry` 等)。这个 binary 跑起来不会有
v3 verb,不会有批次 1,不会有 generate_image。

**如果要走 Tauri:必须重打 PyInstaller,把所有 v3 模块加 hiddenimports,
并把 ffmpeg 一起打进去**(目前 spec 没拉 ffmpeg)。

---

## 8. 用户实际启动 Lumeri 的入口

**今天:** 用户**不做任何事**。LaunchAgent `RunAtLoad: true` → 开机/登录就起。Acrab 想看就开浏览器 `http://127.0.0.1:7788` 或 `/v3`。

**没有.app 在 Applications/:**

```
$ find . -maxdepth 4 -name "*.app" -not -path "*/node_modules/*" 2>/dev/null
# (no matches)
$ ls -la tauri-app/src-tauri/target/release/bundle/macos/
# (empty — 没 .app)
```

**没有 DMG 在 dist/:**

```
$ ls -la dist/
total 280576
drwx------  ...   .
drwx------  ...   ..
-rwx------  1 xiehaibo  staff  140956128 Apr  5 08:04 gemia-server   # 只有 PyInstaller 单文件
```

→ **当前没有走 Tauri/DMG 的入口**。Tauri 这套是规划但未投产。

---

## 9. v4 build 时模型代码会跑在哪一层

**今天的真实回答:**

| 选择 | 跑在哪 | 文件系统访问 | 网络访问 | 进程权限 |
|---|---|---|---|---|
| **(a) 直接 `exec(model_code)` 在 dispatcher 里** | server.py 同一 Python 进程 (PID 95183) | 完整 macOS,Acrab uid,**包括 ~/.gemia/config.json / ~/.ssh / iCloud / ...** | 完整,可调任何 HTTP | 完整 Acrab 用户权限 |
| **(b) `subprocess.run([sys.executable, "-c", model_code])`** | 子进程,但 **相同 uid + 默认 cwd + 完整 PATH** | 完整(macOS sandbox 无介入,因为父进程未 entitle) | 完整 | 完整 |
| **(c) 走 `lumerai/sandbox.py:execute_script`** | 子进程 + `RLIMIT_CPU` + `RLIMIT_AS=2GB` + AST 白名单 | **未拦** — RLIMIT 不拦 FS,AST 白名单是软的 | **未拦** — 同上 | 完整 |
| **(d) 走 `gemia/creative_sandbox_runner.py`** | 子进程 + **macOS `sandbox-exec` 包装**(profile 真的拦 FS/网络) | 拦到 workspace + declared paths | 默认 deny | 完整(但 sandbox-exec 限定 syscall) |

**(c) 与 (d) 都存在但 v3 路径上未使用:**

```
$ grep -rn "sandbox-exec\|RLIMIT_\|setrlimit\|preexec_fn" gemia/ server.py | grep -v test
gemia/creative_sandbox_runner.py:63:    ``sandbox-exec`` when available...
gemia/creative_sandbox_runner.py:265:    sandbox_exec = shutil.which("sandbox-exec")
```

只有 `creative_sandbox_runner.py` 命中,且这文件只被 `server.py:/run-skill`
端点和 `runtime_vnext.py` 调用 — **agent_loop_v3 + DISPATCHER + 所有 11 个
batch-0/1 verb 都 NOT 通过 sandbox-exec**。

`lumerai/sandbox.py` (`RLIMIT_CPU` + AST 白名单):同样只被 `runtime_vnext`
调用,也不在 v3 路径。

**所以 v4 build 选 (a) 直接 exec,模型代码可以:**
- 读 `~/.gemia/config.json`(OpenRouter key, Pexels key, Pixabay key, Google OAuth, Gemini Studio key 都在)
- 读 `~/.ssh/`
- `urllib.request.urlopen` 到任何 URL,把上面的 key 发出去
- `os.unlink` 任何文件
- `subprocess.Popen(["rm", "-rf", str(Path.home())])`(夸张,但允许)

**没有任何防线。** 模型不是恶意,但写错代码 / 被 prompt 注入诱导能造成真损失。

---

## 10. 当前环境的现成隔离

```
当前 v3 agent_loop → DISPATCHER → 11 个 dispatcher
                       ↓
              subprocess.Popen([ffmpeg, ...])
                       ↓
              ffmpeg 子进程,uid=xiehaibo,无 sandbox
```

**已有但未使用的隔离基础设施:**

| 设施 | 文件 | 当前用法 | 强度 |
|---|---|---|---|
| `lumerai/sandbox.execute_script` | `lumerai/sandbox.py:80-180` | `runtime_vnext.py` `/next` UI(被废弃) | RLIMIT_CPU + RLIMIT_AS + AST 白名单,**无 FS/网络隔离** |
| `gemia/creative_sandbox_runner` | `creative_sandbox_runner.py:265-313` | `server.py:/run-skill`(v2 端点,基本未投产) | **真 sandbox-exec**(macOS TrustedBSD),`(deny default)` + `(allow file-write* SESSION_DIR)` + `(deny network*)` |
| Python entitlements | `/opt/homebrew/bin/python3` | LaunchAgent 跑 | 无特殊 entitle,标准用户进程 |
| Tauri App Sandbox | 未投产 | — | 未启用 — `tauri.conf.json` 没设 macOS entitlements 文件,Tauri build 是 unsandboxed |

**`creative_sandbox_runner.py` 的 sandbox-exec profile 实际能用:**

```
$ /usr/bin/sandbox-exec -p "(version 1)(allow default)" /usr/bin/true && echo ok
ok
```

→ macOS `sandbox-exec` 工具存在且可执行,profile 验证通过。

**对 v4 build 的现实结论:**
- 当前 v3 路径完全没隔离
- 现成的隔离代码(sandbox-exec + RLIMIT)有,但只挂在 v2 / vNext 死路径上
- **v4 build 需要:把模型代码路径接到 `creative_sandbox_runner` 或新写一个等价层** —— claude-02 已论证选 `creative_sandbox_runner` 复用最经济
- LaunchAgent 进程的 entitlement 是裸 Homebrew Python,无 hardened runtime / sandbox / SIP 介入 — sandbox-exec 是仅有的真防线

---

## 11. 给 Opus 阶段的硬约束清单

1. **v4 build 上线前,必须把 v3 agent loop 接进 `creative_sandbox_runner`(或同等 sandbox-exec wrapper)。** 直接 exec 模型代码 = 把 Acrab 的 key + 家目录 + iCloud 暴露给 Gemini 的输出。
2. **PyInstaller spec 必须重做** — 当前 `gemia-server.spec` 的 `hiddenimports` 列的是 v2 模块,没列 v3。要 Tauri ship 必须更新 spec + 把 ffmpeg 一起打进去。
3. **`gemini_studio_api_key` 字段** — Tauri 的 `save_config` 还只写 `openrouter_api_key` 和 `gemini_api_key`,投产前要扩 UI + Rust command。
4. **proxy env vs config.json:proxy 双轨**:`run-7788.sh` 清空 env proxy,但代码读 `config.json:proxy`。任何依赖 `HTTPS_PROXY` env var 默认行为的库会在 LaunchAgent 下行为变化 — 批次 2 真调 AI Studio 时这是潜在坑(`google_genai_client.py` 显式读 config.json 走过,但其他路径若用 `requests`/`httpx` 默认 env-aware,会绕过 config 配的代理)。
5. **watchdog kickstart 节奏**:60s 一次健康检查,意思是 server 崩了最长 60s + 启动时间才恢复。批次 2 真烧钱场景里 60s 间隔可能掩盖快速失败循环。需实地运行确认 watchdog 是否给 backoff。
6. **没有依赖锁定**:`pyproject.toml` 只声明 `>=` 边界,没 lockfile。numpy 2.4→2.5 之类升级可能默默改 batch-1 verb 的输出。`pip freeze > requirements-runtime.lock` 是 1 分钟的事,Lumeri 1.0 ship 前应该做。
7. **批次 2.1 部署影响**:`generate_image` 现已落地,但 LaunchAgent 跑的是 `python3 -m gemia server` 即时加载源码,**新文件 `gemia/ai/google_genai_client.py` 和 `gemia/tools/generate_image.py` 会被自动加载** —— **不需要重启 server。** 但 `DISPATCHER` 已在 server 启动时构造完(`gemia/tools/__init__.py` 顶层代码),所以 **新加的 dispatcher 注册需要重启 server 才生效**:`launchctl kickstart -k gui/$(id -u)/com.gemia.sidecar`。

---

## 12. 需实地运行确认的事项

非阻塞但应在批次 2 真调前补:

1. **批次 1 verb 跑长任务时 turn cancel,ffmpeg 子进程是否被 kill?**
   - 实测:跑一个 60s 的 `transform_geometry rotate`,turn 中途断浏览器/触发 max_tool_steps cap → `ps -o pid,ppid,command -A | grep ffmpeg` 看父 PID 是否还指向 95183。
2. **Pillow / cv2 当前是否装在 system Python?**
   - 上面探测脚本的 import 名映射出错。`python3 -c "import PIL; print(PIL.__version__)"` 和 `python3 -c "import cv2; print(cv2.__version__)"` 跑一遍确认。
3. **watchdog 重启的 backoff 行为**
   - 实测:`launchctl kill TERM gui/$(id -u)/com.gemia.sidecar` 杀掉,看 watchdog 多快拉起 + 是否累计指数 backoff。
4. **`gemia-server` PyInstaller 单文件能否运行**
   - `./dist/gemia-server --port 7790`(避开 7788)试启,如果 import error 说明 v2-only hiddenimports 已不够。

---

## 13. 一图概括

```
┌──────────────────────────────────────────────────────────────────────┐
│  TODAY (LaunchAgent topology — production)                           │
│                                                                      │
│  launchd ───▶ /bin/bash run-7788.sh ──exec──▶ python3 -m gemia server│
│       ▲                                          │                   │
│       │                                          ▼                   │
│       │                                       server.py 主进程       │
│       │                                       PID 95183              │
│       │                                       Listen *:7788          │
│       │                                          │                   │
│       │                                          ▼                   │
│       │                                  subprocess.Popen("ffmpeg")  │
│       │                                  ├─ ffmpeg child (per verb)  │
│       │                                  └─ ffprobe child            │
│       │                                                              │
│  launchctl StartInterval=60 ─▶ ensure-7788.sh ─curl http :7788/      │
│                                  │                                   │
│                                  └─ unhealthy → launchctl kickstart  │
│                                                                      │
│  user ──▶ Safari/Chrome ──▶ http://127.0.0.1:7788/v3                 │
│           (no Tauri, no .app, no DMG)                                │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  PLANNED Tauri topology (built but stale, NOT in production)         │
│                                                                      │
│  user ──▶ /Applications/Gemia.app                                    │
│              │                                                       │
│              ▼                                                       │
│           Tauri Rust process                                         │
│              ├─ app.shell().sidecar("gemia-server").spawn()          │
│              │       │                                               │
│              │       ▼                                               │
│              │   gemia-server (PyInstaller binary, 140MB)            │
│              │   Listen *:7788                                       │
│              │       │                                               │
│              │       ▼                                               │
│              │   subprocess.Popen("ffmpeg")  ←─ FAIL on user machines│
│              │                                  unless ffmpeg in PATH│
│              │                                                       │
│              └─ webview ──HTTP api_call──▶ 127.0.0.1:7788            │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  v4 BUILD isolation surface (today: NONE used in v3 path)            │
│                                                                      │
│  v3 path:                                                            │
│    agent_loop_v3 → DISPATCHER → dispatcher → subprocess.Popen        │
│                                              ▲                       │
│                                              └─ no sandbox-exec,     │
│                                                 no RLIMIT, full uid  │
│                                                                      │
│  Available (unused in v3):                                           │
│    creative_sandbox_runner.py: sandbox-exec profile + path validation│
│      → only invoked by /run-skill (v2 dead path)                     │
│    lumerai/sandbox.py: RLIMIT_CPU + AST blacklist (花架子 per Codex) │
│      → only invoked by runtime_vnext.py (abandoned)                  │
│                                                                      │
│  v4 build options:                                                   │
│    (a) wire to creative_sandbox_runner  ← cheap, real sandbox        │
│    (b) write new sandbox layer          ← duplication, costly        │
│    (c) ship without isolation           ← unacceptable for Acrab key │
└──────────────────────────────────────────────────────────────────────┘
```

---

*Verified via: `plutil -p` LaunchAgent plists, `ps auxww`, `lsof`, `which`,
`python3 -c sys.prefix`, `grep -rn subprocess`, `cat tauri.conf.json`,
`cat lib.rs`, `ls dist/`. All commands rerunnable from this machine
2026-05-30.*
