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
 *   - tool_exec_progress shows real percent when present, indeterminate
 *     spinner when omitted. We never fabricate progress.
 *   - All asset previews load from /sessions/{id}/assets/{aid}. Tool
 *     results expose asset_id/kind/asset_url, never local filesystem paths.
 */

(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

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
    sendBtn: $("#send-btn"),
    sandboxBtn: $("#sandbox-toggle-btn"),
    planBtn: $("#plan-toggle-btn"),
    planBar: $("#plan-bar"),
    tasksChip: $("#tasks-chip"),
    tasksBar: $("#tasks-bar"),
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
    /** @type {Map<string, {job_id:string,status:string,summary:string,exit_code:number|null,elapsed_sec:number|null,output_tail:string}>} */
    backgroundTasks: new Map(), // job_id → background shell task (run_in_background run_shell)
    _tasksBarOpen: false,       // header chip expands the per-task list + kill buttons
  };

  function newTurn(userMessage) {
    return {
      userMessage,
      assistantText: "",
      streaming: false,
      toolCalls: new Map(),     // call_id -> ToolCallState
      orderedCallIds: [],
      banners: [],              // { kind: "budget"|"turn_error"|"unknown", text, sub? }
      complete: false,
    };
  }

  // ── render ──────────────────────────────────────────────────────────

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
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
    els.sessionLabel.textContent = state.sessionId || "—";
    const busy = !state.sessionId || state.turnInProgress;
    els.sendBtn.disabled = busy;
    els.uploadBtn.disabled = busy;
    document.querySelectorAll(".pt-action-btn, .pt-edit-btn").forEach((b) => { b.disabled = busy; });
    updateEditHint();   // selection-aware split/delete rule wins over the blanket disable above

    if (!state.turns.length) {
      els.timeline.hidden = true;
      els.emptyState.hidden = false;
    } else {
      els.emptyState.hidden = true;
      els.timeline.hidden = false;
      els.timeline.innerHTML = state.turns.map((turn, idx) => renderTurn(turn, idx)).join("");
    }

    renderAssets();
    renderMediaLibrary();
    renderPlanUi();
    renderBackgroundTasks();
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
        <span class="plan-bar-text">计划已就绪 — 批准后 Lumeri 将开始执行改动。</span>
        <span class="plan-bar-actions">
          <button type="button" class="plan-approve" data-plan-approve>批准并执行</button>
          <button type="button" class="plan-refine" data-plan-dismiss>继续规划</button>
        </span>
      `;
    } else {
      els.planBar.innerHTML = `
        <span class="plan-bar-text">计划模式已开启 — Lumeri 只查看和规划，等你批准后才会改动项目。</span>
      `;
    }
  }

  const _bgRunning = (s) => s === "running" || s === "submitted" || s === "queued";

  // Header chip (running count / terminal indicator) + collapsible per-task
  // list with kill buttons. Signature-guarded like renderPlanUi: render() runs
  // on every SSE event, and rebuilding the bar's innerHTML on each one would
  // drop the kill button's focus and restart its CSS.
  function renderBackgroundTasks() {
    if (!els.tasksChip || !els.tasksBar) return;
    const tasks = [...state.backgroundTasks.values()];
    const running = tasks.filter((t) => _bgRunning(t.status));
    const failed = tasks.filter((t) => t.status === "failed");
    const sig = tasks
      .map((t) => `${t.job_id}:${t.status}:${t.exit_code}:${t._killing ? 1 : 0}`)
      .join("|") + `|open=${state._tasksBarOpen}`;
    if (sig === state._tasksSig) return;
    state._tasksSig = sig;

    if (!tasks.length) {
      els.tasksChip.hidden = true;
      els.tasksBar.hidden = true;
      els.tasksBar.innerHTML = "";
      return;
    }

    els.tasksChip.hidden = false;
    els.tasksChip.textContent = running.length
      ? `后台任务 ×${running.length}`
      : (failed.length ? `后台任务 ✗${failed.length}` : "后台任务 ✓");
    els.tasksChip.classList.toggle("running", running.length > 0);
    els.tasksChip.classList.toggle("has-failed", running.length === 0 && failed.length > 0);
    els.tasksChip.title = running.length
      ? `${running.length} 个后台命令运行中（点击展开）`
      : "后台命令已全部结束（点击展开）";

    if (!state._tasksBarOpen) {
      els.tasksBar.hidden = true;
      els.tasksBar.innerHTML = "";
      return;
    }
    els.tasksBar.hidden = false;
    els.tasksBar.innerHTML = tasks.map((t) => {
      const isRunning = _bgRunning(t.status);
      const elapsed = typeof t.elapsed_sec === "number" ? `${t.elapsed_sec.toFixed(0)}s` : "";
      let statusLabel;
      if (isRunning) statusLabel = "运行中";
      else if (t.status === "done") statusLabel = `完成 (退出码 ${t.exit_code ?? 0})`;
      else statusLabel = "已停止/失败";
      const killBtn = isRunning
        ? (t._killing
            ? `<span class="task-killing">停止中…</span>`
            : `<button type="button" class="task-kill" data-task-kill="${escapeHTML(t.job_id)}">停止</button>`)
        : "";
      return `
        <div class="task-row ${isRunning ? "running" : escapeHTML(t.status)}">
          <span class="task-id">${escapeHTML(t.job_id)}</span>
          <span class="task-summary" title="${escapeHTML(t.summary || "")}">${escapeHTML(t.summary || "")}</span>
          <span class="task-status">${statusLabel}${elapsed ? " · " + elapsed : ""}</span>
          ${killBtn}
        </div>`;
    }).join("");
  }

  function renderTurn(turn, idx) {
    const callsHtml = buildCallGroups(turn).map(renderCallGroup).join("");
    const bannersHtml = turn.banners.map(renderBanner).join("");
    const assistantHtml = (turn.assistantText || turn.streaming)
      ? `<div class="assistant-bubble${turn.streaming ? " streaming" : ""}">${escapeHTML(turn.assistantText)}</div>`
      : "";
    return `
      <div class="turn-divider">turn ${idx + 1}</div>
      <div class="user-bubble">${escapeHTML(turn.userMessage)}</div>
      ${callsHtml}
      ${bannersHtml}
      ${assistantHtml}
    `;
  }

  // A failed call whose recovery is one of these can be "fixed" by a later
  // call — so a success that follows it closes a self-correction arc.
  const RECOVERABLE_RECOVERY = new Set(["fix_args", "switch_tool", "transient_retry"]);

  /** Group a turn's tool calls so a run of recoverable failures followed by a
   *  success renders as one "self-corrected" arc, not scattered cards. */
  function buildCallGroups(turn) {
    const groups = [];
    let openFailures = [];
    const flushSingles = () => {
      for (const f of openFailures) groups.push({ type: "single", tc: f });
      openFailures = [];
    };
    for (const cid of turn.orderedCallIds) {
      const tc = turn.toolCalls.get(cid);
      if (!tc) continue;
      if (tc.status === "failed" && RECOVERABLE_RECOVERY.has(tc.recovery)) {
        openFailures.push(tc);
      } else if (tc.status === "done" && openFailures.length) {
        groups.push({ type: "arc", calls: [...openFailures, tc] });
        openFailures = [];
      } else {
        flushSingles();
        groups.push({ type: "single", tc });
      }
    }
    flushSingles();  // trailing unresolved failures render on their own
    return groups;
  }

  function renderCallGroup(group) {
    if (group.type === "single") return renderToolCall(group.tc);
    return `
      <div class="self-correct-arc">
        <div class="self-correct-badge">⟳ self-corrected</div>
        ${group.calls.map(renderToolCall).join("")}
      </div>
    `;
  }

  function renderTypedError(tc) {
    if (!tc.error) return "";
    const codeChip = tc.errorCode
      ? `<span class="err-chip err-code">${escapeHTML(tc.errorCode)}</span>` : "";
    const recoveryChip = tc.recovery
      ? `<span class="err-chip err-recovery">${escapeHTML(tc.recovery)}</span>` : "";
    const optsHtml = (tc.validOptions && tc.validOptions.length)
      ? `<div class="err-options">options: ${tc.validOptions.map((o) => `<code>${escapeHTML(o)}</code>`).join(" ")}</div>`
      : "";
    const hintHtml = tc.hint
      ? `<div class="err-hint">${escapeHTML(tc.hint)}</div>` : "";
    return `
      <div class="tool-error">
        <div class="err-head">${codeChip}${recoveryChip}<span class="err-msg">${escapeHTML(tc.error)}</span></div>
        ${optsHtml}
        ${hintHtml}
      </div>
    `;
  }

  function renderToolCall(tc) {
    const reasoningHtml = tc.reasoning
      ? `<div class="tool-reasoning">${escapeHTML(tc.reasoning)}</div>`
      : "";
    const argsHtml = tc.args
      ? `<div class="tool-args">${escapeHTML(JSON.stringify(tc.args, null, 2))}</div>`
      : "";
    const summaryHtml = tc.summary
      ? `<div class="tool-summary">${escapeHTML(tc.summary)}</div>`
      : "";
    const errorHtml = renderTypedError(tc);
    const progressHtml = renderProgress(tc);
    const previewHtml = tc.previewAssetId && state.sessionId
      ? `<a class="tool-preview-link" href="/sessions/${state.sessionId}/assets/${tc.previewAssetId}" target="_blank" rel="noopener">open ${tc.previewAssetId} ↗</a>`
      : "";
    const childrenHtml = renderSubagents(tc);
    return `
      ${reasoningHtml}
      <div class="tool-card${tc.status === "failed" ? " failed" : ""}">
        <div class="tool-card-head">
          <span class="tool-name">${escapeHTML(tc.tool_name)}</span>
          <span class="tool-status ${tc.status}">${tc.status}</span>
        </div>
        ${argsHtml}
        ${progressHtml}
        ${summaryHtml}
        ${previewHtml}
        ${errorHtml}
        ${childrenHtml}
      </div>
    `;
  }

  // Render a spawn_subtasks call's children indented beneath it. Each child is a
  // group with a status chip + summary; the child's own tool calls render as
  // compact indented lines within the group.
  function renderSubagents(tc) {
    if (!tc.children || !tc.childOrder || !tc.childOrder.length) return "";
    const groups = tc.childOrder.map((agentId) => {
      const child = tc.children.get(agentId);
      if (!child) return "";
      const callsHtml = (child.callOrder || []).map((k) => {
        const c = child.calls.get(k);
        if (!c) return "";
        const detail = c.status === "done"
          ? escapeHTML(c.summary || "done")
          : c.status === "failed"
            ? escapeHTML(c.error || "failed")
            : escapeHTML(c.progress?.message || c.status);
        return `<div class="subagent-call"><span class="subagent-branch">├─</span> <span class="tool-name">${escapeHTML(c.tool_name)}</span> <span class="tool-status ${c.status}">${c.status}</span> <span class="subagent-call-detail">${detail}</span></div>`;
      }).join("");
      const metaBits = [];
      if (typeof child.steps === "number") metaBits.push(`${child.steps} steps`);
      if (typeof child.spentUsd === "number") metaBits.push(`$${child.spentUsd}`);
      if (typeof child.spentSeconds === "number") metaBits.push(`${child.spentSeconds}s`);
      const metaHtml = metaBits.length ? `<span class="subagent-meta">${escapeHTML(metaBits.join(" · "))}</span>` : "";
      const summaryHtml = child.summary ? `<div class="subagent-summary">${escapeHTML(child.summary)}</div>` : "";
      const assetsHtml = (child.assetIds && child.assetIds.length)
        ? `<div class="subagent-assets">${child.assetIds.map((a) => `<code>${escapeHTML(a)}</code>`).join(" ")}</div>`
        : "";
      return `
        <div class="subagent-group">
          <div class="subagent-head">
            <span class="subagent-id">${escapeHTML(child.agent_id)}</span>
            <span class="subagent-profile">${escapeHTML(child.profile)}</span>
            <span class="tool-status ${child.status}">${escapeHTML(child.status)}</span>
            ${metaHtml}
          </div>
          ${child.goal ? `<div class="subagent-goal">${escapeHTML(child.goal)}</div>` : ""}
          ${callsHtml}
          ${summaryHtml}
          ${assetsHtml}
        </div>
      `;
    }).join("");
    return `<div class="subagents">${groups}</div>`;
  }

  function renderProgress(tc) {
    if (tc.status !== "running") return "";
    const hasPercent = typeof tc.progress?.percent === "number";
    if (hasPercent) {
      const pct = Math.max(0, Math.min(100, tc.progress.percent));
      return `
        <div class="progress-block">
          <div class="progress-bar"><div class="progress-bar-fill" style="width:${pct.toFixed(1)}%"></div></div>
          <div class="progress-text">${pct.toFixed(1)}% ${escapeHTML(tc.progress?.message || "")}</div>
        </div>
      `;
    }
    return `
      <div class="progress-block">
        <div class="progress-bar"><div class="progress-bar-fill indeterminate"></div></div>
        <div class="progress-text">working…</div>
      </div>
    `;
  }

  function renderBanner(banner) {
    const cls = banner.kind === "budget" ? "banner-budget"
              : banner.kind === "turn_error" ? "banner-turn-error"
              : banner.kind === "plan" ? "banner-plan"
              : banner.kind === "info" ? "banner-info"
              : "banner-unknown";
    const sub = banner.sub ? `<small>${escapeHTML(banner.sub)}</small>` : "";
    return `<div class="banner ${cls}">${escapeHTML(banner.text)}${sub}</div>`;
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
      els.assetGrid.innerHTML = `<p class="placeholder">No assets yet.</p>`;
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
        <div class="asset-card${a.final ? " final" : ""}">
          ${playerHtml}
          <div class="asset-meta">
            <span class="asset-id">${a.asset_id}</span> · ${escapeHTML(a.source)} · ${escapeHTML(a.summary || "")}
            ${a.final ? `<br><strong style="color:var(--ok)">FINAL</strong>` : ""}
          </div>
        </div>
      `;
    }).join("");
  }

  function renderMediaLibrary() {
    if (!els.mediaLibraryGrid) return;
    if (state.mediaLibraryStatus === "loading") {
      els.mediaLibraryGrid.innerHTML = `<p class="placeholder">Loading media library…</p>`;
      return;
    }
    if (state.mediaLibraryStatus === "signed-out") {
      els.mediaLibraryGrid.innerHTML = `<p class="placeholder">Sign in to load media library.</p>`;
      return;
    }
    if (!state.mediaLibrary.length) {
      els.mediaLibraryGrid.innerHTML = `<p class="placeholder">No media-library assets yet.</p>`;
      return;
    }
    els.mediaLibraryGrid.innerHTML = state.mediaLibrary.map((asset) => {
      const assetId = asset.asset_id || asset.id || "";
      const summary = asset.annotation_summary || {};
      const tags = (summary.tags || []).slice(0, 5);
      const labels = (summary.labels || []).slice(0, 3);
      const anns = state.mediaAnnotations.get(assetId) || [];
      const annHtml = anns.length
        ? `<div class="annotation-list">${anns.map(renderAnnotation).join("")}</div>`
        : "";
      const thumb = asset.thumbnail_src
        ? `<img class="library-thumb" src="${escapeHTML(asset.thumbnail_src)}" alt="" loading="lazy" />`
        : `<div class="library-thumb blank">${escapeHTML(asset.media_kind || "media")}</div>`;
      return `
        <div class="library-card" data-library-asset="${escapeHTML(assetId)}">
          ${thumb}
          <div class="library-card-body">
            <div class="library-title">${escapeHTML(asset.name || assetId)}</div>
            <div class="library-meta">${escapeHTML(assetId)} · ${escapeHTML(asset.media_kind || "media")} · ${formatSeconds(asset.duration)}</div>
            <div class="library-tags">
              <span>${Number(summary.count || 0)} mark(s)</span>
              ${tags.map((tag) => `<span>${escapeHTML(tag)}</span>`).join("")}
              ${labels.map((label) => `<span>${escapeHTML(label)}</span>`).join("")}
            </div>
            <div class="library-card-actions">
              <button type="button" class="library-small-btn" data-library-annotate="${escapeHTML(assetId)}">annotate</button>
              <button type="button" class="library-small-btn" data-library-load="${escapeHTML(assetId)}">markers</button>
            </div>
            ${annHtml}
          </div>
        </div>
      `;
    }).join("");
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

  // Resolve the child tool-call state a tool_exec_* event with an agent_id
  // belongs to. Child tool activity rides the EXISTING tool_exec_* kinds
  // (gemia/subtasks.py) carrying { call_id: <spawn call>, agent_id, tool_call_id }.
  // Returns null (→ caller ignores) if the spawn call or child isn't tracked yet.
  function childCallState(ev) {
    const t = state.currentTurn;
    if (!t) return null;
    const spawn = t.toolCalls.get(ev.call_id);
    if (!spawn || !spawn.children) return null;
    const child = spawn.children.get(ev.agent_id);
    if (!child) return null;
    const key = ev.tool_call_id || ev.call_id;
    let tc = child.calls.get(key);
    if (!tc) {
      tc = {
        tool_call_id: key,
        tool_name: ev.tool_name || "tool",
        status: "running",
        progress: null,
        summary: null,
        previewAssetId: null,
        error: null,
        errorCode: null,
      };
      child.calls.set(key, tc);
      child.callOrder.push(key);
    }
    return tc;
  }

  // ── event handlers (one per kind, no silent drop) ──────────────────

  const handlers = {
    turn_start: () => {
      state.turnInProgress = true;
      if (state.currentTurn) {
        state.currentTurn.streaming = false;
      }
    },
    model_text_delta: (ev) => {
      const t = state.currentTurn;
      if (!t) return;
      t.assistantText += ev.delta;
      t.streaming = true;
    },
    model_tool_call_start: (ev) => {
      const t = state.currentTurn;
      if (!t) return;
      // Text the model streamed right before this call is its lead-in
      // reasoning — for a corrective retry, this is the "diagnosis" line.
      // Move it off the trailing bubble and onto the card so a self-correction
      // reads as one arc (reason → fix) instead of a detached paragraph.
      const reasoning = (t.assistantText || "").trim();
      t.assistantText = "";
      t.streaming = false;
      t.toolCalls.set(ev.call_id, {
        call_id: ev.call_id,
        tool_name: ev.tool_name,
        status: "pending",
        args: null,
        progress: null,
        summary: null,
        error: null,
        errorCode: null,
        recovery: null,
        validOptions: null,
        hint: null,
        reasoning: reasoning || null,
        previewAssetId: null,
        // Multi-agent fan-out: subagent_start populates this Map (agent_id ->
        // child group) under a spawn_subtasks call. Child tool_exec_* events
        // (carrying agent_id) route into the child's own tool list.
        children: new Map(),
        childOrder: [],
      });
      t.orderedCallIds.push(ev.call_id);
    },
    model_tool_call_ready: (ev) => {
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (tc) tc.args = ev.args;
    },
    tool_exec_start: (ev) => {
      // agent_id present → child tool activity, route into the child group.
      if (ev.agent_id) { const c = childCallState(ev); if (c) c.status = "running"; return; }
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (tc) tc.status = "running";
    },
    tool_exec_progress: (ev) => {
      if (ev.agent_id) {
        const c = childCallState(ev);
        if (c) c.progress = {
          percent: typeof ev.percent === "number" ? ev.percent : null,
          message: ev.message || null,
        };
        return;
      }
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (!tc) return;
      tc.progress = {
        percent: typeof ev.percent === "number" ? ev.percent : null,
        message: ev.message || null,
      };
    },
    tool_exec_result: (ev) => {
      if (ev.agent_id) {
        const c = childCallState(ev);
        if (c) {
          c.status = "done";
          c.summary = ev.result?.summary || null;
          c.previewAssetId = ev.result?.asset_id || null;
        }
        return;
      }
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (!tc) return;
      tc.status = "done";
      tc.summary = ev.result?.summary || null;
      tc.previewAssetId = ev.result?.asset_id || null;
      if (tc.previewAssetId) {
        state.assets.push({
          asset_id: tc.previewAssetId,
          kind: ev.result?.kind || inferKindFromAssetId(tc.previewAssetId),
          summary: ev.result?.summary || "",
          source: "tool",
          final: false,
        });
      }
    },
    tool_exec_error: (ev) => {
      if (ev.agent_id) {
        const c = childCallState(ev);
        if (c) { c.status = "failed"; c.error = ev.error || "unknown error"; c.errorCode = ev.error_code || null; }
        return;
      }
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (tc) {
        tc.status = "failed";
        tc.error = ev.error || "unknown error";
        // Typed-error fields (present when the host raised a GemiaError/ToolError).
        // These are what turn a dead-end into a fixable, visible diagnosis.
        tc.errorCode = ev.error_code || null;
        tc.recovery = ev.recovery || null;
        tc.validOptions = Array.isArray(ev.valid_options) ? ev.valid_options : null;
        tc.hint = ev.hint || null;
      }
    },
    subagent_start: (ev) => {
      // A child of a spawn_subtasks call is starting. Create its group under the
      // spawn call so its tool activity + result render indented beneath it.
      const t = state.currentTurn;
      const spawn = t?.toolCalls.get(ev.call_id);
      if (!spawn || !spawn.children) return;
      if (!spawn.children.has(ev.agent_id)) {
        spawn.children.set(ev.agent_id, {
          agent_id: ev.agent_id,
          goal: ev.goal || "",
          profile: ev.tool_profile || "",
          status: "running",
          summary: null,
          assetIds: [],
          spentUsd: null,
          spentSeconds: null,
          steps: null,
          calls: new Map(),
          callOrder: [],
        });
        spawn.childOrder.push(ev.agent_id);
      }
    },
    subagent_result: (ev) => {
      // A child finished (any status). Close its group with the terminal record.
      const spawn = state.currentTurn?.toolCalls.get(ev.call_id);
      const child = spawn?.children?.get(ev.agent_id);
      if (!child) return;
      child.status = ev.status || "ok";
      child.summary = ev.summary || null;
      child.assetIds = Array.isArray(ev.asset_ids) ? ev.asset_ids : [];
      child.spentUsd = typeof ev.spent_usd === "number" ? ev.spent_usd : null;
      child.spentSeconds = typeof ev.spent_seconds === "number" ? ev.spent_seconds : null;
      child.steps = typeof ev.steps === "number" ? ev.steps : null;
    },
    background_task_update: (ev) => {
      // A run_in_background run_shell job changed status. These arrive both
      // mid-turn and between turns (the per-session watcher runs on the
      // session loop), so they update session-scoped state, not currentTurn.
      if (!ev.job_id) return;
      const prev = state.backgroundTasks.get(ev.job_id) || {};
      state.backgroundTasks.set(ev.job_id, {
        job_id: ev.job_id,
        status: ev.status || prev.status || "running",
        summary: ev.summary || prev.summary || "",
        exit_code: typeof ev.exit_code === "number" ? ev.exit_code : (prev.exit_code ?? null),
        elapsed_sec: typeof ev.elapsed_sec === "number" ? ev.elapsed_sec : (prev.elapsed_sec ?? null),
        output_tail: ev.output_tail || prev.output_tail || "",
        // A fresh authoritative status clears any optimistic "停止中…" flag.
        _killing: false,
      });
    },
    budget_gate: (ev) => {
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (tc) tc.status = "gated";
      const alt = (ev.alternatives || []).join(", ");
      t?.banners.push({
        kind: "budget",
        text: `budget gate on ${ev.tool_name}: ${ev.reason}`,
        sub: alt ? `alternatives: ${alt}` : "",
      });
    },
    plan_gate: (ev) => {
      // A mutating tool was blocked by plan mode. Same card treatment as a
      // budget gate; one compact banner per gated tool.
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (tc) tc.status = "gated";
      t?.banners.push({
        kind: "plan",
        text: `计划模式拦截了 ${ev.tool_name}（规划期间不执行改动）`,
      });
    },
    plan_mode_changed: (ev) => {
      state.planMode = !!ev.enabled;
      if (!state.planMode) state.planReady = false;
    },
    completion_check: () => {
      // Host-side one-shot goal check before an honest stop; nothing to render.
    },
    turn_wrapup: (ev) => {
      // Informational "stopped because X; here's what was / wasn't done".
      state.currentTurn?.banners.push({
        kind: "info",
        text: ev.message || `turn stopped (${ev.reason || "unknown"})`,
      });
    },
    ask_question: (ev) => {
      // Minimal surfacing so a pending elicit is visible instead of an
      // "unknown event" error. Full web answer controls are tracked separately;
      // today the question can be answered from the CLI or it falls back to
      // defaults on timeout.
      const q = ev.question || {};
      state.currentTurn?.banners.push({
        kind: "info",
        text: `Lumeri 提问：${q.title || "需要你的输入"}`,
        sub: q.description || "",
      });
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
      const text = `SSE replay gap: missed ${ev.missed_event_count || "some"} event(s); refreshing session state.`;
      const banner = {
        kind: "unknown",
        text,
        sub: `requested=${ev.requested_last_event_id}, oldest=${ev.oldest_available_event_id}, latest=${ev.latest_event_id}`,
      };
      if (state.currentTurn) state.currentTurn.banners.push(banner);
      state.errors.push(text);
      state.turnInProgress = false;
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
      const t = state.currentTurn;
      state.turnInProgress = false;
      if (!t) return;
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
    },
    turn_error: (ev) => {
      state.turnInProgress = false;
      const t = state.currentTurn;
      if (t) {
        t.streaming = false;
        t.complete = true;
        t.banners.push({ kind: "turn_error", text: `turn error: ${ev.error || "unknown"}` });
      }
    },
  };

  function dispatch(ev) {
    // Debug hook: raw event log accessible from DevTools console and test harnesses.
    (window.__lumeriEvents = window.__lumeriEvents || []).push(ev);
    const handler = handlers[ev.kind];
    if (!handler) {
      const t = state.currentTurn;
      const banner = { kind: "unknown", text: `unknown event kind: ${ev.kind}`, sub: JSON.stringify(ev) };
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

  // ── SSE connection ──────────────────────────────────────────────────

  function setConnPill(text, cls) {
    els.connPill.textContent = text;
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
      } catch (parseErr) {
        const banner = { kind: "unknown", text: `SSE parse error: ${parseErr.message}`, sub: e.data?.slice(0, 200) };
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
          <button class="ptl-btn pt-edit-btn" id="ptl-split" title="在指针处分割 (S)"><svg viewBox="0 0 16 16"><path d="M8 2.5v11"/><rect x="2.6" y="5" width="3.4" height="6" rx="1.1"/><rect x="10" y="5" width="3.4" height="6" rx="1.1"/></svg><span>分割</span></button>
          <button class="ptl-btn pt-edit-btn" id="ptl-delete" title="删除所选 (Del)"><svg viewBox="0 0 16 16"><path d="M3 4.5h10"/><path d="M6 4.5V3h4v1.5"/><path d="M4.6 4.5 5.1 13.3h5.8l.5-8.8"/><path d="M6.9 6.8v4.3M9.1 6.8v4.3"/></svg><span>删除</span></button>
          <button class="ptl-btn pt-edit-btn" id="ptl-marker" title="在指针处加标记 (M)"><svg viewBox="0 0 16 16"><path d="M4.5 2v12"/><path d="M4.5 2.8h7.3l-1.8 2.6 1.8 2.6H4.5"/></svg><span>标记</span></button>
        </div>
        <div class="ptl-sep"></div>
        <button class="ptl-btn ptl-toggle" id="ptl-snap" title="吸附对齐"><svg viewBox="0 0 16 16"><path d="M4 2.5v5a4 4 0 0 0 8 0v-5"/><path d="M4 2.5h2.4M9.6 2.5H12M4 6h2.4M9.6 6H12"/></svg><span>吸附</span></button>
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
          <div class="ptl-corner">轨道</div>
          <canvas id="ptl-ruler"></canvas>
        </div>
        <div class="ptl-lanes-row">
          <div class="ptl-headers" id="ptl-headers"></div>
          <div class="ptl-scroll" id="ptl-scroll"><div class="ptl-content" id="ptl-content"></div></div>
        </div>
      </div>
      <div class="pt-quick-actions" id="pt-quick-actions">
        <button class="pt-action-btn" data-cmd="export the project at 1080p quality">↑ 导出 1080p</button>
        <button class="pt-action-btn" data-cmd="export the project as draft quality">草稿导出</button>
        <button class="pt-action-btn" data-cmd="add a title overlay at the start of the timeline">加标题</button>
        <button class="pt-action-btn" data-cmd="get the current timeline layout">获取布局</button>
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
    el.style.setProperty("--cedge", col[1]);
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
      return `<div class="ptl-head ${escapeHTML(t.kind)}" style="height:${TL_TRACK_H}px">`
        + `<span class="ptl-head-name">${escapeHTML(t.name || t.id)}</span>`
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
    state.lastEventId = null;
    state.projectTimeline = null;
    state.mediaAnnotations = new Map();
    state.planMode = false;     // fresh sessions start with plan mode off
    state.planReady = false;
    connectSse(state.sessionId);
    startTimelinePoll();
    fetchMediaLibrary().catch(() => {});
    render();
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
    // Reconcile background shell tasks after an SSE gap: the server snapshot is
    // authoritative (it survives the 200-event ring overflow the SSE replay
    // can't). exit_code/output_tail aren't in the REST list — keep any we
    // already learned from a prior background_task_update.
    if (Array.isArray(data.tasks)) {
      const next = new Map();
      for (const t of data.tasks) {
        if (!t || !t.job_id) continue;
        const prev = state.backgroundTasks.get(t.job_id) || {};
        next.set(t.job_id, {
          job_id: t.job_id,
          status: t.status || prev.status || "running",
          summary: t.summary || prev.summary || "",
          exit_code: prev.exit_code ?? null,
          elapsed_sec: typeof t.elapsed_sec === "number" ? t.elapsed_sec : (prev.elapsed_sec ?? null),
          output_tail: prev.output_tail || "",
          _killing: false,
        });
      }
      state.backgroundTasks = next;
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

  async function uploadFile(file) {
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
      els.uploadBtn.parentNode.insertBefore(label, els.uploadBtn);
    }
    label.textContent = text || "";
  }

  async function submitTurn(message) {
    if (!state.sessionId) throw new Error("no session");
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
      const body = await r.text();
      turn.banners.push({ kind: "turn_error", text: `POST /turn failed: ${r.status}`, sub: body.slice(0, 200) });
      state.turnInProgress = false;
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

  document.addEventListener("click", (e) => {
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

  els.sendBtn.addEventListener("click", () => {
    const msg = els.promptInput.value.trim();
    if (!msg) return;
    submitTurn(msg).then(() => { els.promptInput.value = ""; })
                   .catch((err) => {
                     state.errors.push(`submit turn failed: ${err.message}`);
                     render();
                   });
  });

  els.promptInput.addEventListener("keydown", (e) => {
    // Enter sends; Shift+Enter = newline. Never send mid-IME-composition (中文输入法候选).
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      els.sendBtn.click();
    }
  });

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

  // ── background tasks ────────────────────────────────────────────────
  // Chip toggles the per-task list; kill buttons POST to the kill route. The
  // authoritative "failed/killed" status still arrives via the watcher's
  // background_task_update SSE, so this only sets an optimistic "停止中…".
  els.tasksChip?.addEventListener("click", () => {
    state._tasksBarOpen = !state._tasksBarOpen;
    render();
  });

  async function killBackgroundTask(jobId) {
    if (!state.sessionId) return;
    const t = state.backgroundTasks.get(jobId);
    if (t) t._killing = true;   // reflected as "停止中…" until the SSE lands
    render();
    try {
      const r = await fetch(
        `/sessions/${state.sessionId}/tasks/${encodeURIComponent(jobId)}/kill`,
        { method: "POST" },
      );
      if (!r.ok) throw new Error(`kill failed: ${r.status}`);
    } catch (err) {
      if (t) t._killing = false;
      state.errors.push(`停止任务失败: ${err.message}`);
      render();
    }
  }

  document.addEventListener("click", (e) => {
    const killEl = e.target.closest("[data-task-kill]");
    if (!killEl) return;
    const jobId = killEl.dataset.taskKill;
    if (jobId) killBackgroundTask(jobId);
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

    function applySession(data) {
      session = data || {};
      const acct = session.account;
      if (acct && acct.email) {
        accountBtn.textContent = acct.email;
        accountBtn.classList.add("signed-in");
      } else {
        accountBtn.textContent = "登录";
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
        avatar.textContent = (acct.email || acct.name || "?").trim().charAt(0).toUpperCase();
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
    if (state.sessionId) {
      navigator.sendBeacon?.(`/sessions/${state.sessionId}/close`);
    }
  });
})();
