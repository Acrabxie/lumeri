# v4 build — 钉死的规则 (RULES)

> 这些是已拍板、**以后不再每次重新讨论/担忧**的结论。新决策追加到末尾并注明日期。

---

## R0 — vNext 根因 (2026-06-07 一次性钉死)

**vNext 未成主路径 = Codex 在一次性 JSON 死架构上的无效尝试,非设计/技术失败;
v4 build 长在已验证的 v3 活架构上。**

含义,以后不再反复担忧:
- vNext (`gemia/runtime_vnext.py`) 那条 model→Python→sandbox 循环没 ship,**不是**
  因为"沙盒/写代码 loop 这条路本身走不通"。它是 Codex 在一个一次性 JSON 死架构上的
  尝试,架构本身没活下来。
- v4 build **不重蹈**:它直接长在**已经在跑、已被实测验证**的 v3 活架构上
  (`agent_loop_v3.py` 主循环 + verb dispatcher + `creative_sandbox_runner` 的
  sandbox-exec 边界 + 真实产物落 `~/Lumeri/workspace/`)。
- 因此 05/claude-05 风险文档里"vNext 为何没成主路径"这一项 **视为已解答**,
  不再作为 v4 落地的阻塞性疑虑。

---

## R1 — A 层 (permissions.py) 降级为预检,sandbox-exec 是唯一真边界 (拍板 2026-06-07)

- `creative_sandbox_permissions.py` 的 Python AST / argv 白名单对 v4 是**过度加码**
  (封 `os`/`subprocess` → 连从 Python 调 ffmpeg 都不行;封 `bash` → run_shell 进不去)。
- v4 路径下 A 层**降级为 pre-flight:只报错、不封锁**,给模型早期友好反馈即可。
- **唯一真边界 = B 层 sandbox-exec 内核级 TrustedBSD** (见 R3 / `gemia/sandbox_v4.py`)。
- 不在 v4 再叠精细 argv 白名单(Acrab "别过度加码")。
- 落地点:M3 接 verb 时,v4 verb 不走 `validate_command` 的封锁分支(或给 v4 放宽 policy)。

## R2 — 区外 file-write-create 的根 (拍板 2026-06-07)

区外"可创建不可改删"的根 = **`$HOME` + `/private/tmp` + `/Volumes/Extreme SSD`**
(Acrab 产物习惯落外置盘)。已固化为 `gemia/sandbox_v4.DEFAULT_OUTSIDE_CREATE_ROOTS`。

## R3 — 两档 sandbox 模型 = v4 安全命门 (M1 已实测落地 2026-06-07)

- 实现:`gemia/sandbox_v4.build_two_tier_profile()`。回归探针:
  `tests/test_sandbox_v4_isolation.py`(可重跑,8 tests)。
- 两档语义(macOS sandbox-exec SBPL,实测确认):
  - **工作区** (`~/Lumeri/workspace`):全权 `file-write*`(读/写/改/删/建)。
  - **区外** (R2 三根):可读(除凭证)+ 只能**创建新文件/新目录**;
    覆写/追加/O_RDWR/删除/重命名**已有**文件 = EPERM。
  - **凭证** (`~/.ssh`, `~/.config/gcloud`, `~/.gemia/config.json`):
    **不可读 + 不可写**(纵深防御)。
  - **网络**:沙盒内 `deny network*`;联网走 host fetch verb(注入外泄面留 host)。
- 三个静默失效坑(已在 builder 内根治,勿手写 profile 时再栽):
  1. SBPL 按 **canonical realpath** 匹配 → 所有路径必须 `.resolve()`
     (`/tmp`→`/private/tmp`),否则规则静默不命中。
  2. **SBPL 优先级 = 最具体的 operation 匹配胜出,不是单纯 last-match**
     (2026-06-07 实测纠正)。只有 **同 operation 之间**才按 last-match
     (所以凭证 `(deny file-read*)` 排在 `(allow file-read*)` 之后才生效,
      工作区 `(allow file-write*)` 同理)。
  3. ★ 因 #2:宽 `(deny file-write* …)` **压不住**更具体的
     `(allow file-write-create $HOME)`——曾导致沙盒内能在 `~/.ssh` 里
     **新建** `authorized_keys`(整体安全探针 `test_run_shell_cannot_write_into_ssh`
      抓到,errno 实测:V1 CREATE=OK 洞 / V2 加 `deny file-write-create` 后 DENIED)。
     根治:凭证路径必须 **同时** `(deny file-write-create …)` + `(deny file-write* …)`,
     create-deny 与 home create-allow 同 verb 具体度、且排在其后 → deny 胜。
     回归:`test_sandbox_v4_isolation.test_credential_dir_create_denied`。

## R4 — 凭证隔离零 verb 改动 (verdict 2026-06-06)

`generate_image` 已是"模型只发 prompt、host 父进程持 ADC 调 Vertex、base64 落盘不入 SSE"
范式。沙盒子进程**不需要 ADC**,生成请求一律走 host verb。凭证隔离 = R3 的 profile
`deny file-read*` 三行 + host 持网络,**不改任何 verb**。

---

## 不动清单 (贯穿 v4 build)

- 不动 `main` 分支 / `agent_workflow.py`(v2 演戏路径)/ 二进制 / UI / 现有 v3 执行路径。
- 安全验证(R3 隔离实测)由主 Claude 亲自跑,不分派、不只看子代理报告。
- 任何子代理报告"接了 sandbox"但未经主 Claude 实测验证的,不算完成。
