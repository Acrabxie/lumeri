/* Lumeri v3 frontend — vanilla JS, no build step.
 *
 * Connects to the agent loop via:
 *   POST /sessions                              create session
 *   POST /sessions/{id}/assets                  raw body + X-Filename
 *   POST /sessions/{id}/turn                    {"message": "..."} (202)
 *   GET  /sessions/{id}/stream                  EventSource + last_event_id replay
 *   GET  /sessions/{id}/assets/{aid}            preview URL for the asset
 *   POST /sessions/{id}/close                   teardown
 *
 * Invariants (mirror the agent loop's promises):
 *   - Every event kind has a handler. Unknown kinds raise a visible
 *     banner — never silent drop.
 *   - Tool execution is presented as a high-level activity state; raw tool
 *     payloads and model work logs never enter the user-facing stream.
 *   - All asset previews load from /sessions/{id}/assets/{aid}. Tool
 *     results expose asset_id/kind/asset_url, never local filesystem paths.
 */

(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  // Inline the icon sprite once so every <use href="#i-*"> resolves, including
  // ones rendered before the fetch lands (SVG <use> re-resolves on DOM insert).
  fetch("/video/icons.svg")
    .then((r) => (r.ok ? r.text() : ""))
    .then((t) => {
      if (!t) return;
      const holder = document.createElement("div");
      holder.innerHTML = t;
      const sprite = holder.querySelector("svg");
      if (sprite) document.body.prepend(sprite);
    })
    .catch(() => {});

  const els = {
    sessionLabel: $("#session-id-label"),
    connPill: $("#connection-pill"),
    newSessionBtn: $("#new-session-btn"),
    timeline: $("#timeline"),
    emptyState: $("#empty-state"),
    assetGrid: $("#asset-grid"),
    mediaLibraryGrid: $("#media-library-grid"),
    libraryRefreshBtn: $("#library-refresh-btn"),
    libraryAnnotateBtn: $("#library-annotate-btn"),
    uploadInput: $("#upload-input"),
    uploadBtn: $("#upload-btn"),
    promptInput: $("#prompt-input"),
    voiceInputBtn: $("#voice-input-btn"),
    voiceInputStatus: $("#voice-input-status"),
    sendBtn: $("#send-btn"),
    inputShell: $("#input-shell"),
    sandboxBtn: $("#sandbox-toggle-btn"),
    planBtn: $("#plan-toggle-btn"),
    planBar: $("#plan-bar"),
    askDock: $("#ask-dock"),
    slashMenu: $("#slash-menu"),
    historyToggleBtn: $("#history-toggle-btn"),
    historyDrawer: $("#history-drawer"),
    historyDrawerBody: $("#history-drawer-body"),
  };

  /** @typedef {{ asset_id: string, kind: string, summary: string, source: "user"|"tool", final?: boolean }} AssetEntry */

  const state = {
    sessionId: null,
    eventSource: null,
    turnInProgress: false,
    turns: [],                  // array of TurnRecord
    currentTurn: null,          // TurnRecord (also last in turns[])
    selectedClipId: null,       // direct-edit: currently selected clip
    ptDrag: null,               // direct-edit: active drag/trim gesture
    /** @type {AssetEntry[]} */
    assets: [],
    /** @type {string[]} */
    errors: [],
    uploadStatus: null,
    lastEventId: null,
    reconnectTimer: null,
    projectTimeline: null,      // fetched from /sessions/{id}/timeline
    timelinePollTimer: null,
    mediaLibrary: [],
    mediaAnnotations: new Map(), // media-library asset_id -> annotations[]
    mediaLibraryStatus: "idle",
    planMode: false,            // mirrors the backend per-session flag
    planReady: false,           // a turn completed while planning → offer approval
    pendingAsk: null,           // {question_id, question} while elicit awaits
    sessionTitle: null,         // auto-generated title
    userMessageCount: 0,        // user message counter for auto-title triggers
    stopPending: false,
  };

  function newTurn(userMessage) {
    return {
      userMessage,
      assistantText: "",
      pendingAssistantText: "", // held until the host knows it is a final reply
      streaming: false,
      toolCalls: new Map(),     // call_id -> ToolCallState
      orderedCallIds: [],
      guidance: [],             // user steering messages inside this same turn
      banners: [],              // { kind: "budget"|"turn_error"|"unknown", text }
      complete: false,
    };
  }

  // ── render ──────────────────────────────────────────────────────────

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // The activity stream is an orientation aid, not a developer console. The
  // only specific wording comes from Lumeri's own constrained activity label;
  // the display layer rejects anything that could be code or implementation
  // detail before it reaches the DOM.
  const ACTIVITY_TEXT_MAX_CHARS = 72;
  const PROGRESS_REPORT_MAX_CHARS = 240;
  const ACTIVITY_TEXT_UNSAFE_RE = /[`{}[\]<>\\]|[=;]|(?:https?|file):\/\/|(?:^|\s)(?:\/|~\/|[A-Za-z]:[\\/])|\b[\w.-]+\.(?:py|js|jsx|ts|tsx|json|md|yaml|yml|sh|bash|zsh|html|css|sql)\b|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b(?:api[_-]?key|token|password|secret|system[_ -]?prompt|reasoning|thought[_ -]?signature|asset[_ -]?id)\b|(?:代码|路径|工具名?|参数|命令|思维链|推理|内部)/i;

  const TOOL_CATEGORY = {
    generate_image: "创建", generate_video: "创建", generate_audio: "创建",
    narrate: "创建", build: "创建",
    lumen_render: "创建", lumen_render_range: "创建", vector_motion: "创建",

    edit_image: "编辑", edit_video: "编辑", edit_audio: "编辑",
    composite: "编辑", color_grade: "编辑", adjust_media: "编辑",
    paint_overlay: "编辑", paint_mask_effect: "编辑", add_overlay: "编辑",
    transform_geometry: "编辑", smart_reframe: "编辑",
    subtitle: "编辑", animate_captions: "编辑", lumen_patch: "编辑",
    grade: "编辑", kinetic_type: "编辑", edit_grammar: "编辑",
    camera: "编辑", compose: "编辑", rhythm_edit: "编辑",

    arrange_timeline: "剪辑",
    timeline_insert_clip: "剪辑", timeline_delete_clip: "剪辑",
    timeline_move_clip: "剪辑", timeline_trim_clip: "剪辑",
    timeline_split_clip: "剪辑", timeline_set_clip_time: "剪辑",
    timeline_add_transition: "剪辑", timeline_set_clip_effects: "剪辑",
    timeline_add_track: "剪辑", timeline_set_track: "剪辑",
    timeline_undo: "剪辑", inspect_timeline: "剪辑", get_timeline: "剪辑",
    mix_audio: "剪辑", align_audio: "剪辑", detect_beats: "剪辑",

    search_library: "搜索", search_media: "搜索", search_frames: "搜索",
    web_search: "搜索", web_open: "搜索", fetch: "搜索",

    extract_frame: "分析", probe_media: "分析", analyze_media: "分析",
    get_safe_areas: "分析", inspect_lottie: "分析",
    annotate_media: "分析", get_media_annotations: "分析",
    write_media_annotation: "分析",
    get_lumenframe: "分析", lumen_seek: "分析", render_preview: "分析",

    assemble_shotlist: "脚本", draft_shotlist: "脚本", set_shotlist: "脚本",
    update_shot: "脚本", get_shotlist: "脚本", refine_shot: "脚本",

    export: "导出", project_export: "导出",
    project_export_otio: "导出", project_import_otio: "导出",

    read_file: "文件", write_file: "文件", copy_in: "文件",
    list_dir: "文件", move_file: "文件", organize_files: "文件",
    run_shell: "文件",

    save_skill: "记忆", recall_skills: "记忆",
    remember: "记忆", log_note: "记忆",

    elicit: "交互",
    spawn_subtasks: "执行", check_job: "执行", wait_for_job: "执行",
  };

  const CATEGORY_DEFAULTS = {
    创建: { running: "正在生成素材", done: "素材已生成" },
    编辑: { running: "正在调整素材", done: "素材已调整" },
    剪辑: { running: "正在编排时间线", done: "时间线已更新" },
    搜索: { running: "正在查找资源", done: "查找完成" },
    分析: { running: "正在检视素材", done: "检视完成" },
    脚本: { running: "正在整理拍摄方案", done: "方案已更新" },
    导出: { running: "正在导出成片", done: "成片已导出" },
    文件: { running: "正在处理文件", done: "文件已处理" },
    记忆: { running: "正在记录", done: "已记录" },
    交互: { running: "等待你的选择", done: "已确认" },
    执行: { running: "正在执行", done: "执行完成" },
  };

  const CATEGORY_ICON = {
    创建: "i-spark",
    编辑: "i-sliders",
    剪辑: "i-scissors",
    搜索: "i-search",
    分析: "i-eye",
    脚本: "i-clapperboard",
    导出: "i-export",
    文件: "i-folder",
    记忆: "i-brain",
    交互: "i-chat-q",
    执行: "i-gear",
  };

  function toolCategory(name) {
    return TOOL_CATEGORY[name] || "执行";
  }

  function safeActivityText(value) {
    const text = String(value || "").trim().replace(/\s+/g, " ");
    if (!text || text.length > ACTIVITY_TEXT_MAX_CHARS || ACTIVITY_TEXT_UNSAFE_RE.test(text)) {
      return "";
    }
    return text;
  }

  function safeProgressReport(value) {
    const text = String(value || "").trim().replace(/\s+/g, " ");
    if (!text || text.length > PROGRESS_REPORT_MAX_CHARS || ACTIVITY_TEXT_UNSAFE_RE.test(text)) {
      return "";
    }
    return text;
  }

  function stripActivityMarkup(value) {
    const withoutBlocks = String(value || "").replace(/<(?:activity|report)\b[^>]*>[\s\S]*?<\/(?:activity|report)\s*>/gi, "");
    return withoutBlocks
      .split(/\r?\n/)
      .filter((line) => !/<\/?(?:activity|report)\b/i.test(line))
      .join("\n")
      .trim();
  }

  function activityLabel(tc) {
    const activityText = safeActivityText(tc.activityText);
    const cat = toolCategory(tc.tool_name);
    const defaults = CATEGORY_DEFAULTS[cat] || CATEGORY_DEFAULTS["执行"];
    if (tc.status === "done" || tc.status === "ok") {
      return activityText || defaults.done;
    }
    if (tc.status === "failed" || tc.status === "error" || tc.status === "timeout") {
      return "未能完成";
    }
    if (tc.status === "gated" || tc.status === "needs_user") {
      return tc.status === "needs_user" ? "等待你的选择" : "等待你的批准";
    }
    if (tc.status === "cancelled") return "已取消";
    return activityText || defaults.running;
  }

  function activityPhase(status) {
    if (status === "done" || status === "ok") return "complete";
    if (status === "failed" || status === "error" || status === "timeout") return "attention";
    if (status === "gated" || status === "needs_user" || status === "cancelled") return "waiting";
    return "active";
  }

  // ── Markdown renderer ───────────────────────────────────────────────

  function renderMarkdown(src) {
    if (!src) return "";
    const text = String(src);

    // Extract fenced code blocks before any other processing
    const codeBlocks = [];
    const withPlaceholders = text.replace(/^```(\w*)\n([\s\S]*?)^```/gm, (_, lang, code) => {
      const idx = codeBlocks.length;
      codeBlocks.push(`<pre class="md-code-block"><code class="lang-${escapeHTML(lang || "text")}">${escapeHTML(code.replace(/\n$/, ""))}</code></pre>`);
      return `\x00CB${idx}\x00`;
    });

    // Split into block-level chunks by double newline
    const blocks = withPlaceholders.split(/\n{2,}/);
    const out = [];

    for (let i = 0; i < blocks.length; i++) {
      const block = blocks[i];

      // Code block placeholder
      if (/^\x00CB\d+\x00$/.test(block.trim())) {
        out.push(codeBlocks[+block.trim().slice(3, -1)]);
        continue;
      }

      // Heading
      const hm = block.match(/^(#{1,6})\s+(.+)$/m);
      if (hm && block.trim().startsWith("#")) {
        const lvl = hm[1].length;
        out.push(`<h${lvl} class="md-h">${mdInline(hm[2])}</h${lvl}>`);
        continue;
      }

      // Horizontal rule
      if (/^(\s*[-*_]){3,}\s*$/.test(block.trim())) {
        out.push(`<hr class="md-hr">`);
        continue;
      }

      // Blockquote
      if (block.trim().startsWith(">")) {
        const inner = block.replace(/^>\s?/gm, "");
        out.push(`<blockquote class="md-blockquote">${renderMarkdown(inner)}</blockquote>`);
        continue;
      }

      // Table
      const tableLines = block.trim().split("\n");
      if (tableLines.length >= 2 && tableLines[0].includes("|") && /^[\s|:-]+$/.test(tableLines[1])) {
        out.push(mdTable(tableLines));
        continue;
      }

      // Unordered list
      if (/^[\t ]*[-*+]\s/.test(block.trim())) {
        out.push(mdList(block, "ul"));
        continue;
      }

      // Ordered list
      if (/^[\t ]*\d+[.)]\s/.test(block.trim())) {
        out.push(mdList(block, "ol"));
        continue;
      }

      // Paragraph (may contain inline code block placeholders on their own line)
      const lines = block.split("\n");
      const paraLines = [];
      for (const ln of lines) {
        if (/^\x00CB\d+\x00$/.test(ln.trim())) {
          if (paraLines.length) {
            out.push(`<p>${mdInline(paraLines.join("\n"))}</p>`);
            paraLines.length = 0;
          }
          out.push(codeBlocks[+ln.trim().slice(3, -1)]);
        } else {
          paraLines.push(ln);
        }
      }
      if (paraLines.length) {
        out.push(`<p>${mdInline(paraLines.join("\n"))}</p>`);
      }
    }
    return out.join("\n");
  }

  function mdInline(s) {
    let r = escapeHTML(s);
    // Inline code (must come before bold/italic to avoid conflicts)
    r = r.replace(/`([^`\n]+?)`/g, '<code class="md-inline-code">$1</code>');
    // Entity references — before bold/italic so underscore-delimited IDs
    // (v_001, s0_shot0) are not consumed by emphasis rules.
    r = r.replace(/\b(v_\d+|img_\d+|aud_\d+|lot_\d+)\b/g,
      '<span class="md-entity" data-entity-kind="asset" data-entity-id="$1" role="link" tabindex="0">$1</span>');
    r = r.replace(/\b(clip_[a-f0-9]{8,16})\b/g,
      '<span class="md-entity" data-entity-kind="clip" data-entity-id="$1" role="link" tabindex="0">$1</span>');
    r = r.replace(/\b(s\d+_shot\d+)\b/g,
      '<span class="md-entity" data-entity-kind="shot" data-entity-id="$1" role="link" tabindex="0">$1</span>');
    r = r.replace(/\b(scene\d+)\b/g,
      '<span class="md-entity" data-entity-kind="scene" data-entity-id="$1" role="link" tabindex="0">$1</span>');
    // Images
    r = r.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img class="md-img" alt="$1" src="$2">');
    // Links
    r = r.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a class="md-link" href="$2" target="_blank" rel="noopener">$1</a>');
    // Bold + italic
    r = r.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    // Bold
    r = r.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    r = r.replace(/__(.+?)__/g, "<strong>$1</strong>");
    // Italic
    r = r.replace(/\*(.+?)\*/g, "<em>$1</em>");
    r = r.replace(/_(.+?)_/g, "<em>$1</em>");
    // Strikethrough
    r = r.replace(/~~(.+?)~~/g, "<del>$1</del>");
    // Line break (trailing double space or backslash)
    r = r.replace(/  \n/g, "<br>");
    r = r.replace(/\\\n/g, "<br>");
    // Single newlines within a paragraph → <br>
    r = r.replace(/\n/g, "<br>");
    return r;
  }

  function mdList(block, tag) {
    const lines = block.split("\n");
    const items = [];
    for (const ln of lines) {
      const m = tag === "ul"
        ? ln.match(/^[\t ]*[-*+]\s+(.*)/)
        : ln.match(/^[\t ]*\d+[.)]\s+(.*)/);
      if (m) items.push(`<li>${mdInline(m[1])}</li>`);
      else if (items.length) {
        items[items.length - 1] = items[items.length - 1].replace("</li>", `<br>${mdInline(ln.trim())}</li>`);
      }
    }
    return `<${tag} class="md-list">${items.join("")}</${tag}>`;
  }

  function mdTable(lines) {
    const parseRow = (ln) => ln.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    const headers = parseRow(lines[0]);
    const alignRow = parseRow(lines[1]);
    const aligns = alignRow.map((c) => {
      if (c.startsWith(":") && c.endsWith(":")) return "center";
      if (c.endsWith(":")) return "right";
      return "left";
    });
    let html = '<table class="md-table"><thead><tr>';
    for (let i = 0; i < headers.length; i++) {
      html += `<th style="text-align:${aligns[i] || "left"}">${mdInline(headers[i])}</th>`;
    }
    html += "</tr></thead><tbody>";
    for (let r = 2; r < lines.length; r++) {
      if (!lines[r].trim()) continue;
      const cells = parseRow(lines[r]);
      html += "<tr>";
      for (let i = 0; i < headers.length; i++) {
        html += `<td style="text-align:${aligns[i] || "left"}">${mdInline(cells[i] || "")}</td>`;
      }
      html += "</tr>";
    }
    html += "</tbody></table>";
    return html;
  }

  function lastEventStorageKey(sessionId) {
    return `lumeri:v3:last-event:${sessionId}`;
  }

  function loadLastEventId(sessionId) {
    if (!sessionId) return null;
    try {
      return window.localStorage.getItem(lastEventStorageKey(sessionId));
    } catch {
      return null;
    }
  }

  function saveLastEventId(sessionId, eventId) {
    if (!sessionId || !eventId) return;
    state.lastEventId = String(eventId);
    try {
      window.localStorage.setItem(lastEventStorageKey(sessionId), state.lastEventId);
    } catch {}
  }

  function clearReconnectTimer() {
    if (!state.reconnectTimer) return;
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }

  function render() {
    els.sessionLabel.textContent = state.sessionTitle || state.sessionId || "—";
    const busy = !state.sessionId || state.turnInProgress;
    els.sendBtn.disabled = !state.sessionId;
    els.uploadBtn.disabled = busy;
    els.inputShell.classList.toggle("is-steering", state.turnInProgress);
    els.inputShell.classList.toggle("is-working", state.turnInProgress);
    if (state.turnInProgress && !voiceInput.listening) {
      els.voiceInputBtn.querySelector("use")?.setAttribute("href", "#i-pause");
      els.voiceInputBtn.setAttribute("aria-label", "停止当前执行");
      els.voiceInputBtn.title = "停止当前执行";
      els.voiceInputBtn.disabled = state.stopPending;
    } else if (!state.turnInProgress && !voiceInput.listening) {
      els.voiceInputBtn.querySelector("use")?.setAttribute("href", "#i-mic");
      els.voiceInputBtn.setAttribute("aria-label", "语音输入");
      els.voiceInputBtn.title = "语音输入";
      els.voiceInputBtn.disabled = false;
    }
    els.promptInput.placeholder = "描述你想要的视频，或输入 / 唤起命令…";
    els.sendBtn.title = state.turnInProgress ? "引导当前执行" : "发送";
    els.sendBtn.setAttribute("aria-label", state.turnInProgress ? "引导当前执行" : "发送");
    document.querySelectorAll(".pt-action-btn, .pt-edit-btn").forEach((b) => { b.disabled = busy; });
    updateEditHint();   // selection-aware split/delete rule wins over the blanket disable above

    const railEmpty = document.getElementById("rail-empty");
    if (!state.turns.length) {
      els.timeline.hidden = true;
      els.emptyState.hidden = false;
      if (railEmpty) railEmpty.hidden = false;
    } else {
      els.emptyState.hidden = true;
      if (railEmpty) railEmpty.hidden = true;
      els.timeline.hidden = false;
      els.timeline.innerHTML = state.turns.map((turn, idx) => renderTurn(turn, idx)).join("");
    }

    // 有素材就自动展开左侧时间轴抽屉（一次性，之后尊重用户手动开合）。
    if (!state._drawerAutoShown && state.assets && state.assets.length > 0) {
      state._drawerAutoShown = true;
      toggleDrawer(true);
    }

    renderAssets();
    renderMediaLibrary();
    renderPlanUi();
  }

  // Plan-mode toggle button + hint/approval bar. Signature-guarded like
  // renderAssets: render() runs on every SSE event, and rebuilding the bar's
  // innerHTML would restart its CSS transitions and drop button focus.
  function renderPlanUi() {
    if (!els.planBtn || !els.planBar) return;
    const sig = `${state.planMode}|${state.planReady}|${state.turnInProgress}|${!!state.sessionId}`;
    if (sig === state._planSig) return;
    state._planSig = sig;

    els.planBtn.disabled = !state.sessionId;
    els.planBtn.classList.toggle("on", state.planMode);
    els.planBtn.title = state.planMode
      ? "计划模式已开启：只查看和规划，不做改动（点击关闭）"
      : "计划模式：只查看和规划，批准后才执行改动";

    if (!state.planMode) {
      els.planBar.hidden = true;
      els.planBar.innerHTML = "";
      return;
    }
    els.planBar.hidden = false;
    if (state.planReady && !state.turnInProgress) {
      els.planBar.innerHTML = `
        <span class="plan-bar-text">计划已就绪</span>
        <span class="plan-bar-actions">
          <button type="button" class="plan-approve" data-plan-approve>批准</button>
          <button type="button" class="plan-refine" data-plan-dismiss>继续规划</button>
        </span>
      `;
    } else {
      els.planBar.innerHTML = `
        <span class="plan-bar-text" title="Lumeri 只查看和规划，等你批准后才会改动项目">计划模式已开启</span>
      `;
    }
  }

  function renderTurn(turn, idx) {
    const callsHtml = buildCallGroups(turn).map(renderCallGroup).join("");
    const bannersHtml = turn.banners.map(renderBanner).join("");
    const guidanceHtml = (turn.guidance || []).map((text) =>
      `<div class="turn-guidance"><span class="turn-guidance-label">引导</span>${escapeHTML(text)}</div>`
    ).join("");
    const hasAssistant = turn.assistantText || turn.streaming;
    const assistantHtml = hasAssistant
      ? `<div class="assistant-bubble${turn.streaming ? " streaming" : ""}">${renderMarkdown(turn.assistantText)}</div>`
      : "";
    const actionsHtml = (hasAssistant && turn.assistantText && !turn.streaming)
      ? `<div class="assistant-actions">
          <button type="button" class="assistant-action-btn" data-copy-assistant="${idx}" title="复制">
            <svg aria-hidden="true"><use href="#i-copy"/></svg>
          </button>
          <button type="button" class="assistant-action-btn" data-speak-assistant="${idx}" title="朗读">
            <svg aria-hidden="true"><use href="#i-volume"/></svg>
          </button>
        </div>`
      : "";
    return `
      ${idx ? '<div class="turn-divider" role="separator"></div>' : ""}
      <div class="user-bubble">${renderMarkdown(turn.userMessage)}</div>
      ${guidanceHtml}
      ${callsHtml}
      ${bannersHtml}
      ${assistantHtml}
      ${actionsHtml}
    `;
  }

  // Keep each activity at the same calm, high-level granularity. The backend
  // still tracks recoveries; exposing that diagnostic arc is not useful here.
  function callGroupStatus(calls) {
    if (calls.some((tc) => tc.status === "running")) return "running";
    if (calls.some((tc) => tc.status === "pending")) return "pending";
    if (calls.some((tc) => tc.status === "needs_user")) return "needs_user";
    if (calls.some((tc) => tc.status === "gated")) return "gated";
    if (calls.some((tc) => tc.status === "failed" || tc.status === "error" || tc.status === "timeout")) return "failed";
    if (calls.every((tc) => tc.status === "cancelled")) return "cancelled";
    return calls[calls.length - 1]?.status || "pending";
  }

  function buildCallGroups(turn) {
    const groups = [];
    for (const tc of turn.orderedCallIds.map((cid) => turn.toolCalls.get(cid)).filter(Boolean)) {
      const category = toolCategory(tc.tool_name);
      const activityText = safeActivityText(tc.activityText);
      const progressReport = safeProgressReport(tc.progressReport);
      const previous = groups[groups.length - 1];
      if (previous?.category === category) {
        previous.calls.push(tc);
        if (!previous.progressReport && progressReport) previous.progressReport = progressReport;
        if (!previous.activityText && activityText) previous.activityText = activityText;
      } else {
        groups.push({ calls: [tc], category, activityText, progressReport });
      }
    }
    return groups;
  }

  function renderCallGroup(group) {
    const last = group.calls[group.calls.length - 1];
    const progressReport = safeProgressReport(group.progressReport);
    const reportHtml = progressReport
      ? `<div class="midturn-report" aria-label="Lumeri 阶段汇报">
          <span class="midturn-report-label">Lumeri</span>
          <div>${renderMarkdown(progressReport)}</div>
        </div>`
      : "";
    return reportHtml + renderToolCall({
      ...last,
      activityText: group.activityText || last.activityText,
      status: callGroupStatus(group.calls),
    });
  }

  function renderToolCall(tc) {
    const label = activityLabel(tc);
    const phase = activityPhase(tc.status);
    const category = toolCategory(tc.tool_name);
    const iconId = CATEGORY_ICON[category] || "i-gear";
    return `
      <div class="activity-line activity-line--${phase}" aria-label="${escapeHTML(category + ' ' + label)}">
        <svg class="activity-icon" aria-hidden="true"><use href="#${iconId}"/></svg>
        <span class="activity-category">${escapeHTML(category)}</span>
        <span class="activity-desc">${escapeHTML(label)}</span>
      </div>
    `;
  }

  function renderBanner(banner) {
    const cls = banner.kind === "budget" ? "banner-budget"
              : banner.kind === "turn_error" ? "banner-turn-error"
              : banner.kind === "plan" ? "banner-plan"
              : banner.kind === "info" ? "banner-info"
              : "banner-unknown";
    return `<div class="banner ${cls}">${escapeHTML(banner.text)}</div>`;
  }

  function renderAssets() {
    // Guard: only touch the DOM when the asset set actually changed. render() runs
    // on every SSE event (see es.onmessage) and every 3s timeline poll; without
    // this guard each call reset assetGrid.innerHTML, destroying and recreating
    // every <video>/<audio>/<img> and forcing the browser to re-fetch each
    // /sessions/{id}/assets/{aid}. While a turn streamed that was several
    // re-fetches per second per asset — the "endless /assets/v_002 requests".
    const sig = !state.assets.length
      ? "empty"
      : state.sessionId + "|" + state.assets.map((a) =>
          `${a.asset_id}:${a.kind}:${a.final ? 1 : 0}:${a.source}:${a.summary || ""}`
        ).join(",");
    if (sig === state._assetsSig) return;
    state._assetsSig = sig;

    if (!state.assets.length) {
      els.assetGrid.innerHTML = "";
      return;
    }
    els.assetGrid.innerHTML = state.assets.map((a) => {
      const url = `/sessions/${state.sessionId}/assets/${a.asset_id}`;
      const playerHtml = a.kind === "image"
        ? `<img src="${url}" alt="${a.asset_id}" />`
        : a.kind === "audio"
          ? `<audio src="${url}" controls preload="metadata"></audio>`
          : `<video src="${url}" controls preload="metadata"${a.final ? " autoplay muted" : ""}></video>`;
      return `
        <div class="asset-card${a.final ? " final" : ""}" data-asset-id="${a.asset_id}">
          ${playerHtml}
          <div class="asset-meta">
            ${a.final ? `<svg class="asset-final" viewBox="0 0 24 24" role="img" aria-label="最终成片"><use href="#i-check-circle"/></svg>` : ""}
            <span class="asset-id">${a.asset_id}</span> · ${escapeHTML(a.source)} · ${escapeHTML(a.summary || "")}
          </div>
        </div>
      `;
    }).join("");
  }

  function renderMediaLibrary() {
    if (!els.mediaLibraryGrid) return;
    if (state.mediaLibraryStatus === "loading") {
      els.mediaLibraryGrid.innerHTML = `<p class="placeholder">加载中…</p>`;
      return;
    }
    if (state.mediaLibraryStatus === "signed-out") {
      els.mediaLibraryGrid.innerHTML = `<p class="placeholder">登录后可用</p>`;
      return;
    }
    if (!state.mediaLibrary.length) {
      els.mediaLibraryGrid.innerHTML = `<p class="placeholder">暂无素材</p>`;
      return;
    }
    els.mediaLibraryGrid.innerHTML = state.mediaLibrary.map((asset) => {
      const assetId = asset.asset_id || asset.id || "";
      const summary = asset.annotation_summary || {};
      const kind = asset.media_kind || "media";
      // 机器话不示人：hash 文件名/内部 ID 退到 title 悬停，卡面只留人话
      const kindLabel = LIBRARY_KIND_LABEL[kind] || "素材";
      const title = libraryDisplayName(asset, kindLabel);
      const allTags = [...(summary.tags || []), ...(summary.labels || [])];
      const shownTags = allTags.slice(0, 2);
      const moreTags = allTags.length - shownTags.length;
      const markerCount = Number(summary.count || 0);
      const anns = state.mediaAnnotations.get(assetId) || [];
      const annHtml = anns.length
        ? `<div class="annotation-list">${anns.map(renderAnnotation).join("")}</div>`
        : "";
      // 缩略图缺失 → 类型图标占位，不给黑块
      const thumb = asset.thumbnail_src
        ? `<img class="library-thumb" src="${escapeHTML(asset.thumbnail_src)}" alt="" loading="lazy" />`
        : `<div class="library-thumb blank" aria-hidden="true"><svg viewBox="0 0 24 24"><use href="#${LIBRARY_KIND_ICON[kind] || "i-file"}"/></svg></div>`;
      const tagsHtml = (markerCount || shownTags.length)
        ? `<div class="library-tags">
            ${markerCount ? `<span title="标记数"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-marker"/></svg>${markerCount}</span>` : ""}
            ${shownTags.map((tag) => `<span>${escapeHTML(tag)}</span>`).join("")}
            ${moreTags > 0 ? `<span>+${moreTags}</span>` : ""}
          </div>`
        : "";
      return `
        <div class="library-card" data-library-asset="${escapeHTML(assetId)}" title="${escapeHTML(asset.name || assetId)}">
          ${thumb}
          <div class="library-card-body">
            <div class="library-title">${escapeHTML(title)}</div>
            <div class="library-meta">${escapeHTML(kindLabel)}${kind === "image" ? "" : (formatMediaDuration(asset.duration) ? " · " + escapeHTML(formatMediaDuration(asset.duration)) : "")}</div>
            ${tagsHtml}
            <div class="library-card-actions">
              <button type="button" class="library-small-btn icon-btn" title="标注" aria-label="标注" data-library-annotate="${escapeHTML(assetId)}"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-wand"/></svg></button>
              <button type="button" class="library-small-btn icon-btn" title="标记" aria-label="标记" data-library-load="${escapeHTML(assetId)}"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-marker"/></svg></button>
            </div>
            ${annHtml}
          </div>
        </div>
      `;
    }).join("");
  }

  const LIBRARY_KIND_LABEL = { video: "视频", image: "图片", audio: "音频" };
  const LIBRARY_KIND_ICON = { video: "i-film", image: "i-image", audio: "i-music" };

  // Human title for a media card: strip the extension; if what's left still
  // reads as a machine id, fall back to "未命名<类型>". A hash must be
  // hex-only AND contain a digit AND be long — so a readable name that merely
  // happens to use a–f letters (e.g. "faceded-beef") is NOT mistaken for one.
  function libraryDisplayName(asset, kindLabel) {
    const base = String(asset.name || "").replace(/\.[a-z0-9]{2,5}$/i, "");
    const compact = base.replace(/[-_]/g, "");
    const looksHashed = compact.length >= 16 && /^[0-9a-f]+$/i.test(compact) && /[0-9]/.test(compact);
    const machine = !base || looksHashed || /^asset[_-]/i.test(base);
    return machine ? `未命名${kindLabel}` : base;
  }

  // Media duration for a card: "14.7 秒" under a minute, "2:05" beyond.
  // Distinct from formatSeconds (used for annotation timecodes) — returns ""
  // for missing/zero so images and durationless assets show no "0.0s".
  function formatMediaDuration(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n) || n <= 0) return "";
    if (n < 60) return `${n < 10 ? n.toFixed(1) : Math.round(n)} 秒`;
    const m = Math.floor(n / 60);
    const s = Math.round(n % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function renderAnnotation(annotation) {
    const range = annotation.scope === "time_range"
      ? `${formatSeconds(annotation.start_sec)}-${formatSeconds(annotation.end_sec)}`
      : annotation.scope;
    const tags = (annotation.tags || []).slice(0, 4).map((tag) => `<span>${escapeHTML(tag)}</span>`).join("");
    return `
      <div class="annotation-item">
        <div><strong>${escapeHTML(annotation.label || "marker")}</strong> <span>${escapeHTML(range)}</span></div>
        ${annotation.note ? `<p>${escapeHTML(annotation.note)}</p>` : ""}
        ${tags ? `<div class="library-tags">${tags}</div>` : ""}
      </div>
    `;
  }

  function formatSeconds(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return "0.0s";
    return `${n.toFixed(1)}s`;
  }

  // ── event handlers (one per kind, no silent drop) ──────────────────

  const handlers = {
    turn_start: () => {
      state.turnInProgress = true;
      state.stopPending = false;
      if (state.currentTurn) {
        state.currentTurn.streaming = false;
      }
    },
    turn_guidance_queued: () => {},
    turn_guidance_applied: () => {
      const t = state.currentTurn;
      if (!t) return;
      // Text streamed before the safe steering boundary is a superseded draft.
      t.assistantText = "";
      t.pendingAssistantText = "";
      t.streaming = false;
    },
    turn_cancelled: (ev) => {
      dismissAskDock();
      state.turnInProgress = false;
      state.stopPending = false;
      const t = state.currentTurn;
      if (!t) return;
      for (const tc of t.toolCalls.values()) {
        if (tc.status === "pending" || tc.status === "running") tc.status = "cancelled";
      }
      t.pendingAssistantText = "";
      t.streaming = false;
      t.complete = true;
      t.banners.push({
        kind: "info",
        text: String(ev.message || "已停止当前执行，已经完成的进度会保留"),
      });
      autoSaveSession();
    },
    model_text_delta: (ev) => {
      const t = state.currentTurn;
      if (!t) return;
      // A provider can emit private-looking lead-in text immediately before a
      // tool call. Hold all deltas until the turn completes; a following tool
      // proposal discards the buffer, while a text-only turn releases it as the
      // final user-facing reply.
      t.pendingAssistantText += ev.delta;
    },
    model_tool_call_start: (ev) => {
      const t = state.currentTurn;
      if (!t) return;
      // Text streamed before a tool proposal can be internal deliberation.
      // Discard it rather than turning it into a user-facing work log.
      t.assistantText = "";
      t.pendingAssistantText = "";
      t.streaming = false;
      t.toolCalls.set(ev.call_id, {
        call_id: ev.call_id,
        tool_name: ev.tool_name,
        status: "pending",
        activityText: "",
        progressReport: "",
      });
      t.orderedCallIds.push(ev.call_id);
    },
    // Raw arguments are deliberately never retained by the display layer.
    // The only text allowed through is Lumeri's backend-validated activity label.
    model_tool_call_ready: (ev) => {
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (tc) {
        tc.activityText = safeActivityText(ev.activity_text);
        tc.progressReport = safeProgressReport(ev.progress_report);
      }
    },
    tool_exec_start: (ev) => {
      if (ev.agent_id) return;
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (tc) tc.status = "running";
    },
    tool_exec_progress: () => {},
    tool_exec_result: (ev) => {
      if (ev.agent_id) return;
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (!tc) return;
      tc.status = "done";
      const assetId = ev.result?.asset_id;
      if (assetId) {
        state.assets.push({
          asset_id: assetId,
          kind: ev.result?.kind || inferKindFromAssetId(assetId),
          summary: ev.result?.summary || "",
          source: "tool",
          final: false,
        });
      }
    },
    tool_exec_error: (ev) => {
      if (ev.agent_id) return;
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (tc) {
        tc.status = "failed";
      }
    },
    // The parent spawn_subtasks row already says that work is happening in
    // parallel. Child goals, summaries, paths, and internal tool calls stay out
    // of the user-facing activity stream.
    subagent_start: () => {},
    subagent_result: () => {},
    budget_gate: (ev) => {
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (tc) tc.status = "gated";
      if (t && !t.banners.some((b) => b.kind === "budget")) {
        t.banners.push({
          kind: "budget",
          text: "当前任务已暂停",
        });
      }
    },
    plan_gate: (ev) => {
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (tc) tc.status = "gated";
      if (t && !t.banners.some((b) => b.kind === "plan")) {
        t.banners.push({
          kind: "plan",
          text: "计划模式下等待你的批准",
        });
      }
    },
    plan_mode_changed: (ev) => {
      state.planMode = !!ev.enabled;
      if (!state.planMode) state.planReady = false;
    },
    completion_check: () => {
      // Host-side one-shot goal check re-prompts the model for a FINAL reply
      // (goal-check / visual self-check / failure disclosure). The text the
      // model streamed before this gate was only a draft — discard it so the
      // post-gate round becomes the single user-facing message. Left as-is,
      // model_text_delta would APPEND the gate round onto the draft and the
      // user sees the same answer twice (natural reply + report-style restate).
      const t = state.currentTurn;
      if (!t) return;
      t.assistantText = "";
      t.pendingAssistantText = "";
      t.streaming = false;
    },
    turn_wrapup: (ev) => {
      // This is Lumeri's hand-off, not a generic host banner. For an
      // incomplete-goal stop the backend deliberately released the model's
      // own closing report just before this event; preserve that richer text.
      // Other stop reasons may leave a partial stream fragment, so use the
      // backend's deterministic wrap-up instead of presenting a broken draft.
      dismissAskDock();
      const t = state.currentTurn;
      state.turnInProgress = false;
      state.stopPending = false;
      if (!t) return;
      const modelReport = ev.reason === "incomplete_goal"
        ? stripActivityMarkup(t.pendingAssistantText).trim()
        : "";
      const fallbackReport = String(ev.message || "").trim();
      t.assistantText = modelReport || fallbackReport
        || "我先停在这里。当前进度已经保留；你让我继续，我会从这里接着处理。";
      t.pendingAssistantText = "";
      t.streaming = false;
      t.complete = true;
      autoSaveSession();
    },
    ask_question: (ev) => {
      const q = ev.question || {};
      state.pendingAsk = {
        question_id: q.question_id,
        question: q,
      };
      renderAskDock();
    },
    timeline_op: () => {
      // Timeline patch landed: refresh the project timeline panel immediately
      // rather than waiting for the next poll interval.
      fetchProjectTimeline({ force: true });
    },
    protocol_hello: (ev) => {
      // Per-connection id-less frame at the top of every stream. The web
      // client is served BY the backend, so a mismatch is near-impossible —
      // record it for debugging, no banner.
      state.protocolVersion = ev.protocol_version;
    },
    replay_gap: (ev) => {
      const text = "连接已恢复，正在同步最新状态";
      const banner = {
        kind: "info",
        text,
      };
      if (state.currentTurn) state.currentTurn.banners.push(banner);
      state.errors.push(text);
      state.turnInProgress = false;
      state.stopPending = false;
      const sessionId = state.sessionId;
      if (state.eventSource) {
        state.eventSource.close();
        state.eventSource = null;
      }
      refreshSessionState().then(() => {
        if (state.sessionId === sessionId) connectSse(sessionId);
      }).catch((err) => {
        state.errors.push(`session refresh failed: ${err.message}`);
        scheduleReconnect(1000);
      }).finally(render);
    },
    turn_complete: (ev) => {
      dismissAskDock();
      const t = state.currentTurn;
      state.turnInProgress = false;
      state.stopPending = false;
      if (!t) return;
      t.assistantText = stripActivityMarkup(t.pendingAssistantText);
      t.pendingAssistantText = "";
      t.streaming = false;
      t.complete = true;
      // Backend now sends only user-facing deliverables in final_asset_ids
      // (usually export outputs). Mark every listed deliverable as final.
      const finals = ev.deliverable_asset_ids || ev.final_asset_ids || [];
      for (const deliverable of finals) {
        const existing = state.assets.find((a) => a.asset_id === deliverable);
        if (existing) existing.final = true;
      }
      // Refresh timeline after every completed turn — verb results may have
      // updated the project even if no timeline_op event was fired this turn.
      fetchProjectTimeline({ force: true });
      // While planning, a completed turn means the plan text is on screen —
      // surface the approval bar.
      if (state.planMode) state.planReady = true;
      // Auto-save after every completed turn; auto-title at turn 1 and 5.
      autoSaveSession();
      if (state.userMessageCount === 1 || state.userMessageCount === 5) {
        autoGenerateTitle();
      }
    },
    turn_error: (ev) => {
      dismissAskDock();
      state.turnInProgress = false;
      state.stopPending = false;
      const t = state.currentTurn;
      if (t) {
        t.streaming = false;
        t.complete = true;
        // An "incomplete_goal" stop is not a failure — the model has already
        // delivered its own words (when the turn did work) and a soft
        // turn_wrapup note follows. Render it gently, never as a red interrupt.
        // Genuine host failures (budget, doom loop, stream error) still show
        // the turn_error banner.
        if (ev.reason !== "incomplete_goal") {
          t.banners.push({ kind: "turn_error", text: "本轮任务暂时暂停" });
        }
      }
    },
  };

  function dispatch(ev) {
    // Debug hook: raw event log accessible from DevTools console and test harnesses.
    (window.__lumeriEvents = window.__lumeriEvents || []).push(ev);
    const handler = handlers[ev.kind];
    if (!handler) {
      const t = state.currentTurn;
      const banner = { kind: "unknown", text: "收到一个暂未显示的活动状态" };
      if (t) t.banners.push(banner);
      state.errors.push(banner.text);
      console.error("unhandled SSE event kind", ev);
      return;
    }
    handler(ev);
  }

  function inferKindFromAssetId(assetId) {
    if (String(assetId).startsWith("img_")) return "image";
    if (String(assetId).startsWith("aud_")) return "audio";
    return "video";
  }

  // ── ask dock (declarative answering) ────────────────────────────────

  function dismissAskDock() {
    state.pendingAsk = null;
    if (els.askDock) {
      els.askDock.hidden = true;
      els.askDock.innerHTML = "";
    }
  }

  function _askControlDom(key, ctrl) {
    const wrap = document.createElement("div");
    wrap.className = "ask-field";
    wrap.dataset.controlKey = key;

    const label = document.createElement("span");
    label.className = "ask-field-label";
    label.textContent = key;
    wrap.appendChild(label);

    const errEl = document.createElement("div");
    errEl.className = "ask-field-error";

    const type = ctrl.type;
    if (type === "select") {
      const group = document.createElement("div");
      group.className = "ask-radio-group";
      for (const opt of (ctrl.options || [])) {
        const lbl = document.createElement("label");
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = `ask-${key}`;
        radio.value = opt.value;
        if (ctrl.default != null && opt.value === ctrl.default) radio.checked = true;
        lbl.appendChild(radio);
        lbl.appendChild(document.createTextNode(opt.label));
        group.appendChild(lbl);
      }
      wrap.appendChild(group);
    } else if (type === "multi_select") {
      const group = document.createElement("div");
      group.className = "ask-check-group";
      for (const opt of (ctrl.options || [])) {
        const lbl = document.createElement("label");
        const chk = document.createElement("input");
        chk.type = "checkbox";
        chk.name = `ask-${key}`;
        chk.value = opt.value;
        lbl.appendChild(chk);
        lbl.appendChild(document.createTextNode(opt.label));
        group.appendChild(lbl);
      }
      wrap.appendChild(group);
    } else if (type === "text") {
      const inp = ctrl.multiline ? document.createElement("textarea") : document.createElement("input");
      inp.className = "ask-text-input";
      inp.name = `ask-${key}`;
      if (ctrl.placeholder) inp.placeholder = ctrl.placeholder;
      if (ctrl.max_length) inp.maxLength = ctrl.max_length;
      if (ctrl.multiline) inp.rows = 3;
      wrap.appendChild(inp);
    } else if (type === "slider") {
      const sw = document.createElement("div");
      sw.className = "ask-slider-wrap";
      const range = document.createElement("input");
      range.type = "range";
      range.name = `ask-${key}`;
      range.min = ctrl.min ?? 0;
      range.max = ctrl.max ?? 100;
      range.step = ctrl.step ?? 1;
      range.value = ctrl.default ?? ctrl.min ?? 0;
      const valSpan = document.createElement("span");
      valSpan.className = "ask-slider-val";
      valSpan.textContent = range.value;
      range.addEventListener("input", () => { valSpan.textContent = range.value; });
      sw.appendChild(range);
      sw.appendChild(valSpan);
      wrap.appendChild(sw);
    } else if (type === "panel") {
      const pg = document.createElement("div");
      pg.className = "ask-panel-group";
      if (ctrl.description) {
        const t = document.createElement("div");
        t.className = "ask-panel-group-title";
        t.textContent = ctrl.description;
        pg.appendChild(t);
      }
      for (const [fk, fv] of Object.entries(ctrl.fields || {})) {
        pg.appendChild(_askControlDom(`${key}.${fk}`, fv));
      }
      wrap.appendChild(pg);
    } else {
      const note = document.createElement("div");
      note.style.cssText = "font-size:11px;opacity:.6";
      note.textContent = `(${type || "unknown"} control — 不支持在线编辑)`;
      wrap.appendChild(note);
    }

    wrap.appendChild(errEl);
    return wrap;
  }

  function _collectAskValue(key, ctrl, dock) {
    const type = ctrl.type;
    if (type === "select") {
      const checked = dock.querySelector(`input[name="ask-${CSS.escape(key)}"]:checked`);
      return checked ? checked.value : null;
    }
    if (type === "multi_select") {
      return [...dock.querySelectorAll(`input[name="ask-${CSS.escape(key)}"]:checked`)]
        .map((c) => c.value);
    }
    if (type === "text") {
      const inp = dock.querySelector(`[name="ask-${CSS.escape(key)}"]`);
      return inp ? inp.value : "";
    }
    if (type === "slider") {
      const inp = dock.querySelector(`input[name="ask-${CSS.escape(key)}"]`);
      return inp ? parseFloat(inp.value) : null;
    }
    if (type === "panel") {
      const result = {};
      for (const [fk, fv] of Object.entries(ctrl.fields || {})) {
        result[fk] = _collectAskValue(`${key}.${fk}`, fv, dock);
      }
      return result;
    }
    return null;
  }

  function renderAskDock() {
    const dock = els.askDock;
    if (!dock || !state.pendingAsk) return;
    const q = state.pendingAsk.question;

    dock.innerHTML = "";

    const title = document.createElement("div");
    title.className = "ask-dock-title";
    title.textContent = q.title || "需要你的输入";
    dock.appendChild(title);

    const desc = document.createElement("div");
    desc.className = "ask-dock-desc";
    desc.textContent = q.description || "";
    dock.appendChild(desc);

    for (const [key, ctrl] of Object.entries(q.controls || {})) {
      dock.appendChild(_askControlDom(key, ctrl));
    }

    const actions = document.createElement("div");
    actions.className = "ask-actions";
    const submitBtn = document.createElement("button");
    submitBtn.className = "ask-submit";
    submitBtn.textContent = "提交";
    submitBtn.addEventListener("click", () => submitAskAnswer());
    actions.appendChild(submitBtn);
    dock.appendChild(actions);

    dock.hidden = false;
  }

  async function submitAskAnswer() {
    if (!state.pendingAsk || !state.sessionId) return;
    const q = state.pendingAsk.question;
    const dock = els.askDock;

    const answers = {};
    for (const [key, ctrl] of Object.entries(q.controls || {})) {
      answers[key] = _collectAskValue(key, ctrl, dock);
    }

    const submitBtn = dock.querySelector(".ask-submit");
    if (submitBtn) submitBtn.disabled = true;

    dock.querySelectorAll(".ask-field-error").forEach((e) => { e.textContent = ""; });

    try {
      const res = await fetch(`/sessions/${state.sessionId}/ask_response`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question_id: state.pendingAsk.question_id,
          answers,
        }),
      });
      if (res.ok) {
        dismissAskDock();
        return;
      }
      const body = await res.json().catch(() => ({}));
      if (res.status === 422 && body.field_errors) {
        for (const [field, msg] of Object.entries(body.field_errors)) {
          const el = dock.querySelector(`[data-control-key="${CSS.escape(field)}"] .ask-field-error`);
          if (el) el.textContent = msg;
        }
      } else {
        state.currentTurn?.banners.push({
          kind: "info",
          text: "提交未成功，请稍后重试",
        });
        render();
      }
    } catch (err) {
      state.currentTurn?.banners.push({
        kind: "info",
        text: "提交未成功，请稍后重试",
      });
      render();
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  // ── SSE connection ──────────────────────────────────────────────────

  // Status is a bare dot — the state name lives in title/aria-label only.
  function setConnPill(text, cls) {
    els.connPill.title = text;
    els.connPill.setAttribute("aria-label", text);
    els.connPill.className = `status-pill ${cls}`;
  }

  function scheduleReconnect(delayMs = 1000) {
    clearReconnectTimer();
    state.reconnectTimer = window.setTimeout(() => {
      state.reconnectTimer = null;
      if (state.sessionId) connectSse(state.sessionId);
    }, delayMs);
  }

  function connectSse(sessionId) {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    const lastId = state.lastEventId || loadLastEventId(sessionId);
    const qs = lastId ? `?last_event_id=${encodeURIComponent(lastId)}` : "";
    const es = new EventSource(`/sessions/${sessionId}/stream${qs}`);
    es.onopen = () => {
      clearReconnectTimer();
      setConnPill("live", "live");
    };
    es.onerror = () => {
      setConnPill("reconnecting", "reconnecting");
      scheduleReconnect(1500);
    };
    es.onmessage = (e) => {
      try {
        if (e.lastEventId) saveLastEventId(sessionId, e.lastEventId);
        const ev = JSON.parse(e.data);
        dispatch(ev);
        render();
      } catch {
        const banner = { kind: "unknown", text: "收到一个无法读取的活动状态" };
        state.currentTurn?.banners.push(banner);
        render();
      }
    };
    state.eventSource = es;
  }

  // ── Project timeline (CapCut-style editor) ──────────────────────────
  // A px/second timeline: adaptive ruler, wheel/key zoom, multi video+audio
  // tracks, per-clip filmstrip + waveform (when the clip exposes asset_id),
  // draggable playhead, client-side markers, and drag/move/trim/split/delete
  // wired to the SAME /sessions/{id}/timeline/op endpoint as the model verbs.

  const TL = {
    pps: 64, minPps: 8, maxPps: 480,
    snap: true,
    playhead: 0,
    scrubbing: false,
    drag: null,
    markers: [],            // {time,label,color} — client-side only (no backend marker model yet)
    extraTracks: [],        // client-added empty display lanes (no backend add_track op)
    built: false,
    model: null,
    rulerCtx: null,
    filmstrip: new Map(),   // key -> string[] (data URLs)
    filmstripBusy: new Set(),
    wave: new Map(),        // assetId -> number[] peaks
    waveBusy: new Set(),
    audioCtx: null,
  };
  const TL_RULER_H = 26, TL_TRACK_H = 58, TL_MIN_CONTENT = 30;
  const TL_MARKER_COLORS = ["#ff3b4e", "#ffb13b", "#4ea1ff", "#7a5cff", "#2fd178"];
  const TL_CLIP_COLOR = {
    video:   ["#1d4a34", "#37a06a"],
    image:   ["#34295f", "#7a5cff"],
    lottie:  ["#203f46", "#35c3b8"],
    paint:   ["#4c2148", "#ff5ac8"],
    audio:   ["#1f3a5c", "#4ea1ff"],
    text:    ["#4a2c18", "#d98a4e"],
    overlay: ["#34295f", "#7a5cff"],
  };

  const tlPanel   = () => document.getElementById("project-timeline-panel");
  const tlScroll  = () => document.getElementById("ptl-scroll");
  const tlContent = () => document.getElementById("ptl-content");
  const tlRuler   = () => document.getElementById("ptl-ruler");
  const tlHeaders = () => document.getElementById("ptl-headers");
  const timeToX = (t) => t * TL.pps;
  const pxToTime = (px) => px / TL.pps;

  function fmtTC(s) {
    if (!isFinite(s) || s < 0) s = 0;
    const fps = Math.round((TL.model && TL.model.fps) || 30);
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    const f = Math.floor((s - Math.floor(s)) * fps);
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
  }

  function trackCompatible(mediaKind, trackKind) {
    if (mediaKind === "audio") return trackKind === "audio";
    return trackKind === "video" || trackKind === "overlay" || trackKind === "text";
  }

  // Build the model the editor lays out: real tracks if present, else default
  // empty Video+Audio lanes so the timeline shows on load. Client-added lanes
  // (extraTracks) are merged in unless a real track already uses the id.
  function timelineModel(data) {
    const d = data || state.projectTimeline || {};
    let tracks = Array.isArray(d.tracks) ? d.tracks.map((t) => ({ ...t })) : [];
    if (!tracks.length) {
      tracks = [
        { id: "V1", kind: "video", name: "视频", clips: [] },
        { id: "A1", kind: "audio", name: "音频", clips: [] },
      ];
    }
    const rank = (k) => (k === "audio" ? 2 : k === "overlay" || k === "text" ? 0 : 1);
    tracks = tracks.map((t, i) => ({ ...t, _i: i })).sort((a, b) => rank(a.kind) - rank(b.kind) || a._i - b._i);
    let lastEnd = 0;
    for (const t of tracks) for (const c of (t.clips || [])) lastEnd = Math.max(lastEnd, (c.start || 0) + (c.duration || 0));
    const contentDur = Math.max(d.duration || 0, lastEnd, TL_MIN_CONTENT);
    return { tracks, duration: d.duration || 0, contentDur, fps: d.fps || 30, width: d.width || 1920, height: d.height || 1080, patch_seq: d.patch_seq || 0 };
  }

  function buildTimelineShell() {
    if (TL.built) return;
    const panel = tlPanel();
    if (!panel) return;
    panel.hidden = false;
    panel.classList.add("ptl");
    panel.innerHTML = `
      <div class="ptl-toolbar">
        <div class="ptl-tgroup">
          <button class="ptl-btn ptl-ico-btn pt-edit-btn" id="ptl-undo" title="撤销 (⌘Z)"><svg viewBox="0 0 16 16"><path d="M6.5 4 3.5 7l3 3"/><path d="M3.5 7H10a3.5 3.5 0 0 1 0 7H7.5"/></svg></button>
          <button class="ptl-btn ptl-ico-btn pt-edit-btn" id="ptl-redo" title="重做"><svg viewBox="0 0 16 16"><path d="M9.5 4l3 3-3 3"/><path d="M12.5 7H6a3.5 3.5 0 0 0 0 7h2.5"/></svg></button>
        </div>
        <div class="ptl-sep"></div>
        <div class="ptl-tgroup">
          <button class="ptl-btn ptl-ico-btn pt-edit-btn" id="ptl-split" title="在指针处分割 (S)" aria-label="分割"><svg viewBox="0 0 16 16"><path d="M8 2.5v11"/><rect x="2.6" y="5" width="3.4" height="6" rx="1.1"/><rect x="10" y="5" width="3.4" height="6" rx="1.1"/></svg></button>
          <button class="ptl-btn ptl-ico-btn pt-edit-btn" id="ptl-delete" title="删除所选 (Del)" aria-label="删除"><svg viewBox="0 0 16 16"><path d="M3 4.5h10"/><path d="M6 4.5V3h4v1.5"/><path d="M4.6 4.5 5.1 13.3h5.8l.5-8.8"/><path d="M6.9 6.8v4.3M9.1 6.8v4.3"/></svg></button>
          <button class="ptl-btn ptl-ico-btn pt-edit-btn" id="ptl-marker" title="在指针处加标记 (M)" aria-label="标记"><svg viewBox="0 0 16 16"><path d="M4.5 2v12"/><path d="M4.5 2.8h7.3l-1.8 2.6 1.8 2.6H4.5"/></svg></button>
        </div>
        <div class="ptl-sep"></div>
        <button class="ptl-btn ptl-ico-btn ptl-toggle" id="ptl-snap" title="吸附对齐" aria-label="吸附对齐"><svg viewBox="0 0 16 16"><path d="M4 2.5v5a4 4 0 0 0 8 0v-5"/><path d="M4 2.5h2.4M9.6 2.5H12M4 6h2.4M9.6 6H12"/></svg></button>
        <div class="ptl-spacer"></div>
        <div class="ptl-tc" id="ptl-tc">00:00:00</div>
        <div class="ptl-zoom">
          <button class="ptl-btn ptl-ico-btn" id="ptl-zoom-out" title="缩小 (−)"><svg viewBox="0 0 16 16"><circle cx="6.8" cy="6.8" r="3.8"/><path d="M9.6 9.6 13.5 13.5"/><path d="M5 6.8h3.6"/></svg></button>
          <input type="range" id="ptl-zoom" class="ptl-range" min="${TL.minPps}" max="${TL.maxPps}" value="${TL.pps}" />
          <button class="ptl-btn ptl-ico-btn" id="ptl-zoom-in" title="放大 (＋)"><svg viewBox="0 0 16 16"><circle cx="6.8" cy="6.8" r="3.8"/><path d="M9.6 9.6 13.5 13.5"/><path d="M5 6.8h3.6M6.8 5v3.6"/></svg></button>
        </div>
      </div>
      <div class="ptl-main">
        <div class="ptl-ruler-row">
          <div class="ptl-corner"></div>
          <canvas id="ptl-ruler"></canvas>
        </div>
        <div class="ptl-lanes-row">
          <div class="ptl-headers" id="ptl-headers"></div>
          <div class="ptl-scroll" id="ptl-scroll"><div class="ptl-content" id="ptl-content"></div></div>
        </div>
      </div>
      <div class="pt-quick-actions" id="pt-quick-actions">
        <button class="pt-action-btn" data-cmd="export the project at 1080p quality" title="导出 1080p"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-export"/></svg>1080p</button>
        <button class="pt-action-btn" data-cmd="export the project as draft quality" title="导出草稿"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-export"/></svg>草稿</button>
        <button class="pt-action-btn" data-cmd="add a title overlay at the start of the timeline" title="在片头加标题"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-text"/></svg>标题</button>
        <button class="pt-action-btn" data-cmd="get the current timeline layout" title="获取时间线布局"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-layers"/></svg>布局</button>
      </div>`;

    TL.rulerCtx = tlRuler().getContext("2d");
    TL.built = true;
    loadMarkers();

    document.getElementById("ptl-undo").onclick = () => { if (editingEnabled()) postTimelineOp({ op: "undo", steps: 1 }); };
    document.getElementById("ptl-redo").onclick = () => { /* backend exposes no redo op yet */ };
    document.getElementById("ptl-split").onclick = splitSelected;
    document.getElementById("ptl-delete").onclick = deleteSelected;
    document.getElementById("ptl-marker").onclick = () => addMarker(TL.playhead);
    const snapBtn = document.getElementById("ptl-snap");
    const syncSnap = () => snapBtn.classList.toggle("on", TL.snap);
    snapBtn.onclick = () => { TL.snap = !TL.snap; syncSnap(); };
    syncSnap();
    const zoom = document.getElementById("ptl-zoom");
    zoom.oninput = () => setPps(+zoom.value);
    document.getElementById("ptl-zoom-in").onclick = () => setPps(TL.pps * 1.25);
    document.getElementById("ptl-zoom-out").onclick = () => setPps(TL.pps / 1.25);

    const scroll = tlScroll();
    scroll.addEventListener("scroll", () => {
      drawRuler();
      tlHeaders().style.transform = `translateY(${-scroll.scrollTop}px)`;
    });
    // Mouse wheel = zoom anchored at the cursor (like 剪映). Shift-wheel or a
    // horizontal-dominant wheel = pan. ⌘/ctrl also zoom (trackpad pinch).
    const wheelZoom = (e, rectEl) => {
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        scroll.scrollLeft += (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY);
        return;
      }
      if (!e.deltaY) return;
      e.preventDefault();
      const px = e.clientX - rectEl.getBoundingClientRect().left;
      setPps(TL.pps * (e.deltaY < 0 ? 1.12 : 0.89), pxToTime(scroll.scrollLeft + px), px);
    };
    scroll.addEventListener("wheel", (e) => wheelZoom(e, scroll), { passive: false });

    const ruler = tlRuler();
    const seek = (clientX) => {
      const r = ruler.getBoundingClientRect();
      setPlayhead(Math.max(0, pxToTime(scroll.scrollLeft + (clientX - r.left))));
    };
    ruler.addEventListener("pointerdown", (e) => { try { ruler.setPointerCapture(e.pointerId); } catch {} TL.scrubbing = true; seek(e.clientX); });
    ruler.addEventListener("pointermove", (e) => { if (TL.scrubbing) seek(e.clientX); });
    ruler.addEventListener("pointerup", () => { TL.scrubbing = false; });
    ruler.addEventListener("dblclick", (e) => {
      const r = ruler.getBoundingClientRect();
      addMarker(Math.max(0, pxToTime(scroll.scrollLeft + (e.clientX - r.left))));
    });
    ruler.addEventListener("wheel", (e) => wheelZoom(e, ruler), { passive: false });

    setupClipPointer();
    setupTimelineKeys();
    window.addEventListener("resize", () => { sizeRuler(); drawRuler(); });
    renderProjectTimeline(state.projectTimeline);   // show empty editor immediately
  }

  // ── ruler ───────────────────────────────────────────────────────────
  function sizeRuler() {
    const ruler = tlRuler(); if (!ruler || !TL.rulerCtx) return;
    const w = ruler.clientWidth || 1, dpr = window.devicePixelRatio || 1;
    ruler.width = Math.max(1, Math.floor(w * dpr));
    ruler.height = Math.floor(TL_RULER_H * dpr);
    TL.rulerCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function chooseStep() {
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    for (const s of steps) if (s * TL.pps >= 66) return s;
    return 600;
  }
  function fmtRulerLabel(t, step) {
    const m = Math.floor(t / 60), s = t % 60;
    if (step < 1) return `${m}:${String(Math.floor(s)).padStart(2, "0")}.${Math.round((s - Math.floor(s)) * 10)}`;
    return `${m}:${String(Math.floor(s)).padStart(2, "0")}`;
  }
  function drawRuler() {
    const ruler = tlRuler(), ctx = TL.rulerCtx, scroll = tlScroll();
    if (!ruler || !ctx || !scroll) return;
    const dpr = window.devicePixelRatio || 1;
    if (ruler.width !== Math.floor((ruler.clientWidth || 1) * dpr)) sizeRuler();
    const w = ruler.clientWidth || 1, h = TL_RULER_H, left = scroll.scrollLeft;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#0c0f13"; ctx.fillRect(0, 0, w, h);
    const step = chooseStep();
    const minor = step / (step >= 5 ? 5 : 2);
    const tEnd = pxToTime(left + w);
    ctx.font = "10px ui-monospace, Menlo, monospace";
    ctx.strokeStyle = "#222a33"; ctx.beginPath();
    for (let t = Math.floor(left / TL.pps / minor) * minor; t <= tEnd + minor; t += minor) {
      const x = Math.round(timeToX(t) - left) + 0.5;
      if (x < -2 || x > w + 2) continue;
      ctx.moveTo(x, h - 6); ctx.lineTo(x, h);
    }
    ctx.stroke();
    ctx.strokeStyle = "#3c4654"; ctx.fillStyle = "#9aa4b2"; ctx.beginPath();
    for (let t = Math.floor(left / TL.pps / step) * step; t <= tEnd + step; t += step) {
      const x = Math.round(timeToX(t) - left) + 0.5;
      if (x < -40 || x > w + 40) continue;
      ctx.moveTo(x, 5); ctx.lineTo(x, h);
      ctx.fillText(fmtRulerLabel(t, step), x + 3, 13);
    }
    ctx.stroke();
    for (const mk of TL.markers) {
      const x = timeToX(mk.time) - left;
      if (x < -6 || x > w + 6) continue;
      ctx.fillStyle = mk.color || "#ffcf3b";
      ctx.beginPath(); ctx.moveTo(x, 2); ctx.lineTo(x + 5, 8); ctx.lineTo(x, 14); ctx.lineTo(x - 5, 8); ctx.closePath(); ctx.fill();
    }
    const px = timeToX(TL.playhead) - left;
    if (px >= -6 && px <= w + 6) {
      ctx.fillStyle = "#ff3b4e";
      ctx.beginPath(); ctx.moveTo(px - 5, 0); ctx.lineTo(px + 5, 0); ctx.lineTo(px, 7); ctx.closePath(); ctx.fill();
    }
  }

  // ── zoom / playhead / markers ───────────────────────────────────────
  function setPps(next, anchorTime, anchorPx) {
    const scroll = tlScroll(); if (!scroll) return;
    if (state.ptDrag) return;   // don't zoom mid-drag: clip DOM can't reflow under the render guard
    if (anchorTime == null) {
      anchorTime = pxToTime(scroll.scrollLeft + scroll.clientWidth / 2);
      anchorPx = scroll.clientWidth / 2;
    }
    TL.pps = Math.max(TL.minPps, Math.min(TL.maxPps, next));
    const z = document.getElementById("ptl-zoom"); if (z) z.value = String(Math.round(TL.pps));
    renderProjectTimeline(state.projectTimeline);
    scroll.scrollLeft = Math.max(0, timeToX(anchorTime) - anchorPx);
    drawRuler();
  }
  function positionPlayhead() {
    const ph = document.getElementById("ptl-playhead");
    if (ph) ph.style.left = timeToX(TL.playhead) + "px";
    const tc = document.getElementById("ptl-tc");
    if (tc) tc.textContent = fmtTC(TL.playhead);
  }
  function setPlayhead(t) {
    TL.playhead = Math.max(0, t);
    positionPlayhead();
    drawRuler();
  }
  function markerKey() { return `lumeri:v3:markers:${state.sessionId || "_"}`; }
  function loadMarkers() {
    try { TL.markers = JSON.parse(window.localStorage.getItem(markerKey()) || "[]") || []; } catch { TL.markers = []; }
  }
  function saveMarkers() {
    try { window.localStorage.setItem(markerKey(), JSON.stringify(TL.markers)); } catch {}
  }
  function positionMarkers() {
    const layer = document.getElementById("ptl-markers");
    if (!layer) return;
    layer.innerHTML = TL.markers.map((m) =>
      `<div class="ptl-marker" style="left:${timeToX(m.time)}px;border-color:${m.color || "#ffcf3b"}" title="${escapeHTML(m.label || "")}"></div>`
    ).join("");
  }
  function addMarker(time) {
    const m = { time: Math.max(0, +Number(time).toFixed(3)), label: `标记 ${TL.markers.length + 1}`, color: TL_MARKER_COLORS[TL.markers.length % TL_MARKER_COLORS.length] };
    const near = TL.markers.findIndex((x) => Math.abs(x.time - m.time) < 0.15);
    if (near >= 0) TL.markers.splice(near, 1); else TL.markers.push(m);
    TL.markers.sort((a, b) => a.time - b.time);
    saveMarkers(); positionMarkers(); drawRuler();
  }

  // ── clip element + lazy media (filmstrip / waveform) ────────────────
  function buildClipEl(clip, track) {
    const el = document.createElement("div");
    const kind = clip.media_kind || "video";
    const isPaint = String(clip.name || "").startsWith("paint:");
    el.className = `ptl-clip ${kind}` + (isPaint ? " paint" : "") + (clip.id === state.selectedClipId ? " selected" : "");
    el.dataset.clipId = clip.id;
    el.dataset.trackId = clip.track_id || track.id;
    el.dataset.start = clip.start;
    el.dataset.duration = clip.duration;
    el.dataset.sourceIn = clip.source_in ?? 0;
    el.dataset.sourceOut = clip.source_out ?? 0;
    el.dataset.mediaKind = kind;
    el.dataset.assetId = clip.asset_id || "";
    el.style.left = timeToX(clip.start) + "px";
    el.style.width = Math.max(timeToX(clip.duration), 8) + "px";
    const col = isPaint ? TL_CLIP_COLOR.paint : (TL_CLIP_COLOR[kind] || TL_CLIP_COLOR.video);
    el.style.setProperty("--cfill", col[0]);
    const label = kind === "text" ? (clip.text_config?.content?.slice(0, 24) || clip.name) : clip.name;
    // Outgoing transition (payload key "transition" ← lumerai transition_after):
    // a badge on the clip's right edge. Export renders a hard cut until xfade
    // lands, so the title says "preview only" honestly.
    const trans = clip.transition && clip.transition.kind && clip.transition.kind !== "cut" ? clip.transition : null;
    const transHtml = trans
      ? `<span class="ptl-clip-trans" title="${escapeHTML(trans.kind)} ${Number(trans.duration_sec || 0).toFixed(2)}s — 导出暂为硬切">⇄</span>`
      : "";
    el.innerHTML =
      `<div class="ptl-clip-media"></div><div class="ptl-clip-grad"></div>` +
      `<span class="ptl-clip-label">${escapeHTML(label || "clip")}</span>` + transHtml +
      `<div class="ptl-handle l" data-handle="left"></div><div class="ptl-handle r" data-handle="right"></div>`;
    return el;
  }
  function hydrateMedia() {
    const content = tlContent(); if (!content) return;
    content.querySelectorAll(".ptl-clip").forEach((el) => {
      const assetId = el.dataset.assetId;
      if (!assetId) return;                       // no source → solid color (graceful)
      if (el.dataset.mediaKind === "audio") ensureWaveform(el, assetId);
      else if (el.dataset.mediaKind === "video") ensureFilmstrip(el, assetId);
    });
  }
  function ensureFilmstrip(el, assetId) {
    const w = el.clientWidth || timeToX(+el.dataset.duration);
    const tiles = Math.max(1, Math.min(16, Math.round(w / 88)));
    const inS = +el.dataset.sourceIn, outS = +el.dataset.sourceOut;
    const key = `${assetId}|${inS.toFixed(2)}|${outS.toFixed(2)}|${tiles}`;
    const media = el.querySelector(".ptl-clip-media");
    const paint = (urls) => { if (media) media.innerHTML = urls.map((u) => `<div class="fs-tile" style="background-image:url(${u})"></div>`).join(""); };
    const cached = TL.filmstrip.get(key);
    if (cached) { paint(cached); return; }              // cached (incl. empty) → no re-extract
    if (TL.filmstripBusy.has(key)) return;
    TL.filmstripBusy.add(key);
    extractFilmstrip(assetId, inS, outS, tiles).then((urls) => {
      TL.filmstripBusy.delete(key);
      TL.filmstrip.set(key, urls || []);                // negative-cache failures: stops the retry storm
      if (document.body.contains(el)) paint(urls || []);
    }).catch(() => TL.filmstripBusy.delete(key));
  }
  function extractFilmstrip(assetId, inS, outS, tiles) {
    return new Promise((resolve) => {
      const url = `/sessions/${state.sessionId}/assets/${assetId}`;
      const video = document.createElement("video");
      video.muted = true; video.preload = "auto"; video.crossOrigin = "anonymous";
      const out = [];
      let done = false;
      const teardown = () => { try { video.pause(); video.removeAttribute("src"); video.load(); } catch {} };
      const finishUp = () => { if (!done) { done = true; clearTimeout(timer); teardown(); resolve(out); } };
      const timer = setTimeout(finishUp, 12000);
      video.addEventListener("error", finishUp, { once: true });
      video.addEventListener("loadedmetadata", async () => {
        const dur = isFinite(video.duration) && video.duration > 0 ? video.duration : (outS || 1);
        const a = inS || 0, b = (outS && outS > a) ? outS : dur;
        const span = Math.max(0.05, b - a);
        const th = 46, tw = Math.round(th * ((video.videoWidth / Math.max(1, video.videoHeight)) || 1.7));
        const canvas = document.createElement("canvas");
        canvas.width = tw; canvas.height = th;
        const ctx = canvas.getContext("2d");
        for (let i = 0; i < tiles && !done; i++) {
          const t = Math.min(a + span * ((i + 0.5) / tiles), dur - 0.02);
          await seekVideo(video, Math.max(0, t));
          try { ctx.drawImage(video, 0, 0, tw, th); out.push(canvas.toDataURL("image/jpeg", 0.55)); } catch { break; }
        }
        finishUp();
      }, { once: true });
      video.src = url;   // set src last, after listeners are attached
    });
  }
  function seekVideo(video, t) {
    return new Promise((res) => {
      let settled = false;
      const on = () => { if (!settled) { settled = true; video.removeEventListener("seeked", on); clearTimeout(guard); res(); } };
      video.addEventListener("seeked", on);
      const guard = setTimeout(on, 1500);
      try { video.currentTime = t; } catch { on(); }
    });
  }
  function ensureWaveform(el, assetId) {
    const inS = +el.dataset.sourceIn, outS = +el.dataset.sourceOut;
    const key = `${assetId}|${inS.toFixed(2)}|${outS.toFixed(2)}`;   // trim-aware: each slice gets its own peaks
    const media = el.querySelector(".ptl-clip-media");
    const draw = (peaks) => { if (peaks && peaks.length && media && document.body.contains(el)) drawWave(media, peaks); };
    const cached = TL.wave.get(key);
    if (cached) { draw(cached); return; }
    if (TL.waveBusy.has(key)) return;
    TL.waveBusy.add(key);
    decodeWave(assetId, inS, outS).then((peaks) => {
      TL.waveBusy.delete(key);
      TL.wave.set(key, peaks || []);                    // cache (incl. empty) → no re-decode storm
      draw(peaks);
    }).catch(() => TL.waveBusy.delete(key));
  }
  async function decodeWave(assetId, inS, outS, samples = 240) {
    const res = await fetch(`/sessions/${state.sessionId}/assets/${assetId}`);
    if (!res.ok) throw new Error("wave fetch " + res.status);
    const buf = await res.arrayBuffer();
    if (!TL.audioCtx) TL.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await TL.audioCtx.decodeAudioData(buf.slice(0));
    const ch = decoded.getChannelData(0);
    const sr = decoded.sampleRate || 44100;
    const s0 = inS > 0 ? Math.min(ch.length, Math.floor(inS * sr)) : 0;
    const s1 = (outS && outS > inS) ? Math.min(ch.length, Math.floor(outS * sr)) : ch.length;
    const len = Math.max(1, s1 - s0);
    const bucket = Math.max(1, Math.floor(len / samples));
    const peaks = new Array(samples).fill(0);
    let max = 0.0001;
    for (let i = 0; i < samples; i++) {
      let p = 0; const s = s0 + i * bucket, e = Math.min(s + bucket, s1);
      for (let j = s; j < e; j++) { const v = Math.abs(ch[j]); if (v > p) p = v; }
      peaks[i] = p; if (p > max) max = p;
    }
    for (let i = 0; i < samples; i++) peaks[i] /= max;
    return peaks;
  }
  function drawWave(media, peaks) {
    const w = Math.max(2, media.clientWidth), h = Math.max(2, media.clientHeight);
    const dpr = window.devicePixelRatio || 1;
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.strokeStyle = "rgba(180,225,255,0.85)"; ctx.lineWidth = 1; ctx.beginPath();
    const mid = h / 2;
    for (let x = 0; x < w; x++) {
      const p = peaks[Math.floor((x / w) * peaks.length)] || 0;
      const amp = p * (h / 2 - 2);
      ctx.moveTo(x + 0.5, mid - amp); ctx.lineTo(x + 0.5, mid + amp);
    }
    ctx.stroke();
    media.innerHTML = ""; media.appendChild(canvas);
  }

  async function fetchProjectTimeline(options = {}) {
    if (!state.sessionId) return;
    if (state.ptDrag) return;   // never re-fetch/reconcile mid-drag (would detach the dragged clip)
    try {
      const r = await fetch(`/sessions/${state.sessionId}/timeline`);
      if (!r.ok) return;
      const data = await r.json();
      state.projectTimeline = data;
      if (!options.force && data.patch_seq === TL._renderedSeq) return;   // unchanged → skip 3s DOM rebuild + media re-hydrate
      renderProjectTimeline(data);
    } catch { /* ignore network errors */ }
  }

  function startTimelinePoll() {
    stopTimelinePoll();
    TL._renderedSeq = null;   // force the first fetch of a (new) session to render authoritative state
    state.timelinePollTimer = setInterval(fetchProjectTimeline, 3000);
    fetchProjectTimeline();
  }

  function stopTimelinePoll() {
    if (state.timelinePollTimer) {
      clearInterval(state.timelinePollTimer);
      state.timelinePollTimer = null;
    }
  }

  function renderProjectTimeline(data) {
    if (state.ptDrag) return;   // defensive: don't rebuild the DOM under an active drag
    if (!TL.built) { buildTimelineShell(); return; }   // build() calls back into render once
    const model = timelineModel(data);
    TL.model = model;
    TL._renderedSeq = model.patch_seq;   // poll skips re-render until patch_seq changes
    const content = tlContent(), headers = tlHeaders();
    if (!content || !headers) return;

    const contentW = Math.ceil(model.contentDur * TL.pps);
    content.style.width = contentW + "px";
    content.style.height = (model.tracks.length * TL_TRACK_H) + "px";

    headers.innerHTML = model.tracks.map((t) => {
      const isA = t.kind === "audio";
      return `<div class="ptl-head ${escapeHTML(t.kind)}" style="height:${TL_TRACK_H}px" title="${escapeHTML(t.name || t.id)}">`
        + `<span class="ptl-head-kind">${isA ? "♪" : "▦"} ${escapeHTML(t.id)}</span></div>`;
    }).join("");

    content.innerHTML = "";
    model.tracks.forEach((t, i) => {
      const lane = document.createElement("div");
      lane.className = `ptl-lane ${t.kind}`;
      lane.dataset.trackId = t.id;
      lane.dataset.trackKind = t.kind;
      lane.style.top = (i * TL_TRACK_H) + "px";
      lane.style.height = TL_TRACK_H + "px";
      (t.clips || []).forEach((clip) => lane.appendChild(buildClipEl(clip, t)));
      content.appendChild(lane);
    });
    const ph = document.createElement("div"); ph.className = "ptl-playhead"; ph.id = "ptl-playhead"; content.appendChild(ph);
    const mk = document.createElement("div"); mk.id = "ptl-markers"; content.appendChild(mk);

    positionPlayhead();
    positionMarkers();
    sizeRuler();
    drawRuler();
    updateEditHint();
    requestAnimationFrame(hydrateMedia);
  }

  function fmtSec(s) {
    if (!isFinite(s)) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  // ── direct edit (DE): user drag/trim via the shared /timeline/op endpoint ──
  // Every gesture compiles to ONE patches.py op applied through the SAME
  // ProjectStore path as the model's verbs — no parallel edit state.

  function editingEnabled() {
    return !!state.sessionId && !state.turnInProgress;
  }

  async function postTimelineOp(opBody) {
    if (!state.sessionId) return null;
    try {
      const r = await fetch(`/sessions/${state.sessionId}/timeline/op`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(opBody),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        // Rejected (E_OVERLAP/E_RANGE/…). Surface the typed code, snap back.
        state.errors.push(`edit rejected: ${[data.code, data.error].filter(Boolean).join(" ") || r.status}`);
        await fetchProjectTimeline();
        render();
        return null;
      }
      // Export honesty (docs/timeline-canonical-plan.md §4): the edit applied,
      // but stored fields the exporter won't render — surface the typed
      // W_NOT_EXPORTED warnings in the message strip. Warn, never silent.
      if (Array.isArray(data.warnings) && data.warnings.length) {
        for (const w of data.warnings) state.errors.push(String(w));
        render();
      }
      state.projectTimeline = data;
      renderProjectTimeline(data);     // reconcile from authoritative post-state
      return data;
    } catch (err) {
      state.errors.push(`edit failed: ${err.message}`);
      await fetchProjectTimeline();
      render();
      return null;
    }
  }

  function selectClip(clipId) {
    state.selectedClipId = clipId;
    document.querySelectorAll("#ptl-content .ptl-clip").forEach((el) => {
      el.classList.toggle("selected", el.dataset.clipId === clipId);
    });
    updateEditHint();
  }

  function focusEntity(kind, id) {
    if (kind === "clip") {
      toggleDrawer(true);
      setActiveTab("timeline");
      selectClip(id);
      const el = document.querySelector(`#ptl-content .ptl-clip[data-clip-id="${CSS.escape(id)}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    } else if (kind === "asset") {
      const card = document.querySelector(`.asset-card[data-asset-id="${CSS.escape(id)}"]`);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        card.classList.add("flash");
        setTimeout(() => card.classList.remove("flash"), 1200);
      }
    } else if (kind === "shot" || kind === "scene") {
      if (!stageTabs.includes("outline")) { stageTabs.push("outline"); saveStageTabs(); }
      setActiveTab("outline");
      const sel = kind === "shot"
        ? `.outline-row[data-shot-id="${CSS.escape(id)}"]`
        : `.outline-scene[data-scene-id="${CSS.escape(id)}"]`;
      requestAnimationFrame(() => {
        const el = document.querySelector(sel);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "nearest" });
          el.classList.add("flash");
          setTimeout(() => el.classList.remove("flash"), 1200);
        }
      });
    }
  }

  function selectedClip() {
    const tl = state.projectTimeline;
    if (!tl || !state.selectedClipId) return null;
    for (const tr of tl.tracks || []) {
      for (const c of (tr.clips || [])) {
        if (c.id === state.selectedClipId) return c;
      }
    }
    return null;
  }

  function updateEditHint() {
    const has = !!selectedClip() && editingEnabled();
    const split = document.getElementById("ptl-split");
    const del = document.getElementById("ptl-delete");
    if (split) split.disabled = !has;
    if (del) del.disabled = !has;
  }

  function splitSelected() {
    const c = selectedClip();
    if (!c || !editingEnabled()) return;
    const inside = TL.playhead > c.start + 0.04 && TL.playhead < c.start + c.duration - 0.04;
    const at = inside ? TL.playhead : c.start + c.duration / 2;
    postTimelineOp({ op: "split", clip_id: c.id, at_time: +at.toFixed(6) });
  }
  function deleteSelected() {
    const c = selectedClip();
    if (!c || !editingEnabled()) return;
    postTimelineOp({ op: "delete", clip_id: c.id }).then((r) => { if (r) { state.selectedClipId = null; updateEditHint(); } });
  }

  function laneUnder(clientY) {
    const content = tlContent(); if (!content) return null;
    let found = null;
    content.querySelectorAll(".ptl-lane").forEach((lane) => {
      const r = lane.getBoundingClientRect();
      if (clientY >= r.top && clientY <= r.bottom) found = lane;
    });
    return found;
  }

  // Pointer drag/trim on the content layer. Every gesture compiles to ONE
  // patches.py op (move/trim/set_time) through the shared /timeline/op path.
  function setupClipPointer() {
    const content = tlContent();
    if (!content) return;

    content.addEventListener("pointerdown", (ev) => {
      const clipEl = ev.target.closest(".ptl-clip");
      if (!clipEl) return;
      selectClip(clipEl.dataset.clipId);
      if (!editingEnabled()) return;
      const handle = ev.target.closest(".ptl-handle");
      const d = {
        clipId: clipEl.dataset.clipId,
        mode: handle ? handle.dataset.handle : "move",   // left | right | move
        startX: ev.clientX,
        origStart: parseFloat(clipEl.dataset.start) || 0,
        origDur: parseFloat(clipEl.dataset.duration) || 0,
        sourceIn: parseFloat(clipEl.dataset.sourceIn) || 0,
        sourceOut: parseFloat(clipEl.dataset.sourceOut) || 0,
        mediaKind: clipEl.dataset.mediaKind,
        origTrack: clipEl.dataset.trackId,
        el: clipEl,
      };
      TL.drag = d;
      state.ptDrag = d;             // pauses polling/reconcile mid-gesture
      clipEl.classList.add("dragging");
      try { clipEl.setPointerCapture(ev.pointerId); } catch {}
      ev.preventDefault();
    });

    content.addEventListener("pointermove", (ev) => {
      const d = TL.drag;
      if (!d) return;
      const dt = pxToTime(ev.clientX - d.startX);
      if (d.mode === "move") {
        d.pendStart = Math.max(0, snapSeconds(d.origStart + dt, d));
        d.el.style.left = timeToX(d.pendStart) + "px";
        const lane = laneUnder(ev.clientY);
        const tid = lane && lane.dataset.trackId;
        if (lane && tid && !tid.endsWith("*") && trackCompatible(d.mediaKind, lane.dataset.trackKind) && tid !== d.origTrack) {
          d.pendTrack = tid;                            // only real (persisted) lanes are drop targets
          if (d.el.parentNode !== lane) lane.appendChild(d.el);
        }
      } else if (d.mode === "right") {
        const end = snapSeconds(d.origStart + d.origDur + dt, d);
        d.pendDur = Math.max(0.1, end - d.origStart);
        d.el.style.width = Math.max(timeToX(d.pendDur), 8) + "px";
      } else { // left-trim head
        const ns = snapSeconds(d.origStart + dt, d);
        const lo = Math.max(0, d.origStart - d.sourceIn);   // can't pull the head before the source start
        d.pendStart = Math.max(lo, Math.min(ns, d.origStart + d.origDur - 0.1));
        d.pendDur = d.origDur - (d.pendStart - d.origStart);
        d.el.style.left = timeToX(d.pendStart) + "px";
        d.el.style.width = Math.max(timeToX(d.pendDur), 8) + "px";
      }
    });

    const finish = () => {
      const d = TL.drag;
      if (!d) return;
      TL.drag = null; state.ptDrag = null;
      d.el.classList.remove("dragging");
      if (d.mode === "move" && d.pendStart != null) {
        const op = { op: "move", clip_id: d.clipId, start: +d.pendStart.toFixed(6) };
        if (d.pendTrack && d.pendTrack !== d.origTrack) op.track_id = d.pendTrack;
        postTimelineOp(op);
      } else if (d.mode === "right" && d.pendDur != null) {
        if (d.mediaKind === "video" || d.mediaKind === "audio")
          postTimelineOp({ op: "trim", clip_id: d.clipId, source_out: +(d.sourceIn + d.pendDur).toFixed(6) });
        else
          postTimelineOp({ op: "set_time", clip_id: d.clipId, duration: +d.pendDur.toFixed(6) });
      } else if (d.mode === "left" && d.pendStart != null) {
        if (d.mediaKind === "video" || d.mediaKind === "audio") {
          const newIn = Math.max(0, d.sourceIn + (d.pendStart - d.origStart));
          if (d.pendStart < d.origStart) {
            // expanding head leftward: move first (frees the right side), then extend the in-point
            postTimelineOp({ op: "move", clip_id: d.clipId, start: +d.pendStart.toFixed(6) })
              .then((r) => { if (r) postTimelineOp({ op: "trim", clip_id: d.clipId, source_in: +newIn.toFixed(6) }); });
          } else {
            // shrinking head rightward: trim in place first, then slide into the freed space
            postTimelineOp({ op: "trim", clip_id: d.clipId, source_in: +newIn.toFixed(6) })
              .then((r) => { if (r) postTimelineOp({ op: "move", clip_id: d.clipId, start: +d.pendStart.toFixed(6) }); });
          }
        } else {
          postTimelineOp({ op: "set_time", clip_id: d.clipId, start: +d.pendStart.toFixed(6), duration: +d.pendDur.toFixed(6) });
        }
      } else {
        renderProjectTimeline(state.projectTimeline);   // plain click: re-sync layout
      }
    };
    content.addEventListener("pointerup", finish);
    content.addEventListener("pointercancel", finish);
  }

  function setupTimelineKeys() {
    document.addEventListener("keydown", (ev) => {
      const t = ev.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); deleteSelected(); return; }
      if (ev.key === "s" || ev.key === "S") { splitSelected(); return; }
      if (ev.key === "m" || ev.key === "M") { addMarker(TL.playhead); return; }
      if ((ev.metaKey || ev.ctrlKey) && (ev.key === "z" || ev.key === "Z")) { ev.preventDefault(); if (editingEnabled()) postTimelineOp({ op: "undo", steps: 1 }); return; }
      if (ev.key === "+" || ev.key === "=") { setPps(TL.pps * 1.25); return; }
      if (ev.key === "-" || ev.key === "_") { setPps(TL.pps / 1.25); return; }
    });
  }

  function setupTimelineDirectEdit() {
    buildTimelineShell();   // builds the panel DOM + wires every interaction
  }

  // Snap a timeline-second to nearby clip edges / playhead / markers (≈8px),
  // else to a 0.5s grid; clamp >= 0.
  function snapSeconds(sec, d) {
    sec = Math.max(0, sec);
    if (!TL.snap) return sec;
    const tol = pxToTime(8);
    let best = sec, bestDist = tol;
    const cand = [TL.playhead, ...TL.markers.map((m) => m.time)];
    document.querySelectorAll("#ptl-content .ptl-clip").forEach((el) => {
      if (d && el.dataset.clipId === d.clipId) return;
      const s = parseFloat(el.dataset.start) || 0;
      cand.push(s, s + (parseFloat(el.dataset.duration) || 0));
    });
    for (const c of cand) { const dist = Math.abs(sec - c); if (dist < bestDist) { best = c; bestDist = dist; } }
    if (bestDist === tol) best = Math.round(sec * 2) / 2;
    return Math.max(0, best);
  }

  // ── API calls ───────────────────────────────────────────────────────

  async function createSession() {
    clearReconnectTimer();
    stopTimelinePoll();
    if (state.eventSource) {
      try { await fetch(`/sessions/${state.sessionId}/close`, { method: "POST" }); } catch {}
      state.eventSource.close();
    }
    setConnPill("opening…", "");
    const r = await fetch("/sessions", { method: "POST" });
    if (!r.ok) throw new Error(`POST /sessions failed: ${r.status}`);
    const data = await r.json();
    state.sessionId = data.session_id;
    // reset per-session timeline view state so nothing leaks across sessions
    loadMarkers();              // markers are per-session (localStorage); reload under the real key
    TL.extraTracks = [];        // client-added display lanes
    TL.playhead = 0;
    state.selectedClipId = null;
    state.turns = [];
    state.currentTurn = null;
    state.assets = [];
    state.errors = [];
    state.turnInProgress = false;
    state.stopPending = false;
    state.lastEventId = null;
    state.projectTimeline = null;
    state.mediaAnnotations = new Map();
    state.planMode = false;     // fresh sessions start with plan mode off
    state.planReady = false;
    state.sessionTitle = null;
    state.userMessageCount = 0;
    connectSse(state.sessionId);
    startTimelinePoll();
    fetchMediaLibrary().catch(() => {});
    render();
  }

  // ── session persistence (auto-save + auto-title) ────────────────────

  function _collectSessionMessages() {
    const msgs = [];
    for (const turn of state.turns) {
      if (turn.userMessage) msgs.push({ role: "user", content: turn.userMessage, timestamp: Date.now() });
      for (const guidance of (turn.guidance || [])) {
        msgs.push({ role: "status", content: guidance, statusType: "guidance", timestamp: Date.now() });
      }
      if (turn.assistantText) msgs.push({ role: "status", content: turn.assistantText, statusType: "succeeded", timestamp: Date.now() });
    }
    return msgs;
  }

  async function autoSaveSession() {
    if (!state.sessionId) return;
    const messages = _collectSessionMessages();
    if (!messages.length) return;
    const payload = {
      session_id: state.sessionId,
      title: state.sessionTitle || undefined,
      messages,
      project_state: null,
      project: null,
    };
    try {
      await fetch("/session-history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch {}
  }

  async function autoGenerateTitle() {
    if (!state.sessionId) return;
    const messages = _collectSessionMessages();
    if (!messages.length) return;
    try {
      const r = await fetch(`/sessions/${state.sessionId}/auto_title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages }),
      });
      if (!r.ok) return;
      const data = await r.json();
      if (data.title) {
        state.sessionTitle = data.title;
        els.sessionLabel.textContent = data.title;
        autoSaveSession();
      }
    } catch {}
  }

  async function refreshSessionState() {
    if (!state.sessionId) return;
    const r = await fetch(`/sessions/${state.sessionId}`);
    if (!r.ok) throw new Error(`GET /sessions/${state.sessionId} failed: ${r.status}`);
    const data = await r.json();
    const finalIds = new Set(state.assets.filter((a) => a.final).map((a) => a.asset_id));
    state.assets = (data.assets || []).map((a) => ({
      asset_id: a.asset_id,
      kind: a.kind || inferKindFromAssetId(a.asset_id),
      summary: a.summary || "",
      source: "tool",
      final: finalIds.has(a.asset_id),
    }));
    if (data.latest_event_id !== null && data.latest_event_id !== undefined) {
      saveLastEventId(state.sessionId, data.latest_event_id);
    }
    if (typeof data.plan_mode === "boolean") {
      state.planMode = data.plan_mode;
      if (!state.planMode) state.planReady = false;
    }
    if (typeof data.turn_in_progress === "boolean") {
      state.turnInProgress = data.turn_in_progress;
      if (!state.turnInProgress) state.stopPending = false;
    }
  }

  async function fetchMediaLibrary() {
    state.mediaLibraryStatus = "loading";
    render();
    try {
      const r = await fetch("/media-library/list?limit=100");
      if (r.status === 401) {
        state.mediaLibrary = [];
        state.mediaLibraryStatus = "signed-out";
        render();
        return;
      }
      if (!r.ok) throw new Error(`GET /media-library/list failed: ${r.status}`);
      const data = await r.json();
      state.mediaLibrary = Array.isArray(data.assets) ? data.assets : [];
      state.mediaLibraryStatus = "ready";
    } catch (err) {
      state.mediaLibrary = [];
      state.mediaLibraryStatus = "error";
      state.errors.push(`media library failed: ${err.message}`);
    }
    render();
  }

  async function annotateLibraryAsset(assetId) {
    const body = assetId
      ? { asset_ids: [assetId], mode: "quick", language: promptLanguage() }
      : { all: true, kind: "video", mode: "quick", max_assets: 20, language: promptLanguage() };
    const r = await fetch("/media-library/annotate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`annotate failed: ${r.status}`);
    await r.json();
    await fetchMediaLibrary();
    if (assetId) await loadMediaAnnotations(assetId);
  }

  async function loadMediaAnnotations(assetId) {
    const r = await fetch(`/media-library/${encodeURIComponent(assetId)}/annotations`);
    if (!r.ok) throw new Error(`annotations failed: ${r.status}`);
    const data = await r.json();
    state.mediaAnnotations.set(assetId, Array.isArray(data.annotations) ? data.annotations : []);
    render();
  }

  function promptLanguage() {
    return /[\u4e00-\u9fff]/.test(els.promptInput?.value || "") ? "zh" : "en";
  }

  async function uploadFile(file, retryExpiredSession = true) {
    if (!state.sessionId) throw new Error("no session");
    setUploadStatus(`uploading ${file.name}…`);
    const r = await fetch(`/sessions/${state.sessionId}/assets`, {
      method: "POST",
      headers: {
        "X-Filename": encodeURIComponent(file.name),
        "Content-Type": file.type || "application/octet-stream",
      },
      body: file,
    });
    // The local runtime keeps active sessions in memory.  A safe auto-sync
    // restart can therefore leave an already-open browser tab holding a stale
    // session id.  Preserve its transcript, open a fresh runtime session and
    // retry the user's upload once instead of surfacing an unexplained 404.
    if (r.status === 404 && retryExpiredSession) {
      setUploadStatus("会话已更新，正在恢复后重新上传…");
      await autoSaveSession();
      await createSession();
      return uploadFile(file, false);
    }
    if (!r.ok) {
      setUploadStatus(`upload failed (${r.status})`);
      throw new Error(`upload failed: ${r.status}`);
    }
    const data = await r.json();
    state.assets.push({
      asset_id: data.asset_id,
      kind: file.type?.startsWith("image/")
        ? "image"
        : file.type?.startsWith("audio/")
          ? "audio"
          : "video",
      summary: `uploaded ${data.filename} (${(data.size_bytes / 1024).toFixed(1)} KB)`,
      source: "user",
      final: false,
    });
    setUploadStatus(`uploaded as ${data.asset_id}`);
    fetchMediaLibrary().catch(() => {});
    render();
    return data.asset_id;
  }

  function setUploadStatus(text) {
    state.uploadStatus = text;
    let label = document.querySelector(".upload-status");
    if (!label) {
      label = document.createElement("span");
      label.className = "upload-status";
      // Upload button now lives in the closed "+" menu — anchor the status as a
      // toast on the input shell instead so it stays visible.
      (document.getElementById("input-shell") || document.body).appendChild(label);
    }
    label.textContent = text || "";
  }

  async function submitTurn(message) {
    if (!state.sessionId) throw new Error("no session");
    state.userMessageCount++;
    const turn = newTurn(message);
    state.turns.push(turn);
    state.currentTurn = turn;
    state.turnInProgress = true;
    state.planReady = false;   // a new turn supersedes any pending approval offer
    render();
    const r = await fetch(`/sessions/${state.sessionId}/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!r.ok && r.status !== 202) {
      turn.banners.push({ kind: "turn_error", text: "任务未能开始，请稍后重试" });
      state.turnInProgress = false;
      render();
    }
  }

  async function steerTurn(message) {
    if (!state.sessionId || !state.turnInProgress) throw new Error("no active turn");
    const r = await fetch(`/sessions/${state.sessionId}/steer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.error || `引导未送达 (${r.status})`);
    }
    if (state.currentTurn) state.currentTurn.guidance.push(message);
    render();
  }

  async function stopCurrentTurn() {
    if (!state.sessionId || !state.turnInProgress || state.stopPending) return;
    state.stopPending = true;
    render();
    try {
      const r = await fetch(`/sessions/${state.sessionId}/stop`, { method: "POST" });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(data.error || `停止未生效 (${r.status})`);
      }
    } catch (err) {
      state.stopPending = false;
      state.currentTurn?.banners.push({ kind: "info", text: "停止请求未成功，请再试一次" });
      state.errors.push(`stop turn failed: ${err.message}`);
      render();
    }
  }

  // ── wiring ──────────────────────────────────────────────────────────

  els.newSessionBtn.addEventListener("click", () => {
    createSession().catch((err) => {
      state.errors.push(`create session failed: ${err.message}`);
      setConnPill("failed", "failed");
      render();
    });
  });

  els.uploadBtn.addEventListener("click", () => els.uploadInput.click());
  els.uploadInput.addEventListener("change", () => {
    const file = els.uploadInput.files?.[0];
    if (!file) return;
    uploadFile(file).catch((err) => {
      state.errors.push(`upload failed: ${err.message}`);
      render();
    }).finally(() => { els.uploadInput.value = ""; });
  });

  els.libraryRefreshBtn?.addEventListener("click", () => {
    fetchMediaLibrary().catch((err) => {
      state.errors.push(`media library failed: ${err.message}`);
      render();
    });
  });

  els.libraryAnnotateBtn?.addEventListener("click", () => {
    annotateLibraryAsset("").catch((err) => {
      state.errors.push(`annotate media failed: ${err.message}`);
      render();
    });
  });

  let _speakingUtterance = null;

  document.addEventListener("click", (e) => {
    // ── Entity reference click → navigate ──
    const entity = e.target.closest(".md-entity[data-entity-kind]");
    if (entity) {
      focusEntity(entity.dataset.entityKind, entity.dataset.entityId);
      return;
    }

    // ── Copy assistant text ──
    const copyBtn = e.target.closest("[data-copy-assistant]");
    if (copyBtn) {
      const turnIdx = Number(copyBtn.dataset.copyAssistant);
      const turn = state.turns[turnIdx];
      if (turn?.assistantText) {
        navigator.clipboard.writeText(turn.assistantText).then(() => {
          const svg = copyBtn.querySelector("svg use");
          if (svg) { svg.setAttribute("href", "#i-check"); setTimeout(() => svg.setAttribute("href", "#i-copy"), 1200); }
        });
      }
      return;
    }

    // ── Speak assistant text ──
    const speakBtn = e.target.closest("[data-speak-assistant]");
    if (speakBtn) {
      if (_speakingUtterance && speechSynthesis.speaking) {
        speechSynthesis.cancel();
        _speakingUtterance = null;
        const svg = speakBtn.querySelector("svg use");
        if (svg) svg.setAttribute("href", "#i-volume");
        return;
      }
      const turnIdx = Number(speakBtn.dataset.speakAssistant);
      const turn = state.turns[turnIdx];
      if (turn?.assistantText && window.speechSynthesis) {
        const u = new SpeechSynthesisUtterance(turn.assistantText);
        u.lang = "zh-CN";
        const svg = speakBtn.querySelector("svg use");
        if (svg) svg.setAttribute("href", "#i-stop");
        u.onend = () => { _speakingUtterance = null; if (svg) svg.setAttribute("href", "#i-volume"); };
        u.onerror = u.onend;
        _speakingUtterance = u;
        speechSynthesis.speak(u);
      }
      return;
    }

    const annotateBtn = e.target.closest("[data-library-annotate]");
    if (annotateBtn) {
      const assetId = annotateBtn.dataset.libraryAnnotate;
      annotateLibraryAsset(assetId).catch((err) => {
        state.errors.push(`annotate ${assetId} failed: ${err.message}`);
        render();
      });
      return;
    }
    const loadBtn = e.target.closest("[data-library-load]");
    if (loadBtn) {
      const assetId = loadBtn.dataset.libraryLoad;
      loadMediaAnnotations(assetId).catch((err) => {
        state.errors.push(`load annotations failed: ${err.message}`);
        render();
      });
    }
  });

  // ── /model picker ───────────────────────────────────────────────────
  // Lists the backend's priority-ordered model catalog + thinking-effort
  // tiers, marks the active pick, and lets the user switch. The selection is
  // global + persisted (config.json:lumeri_v3_model / lumeri_v3_effort) — the
  // same store the CLI /model uses — so it sticks across sessions/restarts.
  async function fetchModelInfo() {
    const r = await fetch("/model");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function postModelSelection(body) {
    const r = await fetch("/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  function openModelPicker() {
    let overlay = $("#model-modal");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "model-modal";
      overlay.className = "auth-modal";
      overlay.hidden = true;
      overlay.innerHTML = `
        <div class="model-backdrop" data-model-close></div>
        <div class="auth-dialog model-dialog" role="dialog" aria-modal="true" aria-labelledby="model-title">
          <button type="button" class="auth-x" data-model-close aria-label="关闭">×</button>
          <h2 id="model-title">模型与思考强度</h2>
          <p class="model-lock-note" id="model-lock-note" hidden></p>
          <div class="model-list" id="model-list"></div>
          <div class="model-add-wrap" id="model-add-wrap">
            <button type="button" class="model-add-btn" id="model-add-btn">+ 添加模型</button>
            <div class="model-add-dropdown" id="model-add-dropdown" hidden>
              <div class="model-add-search-wrap">
                <input type="text" class="model-add-search" id="model-add-search" placeholder="搜索或输入模型 ID…">
                <span class="model-add-spinner" id="model-add-spinner" hidden></span>
              </div>
              <div class="model-add-list" id="model-add-list"></div>
              <button type="button" class="model-add-custom" id="model-add-custom" hidden>添加自定义模型</button>
            </div>
          </div>
          <div class="model-effort-label">思考强度</div>
          <div class="model-efforts" id="model-efforts"></div>
          <div class="model-save-wrap" id="model-save-wrap" hidden>
            <button type="button" class="model-save-btn" id="model-save-btn">保存</button>
          </div>
          <p class="auth-error" id="model-error" hidden></p>
        </div>`;
      document.body.appendChild(overlay);
      overlay.querySelectorAll("[data-model-close]").forEach((el) =>
        el.addEventListener("click", () => { overlay.hidden = true; }));
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !overlay.hidden) overlay.hidden = true;
      });
    }
    const errEl = $("#model-error", overlay);
    const setErr = (msg) => {
      if (!errEl) return;
      if (msg) { errEl.textContent = msg; errEl.hidden = false; }
      else { errEl.hidden = true; }
    };

    let scannedModels = [];
    let lastInfo = null;
    let pendingModel = null;
    let pendingEffort = null;

    function renderInfo(info) {
      lastInfo = info;
      const active = info.active || {};
      const locked = !!active.locked;
      if (locked) {
        pendingModel = active.model;
        pendingEffort = active.effort;
      }
      if (pendingModel === null) pendingModel = active.model;
      if (pendingEffort === null) pendingEffort = active.effort;
      const selModel = pendingModel || active.model;
      const selEffort = pendingEffort || active.effort;
      const list = $("#model-list", overlay);
      const canDelete = (info.priority || []).length > 1;
      list.innerHTML = (info.priority || []).map((it, i) => {
        const on = it.id === selModel;
        return `
          <div class="model-row${on ? " active" : ""}">
            <button type="button" class="model-row-select" data-model-id="${escapeHTML(it.id)}"${locked ? " disabled" : ""}>
              <span class="model-dot">${on ? "●" : "○"}</span>
              <span class="model-name">${escapeHTML(it.label)}${i === 0 ? ' <span class="model-tag">默认</span>' : ""}</span>
              <span class="model-id">${escapeHTML(it.id)}</span>
            </button>${canDelete && !locked ? `<button type="button" class="model-del" data-del-id="${escapeHTML(it.id)}" aria-label="删除 ${escapeHTML(it.label)}">×</button>` : ""}
          </div>`;
      }).join("");
      const efforts = $("#model-efforts", overlay);
      efforts.innerHTML = (info.efforts || []).map((e) => {
        const on = e === selEffort;
        return `<button type="button" class="model-chip${on ? " active" : ""}" data-effort="${escapeHTML(e)}"${locked ? " disabled" : ""}>${escapeHTML(e)}</button>`;
      }).join("");

      list.querySelectorAll("[data-model-id]").forEach((btn) =>
        btn.addEventListener("click", () => { pendingModel = btn.dataset.modelId; renderInfo(info); }));
      list.querySelectorAll("[data-del-id]").forEach((btn) =>
        btn.addEventListener("click", (e) => { e.stopPropagation(); removeModel(btn.dataset.delId); }));
      efforts.querySelectorAll("[data-effort]").forEach((btn) =>
        btn.addEventListener("click", () => { pendingEffort = btn.dataset.effort; renderInfo(info); }));

      const changed = selModel !== active.model || selEffort !== active.effort;
      const saveWrap = $("#model-save-wrap", overlay);
      if (saveWrap) saveWrap.hidden = locked || !changed;
      const addWrap = $("#model-add-wrap", overlay);
      if (addWrap) addWrap.hidden = locked;
      const lockNote = $("#model-lock-note", overlay);
      if (lockNote) {
        lockNote.hidden = !locked;
        lockNote.textContent = locked
          ? `已强制锁定最强模型：${active.label || active.model} · ${active.effort || "max"} 思考强度`
          : "";
      }
    }

    async function apply() {
      setErr("");
      try {
        const body = {};
        if (pendingModel) body.model = pendingModel;
        if (pendingEffort) body.effort = pendingEffort;
        const data = await postModelSelection(body);
        pendingModel = data.active?.model || pendingModel;
        pendingEffort = data.active?.effort || pendingEffort;
        renderInfo(data);
      } catch (e) {
        setErr(`保存失败：${e.message}`);
      }
    }

    $("#model-save-btn", overlay).addEventListener("click", apply);

    async function removeModel(id) {
      setErr("");
      try {
        const r = await fetch("/model/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        renderInfo(data);
      } catch (e) {
        setErr(`删除失败：${e.message}`);
      }
    }

    async function addModel(id, label) {
      setErr("");
      try {
        const r = await fetch("/model/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id, label: label || id }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        renderInfo(data);
        closeAddDropdown();
      } catch (e) {
        setErr(`添加失败：${e.message}`);
      }
    }

    // ── add-model dropdown ──
    const addBtn = $("#model-add-btn", overlay);
    const dropdown = $("#model-add-dropdown", overlay);
    const searchInp = $("#model-add-search", overlay);
    const addList = $("#model-add-list", overlay);
    const customBtn = $("#model-add-custom", overlay);
    const spinner = $("#model-add-spinner", overlay);

    function closeAddDropdown() {
      dropdown.hidden = true;
      searchInp.value = "";
      customBtn.hidden = true;
    }

    addBtn.addEventListener("click", () => {
      if (!dropdown.hidden) { closeAddDropdown(); return; }
      dropdown.hidden = false;
      searchInp.value = "";
      searchInp.focus();
      renderAddList("");
      if (!scannedModels.length) scanModels();
    });

    document.addEventListener("click", (e) => {
      const wrap = $("#model-add-wrap", overlay);
      if (wrap && !wrap.contains(e.target)) closeAddDropdown();
    });

    searchInp.addEventListener("input", () => {
      renderAddList(searchInp.value);
    });

    customBtn.addEventListener("click", () => {
      const v = searchInp.value.trim();
      if (v) addModel(v, v);
    });

    function renderAddList(query) {
      const q = query.toLowerCase().trim();
      const currentIds = new Set(
        [...overlay.querySelectorAll("[data-model-id]")].map((el) => el.dataset.modelId)
      );
      let filtered = scannedModels.filter((m) => !currentIds.has(m.id));
      if (q) filtered = filtered.filter((m) =>
        m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q)
      );
      addList.innerHTML = filtered.slice(0, 20).map((m) => {
        const name = m.name ? ` <span class="model-add-name">${escapeHTML(m.name)}</span>` : "";
        return `<button type="button" class="model-add-opt" data-add-id="${escapeHTML(m.id)}">${escapeHTML(m.id)}${name}</button>`;
      }).join("") || (q ? '<div class="model-add-empty">无匹配结果</div>' : '<div class="model-add-empty">扫描中…</div>');
      addList.querySelectorAll("[data-add-id]").forEach((btn) =>
        btn.addEventListener("click", () => addModel(btn.dataset.addId, btn.dataset.addId)));
      customBtn.hidden = !q || filtered.some((m) => m.id === q);
    }

    let scanAbortCtrl = null;
    async function scanModels() {
      if (scanAbortCtrl) scanAbortCtrl.abort();
      const ctrl = scanAbortCtrl = new AbortController();
      spinner.hidden = false;
      try {
        const r = await fetch("/config/list-models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
          signal: ctrl.signal,
        });
        const d = await r.json().catch(() => ({}));
        if (ctrl.signal.aborted) return;
        if (d.models && d.models.length) {
          scannedModels = d.models;
          renderAddList(searchInp.value);
        }
      } catch (e) {
        if (e.name === "AbortError") return;
      }
      spinner.hidden = true;
    }

    setErr("");
    $("#model-list", overlay).innerHTML = '<div class="model-loading">加载中…</div>';
    $("#model-efforts", overlay).innerHTML = "";
    overlay.hidden = false;
    fetchModelInfo().then(renderInfo).catch((e) => setErr(`加载失败：${e.message}`));
  }

  // ── AI 供应商 Setup 面板 ─────────────────────────────────────────────
  // 列常见 provider（Vertex/Gemini/OpenAI/Claude/OpenRouter）+ 自定义(OpenAI 兼容)，
  // 密钥经 POST /config 白名单存盘、即时生效；测试连接走 POST /config/test-brain。
  function openSetupPanel() {
    let overlay = $("#setup-modal");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "setup-modal";
      overlay.className = "auth-modal";
      overlay.hidden = true;
      overlay.innerHTML = `
        <div class="model-backdrop" data-setup-close></div>
        <div class="auth-dialog setup-dialog" role="dialog" aria-modal="true" aria-labelledby="setup-title">
          <button type="button" class="auth-x" data-setup-close aria-label="关闭">×</button>
          <h2 id="setup-title">AI 供应商配置</h2>
          <p class="setup-sub">拖动排序供应商优先级，选中后配置密钥与模型。</p>
          <div class="setup-providers" id="setup-providers"></div>
          <div class="setup-fields" id="setup-fields"></div>
          <div class="setup-actions">
            <button type="button" class="setup-test" id="setup-test">测试连接</button>
            <button type="button" class="setup-save" id="setup-save">保存并启用</button>
          </div>
          <p class="setup-result" id="setup-result" hidden></p>
          <div class="setup-divider"></div>
          <h3 class="setup-section-title">搜索引擎</h3>
          <p class="setup-sub">配置联网搜索的 API 密钥。留空则自动降级到 DuckDuckGo（免费、无需密钥）。</p>
          <div class="search-provider-chips" id="search-provider-chips"></div>
          <div class="search-fields" id="search-fields"></div>
          <div class="setup-actions">
            <button type="button" class="setup-save search-save" id="search-save">保存搜索配置</button>
          </div>
          <p class="setup-result" id="search-result" hidden></p>
          <p class="auth-error" id="setup-error" hidden></p>
        </div>`;
      document.body.appendChild(overlay);
      overlay.querySelectorAll("[data-setup-close]").forEach((el) =>
        el.addEventListener("click", () => { overlay.hidden = true; }));
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !overlay.hidden) overlay.hidden = true;
      });
    }
    const st = { info: null, sel: "", vals: {}, curProvider: "", scannedModels: [], providerOrder: [] };
    const errEl = $("#setup-error", overlay);
    const resEl = $("#setup-result", overlay);
    const setErr = (m) => { if (m) { errEl.textContent = m; errEl.hidden = false; } else errEl.hidden = true; };
    const setRes = (m, ok) => {
      if (!m) { resEl.hidden = true; return; }
      resEl.textContent = m; resEl.hidden = false;
      resEl.className = "setup-result " + (ok ? "ok" : "bad");
    };

    const FIELD_META = {
      vertex_project:  { label: "GCP 项目 ID", ph: "my-project-123" },
      vertex_location: { label: "区域", ph: "global / us-east5 / us-central1" },
      base_url:        { label: "Base URL", ph: "https://…/v1/chat/completions" },
      key:             { label: "API Key", ph: "sk-…（留空=沿用已存）" },
    };

    function providerCard(p, active) {
      return `<div class="setup-pcard${active ? " active" : ""}" data-pid="${escapeHTML(p.id)}" draggable="true">
        <span class="setup-drag" title="拖动排序">☰</span>
        <div class="setup-ptext">
          <span class="setup-pname">${escapeHTML(p.label)}</span>
          <span class="setup-phint">${escapeHTML(p.hint)}</span>
        </div>
      </div>`;
    }

    function keyStateLabel(p, info) {
      if (!p.key_field) return "";
      const map = { openrouter_api_key: "openrouter", gemini_api_key: "gemini", anthropic_api_key: "anthropic", openai_api_key: "openai" };
      const has = info.has_key && info.has_key[map[p.key_field]];
      return has ? ' <span class="setup-haskey">已配置</span>' : "";
    }

    function getProviders() {
      if (!st.info) return [];
      return st.providerOrder.map((id) => (st.info.providers || []).find((x) => x.id === id)).filter(Boolean);
    }

    // ── drag-to-reorder ──
    let dragSrc = null;
    function initDrag(container) {
      container.addEventListener("dragstart", (e) => {
        const card = e.target.closest("[data-pid]");
        if (!card) return;
        dragSrc = card;
        card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", card.dataset.pid);
      });
      container.addEventListener("dragend", (e) => {
        const card = e.target.closest("[data-pid]");
        if (card) card.classList.remove("dragging");
        container.querySelectorAll("[data-pid]").forEach((c) => c.classList.remove("drag-over"));
        dragSrc = null;
      });
      container.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        const card = e.target.closest("[data-pid]");
        container.querySelectorAll("[data-pid]").forEach((c) => c.classList.remove("drag-over"));
        if (card && card !== dragSrc) card.classList.add("drag-over");
      });
      container.addEventListener("drop", (e) => {
        e.preventDefault();
        const target = e.target.closest("[data-pid]");
        if (!target || !dragSrc || target === dragSrc) return;
        const fromId = dragSrc.dataset.pid;
        const toId = target.dataset.pid;
        const arr = st.providerOrder;
        const fi = arr.indexOf(fromId), ti = arr.indexOf(toId);
        if (fi < 0 || ti < 0) return;
        arr.splice(fi, 1);
        arr.splice(ti, 0, fromId);
        renderProviders();
      });
    }

    function renderProviders() {
      const container = $("#setup-providers", overlay);
      const cur = st.sel;
      container.innerHTML = getProviders().map((p) => providerCard(p, p.id === cur)).join("");
      container.querySelectorAll("[data-pid]").forEach((c) => {
        c.addEventListener("click", (e) => {
          if (e.target.closest(".setup-drag")) return;
          selectProvider(c.dataset.pid);
        });
      });
    }

    // ── model combo-box (auto-scan + free input) ──
    let scanAbort = null;
    function renderModelField(box, p, curVal) {
      const wrap = document.createElement("label");
      wrap.className = "setup-f";
      wrap.innerHTML = `<span>模型 ID</span><div class="setup-model-wrap">
        <input type="text" data-f="model" value="${escapeHTML(curVal)}" placeholder="输入模型 ID 或从列表选择">
        <span class="setup-model-spinner" hidden></span>
        <div class="setup-model-list" hidden></div>
      </div>`;
      box.appendChild(wrap);

      const inp = wrap.querySelector('input[data-f="model"]');
      const spinner = wrap.querySelector(".setup-model-spinner");
      const listEl = wrap.querySelector(".setup-model-list");

      inp.addEventListener("input", () => {
        st.vals.model = inp.value;
        filterModelList(inp.value, listEl, inp);
      });
      inp.addEventListener("focus", () => {
        if (st.scannedModels.length) { filterModelList(inp.value, listEl, inp); listEl.hidden = false; }
      });

      document.addEventListener("click", (e) => {
        if (!wrap.contains(e.target)) listEl.hidden = true;
      });

      // presets as immediate fallback
      if (p.model_presets && p.model_presets.length) {
        st.scannedModels = p.model_presets.map((m) => ({ id: m }));
        renderModelList(st.scannedModels, listEl, inp);
      }

      // auto-scan on render
      autoScanModels(inp, listEl, spinner, p);
    }

    async function autoScanModels(inp, listEl, spinner, p) {
      if (scanAbort) scanAbort.abort();
      const ctrl = scanAbort = new AbortController();
      spinner.hidden = false;
      try {
        const body = buildBody();
        const r = await fetch("/config/list-models", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body), signal: ctrl.signal,
        });
        const d = await r.json().catch(() => ({}));
        if (ctrl.signal.aborted) return;
        if (d.models && d.models.length) {
          st.scannedModels = d.models;
          renderModelList(d.models, listEl, inp);
        }
      } catch (e) {
        if (e.name === "AbortError") return;
      }
      spinner.hidden = true;
    }

    function renderModelList(models, listEl, inp) {
      listEl.innerHTML = models.map((m) => {
        const name = m.name ? `<span class="setup-model-name">${escapeHTML(m.name)}</span>` : "";
        return `<div class="setup-model-opt" data-mid="${escapeHTML(m.id)}">${escapeHTML(m.id)}${name}</div>`;
      }).join("");
      listEl.querySelectorAll("[data-mid]").forEach((opt) => {
        opt.addEventListener("click", () => {
          inp.value = opt.dataset.mid;
          st.vals.model = opt.dataset.mid;
          listEl.hidden = true;
        });
      });
    }

    function filterModelList(query, listEl, inp) {
      if (!st.scannedModels.length) return;
      const q = query.toLowerCase();
      const filtered = q ? st.scannedModels.filter((m) =>
        m.id.toLowerCase().includes(q) || (m.name && m.name.toLowerCase().includes(q))
      ) : st.scannedModels;
      renderModelList(filtered, listEl, inp);
      listEl.hidden = filtered.length === 0;
    }

    function renderFields() {
      const p = getProviders().find((x) => x.id === st.sel);
      const box = $("#setup-fields", overlay);
      if (!p) { box.innerHTML = ""; return; }
      box.innerHTML = "";
      st.scannedModels = [];

      for (const f of p.fields) {
        if (f === "model") {
          let val = st.vals.model ?? "";
          if (!val && st.sel === st.curProvider) val = st.info.model || "";
          renderModelField(box, p, val);
          continue;
        }
        const meta = FIELD_META[f] || { label: f, ph: "" };
        let val = st.vals[f] ?? "";
        if (!val) {
          if (f === "vertex_project") val = st.info.vertex_project || "";
          else if (f === "vertex_location") val = st.info.vertex_location || "";
          else if (f === "base_url") val = st.info.base_url || "";
        }
        const label = document.createElement("label");
        label.className = "setup-f";
        label.innerHTML = `<span>${escapeHTML(meta.label)}</span>
          <input type="text" data-f="${f}" value="${escapeHTML(val)}" placeholder="${escapeHTML(meta.ph)}">`;
        box.appendChild(label);
      }
      if (p.key_field) {
        const label = document.createElement("label");
        label.className = "setup-f";
        label.innerHTML = `<span>${escapeHTML(FIELD_META.key.label)}${keyStateLabel(p, st.info)}</span>
          <input type="password" data-f="key" value="" placeholder="${escapeHTML(FIELD_META.key.ph)}">`;
        box.appendChild(label);
      }

      const effs = st.info.efforts || [];
      const curEff = st.vals.effort || st.info.effort || "medium";
      const effDiv = document.createElement("div");
      effDiv.innerHTML = `<div class="setup-effort-label">思考强度</div><div class="setup-efforts">${effs.map((e) =>
        `<button type="button" class="setup-echip${e === curEff ? " active" : ""}" data-eff="${escapeHTML(e)}">${escapeHTML(e)}</button>`).join("")}</div>`;
      box.appendChild(effDiv);

      box.querySelectorAll("input[data-f]").forEach((inp) => {
        if (inp.dataset.f !== "model") inp.addEventListener("input", () => { st.vals[inp.dataset.f] = inp.value; });
      });
      box.querySelectorAll("[data-eff]").forEach((b) =>
        b.addEventListener("click", () => {
          st.vals.effort = b.dataset.eff;
          box.querySelectorAll("[data-eff]").forEach((x) => x.classList.toggle("active", x === b));
        }));
    }

    function selectProvider(pid) {
      st.sel = pid;
      st.vals = { effort: st.vals.effort };
      st.scannedModels = [];
      renderProviders();
      setRes(""); setErr("");
      renderFields();
    }

    function buildBody() {
      const p = getProviders().find((x) => x.id === st.sel);
      const body = { provider: st.sel };
      if (st.vals.model) body.model = st.vals.model;
      if (st.vals.effort) body.effort = st.vals.effort;
      if (st.vals.base_url) body.base_url = st.vals.base_url;
      if (st.vals.vertex_project) body.vertex_project = st.vals.vertex_project;
      if (st.vals.vertex_location) body.location = st.vals.vertex_location, body.vertex_location = st.vals.vertex_location;
      if (p && p.key_field && st.vals.key) body[p.key_field] = st.vals.key;
      return body;
    }

    async function doSave() {
      setErr(""); setRes("保存中…", true);
      try {
        const r = await fetch("/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildBody()) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        setRes("已保存并启用 ✓（新会话即生效）", true);
      } catch (e) { setRes(""); setErr(`保存失败：${e.message}`); }
    }

    async function doTest() {
      setErr(""); setRes("测试中…（可能需数秒）", true);
      try {
        const r = await fetch("/config/test-brain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildBody()) });
        const d = await r.json().catch(() => ({}));
        if (d.ok) setRes(`连接成功 ✓ ${d.provider}/${d.model} — 回样「${d.sample || ""}」`, true);
        else setRes(`连接失败：${d.error || "未知错误"}（${d.provider || ""}/${d.model || ""}）`, false);
      } catch (e) { setRes(""); setErr(`测试失败：${e.message}`); }
    }

    $("#setup-save", overlay).onclick = doSave;
    $("#setup-test", overlay).onclick = doTest;

    // ── 搜索引擎配置 ──
    const SEARCH_PROVIDERS = [
      { id: "auto",       label: "自动",       hint: "按优先级自动检测已配置的引擎", fields: [] },
      { id: "tavily",     label: "Tavily",     hint: "AI 优化搜索", fields: [{ key: "tavily_api_key", label: "API Key", ph: "tvly-…" }] },
      { id: "brave",      label: "Brave",      hint: "隐私优先搜索", fields: [{ key: "brave_api_key", label: "API Key", ph: "BSA…" }] },
      { id: "serper",     label: "Serper",      hint: "Google 搜索 API", fields: [{ key: "serper_api_key", label: "API Key", ph: "" }] },
      { id: "exa",        label: "Exa",         hint: "语义搜索", fields: [{ key: "exa_api_key", label: "API Key", ph: "" }] },
      { id: "bing",       label: "Bing",        hint: "微软 Bing 搜索", fields: [{ key: "bing_api_key", label: "API Key", ph: "" }] },
      { id: "google_cse", label: "Google CSE",  hint: "自定义搜索引擎", fields: [{ key: "google_cse_key", label: "API Key", ph: "" }, { key: "google_cse_id", label: "搜索引擎 ID (CX)", ph: "" }] },
      { id: "searxng",    label: "SearXNG",     hint: "自托管、免费", fields: [{ key: "searxng_url", label: "实例 URL", ph: "https://searx.example.com" }, { key: "searxng_api_key", label: "Bearer Token（可选）", ph: "" }] },
      { id: "duckduckgo",  label: "DuckDuckGo", hint: "免费、无需密钥", fields: [] },
    ];
    let searchSel = "auto";
    const searchVals = {};
    const searchResEl = $("#search-result", overlay);
    const setSearchRes = (m, ok) => {
      if (!m) { searchResEl.hidden = true; return; }
      searchResEl.textContent = m; searchResEl.hidden = false;
      searchResEl.className = "setup-result " + (ok ? "ok" : "bad");
    };

    function renderSearchChips(searchInfo) {
      const chips = $("#search-provider-chips", overlay);
      chips.innerHTML = SEARCH_PROVIDERS.map((sp) => {
        const on = sp.id === searchSel;
        const hasKey = searchInfo && searchInfo.has_key && searchInfo.has_key[sp.id];
        const dot = hasKey ? '<span class="search-key-dot"></span>' : "";
        return `<button type="button" class="search-chip${on ? " active" : ""}" data-sp="${escapeHTML(sp.id)}">${dot}${escapeHTML(sp.label)}</button>`;
      }).join("");
      chips.querySelectorAll("[data-sp]").forEach((btn) =>
        btn.addEventListener("click", () => { searchSel = btn.dataset.sp; renderSearchChips(searchInfo); renderSearchFields(); }));
    }

    function renderSearchFields() {
      const box = $("#search-fields", overlay);
      const sp = SEARCH_PROVIDERS.find((p) => p.id === searchSel);
      if (!sp || !sp.fields.length) {
        box.innerHTML = sp && sp.id === "auto"
          ? '<p class="search-hint">自动模式按优先级探测：Tavily → Serper → Brave → Exa → Google CSE → Bing → SearXNG → DuckDuckGo</p>'
          : '<p class="search-hint">无需配置</p>';
        return;
      }
      box.innerHTML = sp.fields.map((f) => {
        const val = searchVals[f.key] || "";
        const isSecret = f.key.includes("api_key") || f.key.includes("key");
        return `<label class="setup-f"><span>${escapeHTML(f.label)}</span>
          <input type="${isSecret ? "password" : "text"}" data-sf="${escapeHTML(f.key)}" value="${escapeHTML(val)}" placeholder="${escapeHTML(f.ph || "留空=沿用已存")}"></label>`;
      }).join("");
      box.querySelectorAll("input[data-sf]").forEach((inp) =>
        inp.addEventListener("input", () => { searchVals[inp.dataset.sf] = inp.value; }));
    }

    async function doSearchSave() {
      setSearchRes("保存中…", true);
      try {
        const body = { search_provider: searchSel };
        for (const [k, v] of Object.entries(searchVals)) { if (v) body[k] = v; }
        const r = await fetch("/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        setSearchRes("已保存 ✓", true);
      } catch (e) { setSearchRes(`保存失败：${e.message}`, false); }
    }

    $("#search-save", overlay).onclick = doSearchSave;

    setErr(""); setRes("");
    const provBox = $("#setup-providers", overlay);
    provBox.innerHTML = '<div class="model-loading">加载中…</div>';
    $("#setup-fields", overlay).innerHTML = "";
    initDrag(provBox);
    overlay.hidden = false;
    fetch("/config").then((r) => r.json()).then((cfg) => {
      const info = cfg.brain;
      if (!info) { setErr("需先登录才能配置供应商"); return; }
      st.info = info;
      st.vals = { effort: info.effort || "medium" };
      st.providerOrder = (info.providers || []).map((p) => p.id);
      const cur = info.provider === "openai" && info.base_url ? "custom" : (info.provider || "openrouter");
      st.curProvider = cur;
      const idx = st.providerOrder.indexOf(cur);
      if (idx > 0) { st.providerOrder.splice(idx, 1); st.providerOrder.unshift(cur); }
      renderProviders();
      selectProvider(cur);
      // 搜索引擎初始化
      const searchInfo = cfg.search || {};
      searchSel = searchInfo.provider || "auto";
      renderSearchChips(searchInfo);
      renderSearchFields();
    }).catch((e) => setErr(`加载失败：${e.message}`));
  }

  // ── slash-command palette ───────────────────────────────────────────
  // A "/" at the start of an empty-ish line opens a floating command menu
  // above the composer. Mirrors the CLI slash set (src/slash.js), mapping
  // each command to an existing web action so the two clients stay in sync.
  // 描述 ≤10 字（文字精简）；细节进 /help，不进菜单行
  const SLASH_COMMANDS = [
    { name: "help",    desc: "所有命令" },
    { name: "new",     desc: "开启新会话" },
    { name: "clear",   desc: "清空当前对话" },
    { name: "upload",  desc: "上传素材" },
    { name: "plan",    desc: "只规划，批准后执行" },
    { name: "model",   desc: "切换模型与强度" },
    { name: "setup",   desc: "配置 AI 供应商" },
    { name: "sandbox", desc: "沙盒开关" },
    { name: "library", desc: "刷新媒体库标注" },
    { name: "login",   desc: "登录 / 账户" },
  ];
  const slash = { open: false, items: [], sel: 0 };

  function knownSlash(name) { return SLASH_COMMANDS.some((c) => c.name === name); }

  // Command name iff the line is `/name` (any trailing arg ignored) and known.
  function parseSlashName(line) {
    if (!line.startsWith("/")) return null;
    const sp = line.indexOf(" ");
    const name = (sp === -1 ? line.slice(1) : line.slice(1, sp)).toLowerCase();
    return knownSlash(name) ? name : null;
  }

  // Autocomplete state: active only while the line is a single `/token` (no space).
  function slashMatch(line) {
    if (!line.startsWith("/") || line.includes(" ")) return null;
    const frag = line.slice(1).toLowerCase();
    const matches = SLASH_COMMANDS.filter((c) => c.name.startsWith(frag));
    return matches.length ? matches : null;
  }

  function slashRender() {
    const m = els.slashMenu;
    if (!m) return;
    if (!slash.open) { m.hidden = true; m.innerHTML = ""; return; }
    const rows = slash.items.map((c, i) => `
      <div class="slash-item${i === slash.sel ? " active" : ""}" data-slash="${c.name}">
        <span class="slash-name">/${c.name}</span>
        <span class="slash-desc">${escapeHTML(c.desc)}</span>
      </div>`).join("");
    m.innerHTML = rows;
    m.hidden = false;
    m.querySelector(".slash-item.active")?.scrollIntoView({ block: "nearest" });
  }

  function slashSync() {
    const matches = slashMatch(els.promptInput.value);
    if (!matches) { slash.open = false; slashRender(); return; }
    slash.open = true;
    slash.items = matches;
    slash.sel = 0;
    slashRender();
  }

  function slashClose() { slash.open = false; slashRender(); }

  function execSlash(name) {
    // /help lists everything by re-opening the menu on a bare slash.
    if (name === "help") { els.promptInput.value = "/"; slashSync(); els.promptInput.focus(); return; }
    switch (name) {
      case "new":     els.newSessionBtn.click(); break;
      case "clear":   state.turns = []; state.currentTurn = null; render(); break;
      case "upload":  els.uploadBtn.click(); break;
      case "plan":    els.planBtn?.click(); break;
      case "model":   openModelPicker(); break;
      case "setup":   openSetupPanel(); break;
      case "sandbox": els.sandboxBtn?.click(); break;
      case "library": els.libraryRefreshBtn?.click(); break;
      case "login":   $("#account-btn")?.click(); break;
    }
    els.promptInput.value = "";
    slashClose();
    syncShell();
  }

  // Menu navigation. Returns true when it consumed the key.
  function slashKeydown(e) {
    if (!slash.open || !slash.items.length) return false;
    const n = slash.items.length;
    if (e.key === "ArrowDown") { e.preventDefault(); slash.sel = (slash.sel + 1) % n; slashRender(); return true; }
    if (e.key === "ArrowUp")   { e.preventDefault(); slash.sel = (slash.sel - 1 + n) % n; slashRender(); return true; }
    if (e.key === "Escape")    { e.preventDefault(); slashClose(); return true; }
    if (e.key === "Tab")       { e.preventDefault(); els.promptInput.value = "/" + slash.items[slash.sel].name; slashClose(); return true; }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      execSlash(slash.items[slash.sel].name);
      return true;
    }
    return false;
  }

  els.promptInput.addEventListener("input", slashSync);
  // Clicking a menu row runs it; clicking elsewhere dismisses the menu.
  els.slashMenu?.addEventListener("mousedown", (e) => {
    const row = e.target.closest(".slash-item[data-slash]");
    if (!row) return;
    e.preventDefault();               // keep focus in the textarea
    execSlash(row.dataset.slash);
  });
  document.addEventListener("click", (e) => {
    if (!slash.open) return;
    if (e.target.closest(".composer")) return;
    slashClose();
  });

  els.sendBtn.addEventListener("click", () => {
    const msg = els.promptInput.value.trim();
    if (!msg) return;
    const name = parseSlashName(msg);
    if (name) { execSlash(name); return; }
    const action = state.turnInProgress ? steerTurn(msg) : submitTurn(msg);
    action.then(() => { els.promptInput.value = ""; slashClose(); syncShell(); })
                   .catch((err) => {
                     state.errors.push(`${state.turnInProgress ? "steer" : "submit turn"} failed: ${err.message}`);
                     state.currentTurn?.banners.push({ kind: "info", text: state.turnInProgress ? "引导未送达，请再试一次" : "任务未能开始，请稍后重试" });
                     render();
                   });
  });
  els.promptInput.addEventListener("keydown", (e) => {
    // Slash menu gets first crack at arrows/enter/tab/esc.
    if (slashKeydown(e)) return;
    if (voiceInput.listening && e.key === "Enter") {
      e.preventDefault();
      stopVoiceInput();
      return;
    }
    // Enter sends; Shift+Enter = newline. Never send mid-IME-composition (中文输入法候选).
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      // A bare `/command` runs directly — works even when send is disabled (no session).
      const raw = els.promptInput.value.trim();
      const name = raw && parseSlashName(raw);
      if (name) { execSlash(name); return; }
      els.sendBtn.click();
    }
  });

  // ── input shell: "+" popover · auto-grow · send-appears-on-text ──────
  const shell = $("#input-shell");
  const plusBtn = $("#plus-btn");
  const plusMenu = $("#plus-menu");
  const previewStage = $("#preview-stage");
  const assetsTray = $("#assets-tray");

  // Grow the pill past one line; reveal the ice send-disc once there is text.
  // The grow decision is measured at the NON-grown (buttons-inline) width so it
  // can't feed back on itself: measuring while grown widens the field, un-wraps
  // the text, and would flip the decision back — the boundary jitter bug. We
  // drop .is-grown, read scrollHeight synchronously (no paint between), then
  // restore the real state and size the field to the actual layout.
  function syncShell() {
    const ta = els.promptInput;
    shell.classList.remove("is-grown");
    ta.style.height = "auto";
    const grown = ta.scrollHeight > 48 || ta.value.includes("\n");  // measured at pill width
    shell.classList.toggle("is-grown", grown);
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
    shell.classList.toggle("has-text", ta.value.trim().length > 0);
  }
  els.promptInput.addEventListener("input", syncShell);

  // ── voice input: browser speech recognition → editable composer text ──
  // Recognition never submits a turn. The user can review/correct the text,
  // then explicitly send it. Chrome exposes the API with a webkit prefix.
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const voiceInput = {
    recognition: null,
    listening: false,
    requesting: false,
    baseText: "",
    hadResult: false,
    stopMessage: "",
    errorMessage: "",
    statusTimer: null,
  };

  function setVoiceStatus(message, persistent = false) {
    clearTimeout(voiceInput.statusTimer);
    els.voiceInputStatus.textContent = message || "";
    els.voiceInputStatus.hidden = !message;
    if (message && !persistent) {
      voiceInput.statusTimer = setTimeout(() => { els.voiceInputStatus.hidden = true; }, 4200);
    }
  }

  function joinVoiceText(base, spoken) {
    const left = String(base || "").trimEnd();
    const right = String(spoken || "").trim();
    if (!left) return right;
    if (!right) return left;
    return `${left} ${right}`;
  }

  function renderVoiceState() {
    shell.classList.toggle("is-listening", voiceInput.listening);
    els.voiceInputBtn.classList.toggle("is-listening", voiceInput.listening);
    els.voiceInputBtn.setAttribute("aria-pressed", String(voiceInput.listening));
    els.voiceInputBtn.setAttribute("aria-label", voiceInput.listening ? "停止语音输入" : "语音输入");
    els.voiceInputBtn.title = voiceInput.listening ? "停止语音输入" : "语音输入";
    els.voiceInputBtn.querySelector("use")?.setAttribute("href", voiceInput.listening ? "#i-stop" : "#i-mic");
    syncShell();
  }

  function stopVoiceInput(message = "语音已转成文字，请确认后发送") {
    if (!voiceInput.listening) return;
    voiceInput.stopMessage = message;
    try { voiceInput.recognition?.stop(); } catch {}
  }

  async function requestMicrophonePermission() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setVoiceStatus("麦克风权限只能在 HTTPS 或 localhost 页面申请");
      return false;
    }
    voiceInput.requesting = true;
    els.voiceInputBtn.setAttribute("aria-busy", "true");
    setVoiceStatus("正在申请麦克风权限…", true);
    let stream = null;
    try {
      // Calling getUserMedia directly from the click handler makes Chrome show
      // its permission prompt before speech recognition starts.
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      return true;
    } catch (error) {
      const messages = {
        NotAllowedError: "麦克风权限被拒绝，请在浏览器地址栏中允许后重试",
        SecurityError: "当前页面无法申请麦克风权限，请使用 HTTPS 或 localhost",
        NotFoundError: "没有检测到可用的麦克风",
        NotReadableError: "麦克风正被其他应用占用，请关闭后重试",
        AbortError: "麦克风启动失败，请重试",
      };
      setVoiceStatus(messages[error?.name] || "无法取得麦克风权限，请检查浏览器设置");
      return false;
    } finally {
      // Permission is retained by the browser; release the probe stream so the
      // speech recognizer can own the microphone without two active captures.
      stream?.getTracks().forEach((track) => track.stop());
      voiceInput.requesting = false;
      els.voiceInputBtn.removeAttribute("aria-busy");
    }
  }

  async function startVoiceInput() {
    if (!SpeechRecognition) {
      setVoiceStatus("此浏览器不支持语音输入，请使用最新版 Chrome");
      return;
    }
    if (voiceInput.listening) { stopVoiceInput(); return; }
    if (voiceInput.requesting) return;
    if (!await requestMicrophonePermission()) return;

    const recognition = new SpeechRecognition();
    recognition.lang = document.documentElement.lang || navigator.language || "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    voiceInput.recognition = recognition;
    voiceInput.baseText = els.promptInput.value;
    voiceInput.hadResult = false;
    voiceInput.stopMessage = "";
    voiceInput.errorMessage = "";

    recognition.onstart = () => {
      voiceInput.listening = true;
      renderVoiceState();
      setVoiceStatus("正在听… 再点一次麦克风即可停止", true);
    };
    recognition.onresult = (event) => {
      let spoken = "";
      for (let i = 0; i < event.results.length; i += 1) {
        spoken += event.results[i][0]?.transcript || "";
      }
      voiceInput.hadResult = voiceInput.hadResult || Boolean(spoken.trim());
      els.promptInput.value = joinVoiceText(voiceInput.baseText, spoken);
      els.promptInput.dispatchEvent(new Event("input", { bubbles: true }));
    };
    recognition.onerror = (event) => {
      const messages = {
        "not-allowed": "麦克风权限被拒绝，请在浏览器设置中允许后重试",
        "service-not-allowed": "浏览器已禁止语音识别服务",
        "audio-capture": "没有检测到可用的麦克风",
        "no-speech": "没有听到语音，请再试一次",
        "network": "语音识别网络不可用，请检查连接后重试",
      };
      if (event.error !== "aborted" || !voiceInput.stopMessage) {
        voiceInput.errorMessage = messages[event.error] || "语音识别暂时不可用，请重试";
      }
    };
    recognition.onend = () => {
      voiceInput.listening = false;
      voiceInput.recognition = null;
      renderVoiceState();
      if (voiceInput.errorMessage) setVoiceStatus(voiceInput.errorMessage);
      else if (voiceInput.stopMessage) setVoiceStatus(voiceInput.stopMessage);
      else if (voiceInput.hadResult) setVoiceStatus("语音已转成文字，请确认后发送");
      else setVoiceStatus("语音输入已结束");
      els.promptInput.focus();
    };

    try { recognition.start(); }
    catch { setVoiceStatus("语音输入正在启动，请稍后再试"); }
  }

  if (!SpeechRecognition) {
    els.voiceInputBtn.classList.add("is-unavailable");
    els.voiceInputBtn.setAttribute("aria-disabled", "true");
    els.voiceInputBtn.title = "当前浏览器不支持语音输入";
  }
  els.voiceInputBtn.addEventListener("click", () => {
    if (state.turnInProgress) stopCurrentTurn();
    else startVoiceInput();
  });

  // Starter suggestion chips (rail empty state): click fills the composer.
  document.getElementById("rail-empty")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".suggest-chip");
    if (!chip) return;
    els.promptInput.value = chip.dataset.suggest || chip.textContent.trim();
    syncShell();
    els.promptInput.focus();
  });

  // "+" is the single entry point. Popover opens upward from the shell.
  function openPlus()  { plusMenu.hidden = false; plusBtn.setAttribute("aria-expanded", "true"); }
  function closePlus() { plusMenu.hidden = true;  plusBtn.setAttribute("aria-expanded", "false"); }
  plusBtn.addEventListener("click", (e) => { e.stopPropagation(); plusMenu.hidden ? openPlus() : closePlus(); });
  plusMenu.addEventListener("click", (e) => {
    const item = e.target.closest(".plus-item");
    if (!item) return;
    // plan / sandbox rows: forward to the MOVED real button (keeps its listener);
    // the switch is pointer-events:none so a real click always targets the row.
    // Guard: the programmatic .click() re-enters here with target===inner → skip.
    const inner = item.querySelector("#plan-toggle-btn, #sandbox-toggle-btn");
    if (inner) { if (!inner.contains(e.target)) inner.click(); return; }   // stay open — flip is visible
    const kind = item.dataset.plus;
    if (kind === "slash")    { closePlus(); els.promptInput.value = "/"; slashSync(); els.promptInput.focus(); return; }
    if (kind === "assets")   { closePlus(); toggleTray(true); return; }
    closePlus();   // upload row already fired its own listener (→ file picker)
  });
  document.addEventListener("click", (e) => {
    if (plusMenu.hidden) return;
    if (e.target.closest("#plus-menu") || e.target.closest("#plus-btn")) return;
    closePlus();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !plusMenu.hidden) { closePlus(); plusBtn.focus(); }
  });
  // Switch rows are divs (a real <button> sits inside — nesting buttons is invalid
  // HTML), so give them the keyboard side of the switch contract: Space/Enter flips.
  plusMenu.addEventListener("keydown", (e) => {
    const row = e.target.closest('.plus-item[role="menuitemcheckbox"]');
    if (!row) return;
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); row.click(); }
  });
  // Keep row aria-checked in sync with the moved real buttons' state classes
  // (renderPlanUi toggles .on, renderSandbox toggles .off) without touching render().
  const syncAria = () => {
    plusMenu.querySelector('[data-plus="plan"]')
      ?.setAttribute("aria-checked", els.planBtn?.classList.contains("on") ? "true" : "false");
    plusMenu.querySelector('[data-plus="sandbox"]')
      ?.setAttribute("aria-checked", els.sandboxBtn?.classList.contains("off") ? "false" : "true");
  };
  if (els.planBtn && els.sandboxBtn) {
    const mo = new MutationObserver(syncAria);
    mo.observe(els.planBtn, { attributes: true, attributeFilter: ["class"] });
    mo.observe(els.sandboxBtn, { attributes: true, attributeFilter: ["class"] });
    syncAria();
  }

  // Left-stage timeline drawer (also mirrored on the "+" timeline switch).
  function toggleDrawer(force) {
    const open = force === undefined ? !previewStage.classList.contains("drawer-open") : force;
    if (open && !stageTabs.includes("timeline")) {
      stageTabs.push("timeline");
      saveStageTabs();
    }
    previewStage.classList.toggle("drawer-open", open);
    renderStageTabs();
  }
  // Summoned media-library tray.
  function toggleTray(open) {
    assetsTray.hidden = !open;
    if (open) fetchMediaLibrary().catch(() => {});
  }
  $("#assets-tray-close")?.addEventListener("click", () => toggleTray(false));
  assetsTray?.addEventListener("click", (e) => { if (e.target === assetsTray) toggleTray(false); });

  // ── workspace modules: the strip controls visibility, not exclusive pages ──
  const stagePanel = $("#stage-panel");
  const workspaceBoard = $("#workspace-board");
  const timelineDrawer = $("#timeline-drawer");
  const WorkspaceLayout = window.LumeriWorkspaceLayout;

  const STAGE_VIEWS = {
    timeline: { label: "时间线", ico: '<path d="M5 10v4M9 7v10M13 9v6M17 6v12M21 10v4"/>' },
    outline:  { label: "大纲", ico: '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/><path d="M7 10h6M7 13.5h9.5"/>' },
    tasks:    { label: "后台任务", ico: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>' },
    files:    { label: "文件", ico: '<path d="M3.5 6.5c0-1.1.9-2 2-2h3.6c.5 0 .9.2 1.2.6l1.4 1.9H18.5c1.1 0 2 .9 2 2v8.5c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/>' },
  };
  const PREVIEW_ICO = '<rect x="3.5" y="5" width="17" height="12" rx="2.5"/><path d="M10.4 8.6l3.8 2.4-3.8 2.4z"/><path d="M8.5 20h7"/>';
  const stageTabsBox = $("#stage-tabs");
  const stageTabList = $("#stage-tab-list");
  const stageAddBtn = $("#stage-add-btn");
  const stageAddMenu = $("#stage-add-menu");
  const stageOverflowBtn = $("#stage-overflow-btn");
  const stageOverflowMenu = $("#stage-overflow-menu");
  let stageTabs = [];
  let activeTab = "preview";
  let bgActive = false;   // any session running / has pending jobs → tasks tab badge
  let didMigrateModules = false;
  const DEFAULT_MODULES = ["timeline", "outline", "tasks"];
  const PANEL_MODULES = new Set(["outline", "tasks", "files"]);
  const ALL_WORKSPACE_MODULES = ["preview", "outline", "tasks", "timeline", "files"];
  const WORKSPACE_ORDER_KEY = "lumeri:v3:workspace-order";
  const WORKSPACE_SIZES_KEY = "lumeri:v3:workspace-sizes";
  let workspaceOrder = [...ALL_WORKSPACE_MODULES];
  let workspaceSizes = {};
  try {
    stageTabs = JSON.parse(window.localStorage.getItem("lumeri:v3:stage-tabs") || "[]")
      .filter((k) => STAGE_VIEWS[k]);
  } catch {}
  try {
    // One-time migration from exclusive tabs to the simultaneous modular desk.
    if (window.localStorage.getItem("lumeri:v3:module-layout") !== "1") {
      stageTabs = [...new Set([...DEFAULT_MODULES, ...stageTabs])];
      window.localStorage.setItem("lumeri:v3:module-layout", "1");
      didMigrateModules = true;
    }
  } catch {
    if (!stageTabs.length) stageTabs = [...DEFAULT_MODULES];
  }
  try {
    const saved = JSON.parse(window.localStorage.getItem(WORKSPACE_ORDER_KEY) || "[]");
    const valid = Array.isArray(saved) ? saved.filter((id, i) => ALL_WORKSPACE_MODULES.includes(id) && saved.indexOf(id) === i) : [];
    workspaceOrder = [...valid, ...ALL_WORKSPACE_MODULES.filter((id) => !valid.includes(id))];
  } catch {}
  try {
    const saved = JSON.parse(window.localStorage.getItem(WORKSPACE_SIZES_KEY) || "{}");
    if (saved && typeof saved === "object") workspaceSizes = saved;
  } catch {}

  function saveStageTabs() {
    try { window.localStorage.setItem("lumeri:v3:stage-tabs", JSON.stringify(stageTabs)); } catch {}
  }
  function saveWorkspaceLayout() {
    try {
      window.localStorage.setItem(WORKSPACE_ORDER_KEY, JSON.stringify(workspaceOrder));
      window.localStorage.setItem(WORKSPACE_SIZES_KEY, JSON.stringify(workspaceSizes));
    } catch {}
  }
  function orderedStageTabs() {
    return workspaceOrder.filter((id) => stageTabs.includes(id))
      .concat(stageTabs.filter((id) => !workspaceOrder.includes(id)));
  }
  function visibleWorkspaceIds() {
    const visible = new Set(["preview"]);
    for (const id of orderedStageTabs()) {
      if (PANEL_MODULES.has(id) || (id === "timeline" && previewStage.classList.contains("drawer-open"))) visible.add(id);
    }
    return workspaceOrder.filter((id) => visible.has(id))
      .concat([...visible].filter((id) => !workspaceOrder.includes(id)));
  }
  function applyWorkspaceLayout() {
    if (!workspaceBoard || !WorkspaceLayout) return;
    const ids = visibleWorkspaceIds();
    const inset = 8;
    const bounds = {
      width: Math.max(1, workspaceBoard.clientWidth - inset * 2),
      height: Math.max(1, workspaceBoard.clientHeight - inset * 2),
      gap: 8,
    };
    const packed = WorkspaceLayout.flowModules(
      ids.map((id) => ({ id, ...WorkspaceLayout.clampSize(id, workspaceSizes[id]) })),
      bounds,
    );
    workspaceBoard.querySelectorAll("[data-workspace-module]").forEach((module) => {
      const place = packed.placements[module.dataset.workspaceModule];
      module.hidden = !place;
      if (!place) return;
      module.style.transform = `translate3d(${place.x + inset}px, ${place.y + inset}px, 0)`;
      module.style.width = `${place.width}px`;
      module.style.height = `${place.height}px`;
    });
  }
  function hideWorkspaceModule(id) {
    stageTabs = stageTabs.filter((key) => key !== id);
    if (id === "timeline") previewStage.classList.remove("drawer-open");
    saveStageTabs();
    if (activeTab === id) activeTab = "preview";
    renderStageTabs();
  }
  if (didMigrateModules) saveStageTabs();

  function setActiveTab(k) {
    activeTab = k;
    if (k === "timeline") toggleDrawer(true);
    if (PANEL_MODULES.has(k)) refreshPanel(k);
    renderStageTabs();
  }
  function panelBodyFor(view) {
    return stagePanel?.querySelector(`[data-panel-body="${view}"]`) || null;
  }
  function refreshPanel(view = activeTab) {
    const body = panelBodyFor(view);
    if (!body) return;
    if (view === "outline") renderOutlinePanel(body);
    else if (view === "tasks") renderTasksPanel(body);
    else if (view === "files") renderFilesPanel(body);
  }
  function refreshVisibleModules() {
    stageTabs.filter((k) => PANEL_MODULES.has(k)).forEach(refreshPanel);
  }
  function syncWorkspaceModules() {
    if (!stagePanel) return;
    const visible = stageTabs.filter((k) => PANEL_MODULES.has(k));
    stagePanel.hidden = visible.length === 0;
    const signature = `${visible.join("|")}|tasks:${bgActive}`;
    if (stagePanel.dataset.signature !== signature) {
      stagePanel.dataset.signature = signature;
      stagePanel.innerHTML = visible.map((k) => `
        <section class="workspace-module workspace-side-module" data-workspace-module="${k}" aria-labelledby="workspace-${k}-title">
          <div class="workspace-module-head" data-module-drag="${k}" draggable="true">
            <svg class="module-drag-grip" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-grip"/></svg>
            <span class="workspace-module-title" id="workspace-${k}-title">
              <svg viewBox="0 0 24 24" aria-hidden="true">${STAGE_VIEWS[k].ico}</svg><span class="label">${STAGE_VIEWS[k].label}</span>
            </span>
            ${k === "tasks" && bgActive ? `<span class="tab-badge" title="有后台任务在运行"></span>` : ""}
            <span class="workspace-module-meta">${k === "outline" ? "镜头结构" : k === "tasks" ? "运行状态" : "只读浏览"}</span>
            <button type="button" class="workspace-module-refresh" data-module-refresh="${k}" title="刷新${STAGE_VIEWS[k].label}" aria-label="刷新${STAGE_VIEWS[k].label}"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-refresh"/></svg></button>
            <button type="button" class="workspace-module-close" data-module-close="${k}" title="隐藏${STAGE_VIEWS[k].label}" aria-label="隐藏${STAGE_VIEWS[k].label}"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-close"/></svg></button>
          </div>
          <div class="panel-tray-body" data-panel-body="${k}"><p class="placeholder">加载中…</p></div>
          <div class="module-resize-edge module-resize-edge-x" data-module-resize="${k}" data-resize-axis="x" role="separator" tabindex="0" aria-label="调整${STAGE_VIEWS[k].label}宽度"></div>
          <div class="module-resize-edge module-resize-edge-y" data-module-resize="${k}" data-resize-axis="y" role="separator" tabindex="0" aria-label="调整${STAGE_VIEWS[k].label}高度"></div>
          <div class="module-resize-edge module-resize-corner" data-module-resize="${k}" data-resize-axis="both" role="separator" tabindex="0" aria-label="同时调整${STAGE_VIEWS[k].label}宽度和高度"></div>
        </section>`).join("");
      window.queueMicrotask(refreshVisibleModules);
    }
    stagePanel.querySelectorAll("[data-workspace-module]").forEach((module) => {
      module.classList.toggle("is-focused", module.dataset.workspaceModule === activeTab);
    });
    applyWorkspaceLayout();
  }

  function renderStageTabs() {
    if (!stageTabList) return;
    const tabHtml = (k, label, ico, closable) => `
      <button type="button" class="stage-tab is-visible${activeTab === k ? " active" : ""}" data-stage-tab="${k}" role="tab" aria-selected="${activeTab === k}"${closable ? ` draggable="true" data-tab-drag="${k}"` : ""}>
        <svg viewBox="0 0 24 24" aria-hidden="true">${ico}</svg><span>${label}</span>
        ${k === "tasks" && bgActive ? `<span class="tab-badge" title="有后台任务在运行" aria-label="有后台任务在运行"></span>` : ""}
        ${closable ? `<span class="stage-tab-x" data-stage-remove="${k}" role="button" title="移除" aria-label="移除${label}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-close"/></svg>
        </span>` : ""}
      </button>`;
    stageTabList.innerHTML =
      tabHtml("preview", "预览", PREVIEW_ICO, false)
      + orderedStageTabs().map((k) => tabHtml(k, STAGE_VIEWS[k].label, STAGE_VIEWS[k].ico, true)).join("");
    syncWorkspaceModules();
  }

  function renderStageAddMenu() {
    const avail = Object.keys(STAGE_VIEWS).filter((k) => !stageTabs.includes(k));
    stageAddMenu.innerHTML = avail.length
      ? avail.map((k) => `
          <button type="button" class="plus-item" role="menuitem" data-stage-add="${k}">
            <svg class="plus-ico" viewBox="0 0 24 24" aria-hidden="true">${STAGE_VIEWS[k].ico}</svg>
            <span class="plus-label">${STAGE_VIEWS[k].label}</span>
          </button>`).join("")
      : `<div class="stage-add-empty">已全部添加</div>`;
  }
  function openStageAdd() { renderStageAddMenu(); stageAddMenu.hidden = false; stageAddBtn.setAttribute("aria-expanded", "true"); renderStageTabs(); }
  function closeStageAdd() { stageAddMenu.hidden = true; stageAddBtn.setAttribute("aria-expanded", "false"); renderStageTabs(); }
  stageAddBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    stageAddMenu.hidden ? openStageAdd() : closeStageAdd();
  });
  document.addEventListener("click", (e) => {
    if (stageAddMenu && !stageAddMenu.hidden
        && !e.target.closest("#stage-add-menu") && !e.target.closest("#stage-add-btn")) closeStageAdd();
    if (stageOverflowMenu && !stageOverflowMenu.hidden
        && !e.target.closest("#stage-overflow-menu") && !e.target.closest("#stage-overflow-btn")) closeStageOverflow();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (stageAddMenu && !stageAddMenu.hidden) closeStageAdd();
      if (stageOverflowMenu && !stageOverflowMenu.hidden) closeStageOverflow();
    }
  });

  // ⌘/Ctrl + 1–9 jumps to the Nth stage tab (preview = 1). In a browser the OS
  // may intercept ⌘1–8 for its own tabs; inside the desktop shell it lands.
  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
    if (!/^[1-9]$/.test(e.key)) return;
    const order = ["preview", ...stageTabs];
    const idx = Number(e.key) - 1;
    if (idx >= order.length) return;
    e.preventDefault();
    setActiveTab(order[idx]);
  });

  // Overflow "⋮": secondary actions (refresh the active view) tucked off the bar.
  function renderStageOverflow() {
    const canRefresh = PANEL_MODULES.has(activeTab);
    stageOverflowMenu.innerHTML = `
      <button type="button" class="plus-item" role="menuitem" data-overflow="refresh"${canRefresh ? "" : " disabled"}>
        <svg class="plus-ico" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-refresh"/></svg>
        <span class="plus-label">刷新当前视图</span>
      </button>`;
  }
  function openStageOverflow() { renderStageOverflow(); stageOverflowMenu.hidden = false; stageOverflowBtn.setAttribute("aria-expanded", "true"); }
  function closeStageOverflow() { stageOverflowMenu.hidden = true; stageOverflowBtn.setAttribute("aria-expanded", "false"); }
  stageOverflowBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    stageOverflowMenu.hidden ? openStageOverflow() : closeStageOverflow();
  });

  stageTabsBox?.addEventListener("click", (e) => {
    const ov = e.target.closest("[data-overflow]");
    if (ov) {
      if (ov.dataset.overflow === "refresh") refreshPanel();
      closeStageOverflow();
      return;
    }
    const add = e.target.closest("[data-stage-add]");
    if (add) {
      const k = add.dataset.stageAdd;
      if (!stageTabs.includes(k)) { stageTabs.push(k); saveStageTabs(); }
      closeStageAdd();
      setActiveTab(k);
      return;
    }
    const rm = e.target.closest("[data-stage-remove]");
    if (rm) {
      e.stopPropagation();
      hideWorkspaceModule(rm.dataset.stageRemove);
      return;
    }
    const tab = e.target.closest("[data-stage-tab]");
    if (!tab) return;
    const k = tab.dataset.stageTab;
    // Re-clicking the active timeline tab toggles its drawer; other tabs are idempotent.
    if (k === "timeline" && activeTab === "timeline") { toggleDrawer(); return; }
    setActiveTab(k);
  });
  if (stageTabs.includes("timeline")) previewStage.classList.add("drawer-open");
  renderStageTabs();
  if ("ResizeObserver" in window && workspaceBoard) {
    new ResizeObserver(() => applyWorkspaceLayout()).observe(workspaceBoard);
  } else {
    window.addEventListener("resize", applyWorkspaceLayout);
  }

  // A horizontal drop means "put us side by side": cap both width weights under
  // the full-width (own-row) regime and scale the pair into one row's budget,
  // so e.g. timeline can sit next to preview instead of always owning a row.
  function ensureSideBySide(aId, bId) {
    if (!WorkspaceLayout) return;
    const rowLimit = (WorkspaceLayout.ROW_FILL_LIMIT ?? 136) - 0.5;
    const sideCap = (WorkspaceLayout.FULL_WIDTH_THRESHOLD ?? 78) - 1;
    const pair = [aId, bId].map((id) => ({ id, size: WorkspaceLayout.clampSize(id, workspaceSizes[id]) }));
    let widths = pair.map((item) => Math.min(item.size.width, sideCap));
    const sum = widths[0] + widths[1];
    if (sum > rowLimit) widths = widths.map((value) => value * rowLimit / sum);
    pair.forEach((item, index) => {
      workspaceSizes[item.id] = WorkspaceLayout.clampSize(item.id, { ...item.size, width: widths[index] });
    });
  }

  // Dragging changes only module order; justified flow then re-tiles the desk
  // edge-to-edge without disturbing module contents.
  let draggedModule = null;
  let dropTarget = null;
  let dropAfter = false;
  let dropHorizontal = false;
  const clearDropState = () => {
    workspaceBoard?.querySelectorAll(".is-drop-before, .is-drop-after").forEach((el) =>
      el.classList.remove("is-drop-before", "is-drop-after"));
    dropTarget = null;
  };
  workspaceBoard?.addEventListener("dragstart", (e) => {
    const handle = e.target.closest("[data-module-drag]");
    if (!handle) { e.preventDefault(); return; }
    draggedModule = handle.dataset.moduleDrag;
    handle.closest("[data-workspace-module]")?.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", draggedModule);
  });
  workspaceBoard?.addEventListener("dragover", (e) => {
    if (!draggedModule) return;
    const target = e.target.closest("[data-workspace-module]");
    if (!target || target.dataset.workspaceModule === draggedModule) return;
    e.preventDefault();
    clearDropState();
    dropTarget = target.dataset.workspaceModule;
    const rect = target.getBoundingClientRect();
    const dx = (e.clientX - (rect.left + rect.width / 2)) / Math.max(1, rect.width);
    const dy = (e.clientY - (rect.top + rect.height / 2)) / Math.max(1, rect.height);
    dropHorizontal = Math.abs(dx) > Math.abs(dy);
    dropAfter = dropHorizontal ? dx > 0 : dy > 0;
    target.classList.add(dropAfter ? "is-drop-after" : "is-drop-before");
  });
  workspaceBoard?.addEventListener("drop", (e) => {
    e.preventDefault();
    if (!draggedModule || !dropTarget) return;
    workspaceOrder = workspaceOrder.filter((id) => id !== draggedModule);
    const targetIndex = workspaceOrder.indexOf(dropTarget);
    workspaceOrder.splice(Math.max(0, targetIndex + (dropAfter ? 1 : 0)), 0, draggedModule);
    if (dropHorizontal) ensureSideBySide(draggedModule, dropTarget);
    stageTabs = orderedStageTabs();
    saveStageTabs();
    saveWorkspaceLayout();
    clearDropState();
    workspaceBoard.querySelectorAll(".is-dragging").forEach((el) => el.classList.remove("is-dragging"));
    draggedModule = null;
    renderStageTabs();
  });
  workspaceBoard?.addEventListener("dragend", () => {
    clearDropState();
    workspaceBoard.querySelectorAll(".is-dragging").forEach((el) => el.classList.remove("is-dragging"));
    draggedModule = null;
  });

  // The tab strip mirrors module drag: dragging a tab reorders workspaceOrder,
  // and the justified flow re-tiles the desk. Preview stays pinned first.
  let draggedTab = null;
  const clearTabDropState = () => {
    stageTabList?.querySelectorAll(".is-drop-before, .is-drop-after").forEach((el) =>
      el.classList.remove("is-drop-before", "is-drop-after"));
  };
  stageTabList?.addEventListener("dragstart", (e) => {
    const tab = e.target.closest("[data-tab-drag]");
    if (!tab) { e.preventDefault(); return; }
    draggedTab = tab.dataset.tabDrag;
    tab.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", draggedTab);
  });
  stageTabList?.addEventListener("dragover", (e) => {
    if (!draggedTab) return;
    const target = e.target.closest("[data-tab-drag]");
    if (!target || target.dataset.tabDrag === draggedTab) return;
    e.preventDefault();
    clearTabDropState();
    const rect = target.getBoundingClientRect();
    target.classList.add(e.clientX > rect.left + rect.width / 2 ? "is-drop-after" : "is-drop-before");
  });
  stageTabList?.addEventListener("drop", (e) => {
    const target = e.target.closest("[data-tab-drag]");
    if (!draggedTab || !target || target.dataset.tabDrag === draggedTab) return;
    e.preventDefault();
    const rect = target.getBoundingClientRect();
    const after = e.clientX > rect.left + rect.width / 2;
    workspaceOrder = workspaceOrder.filter((id) => id !== draggedTab);
    const targetIndex = workspaceOrder.indexOf(target.dataset.tabDrag);
    workspaceOrder.splice(Math.max(0, targetIndex + (after ? 1 : 0)), 0, draggedTab);
    stageTabs = orderedStageTabs();
    saveStageTabs();
    saveWorkspaceLayout();
    draggedTab = null;
    renderStageTabs();
  });
  stageTabList?.addEventListener("dragend", () => {
    clearTabDropState();
    stageTabList.querySelectorAll(".is-dragging").forEach((el) => el.classList.remove("is-dragging"));
    draggedTab = null;
  });

  // Edges resize in continuous percentages. The justified flow immediately
  // gives the released space to neighbours, keeping the desk fully tiled.
  let resizeState = null;
  workspaceBoard?.addEventListener("pointerdown", (e) => {
    const edge = e.target.closest("[data-module-resize]");
    if (!edge || !WorkspaceLayout) return;
    e.preventDefault();
    e.stopPropagation();
    edge.focus({ preventScroll: true });
    const id = edge.dataset.moduleResize;
    const size = WorkspaceLayout.clampSize(id, workspaceSizes[id]);
    resizeState = {
      id, axis: edge.dataset.resizeAxis, startX: e.clientX, startY: e.clientY, size,
      boardWidth: Math.max(1, workspaceBoard.clientWidth - 16),
      boardHeight: Math.max(1, workspaceBoard.clientHeight - 16),
    };
    workspaceBoard.classList.add("is-resizing");
    edge.closest("[data-workspace-module]")?.classList.add("is-resizing");
    edge.setPointerCapture?.(e.pointerId);
  });
  document.addEventListener("pointermove", (e) => {
    if (!resizeState || !WorkspaceLayout) return;
    const next = { ...resizeState.size };
    if (resizeState.axis === "x" || resizeState.axis === "both") {
      next.width += (e.clientX - resizeState.startX) / resizeState.boardWidth * 100;
    }
    if (resizeState.axis === "y" || resizeState.axis === "both") {
      next.height += (e.clientY - resizeState.startY) / resizeState.boardHeight * 100;
    }
    workspaceSizes[resizeState.id] = WorkspaceLayout.clampSize(resizeState.id, next);
    applyWorkspaceLayout();
  });
  document.addEventListener("pointerup", () => {
    if (!resizeState) return;
    workspaceBoard?.querySelector(`[data-workspace-module="${resizeState.id}"]`)?.classList.remove("is-resizing");
    workspaceBoard?.classList.remove("is-resizing");
    resizeState = null;
    saveWorkspaceLayout();
  });
  workspaceBoard?.addEventListener("keydown", (e) => {
    const edge = e.target.closest("[data-module-resize]");
    if (!edge || !WorkspaceLayout || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
    const axis = edge.dataset.resizeAxis;
    const horizontalKey = ["ArrowLeft", "ArrowRight"].includes(e.key);
    if ((horizontalKey && axis === "y") || (!horizontalKey && axis === "x")) return;
    e.preventDefault();
    const id = edge.dataset.moduleResize;
    const size = WorkspaceLayout.clampSize(id, workspaceSizes[id]);
    const step = e.shiftKey ? 5 : 2;
    if (horizontalKey) size.width += e.key === "ArrowRight" ? step : -step;
    else size.height += e.key === "ArrowDown" ? step : -step;
    workspaceSizes[id] = WorkspaceLayout.clampSize(id, size);
    saveWorkspaceLayout();
    applyWorkspaceLayout();
  });
  workspaceBoard?.addEventListener("click", (e) => {
    const close = e.target.closest("[data-module-close]");
    if (close) { e.stopPropagation(); hideWorkspaceModule(close.dataset.moduleClose); return; }
    const refresh = e.target.closest("[data-module-refresh]");
    if (refresh) { e.stopPropagation(); refreshPanel(refresh.dataset.moduleRefresh); return; }
    const module = e.target.closest("[data-workspace-module]");
    if (module) setActiveTab(module.dataset.workspaceModule);
  });

  // Live badge on the 后台任务 tab: a slow, visibility-gated /sessions poll
  // flips bgActive when any session is mid-turn or has pending generation jobs.
  async function pollBgTasks() {
    if (document.visibilityState === "hidden") return;
    let active = false;
    try {
      const r = await fetch("/sessions");
      if (r.ok) {
        const sessions = (await r.json()).sessions || [];
        active = sessions.some((s) => s.turn_in_progress || (s.pending_jobs || []).length > 0);
      }
    } catch {}
    if (active !== bgActive) { bgActive = active; renderStageTabs(); }
  }
  pollBgTasks();
  window.setInterval(pollBgTasks, 12000);

  const fmtBytes = (n) => {
    if (!Number.isFinite(n)) return "";
    if (n >= 1 << 30) return (n / (1 << 30)).toFixed(1) + " GB";
    if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
    if (n >= 1024) return Math.round(n / 1024) + " KB";
    return n + " B";
  };
  const fmtAgo = (epoch) => {
    if (!epoch) return "";
    const s = Math.max(0, (Date.now() / 1000) - epoch);
    if (s < 60) return "刚刚";
    if (s < 3600) return Math.floor(s / 60) + " 分钟前";
    if (s < 86400) return Math.floor(s / 3600) + " 小时前";
    return Math.floor(s / 86400) + " 天前";
  };

  // ── outline panel: the shotlist riding /timeline ──────────────────────
  const SHOT_STATUS = { draft: ["草稿", ""], filled: ["已配素材", "filled"], placed: ["已上时间线", "placed"] };
  async function renderOutlinePanel(body) {
    if (!body) return;
    if (!state.sessionId) { body.innerHTML = `<p class="placeholder">暂无会话</p>`; return; }
    let sl = null;
    try {
      const r = await fetch(`/sessions/${state.sessionId}/timeline`);
      if (r.ok) sl = (await r.json()).shotlist;
    } catch {}
    if (!body.isConnected || !stageTabs.includes("outline")) return;
    const scenes = (sl && Array.isArray(sl.scenes)) ? sl.scenes : [];
    const shotCount = scenes.reduce((n, sc) => n + ((sc.shots || []).length), 0);
    if (!shotCount) { body.innerHTML = `<p class="placeholder">暂无大纲 — 让 Lumeri 起草分镜后在这里查看</p>`; return; }
    let html = "";
    if (sl.logline) html += `<p class="outline-logline">${escapeHTML(sl.logline)}</p>`;
    let no = 0;
    for (const sc of scenes) {
      if (sc.title) html += `<div class="outline-scene" data-scene-id="${escapeHTML(sc.id)}">${escapeHTML(sc.title)}</div>`;
      for (const shot of (sc.shots || [])) {
        no += 1;
        const st = SHOT_STATUS[shot.status] || SHOT_STATUS.draft;
        const meta = [
          `${Number(shot.duration_sec || 0).toFixed(1)}s`,
          shot.narration ? `旁白：${shot.narration}` : "",
          shot.on_screen_text ? `字幕：${shot.on_screen_text}` : "",
          shot.mood || "",
        ].filter(Boolean).join(" · ");
        html += `
          <div class="outline-row" data-shot-id="${escapeHTML(shot.id)}">
            <span class="outline-no ${st[1]}" title="${st[0]}">${no}</span>
            <span class="outline-main">
              <span class="outline-beat">${escapeHTML(shot.description || "(未命名镜头)")}</span>
              ${meta ? `<span class="outline-meta">${escapeHTML(meta)}</span>` : ""}
            </span>
          </div>`;
      }
    }
    body.innerHTML = html;
  }

  // ── background tasks panel: GET /sessions (runners + pending jobs) ────
  async function renderTasksPanel(body) {
    if (!body) return;
    let sessions = null;
    try {
      const r = await fetch("/sessions");
      if (r.ok) sessions = (await r.json()).sessions;
    } catch {}
    if (!body.isConnected || !stageTabs.includes("tasks")) return;
    if (!Array.isArray(sessions)) { body.innerHTML = `<p class="placeholder">读取失败</p>`; return; }
    if (!sessions.length) { body.innerHTML = `<p class="placeholder">暂无运行中的会话</p>`; return; }
    body.innerHTML = sessions.map((s) => {
      const mine = s.session_id === state.sessionId;
      const cls = s.turn_in_progress ? "running" : "";
      const stateTxt = s.turn_in_progress ? "执行中" : "空闲";
      const jobs = (s.pending_jobs || []).map((j) => `
        <div class="task-row task-job">
          <span class="task-dot ${j.last_polled_status === "failed" ? "failed" : "running"}"></span>
          <span class="task-main">
            <span class="task-name">${escapeHTML(j.summary || j.kind || j.job_id || "任务")}</span>
            <span class="task-sub">${escapeHTML([j.provider, j.last_polled_status].filter(Boolean).join(" · "))}</span>
          </span>
        </div>`).join("");
      return `
        <div class="task-row" title="${escapeHTML(s.session_id)}">
          <span class="task-dot ${cls}"></span>
          <span class="task-main">
            <span class="task-name">${escapeHTML(s.session_id)}${mine ? " · 当前" : ""}</span>
            <span class="task-sub">${stateTxt}${s.plan_mode ? " · 计划模式" : ""} · ${fmtAgo(s.last_used_at)}</span>
          </span>
        </div>${jobs}`;
    }).join("");
  }

  // ── session history drawer (right-side hamburger) ─────────────────────
  let historyDrawerOpen = false;
  function toggleHistoryDrawer(force) {
    const open = force ?? !historyDrawerOpen;
    if (open === historyDrawerOpen) return;
    historyDrawerOpen = open;
    els.historyToggleBtn.setAttribute("aria-expanded", String(open));
    if (open) {
      els.historyDrawer.hidden = false;
      els.historyDrawer.classList.remove("closing");
      renderHistoryDrawer();
    } else {
      els.historyDrawer.classList.add("closing");
      els.historyDrawer.addEventListener("animationend", () => {
        if (!historyDrawerOpen) {
          els.historyDrawer.hidden = true;
          els.historyDrawer.classList.remove("closing");
        }
      }, { once: true });
    }
  }
  els.historyToggleBtn.addEventListener("click", () => toggleHistoryDrawer());

  async function renderHistoryDrawer() {
    const body = els.historyDrawerBody;
    if (!body) return;
    body.innerHTML = `<p class="placeholder">加载中…</p>`;
    let sessions = null;
    try {
      const r = await fetch("/session-history/list?limit=50");
      if (r.ok) sessions = (await r.json()).sessions;
    } catch {}
    if (!historyDrawerOpen) return;
    if (!Array.isArray(sessions)) { body.innerHTML = `<p class="placeholder">读取失败</p>`; return; }
    if (!sessions.length) { body.innerHTML = `<p class="placeholder">暂无历史会话</p>`; return; }
    body.innerHTML = sessions.map((s) => {
      const title = s.title || "Lumeri Session";
      const time = s.updated_at ? fmtAgo(new Date(s.updated_at).getTime() / 1000) : "";
      const msgs = s.message_count || 0;
      return `
        <button type="button" class="history-row" data-snapshot-id="${escapeHTML(s.id)}">
          <span class="task-main">
            <span class="task-name">${escapeHTML(title)}</span>
            <span class="task-sub">${msgs} 条消息${time ? " · " + time : ""}</span>
          </span>
        </button>`;
    }).join("");

    body.querySelectorAll(".history-row").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.snapshotId;
        if (!id) return;
        loadHistorySession(id);
        toggleHistoryDrawer(false);
      });
    });
  }

  async function loadHistorySession(snapshotId) {
    try {
      const r = await fetch(`/session-history/${encodeURIComponent(snapshotId)}`);
      if (!r.ok) return;
      const session = await r.json();
      state.turns = [];
      state.currentTurn = null;
      state.sessionTitle = session.title || null;
      state.userMessageCount = 0;
      els.sessionLabel.textContent = state.sessionTitle || state.sessionId || "—";
      const msgs = session.messages || [];
      let currentTurn = null;
      for (const msg of msgs) {
        if (msg.role === "user") {
          currentTurn = newTurn(msg.content || "");
          state.turns.push(currentTurn);
          state.userMessageCount++;
        } else if (msg.role === "status" && msg.statusType === "guidance" && currentTurn) {
          currentTurn.guidance.push(msg.content || "");
        } else if (msg.role === "status" && currentTurn) {
          currentTurn.assistantText = msg.content || "";
          currentTurn.complete = true;
        }
      }
      render();
    } catch {}
  }

  // ── files panel: whitelisted read-only browser (/files/*) ────────────
  let filesState = null;   // null = root picker; else {root, session, path}
  const FILE_ICON = (name) => {
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (["mp4", "mov", "webm", "mkv", "avi"].includes(ext)) return "i-film";
    if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(ext)) return "i-image";
    if (["mp3", "wav", "m4a", "aac", "flac", "ogg"].includes(ext)) return "i-music";
    return "i-file";
  };
  async function renderFilesPanel(body) {
    if (!body) return;
    if (!filesState) {
      let roots = [];
      try {
        const r = await fetch("/files/roots");
        if (r.ok) roots = (await r.json()).roots || [];
      } catch {}
      if (!body.isConnected || !stageTabs.includes("files")) return;
      let html = "";
      if (state.sessionId) {
        html += `<button type="button" class="file-row" data-file-root="session">
          <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-folder"/></svg>
          <span class="file-name">当前会话工作区</span></button>`;
      }
      html += roots.map((rt) => `
        <button type="button" class="file-row" data-file-root="${escapeHTML(rt.key)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-folder"/></svg>
          <span class="file-name">${escapeHTML(rt.label)}</span></button>`).join("");
      body.innerHTML = html || `<p class="placeholder">暂无可浏览目录</p>`;
      return;
    }
    const { root, session, path } = filesState;
    const qs = `root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}${session ? `&session=${encodeURIComponent(session)}` : ""}`;
    let data = null;
    try {
      const r = await fetch(`/files/list?${qs}`);
      if (r.ok) data = await r.json();
    } catch {}
    if (!body.isConnected || !stageTabs.includes("files")) return;
    if (!data) { body.innerHTML = `<p class="placeholder">读取失败</p>`; return; }
    const segs = path ? path.split("/") : [];
    const crumbs = [`<button type="button" data-file-crumb="">${escapeHTML(root === "session" ? "工作区" : root)}</button>`]
      .concat(segs.map((seg, i) =>
        `<span>/</span><button type="button" data-file-crumb="${escapeHTML(segs.slice(0, i + 1).join("/"))}">${escapeHTML(seg)}</button>`))
      .join("");
    const rows = (data.entries || []).map((en) => {
      const child = path ? `${path}/${en.name}` : en.name;
      return en.is_dir
        ? `<button type="button" class="file-row" data-file-dir="${escapeHTML(child)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-folder"/></svg>
            <span class="file-name">${escapeHTML(en.name)}</span></button>`
        : `<button type="button" class="file-row" data-file-open="${escapeHTML(child)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#${FILE_ICON(en.name)}"/></svg>
            <span class="file-name">${escapeHTML(en.name)}</span>
            <span class="file-size">${fmtBytes(en.size)}</span></button>`;
    }).join("");
    body.innerHTML = `
      <div class="files-crumbs"><button type="button" data-file-crumb="__roots__" title="所有目录"><svg viewBox="0 0 24 24" aria-hidden="true" style="width:12px;height:12px"><use href="#i-chevron-l"/></svg></button>${crumbs}</div>
      ${rows || `<p class="placeholder">空目录</p>`}
      ${data.truncated ? `<p class="placeholder">（仅显示前 500 项）</p>` : ""}`;
  }
  stagePanel?.addEventListener("click", (e) => {
    const rootBtn = e.target.closest("[data-file-root]");
    if (rootBtn) {
      const key = rootBtn.dataset.fileRoot;
      filesState = { root: key, session: key === "session" ? state.sessionId : "", path: "" };
      refreshPanel("files");
      return;
    }
    const crumb = e.target.closest("[data-file-crumb]");
    if (crumb) {
      if (crumb.dataset.fileCrumb === "__roots__") filesState = null;
      else filesState = { ...filesState, path: crumb.dataset.fileCrumb };
      refreshPanel("files");
      return;
    }
    const dir = e.target.closest("[data-file-dir]");
    if (dir) { filesState = { ...filesState, path: dir.dataset.fileDir }; refreshPanel("files"); return; }
    const file = e.target.closest("[data-file-open]");
    if (file) {
      const { root, session } = filesState;
      const qs = `root=${encodeURIComponent(root)}&path=${encodeURIComponent(file.dataset.fileOpen)}${session ? `&session=${encodeURIComponent(session)}` : ""}`;
      window.open(`/files/get?${qs}`, "_blank", "noopener");
    }
  });

  // Visible live modules refresh together; files/history remain stable while
  // the user is reading or navigating them.
  window.setInterval(() => {
    if (document.visibilityState === "hidden") return;
    if (stageTabs.includes("outline")) refreshPanel("outline");
    if (stageTabs.includes("tasks")) refreshPanel("tasks");
  }, 5000);

  // First-run discovery pulse on "+" (controls are hidden behind it now).
  try {
    if (!window.localStorage.getItem("lumeri:v3:plus-seen")) {
      plusBtn.classList.add("pulse");
      window.localStorage.setItem("lumeri:v3:plus-seen", "1");
    }
  } catch {}

  // ── timeline quick-action buttons ──────────────────────────────────
  // Any .pt-action-btn with a data-cmd attribute pre-fills the prompt and sends.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".pt-action-btn[data-cmd]");
    if (!btn || !state.sessionId || state.turnInProgress) return;
    const cmd = btn.dataset.cmd;
    if (!cmd) return;
    els.promptInput.value = cmd;
    els.sendBtn.click();
  });

  // ── plan mode ───────────────────────────────────────────────────────
  // The backend answers with the authoritative state AND broadcasts a
  // plan_mode_changed SSE event, so other connected clients (e.g. the CLI on
  // the same session) stay in sync.
  async function setPlanMode(enabled) {
    if (!state.sessionId) return;
    const r = await fetch(`/sessions/${state.sessionId}/plan_mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!r.ok) throw new Error(`plan_mode toggle failed: ${r.status}`);
    const data = await r.json();
    state.planMode = !!data.plan_mode;
    if (!state.planMode) state.planReady = false;
    render();
  }

  els.planBtn?.addEventListener("click", () => {
    setPlanMode(!state.planMode).catch((err) => {
      state.errors.push(err.message);
      render();
    });
  });

  const PLAN_APPROVE_MESSAGE = "计划已批准，请立即按计划执行。(Plan approved — execute it now.)";

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-plan-approve]")) {
      if (state.turnInProgress) return;
      setPlanMode(false)
        .then(() => submitTurn(PLAN_APPROVE_MESSAGE))
        .catch((err) => {
          state.errors.push(`approve plan failed: ${err.message}`);
          render();
        });
      return;
    }
    if (e.target.closest("[data-plan-dismiss]")) {
      state.planReady = false;
      render();
    }
  });

  // ── sandbox toggle ──────────────────────────────────────────────────
  function renderSandbox(disabled) {
    els.sandboxBtn.classList.toggle("off", disabled);
    els.sandboxBtn.textContent = disabled ? "沙盒关闭" : "沙盒";
    els.sandboxBtn.title = disabled ? "沙盒已关闭，代码可访问完整系统（点击重新开启）" : "沙盒已开启（点击关闭）";
  }
  async function syncSandbox() {
    try {
      const r = await fetch("/settings/sandbox");
      if (r.ok) renderSandbox(!!(await r.json()).sandbox_disabled);
    } catch {}
  }
  els.sandboxBtn.addEventListener("click", async () => {
    const next = !els.sandboxBtn.classList.contains("off");
    try {
      const r = await fetch("/settings/sandbox", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disabled: next }),
      });
      if (r.ok) renderSandbox(!!(await r.json()).sandbox_disabled);
    } catch (err) {
      state.errors.push(`sandbox toggle failed: ${err.message}`);
      render();
    }
  });
  syncSandbox();
  setupTimelineDirectEdit();

  // ── account / login (Google + email one-time-code) ──────────────────
  function setupAuth() {
    const modal = $("#auth-modal");
    const accountBtn = $("#account-btn");
    if (!modal || !accountBtn) return;
    const viewSignin = $("#auth-view-signin");
    const viewAccount = $("#auth-view-account");
    const googleBtn = $("#auth-google-btn");
    const divider = $("#auth-divider");
    const emailForm = $("#auth-email-form");
    const codeForm = $("#auth-code-form");
    const emailInput = $("#auth-email");
    const codeInput = $("#auth-code");
    const sendBtn = $("#auth-send-code");
    const verifyBtn = $("#auth-verify");
    const resendBtn = $("#auth-resend");
    const changeBtn = $("#auth-change-email");
    const codeTarget = $("#auth-code-target");
    const errBox = $("#auth-error");
    const logoutBtn = $("#auth-logout");
    const acctEmail = $("#auth-account-email");
    const avatar = $("#auth-avatar");

    let session = null;
    let pendingEmail = "";
    let resendTimer = null;

    const showErr = (msg) => { errBox.textContent = msg || ""; errBox.hidden = !msg; };
    const clearErr = () => showErr("");

    // Signed in = Google photo when present, else round initial badge;
    // signed out = person icon. Email lives in title.
    function applySession(data) {
      session = data || {};
      const acct = session.account;
      if (acct && acct.email) {
        if (acct.picture) {
          accountBtn.innerHTML = "";
          const img = document.createElement("img");
          img.className = "account-photo";
          img.alt = "";
          img.referrerPolicy = "no-referrer";
          img.src = acct.picture;
          img.onerror = () => { accountBtn.textContent = acct.email.trim().charAt(0).toUpperCase(); };
          accountBtn.appendChild(img);
        } else {
          accountBtn.textContent = acct.email.trim().charAt(0).toUpperCase();
        }
        accountBtn.title = acct.email;
        accountBtn.setAttribute("aria-label", `账户：${acct.email}`);
        accountBtn.classList.add("signed-in");
      } else {
        accountBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-user"/></svg>';
        accountBtn.title = "登录 / 账户";
        accountBtn.setAttribute("aria-label", "登录 / 账户");
        accountBtn.classList.remove("signed-in");
      }
    }

    async function refreshSession() {
      try {
        const r = await fetch("/auth/session");
        if (r.ok) applySession(await r.json());
      } catch {}
      return session;
    }

    async function postAuth(url, body) {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      let data = {};
      try { data = await r.json(); } catch {}
      if (!r.ok) throw new Error(data.user_message || data.error || `请求失败 (${r.status})`);
      return data;
    }

    function stopResend() { if (resendTimer) { clearInterval(resendTimer); resendTimer = null; } }
    function startResend(secs) {
      stopResend();
      let left = secs;
      const tick = () => {
        resendBtn.disabled = left > 0;
        resendBtn.textContent = left > 0 ? `重新发送（${left}s）` : "重新发送";
        if (left <= 0) { stopResend(); return; }
        left -= 1;
      };
      tick();
      resendTimer = setInterval(tick, 1000);
    }

    function showEmailStep() { emailForm.hidden = false; codeForm.hidden = true; stopResend(); }
    function showCodeStep(email) {
      pendingEmail = email;
      codeTarget.textContent = email;
      emailForm.hidden = true;
      codeForm.hidden = false;
      codeInput.value = "";
      startResend(60);
      codeInput.focus();
    }

    function renderModal() {
      clearErr();
      const acct = session && session.account;
      viewAccount.hidden = !acct;
      viewSignin.hidden = !!acct;
      if (acct) {
        acctEmail.textContent = acct.email || acct.name || "已登录";
        if (acct.picture) {
          avatar.classList.add("has-photo");
          avatar.innerHTML = "";
          const img = document.createElement("img");
          img.className = "account-photo";
          img.alt = "";
          img.referrerPolicy = "no-referrer";
          img.src = acct.picture;
          img.onerror = () => {
            avatar.classList.remove("has-photo");
            avatar.textContent = (acct.email || acct.name || "?").trim().charAt(0).toUpperCase();
          };
          avatar.appendChild(img);
        } else {
          avatar.classList.remove("has-photo");
          avatar.textContent = (acct.email || acct.name || "?").trim().charAt(0).toUpperCase();
        }
        return;
      }
      const hasGoogle = !!(session && session.has_google_client_id);
      googleBtn.hidden = !hasGoogle;
      divider.hidden = !hasGoogle;
      showEmailStep();
    }

    function openModal() {
      renderModal();
      modal.hidden = false;
      if (!(session && session.account)) emailInput.focus();
    }
    function closeModal() { modal.hidden = true; stopResend(); clearErr(); }

    async function requestCode(email) {
      clearErr();
      sendBtn.disabled = true; sendBtn.textContent = "发送中…";
      try {
        await postAuth("/auth/email/start", { email });
        showCodeStep(email);
      } catch (e) {
        showErr(e.message);
      } finally {
        sendBtn.disabled = false; sendBtn.textContent = "发送验证码";
      }
    }

    accountBtn.addEventListener("click", () => { modal.hidden ? openModal() : closeModal(); });
    modal.querySelectorAll("[data-auth-close]").forEach((el) => el.addEventListener("click", closeModal));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.hidden) closeModal(); });

    googleBtn.addEventListener("click", async () => {
      clearErr();
      try {
        const data = await postAuth("/auth/google/start", {});
        if (!data.authorization_url) throw new Error("Google 登录未配置");
        const win = window.open(data.authorization_url, "lumeri-google-login", "width=480,height=640");
        const onMsg = async (ev) => {
          if (ev.origin !== location.origin) return;
          if (!ev.data || ev.data.type !== "lumeri-auth-complete") return;
          window.removeEventListener("message", onMsg);
          try { win && win.close(); } catch {}
          await refreshSession();
          if (session && session.account) { renderModal(); setTimeout(closeModal, 600); }
          else showErr("Google 登录未完成");
        };
        window.addEventListener("message", onMsg);
      } catch (e) { showErr(e.message); }
    });

    emailForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = emailInput.value.trim();
      if (v) requestCode(v);
    });
    resendBtn.addEventListener("click", () => { if (pendingEmail) requestCode(pendingEmail); });
    changeBtn.addEventListener("click", () => { showEmailStep(); clearErr(); emailInput.focus(); });

    codeForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearErr();
      const code = codeInput.value.replace(/\D/g, "");
      if (code.length !== 6) { showErr("请输入 6 位数字验证码"); return; }
      verifyBtn.disabled = true; verifyBtn.textContent = "登录中…";
      try {
        const data = await postAuth("/auth/email/verify", { email: pendingEmail, code });
        applySession(data);
        renderModal();
        setTimeout(closeModal, 500);
      } catch (e2) {
        showErr(e2.message);
      } finally {
        verifyBtn.disabled = false; verifyBtn.textContent = "登录";
      }
    });

    logoutBtn.addEventListener("click", async () => {
      try { applySession(await postAuth("/auth/logout", {})); } catch {}
      renderModal();
    });

    refreshSession().then(() => {
      const params = new URLSearchParams(location.search || "");
      if (params.get("login") === "1" || params.get("auth") === "1") openModal();
    });
  }
  setupAuth();

  // boot
  createSession().catch((err) => {
    state.errors.push(`initial session failed: ${err.message}`);
    setConnPill("failed", "failed");
    render();
  });

  // teardown on page hide
  window.addEventListener("beforeunload", () => {
    stopTimelinePoll();
    if (voiceInput.listening) {
      try { voiceInput.recognition?.abort(); } catch {}
    }
    if (state.sessionId) {
      navigator.sendBeacon?.(`/sessions/${state.sessionId}/close`);
    }
  });
})();
