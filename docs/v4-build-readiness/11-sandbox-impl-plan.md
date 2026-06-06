# 11 — Sandbox 实施计划:两档权限模型可落地性 + 三件能力接入 + 凭证隔离

> Date: 2026-06-06
> 作者:Claude Code(主控)+ codex-1(verb dispatch 映射)+ v4pro-1(02–05 旧文档汇总)
> 范围:**只读调研 + 设计**。无实现代码,未部署任何 profile,未开任何
> 联网/bash/exec 能力。未动 main / agent_workflow.py / 二进制 / UI / v3 路径。
> 权限模型来源:Acrab 两档设计(工作区内全权 / 区外可读可创建不可改删)+
> 凭证可读排除(Claude 补洞)。安全档次:第一档(可信指令源,不做防恶意攻击级)。

---

## TL;DR(给 Acrab 的三句话)

1. **命门已破,可落地。** macOS `sandbox-exec` **原生能精确表达**"可创建新文件
   (含写入内容)/ 不可改已有 / 不可删已有"——靠 SBPL 算子族里 `file-write-create`
   与 `file-write-data` / `file-write-unlink` 的天然区分。**已用一次性探针实测确认**
   (本文 §0 + §1.2)。我先前理论上担心的"创建必然带 data、无法区分"被实测推翻:
   macOS 在 `open()` 时区分新建 vnode vs 已有 vnode——写入新建文件由 `file-write-create`
   授权,改已有文件才需 `file-write-data`。
2. **三件能力都能接,且与昨晚 08 报告的异步架构同构。** 代码执行(c)就是另一种
   LRO,和 Veo/Lyria 走同一套 JobRegistry + check_job/wait_for_job
   (`08-async-architecture.md` §5 已预留 `kind="build"`)。网络(a)走 host fetch verb,
   bash(b)走 sandbox-exec 包裹的受控 run_shell。
3. **凭证隔离零新工作量。** `generate_image` 已经是"模型发 prompt、host 持 ADC、
   base64 落盘不入 SSE"的范式(`tools/generate_image.py:1-17`)。沙盒子进程压根
   不需要 ADC——host(沙盒外的父进程)持凭证调 Vertex。只需在 profile 里
   `(deny file-read* …)` 挖掉凭证目录,链路就断。

---

## 0. 验证方法(诚实声明)

SBPL(Sandbox Profile Language)的算子语义 **Apple 未正式文档化**,`sandbox-exec`
man page 标 deprecated 且内容稀疏。所以本文的命门结论 **不靠推理,靠实测**:

- 写了 3 个一次性 bash 探针,在 `/private/tmp` 临时目录里用真实 `sandbox-exec`
  包裹 `python3` 跑文件操作,观察 EPERM(errno 1)/ 成功。
- 探针 **不属于 Lumeri**,不写进仓库,跑完即 `rm -rf`,**不给模型开任何能力**。
- 这是研究性验证,不是部署。下面所有 PASS/DENIED 都是 2026-06-06 在 Acrab 本机
  (macOS Darwin 25.3.0,`/opt/homebrew/bin/python3` 3.14.3)实跑结果。

> **副产物(一个真实实现坑)**:第一版探针用了未解析的 `/tmp/...` 路径,所有规则
> 静默不命中——因为 macOS `/tmp` 是 `/private/tmp` 的符号链接,而 **sandbox-exec 按
> 完全解析后的真实路径(canonical realpath)匹配 subpath**。profile 里写 `/tmp` 不会
> 匹配 `/private/tmp` 的 vnode。现有 runner 已正确处理(用 `.resolve()`,见
> `creative_sandbox_runner.py:68-70`),但这是任何手写 profile 的必踩坑,§1.4 详述。

---

## 1. 复活 creative_sandbox_runner 的 sandbox-exec wrapper

### 1.1 它现在长什么样,能不能承载两档模型

读 `gemia/creative_sandbox_runner.py:258-311`。现状 wrapper 分两层:

**A 层 — 静态校验(`creative_sandbox_permissions.py`)**:argv 白名单 + Python AST 走查。
只放行 `python/python3/ffmpeg/ffprobe`(permissions.py:27-32),封 `bash/curl/git/...`
(permissions.py:33-49),Python 源码层封 `os/subprocess/urllib/socket/requests/...`
导入(permissions.py:50-64)、封 `eval/exec/open(写模式越界)`(permissions.py:97-99,
256-290)。

**B 层 — sandbox-exec profile(`creative_sandbox_runner.py:293-311`)**:这是真正的
内核级 TrustedBSD 边界,当前生成的 profile 是:

```scheme
(version 1)
(deny default)
(allow process*)
(allow sysctl*)
(allow file-read*)                              ; ← 读全盘,含凭证。GAP 1
(allow file-write* (subpath "<workspace>") …)   ; ← 区外完全不可写
(deny network*)                                 ; 或 (allow network*)
```

**结论:B 层能承载两档模型,但当前 profile 两档都不匹配,需改两处:**

| Acrab 要求 | 当前 profile(293-311) | 差距 |
|---|---|---|
| 工作区内全权(读/写/改/删/跑) | `file-write*` on workspace ✓ + `process*` ✓ | **已满足** |
| 区外可读 | `file-read*` 全放 ✓(但含凭证) | 需挖掉凭证(GAP 1) |
| 区外可创建新文件 | 无任何区外 write 规则,**完全不可创建** | **缺**(GAP 2) |
| 区外不可改/删已有 | 因为整个区外不可写,改删也被拦 | 已满足(但因过度限制) |
| 凭证不可读 | `file-read*` 无过滤,**凭证可读** | **缺**(GAP 1) |

即:当前 profile 比 Acrab 设计 **更严**(区外完全只读不可写),同时 **有个洞**
(凭证可读)。把它调成 Acrab 两档 = 加一条区外 `file-write-create` + 加一条凭证
`deny file-read*`。两处都是 profile 字符串改动,**不动 wrapper 的 Python 逻辑**
(`_sandbox_command` 258-269 / `_sandbox_profile` 293-311 的结构原样,只换 profile 内容)。

`run()` 主流程(74-190)、artifact 发现(384-435)、超时/错误处理已成熟,可直接承载。

### 1.2 ★命门实测:sandbox-exec 能否表达"可创建不可改删"

**能,而且原生精确。** SBPL 的 `file-write*` 是一族算子,关键三个互相独立:
- `file-write-create` — 新建文件系统对象(新 inode / 目录)
- `file-write-data` — 写已有文件的数据(覆写/截断/追加)
- `file-write-unlink` — 删除(unlink / rmdir / rename 的源删除)

**实测**(profile = `deny default` + `allow file-read*` + **仅** `(allow file-write-create (subpath REGION))`,**不给** data、**不给** unlink):

| 操作 | 结果 | errno |
|---|---|---|
| A 新建文件 + 写入内容 | **OK** | — |
| B 覆写已有文件(`open("w")` 截断) | **DENIED** | 1 (EPERM) |
| C 追加已有文件(`open("a")`) | **DENIED** | 1 |
| D `O_RDWR` 打开已有文件 | **DENIED** | 1 |
| E 删除已有文件(`unlink`) | **DENIED** | 1 |
| F 重命名已有文件 | **DENIED** | 1 |
| G `mkdir` 新目录 | **OK** | — |
| 已有文件内容(验证) | `'ORIGINAL'` 原样保留 | — |

**这正是 Acrab 设计的精确语义:** 新建(文件 + 目录 + 写入内容)放行,改已有 /
删已有 / 重命名已有一律 EPERM,已有内容物理不变。

**机理:** macOS 在 `open()` 时区分目标 vnode 是否已存在——
- `open(O_CREAT|O_WRONLY)` 命中不存在的路径 → 走 `file-write-create`,**该授权同时覆盖
  对这个新 vnode 的写入**(所以 A 能写内容,无需 `file-write-data`)。
- `open(O_WRONLY/O_RDWR/O_TRUNC/O_APPEND)` 命中已存在的 vnode → 走 `file-write-data`
  (B/C/D 因没给 data 而 EPERM)。

**两个必须向 Acrab 交代的行为细节(非 bug,是边界):**
1. **同一文件不能多次写。** 一旦在区外创建了文件,它就成了"已有 vnode";再 `open("a")`
   追加会被 `file-write-data` 拦(实测 DENIED errno 1)。即:**区外产物必须一次 `open()`
   写完。** 需要边写边读改的迭代逻辑,应落在工作区内(工作区是 `file-write*` 全权,
   无此限制)。对生成式产物(一次性写 png/mp4/json)这不是问题。
2. **profile 必须用 canonical 真实路径**(§1.4)。

> 补充实测:`file-write*` 通配 = 创建 + 改 + 删全开(工作区用);`(allow file-read*)`
> 后再跟 `(deny file-read* (subpath CREDS))` → 凭证读 EPERM、其余读正常(**deny 在
> allow 之后,last-match-wins**)。这两条直接支撑工作区全权 + 凭证排除。

### 1.3 Profile 草案(DRAFT — 未部署,未写进任何文件)

把 §1.2 三条拼成 Acrab 两档。**这是给 Acrab/Codex 审的草案文本,不是落地代码。**

```scheme
(version 1)
(deny default)

; ---- 进程 / 系统基础(沿用现状 runner)----
(allow process*)
(allow sysctl*)
(allow mach*)                          ; Homebrew python 3.14 import 某些模块需要

; ---- 读:区内区外都可读,唯独挖掉凭证(GAP 1 修复)----
(allow file-read*)
(deny  file-read* (subpath "/Users/xiehaibo/.ssh"))
(deny  file-read* (subpath "/Users/xiehaibo/.config/gcloud"))   ; Vertex ADC
(deny  file-read* (literal "/Users/xiehaibo/.gemia/config.json")); OpenRouter/Pexels/OAuth key
;  ↑ 必须排在 (allow file-read*) 之后:SBPL last-match-wins

; ---- 写档 1:工作区内全权(创建/改/删/重命名)----
(allow file-write* (subpath "/Users/xiehaibo/Lumeri/workspace"))

; ---- 写档 2:区外只可创建新文件 + 新目录,不可改/删已有 ----
;  关键:只给 create,不给 data、不给 unlink → §1.2 实测的"可创建不可改删"
(allow file-write-create (subpath "/Users/xiehaibo"))
(allow file-write-create (subpath "/private/tmp"))
;  (可按需加 /Volumes/Extreme SSD 等 Acrab 常用产出根)

; ---- 网络:第一档默认 deny,fetch 走 host(见 §2a)----
(deny network*)
```

要点:
- **凭证 deny 必须在 `allow file-read*` 之后**,否则被通配 allow 覆盖(last-match-wins,§1.2 实测)。
- **区外只 `file-write-create`,不 `file-write-data`/`file-write-unlink`** —— 这一条就是命门的落地。
- 工作区那条 `file-write*` 放在区外 create 之后/之前都行(工作区是 `/Users/xiehaibo` 子集,
  `file-write*` ⊃ `file-write-create`,工作区路径上 `file-write*` 这条会作为更具体且靠后的
  匹配胜出,授予全权;区外路径只命中 create 那条)。建议工作区那条放最后,语义最清晰。
- 路径全部写 **解析后的真实绝对路径**(§1.4)。

### 1.4 macOS sandbox-exec 的 canonical-path 命门(必踩坑)

sandbox-exec 按 **完全解析符号链接后的真实路径** 评估 `subpath`/`literal`。
profile 里写 `/tmp/x` **不匹配** 实际 vnode `/private/tmp/x`(`/tmp`→`/private/tmp` 软链)。
同理 `/var`→`/private/var`,`~/Lumeri` 若经软链也要解析。

- 现有 runner **已正确处理**:`root_dir`/`workspace_dir` 都 `.resolve()`
  (`creative_sandbox_runner.py:68-70`),declared paths 也 `.resolve()`(93)。
- 新增的区外 create 根、凭证 deny 路径,**必须同样 `Path(...).resolve()` 后再插入 profile**。
- `_escape_profile_path`(314-315)只转义引号/反斜杠,**不解析软链**——解析要在调用方做。

这是第一版探针翻车的原因(§0 副产物),写进文档免得落地时再栽。

### 1.5 permissions.py 的 AST/argv 白名单 —— 对 v4 是"过度加码"

A 层(permissions.py)对 v0 沙盒(只跑受限 Python 片段)是合理的,但对 **v4 build
(模型写真实创作代码)它会几乎封死一切有用代码**:封了 `os`/`shutil`/`subprocess`
(permissions.py:50-64)意味着模型 **不能从 Python 里调 ffmpeg、不能 os.path 操作、
不能列目录**;只放行 `python/ffmpeg/ffprobe` argv(27-32)意味着 **run_shell/git/curl
直接被 A 层拒**。

Acrab 明确"别过度加码 + 第一档不防恶意"。对照之下:
- **真正的边界是 B 层(sandbox-exec 内核级)**,§1.2 已证它能精确管住 FS。
- A 层的 Python AST 白名单与 B 层 **职责重叠且更严**,且是"软"防线(静态分析可绕)。
- **建议(留给 milestone 拍板):** v4 路径下 **放宽/绕过 A 层的 Python import 白名单与
  argv 白名单**,让 sandbox-exec 当唯一真边界;A 层降级为"可选的预检 + 友好报错"
  (例如 `08`/`03` 提的 pre-flight AST parse 只用来给模型早期错误反馈,不做能力封锁)。
  这与 `claude-02-sandbox-options.md` "AST 黑名单是软的、真价值有限" 一致。
- **不要**在 v4 又叠一层精细 argv 白名单——那正是 Acrab 反对的过度加码。

---

## 2. 三件能力怎么接到 sandbox 后面

三件能力 **共用同一个 verb 分发骨架**(codex-1 实测映射,已核验行号):
- 模型看到的工具 schema:`gemia/tools/_schema.py:55`(`TOOL_SCHEMAS`,当前 15 个 verb)。
- 分发表:`gemia/tools/__init__.py` 的 `DISPATCHER`(`_REAL` 真实实现 + `_make_stub` 占位,
  `generate_image` 注册在 `__init__.py:84`)。
- 主循环调用点:`agent_loop_v3.py:555` `result = await DISPATCHER[tc.name](parsed_args, self._tool_ctx)`,
  前置预算闸 `budget.check` 在 `:490`,进度回调 `_make_progress_cb` 在 `:645`(emit `tool_exec_progress`)。
- `ToolContext`(`tools/_context.py:150`)携带 `output_dir/registry/emit_progress`,
  `child_path`(:157)给产物落盘路径。
- 加新 verb = schema 加一项 + `__init__.py` `_REAL` 注册一项 + 写 `gemia/tools/<verb>.py`
  的 `async def dispatch(args, ctx)` + budget_guard 成本表加一项。**不改主循环。**

### (a) 联网 / 拉 GitHub 素材

**推荐:host 提供一个 `fetch` verb,sandbox 内保持 `(deny network*)`。** 理由:
- 网络是注入最坏后果的唯一外泄面(MEMORY 多次记:外泄链路 = 读 key → 联网发出去)。
  把网络 **留在 host(沙盒外)**,沙盒子进程无网络,即使被注入也发不出东西。
- 形态与 `generate_image` 完全一致:模型调 `fetch(url, dest_rel_path)`,**host 进程**
  执行 HTTP(复用 `gemini_client.py` 的 urllib + `~/.gemia/config.json:proxy` 同一
  FlClash 7890 跳点,见 `gemini_client.py:112-123`),把字节写进 **工作区**,返回 asset/路径。
- 第一档放多宽:**不做精细域名白名单**(Acrab 反对)。做两件低成本闸即可——
  (1) 只允许 `https://`(挡 file://、内网 SSRF 的明显误用);(2)落盘强制进工作区。
  GitHub raw / API 直接放行。
- 若坚持让 sandbox 内直接 `git clone`:则 profile 需 `(allow network-outbound (remote ip "*:443"))`
  且 A 层要解封 `git`(当前 permissions.py:38 封了 git)。**不推荐**——把网络塞进沙盒
  等于把外泄面塞进沙盒,与"网络留 host"的注入防御相悖。**结论:走 host fetch verb。**

### (b) bash / 操控

**推荐:`run_shell(command)` verb,在 sandbox-exec 里跑 `bash -c`,profile 限定读写边界
(§1.3 两档)。** 要点:
- bash 在 §1.3 的两档 profile 下跑:工作区全权,区外可读可创建不可改删,凭证不可读,无网络。
  **sandbox-exec 是边界,不靠 argv 白名单**(§1.5)。
- 实现:`subprocess.run(["/usr/bin/sandbox-exec","-p",PROFILE,"/bin/bash","-c",command], …)`
  —— 这正是现有 `_sandbox_command`(creative_sandbox_runner.py:258-269)的形态,只是把
  argv 从 `["python", …]` 换成 `["bash","-c",cmd]`,并解封 A 层。
- **A 层必须解封**:当前 permissions.py:33-49 把 `bash/sh` 列入 `blocked_binaries`,
  会在进 sandbox-exec 前就 raise。v4 路径要么不走 `validate_command`,要么给 v4 一个
  放宽的 policy(§1.5)。
- 第一档够用,不做精细资源配额;沿用现有 `timeout_sec`(run() 默认 30s,79)防跑飞。

### (c) 写代码 / skills:模型写 Python → 跑 → 看 → 改 → 再跑

这是核心,**且应做成异步 job**(见下方 ★)。loop 怎么接进 agent_loop_v3:

- 新 verb `build`(命名见 `03-loop-integration.md`)。dispatcher 形态:模型把脚本字符串
  (或写进工作区的 `.py`)交给 `build` → host 用 §1.3 profile 包裹 sandbox-exec 跑 →
  `creative_sandbox_runner.run()` 已经把 stdout/stderr tail + artifacts 结构化返回
  (CreativeCommandResult,creative_sandbox_runner.py:35-56)→ 作为 tool_result 回模型。
- **看 → 改 → 再跑** 复用现有错误回路:dispatch 抛错 → `agent_loop_v3.py:566`
  emit `tool_exec_error` → 作为 tool_result 喂回模型 → 模型读 traceback 改脚本重调。
  **零新循环机制**(与 ffmpeg 失败回路同构)。`claude-03` 提醒补一层 error 分类
  (script_bug / sandbox_violation / user_input),让模型知道是代码错还是被沙盒拦——
  建议采纳,低成本高收益。
- **skills 持久化到 workspace 可复用:** 工作区是全权档(§1.2),天然适合存可复用脚本。
  复用 v2 已有的 `python -m gemia skill save/list/load/apply` CLI(`v4pro` 汇总自
  `04-v2-skills-archaeology.md` / `claude-04`:该 CLI 是活的,不是死代码)+ SKILL.md /
  `_combos/*.yaml` 模板格式。落地:跑通的 build 脚本存进
  `~/Lumeri/workspace/skills/<slug>.py`(+ 一个 SKILL.md frontmatter),下次
  `apply` 直接复用。**因为存在工作区内,模型对自己的 skills 有完整读写改删权**——
  这点和两档模型天然契合。

#### ★ 与 08 报告异步架构(Veo/Lyria JobRegistry)是否同构 —— 是,且应直接复用

`08-async-architecture.md` §5 **已经替这个问题拍了板**(原文:"这就是另一种 LRO …
形态上完全和 Veo/Lyria 同构");`JobRecord.kind` 显式预留了 `"build"`(08 §2.1)。
代码执行也是长任务(几十秒到几分钟),和"提交→拿句柄→模型决定等不等→完成落 asset"
完全一致。

**复用方案:**
- `build` 的 dispatcher 不阻塞:提交脚本到 sandbox 子进程 → 注册进 **JobRegistry**
  (`gemia/tools/_jobs.py`,08 §2.1 设计,**codex-1 实测:该文件目前尚未实现**)→ 返回
  `{job_id, asset_id_pending, status:"submitted", eta}`。
- 模型用同一套 `check_job` / `wait_for_job`(08 §2.3)轮询/等待——对模型而言
  build job 和 veo job **无区别**。
- **意味着:JobRegistry 实现一次,Veo/Lyria 和 v4 build 三者共用。** 这是 08 "做对一次
  复用两次" 的兑现。**实施顺序建议:先落 JobRegistry(为 Veo),build verb 顺势挂上去**,
  而不是给 build 单独造一套异步。
- 进度事件:`wait_for_job` poll loop 里 `ctx.emit_progress`(08 §6),复用现有
  `_make_progress_cb`(agent_loop_v3.py:645),前端无需改。
- 预算:`budget_guard.check`(budget_guard.py:74)已是按 verb 估成本;注意今天刚修过
  时间闸用累计 `spent_seconds`(budget_guard.py:80 `projected_sec = self.spent_seconds + eta`),
  build 的 eta 要进成本表(budget_guard.py:20-39 风格)。本地 build 无 $ 成本,只占 eta 秒。

---

## 3. 凭证隔离的具体实现

### 3.1 host 怎么在"模型拿不到 ADC"的前提下代调 Vertex

**现有 `generate_image` 已经就是这个形态,无需改造。** 实测链路:

1. 模型调 verb `generate_image(prompt=…)`(schema:`_schema.py:56-68`)。模型 **只发
   prompt**,从不接触任何 token。
2. host 的 dispatcher(`tools/generate_image.py:36-122`)在 **server.py 主进程
   (沙盒外,PID 95183,见 `09-runtime-topology.md` §2)** 里 new 一个 `GoogleGenAIClient`。
3. 该 client 读 **host 持有的凭证**:
   - Vertex project ← `~/.gemia/config.json:vertex_project`(`google_genai_client.py:161-201`)
   - ADC bearer ← `_vertex_access_token(proxy)`(`gemini_client.py:92`),它读
     `~/.config/gcloud/application_default_credentials.json`(`_ADC_PATH`,`gemini_client.py:88`),
     用 refresh_token 向 `oauth2.googleapis.com/token` 换 access token 并缓存(:103-126)。
4. host 把图 POST 回来的 **base64 解码后写盘**(`generate_image.py:82-86`),
   **base64/字节从不进返回 dict / 不进 SSE**(`generate_image.py:8-11` 明确注释),
   只返回 `{asset_id, summary, metadata}`(:108-122)。

**模型拿不到 ADC 的根因:认证发生在 host 父进程,不在沙盒子进程。** 沙盒子进程(模型的
build/run_shell 代码)做的是本地创作计算,**它根本不需要 ADC**——要生图就调
`generate_image` verb 让 host 代办。

### 3.2 沙盒侧把洞堵死

即便如此,§1.1 GAP 1 指出当前 profile `file-read*` 全放,沙盒里的模型代码 **理论上能读
`~/.config/gcloud` 把 refresh_token 读出来**,再(若有网络)发走。Acrab 补的这个洞,
靠 §1.3 profile 的三条 `(deny file-read* …)` 封死:

- `~/.config/gcloud`(ADC)、`~/.ssh`、`~/.gemia/config.json`(里有 OpenRouter/Pexels/
  OAuth/Gemini key,见 `09-runtime-topology.md` §9)全部 `deny file-read*`。
- 实测(§1.2 补充)确认 deny-after-allow 生效:凭证读 EPERM,其余读正常。
- **双保险**:即使凭证误读,§2a 已把网络留在 host、沙盒 `deny network*`——读到也发不出去。
  "读 key → 写含 key 文件 → 联网发出"的链路三段全断(读断 + 网络断)。

### 3.3 verdict

| 问题 | 答案 |
|---|---|
| generate_image 已是"host 持认证、模型调 verb"形态吗? | **是**,无需改(`generate_image.py:1-17,62-86`) |
| 模型(沙盒代码)需要 ADC 吗? | **不需要**,生成请求一律走 host verb |
| 凭证隔离要改什么? | 只在 profile 加 3 行 `deny file-read*`(§1.3),其余零改动 |

---

## 4. 工作量估算

分四块。前提:JobRegistry 是和 Veo 共担的基础设施(08 已估 32–39h 含 Veo/Lyria),
**这里只列 v4 build 增量**,不重复计 JobRegistry 全量。

| 模块 | 工作内容 | 估时 |
|---|---|---|
| **Sandbox 基础层** | 改 `_sandbox_profile` 成两档(create-only 区外 + 凭证 deny + canonical resolve);v4 放宽 A 层 policy;两档单测(create OK / modify-existing DENIED / unlink DENIED / 凭证 read DENIED) | **6–10h** |
| **(a) 网络/fetch** | `tools/fetch.py` dispatcher(host urllib + proxy 复用 + 落工作区 + https-only)+ schema + 注册 + 测试 | **4–6h** |
| **(b) bash/run_shell** | `tools/run_shell.py`(sandbox-exec 包 bash -c,复用 _sandbox_command)+ schema + 注册 + timeout + 测试 | **4–6h** |
| **(c) build + skills** | `tools/build.py`(挂 JobRegistry,async submit/句柄返回)+ error 分类(script_bug/sandbox_violation)+ skills 存取复用 `gemia skill` CLI 接工作区 + schema + 测试 | **10–14h** |
| **(c 前置)JobRegistry** | 若 Veo 尚未落地需先做(08 §7 已估,**与 Veo 共担**) | (08:含在 32–39h 内) |
| **合计(build 增量,不含 JobRegistry 全量)** | | **24–36h(约 3–5 天)** |

口径说明:与 `03-loop-integration.md`(22–31h)/`claude-03`(21–29h)同量级。差异在
本估把 (a)(b) 也算进来(那两份只估 build),并假设 JobRegistry 走 Veo 那条共担线。

---

## 5. 风险 / 未决 / 不做什么

**已被两档设计覆盖的(无需额外加码):**
- 注入最坏后果:区外不可改/删 + 凭证不可读 + 网络留 host → 注入了也删不掉区外、拿不到 key、发不出去。
- 手滑/抽风:`rm -rf` 区外被 unlink-deny 拦;覆写区外配置被 data-deny 拦;timeout 防跑飞。

**需 milestone 拍板的(不阻塞本调研):**
1. A 层放宽到什么程度(§1.5):完全绕过,还是保留 pre-flight AST 只做报错不做封锁?建议后者。
2. 区外 `file-write-create` 的根范围:只 `$HOME`+`/private/tmp`,还是含 `/Volumes/Extreme SSD`?
   (Acrab 产物习惯落外置盘,可能要加。)
3. 区外"一次写完"限制(§1.2 细节1)对模型脚本的实际影响——多数生成式产物单次写,
   但要在 system prompt 提示模型"迭代产物写工作区"。
4. `claude-05-risks.md` 提的:vNext(runtime_vnext.py)已实现过同款 model→Python→sandbox
   循环却没 ship,v4 build 落地前应先想清为何 vNext 没成主路径(产品定位 vs 脚本质量 vs UX)。

**本调研明确不做 / 未做:**
- 未部署任何 profile,未写实现代码,未开联网/bash/exec。
- 未做精细网络域名白名单、未做精细资源配额(Acrab 反对过度加码,第一档不需要)。
- 未动 main / agent_workflow.py / 二进制 / UI / 现有 v3 路径。

---

## 附录 A — 探针证据复现命令(throwaway,非仓库内容)

命门核心探针(2026-06-06 实跑,profile = 仅 `file-write-create`,canonical 路径):

```
A 新建文件+写内容      : OK
B 覆写已有(truncate)  : DENIED errno 1
C 追加已有             : DENIED errno 1
D O_RDWR 打开已有      : DENIED errno 1
E unlink 已有          : DENIED errno 1
F rename 已有          : DENIED errno 1
G mkdir 新目录         : OK
已有文件内容           : 'ORIGINAL'(未变)
```

补充探针:`file-write*` 通配 = create+modify+delete 全开(工作区档);
`(allow file-read*)` 后接 `(deny file-read* (subpath CREDS))` → 凭证读 EPERM、其余读 OK。

---

*交叉参照:`08-async-architecture.md`(异步 c 方案 + build 同构)、
`09-runtime-topology.md`(host 进程拓扑 + 凭证位置 + 现成隔离设施)、
`02-sandbox-options.md`/`claude-02`(sandbox-exec 选型)、
`03-loop-integration.md`/`claude-03`(build verb 接入)、
`04-v2-skills-archaeology.md`/`claude-04`(skills 持久化)、
`05-risks.md`/`claude-05`(风险)。所有结论附 file:line,SBPL 命门附实测。*
