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
    uploadInput: $("#upload-input"),
    uploadBtn: $("#upload-btn"),
    promptInput: $("#prompt-input"),
    sendBtn: $("#send-btn"),
    sandboxBtn: $("#sandbox-toggle-btn"),
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
    frameRuler: null,           // active FrameRuler instance (frame ruler/playhead/scrubber)
    keyframeEditor: null,       // active KeyframeEditor instance (keyframe-track strip)
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

    if (!state.turns.length) {
      els.timeline.hidden = true;
      els.emptyState.hidden = false;
    } else {
      els.emptyState.hidden = true;
      els.timeline.hidden = false;
      els.timeline.innerHTML = state.turns.map((turn, idx) => renderTurn(turn, idx)).join("");
    }

    renderAssets();
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
      </div>
    `;
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
              : "banner-unknown";
    const sub = banner.sub ? `<small>${escapeHTML(banner.sub)}</small>` : "";
    return `<div class="banner ${cls}">${escapeHTML(banner.text)}${sub}</div>`;
  }

  function renderAssets() {
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
      });
      t.orderedCallIds.push(ev.call_id);
    },
    model_tool_call_ready: (ev) => {
      const t = state.currentTurn;
      const tc = t?.toolCalls.get(ev.call_id);
      if (tc) tc.args = ev.args;
    },
    tool_exec_start: (ev) => {
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (tc) tc.status = "running";
    },
    tool_exec_progress: (ev) => {
      const tc = state.currentTurn?.toolCalls.get(ev.call_id);
      if (!tc) return;
      tc.progress = {
        percent: typeof ev.percent === "number" ? ev.percent : null,
        message: ev.message || null,
      };
    },
    tool_exec_result: (ev) => {
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
    timeline_op: () => {
      // Timeline patch landed: refresh the project timeline panel immediately
      // rather than waiting for the next poll interval.
      fetchProjectTimeline();
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
      fetchProjectTimeline();
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
    ask_question: (ev) => {
      // The agent paused on an `elicit` call: render the controls and let the
      // user answer. The modal lives outside render()'s innerHTML so typing is
      // never clobbered by a later event; submitting POSTs to /ask_response,
      // which resolves the awaiting tool call on the session loop.
      if (ev.question) showAskModal(ev.question);
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

  // ── ask mechanism (elicit) ──────────────────────────────────────────
  // Imperative DOM (not part of render()'s innerHTML) so user input survives
  // any events that arrive while the form is open.

  let askModalEl = null;

  function closeAskModal() {
    if (askModalEl && askModalEl.parentNode) askModalEl.parentNode.removeChild(askModalEl);
    askModalEl = null;
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (v != null) node.setAttribute(k, v);
    }
    for (const c of children || []) if (c) node.appendChild(c);
    return node;
  }

  // Build one control's DOM. Returns { node, read } where read() yields the answer.
  function buildAskControl(key, ctrl) {
    const type = ctrl.type;
    const opts = Array.isArray(ctrl.options) ? ctrl.options : [];

    if (type === "select") {
      const sel = el("select", { class: "ask-input" });
      for (const o of opts) {
        const opt = el("option", { value: o.value, text: o.label != null ? o.label : o.value });
        if (ctrl.default != null && o.value === ctrl.default) opt.selected = true;
        sel.appendChild(opt);
      }
      return { node: sel, read: () => sel.value };
    }

    if (type === "multi_select") {
      const box = el("div", { class: "ask-checks" });
      const inputs = [];
      for (const o of opts) {
        const cb = el("input", { type: "checkbox", value: o.value });
        inputs.push(cb);
        box.appendChild(el("label", { class: "ask-check" }, [cb, el("span", { text: o.label != null ? o.label : o.value })]));
      }
      return { node: box, read: () => inputs.filter((i) => i.checked).map((i) => i.value) };
    }

    if (type === "text") {
      const input = ctrl.multiline
        ? el("textarea", { class: "ask-input", rows: "3", placeholder: ctrl.placeholder || "" })
        : el("input", { class: "ask-input", type: "text", placeholder: ctrl.placeholder || "" });
      return { node: input, read: () => input.value };
    }

    if (type === "slider") {
      const min = ctrl.min != null ? ctrl.min : 0;
      const max = ctrl.max != null ? ctrl.max : 100;
      const step = ctrl.step != null ? ctrl.step : 1;
      const start = ctrl.default != null ? ctrl.default : min;
      const range = el("input", { class: "ask-range", type: "range", min, max, step, value: start });
      const out = el("output", { class: "ask-range-val", text: String(start) });
      range.addEventListener("input", () => { out.textContent = range.value; });
      return { node: el("div", { class: "ask-slider" }, [range, out]), read: () => Number(range.value) };
    }

    if (type === "panel") {
      const fields = ctrl.fields || {};
      const wrap = el("div", { class: "ask-panel" });
      if (ctrl.description) wrap.appendChild(el("div", { class: "ask-panel-desc", text: ctrl.description }));
      const readers = {};
      for (const [fk, fctrl] of Object.entries(fields)) {
        const built = buildAskControl(fk, fctrl);
        readers[fk] = built.read;
        wrap.appendChild(el("div", { class: "ask-field" }, [el("label", { class: "ask-label", text: fk }), built.node]));
      }
      return { node: wrap, read: () => Object.fromEntries(Object.entries(readers).map(([k, r]) => [k, r()])) };
    }

    // custom_panel (and any unknown type): JSON fallback editor.
    const ta = el("textarea", { class: "ask-input ask-json", rows: "5", placeholder: "{ }" });
    ta.value = "{}";
    return {
      node: el("div", {}, [el("div", { class: "ask-panel-desc", text: "JSON answer" }), ta]),
      read: () => { try { return JSON.parse(ta.value || "{}"); } catch { return ta.value; } },
    };
  }

  function showAskModal(question) {
    closeAskModal();  // only one ask at a time
    const controls = question.controls || {};
    const readers = {};
    const fieldNodes = [];
    for (const [key, ctrl] of Object.entries(controls)) {
      const built = buildAskControl(key, ctrl);
      readers[key] = built.read;
      fieldNodes.push(el("div", { class: "ask-field" }, [el("label", { class: "ask-label", text: key }), built.node]));
    }

    const errLine = el("div", { class: "ask-error" });
    const submitBtn = el("button", { type: "button", class: "ask-submit", text: "Submit" });
    const card = el("div", { class: "ask-card" }, [
      el("div", { class: "ask-title", text: question.title || "Question" }),
      question.description ? el("div", { class: "ask-desc", text: question.description }) : null,
      el("div", { class: "ask-fields" }, fieldNodes),
      errLine,
      el("div", { class: "ask-actions" }, [submitBtn]),
    ]);
    askModalEl = el("div", { class: "ask-overlay" }, [card]);
    document.body.appendChild(askModalEl);

    submitBtn.addEventListener("click", async () => {
      const answers = Object.fromEntries(Object.entries(readers).map(([k, r]) => [k, r()]));
      submitBtn.disabled = true;
      errLine.textContent = "";
      try {
        const res = await fetch(`/sessions/${state.sessionId}/ask_response`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question_id: question.question_id, answers }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          errLine.textContent = data.error || `submit failed (${res.status})`;
          submitBtn.disabled = false;
          return;
        }
        closeAskModal();
      } catch (err) {
        errLine.textContent = `network error: ${err.message}`;
        submitBtn.disabled = false;
      }
    });
  }

  // Debug/test hook (mirrors window.__lumeriEvents): lets DevTools and test
  // harnesses drive the ask UI directly.
  window.__lumeriAsk = { showAskModal, closeAskModal, buildAskControl };

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

  // ── Project timeline ────────────────────────────────────────────────

  async function fetchProjectTimeline() {
    if (!state.sessionId) return;
    if (state.ptDrag) return;   // never re-fetch/reconcile mid-drag (would detach the dragged clip)
    try {
      const r = await fetch(`/sessions/${state.sessionId}/timeline`);
      if (!r.ok) return;
      const data = await r.json();
      state.projectTimeline = data;
      renderProjectTimeline(data);
    } catch { /* ignore network errors */ }
  }

  function startTimelinePoll() {
    stopTimelinePoll();
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
    const panel = document.getElementById("project-timeline-panel");
    const tracksEl = document.getElementById("project-timeline-tracks");
    const metaEl = document.getElementById("project-timeline-meta");
    if (!panel || !tracksEl || !metaEl) return;

    const tracks = (data.tracks || []).filter(t => t.clips && t.clips.length > 0);
    if (tracks.length === 0 && (!data.duration || data.duration <= 0)) {
      panel.hidden = true;
      return;
    }

    panel.hidden = false;
    const dur = data.duration || 0;

    // Frame ruler + playhead + scrubber + frame-step. Host lives just after the
    // tracks; we (re)build it whenever the project's duration/fps is known so
    // the ruler stays in sync with the timeline length.
    if (dur > 0) {
      let rulerHost = document.getElementById("project-timeline-ruler");
      if (!rulerHost) {
        rulerHost = document.createElement("div");
        rulerHost.id = "project-timeline-ruler";
        rulerHost.className = "project-timeline-ruler";
        if (tracksEl.parentNode) {
          tracksEl.parentNode.insertBefore(rulerHost, tracksEl.nextSibling);
        }
      }
      const prevFrame = state.frameRuler ? state.frameRuler.currentFrame : 0;
      rulerHost.innerHTML = "";
      buildFrameRuler(rulerHost, {
        durationSec: dur,
        fps: data.fps || 30,
        pxPerFrame: FRAME_RULER_DEFAULT_PX_PER_FRAME,
        currentFrame: prevFrame,
      });

      // Frame/timecode readout reflecting the playhead position, kept in sync
      // whenever the ruler moves (seek/scrub/step) — see applyPlayhead wiring.
      updatePlayheadReadout();

      // Keyframe-track editor strip: the visual basis for curve editing. Lives
      // just below the ruler. We (re)render it from the currently selected clip
      // so the strip shows that clip's keyframe track(s); with no selection it
      // falls back to an empty "value" track to keep the layout stable.
      let kfHost = document.getElementById("project-timeline-keyframes");
      if (!kfHost) {
        kfHost = document.createElement("div");
        kfHost.id = "project-timeline-keyframes";
        kfHost.className = "project-timeline-keyframes";
        if (rulerHost.parentNode) {
          rulerHost.parentNode.insertBefore(kfHost, rulerHost.nextSibling);
        }
      }
      renderSelectedClipKeyframes();

      // Clip/layer inspector: lists the selected layer's transform/opacity/blend/
      // effects with light edit controls. Lives just below the keyframe strip.
      renderInspector();
    }

    metaEl.textContent = [
      `${data.width || 1920}×${data.height || 1080}`,
      `${data.fps || 30}fps`,
      dur > 0 ? fmtSec(dur) : "",
      `seq ${data.patch_seq || 0}`,
    ].filter(Boolean).join(" · ");

    if (tracks.length === 0) {
      tracksEl.innerHTML = `<div style="font-size:10px;color:var(--text-dim);padding:4px 0">No clips yet</div>`;
      return;
    }

    tracksEl.innerHTML = tracks.map(track => {
      const isOverlay = track.kind === "overlay";
      const clipHtml = track.clips.map(clip => {
        if (!dur) return "";
        const left = (clip.start / dur) * 100;
        const width = Math.max((clip.duration / dur) * 100, 0.3);
        const sel = clip.id === state.selectedClipId ? " selected" : "";
        const cls = `pt-clip ${clip.media_kind}${sel}`;
        const label = clip.media_kind === "text"
          ? (clip.text_config?.content?.slice(0, 20) || clip.name)
          : clip.name;
        const title = `${clip.name} (${clip.media_kind}) ${fmtSec(clip.start)}–${fmtSec(clip.start + clip.duration)}`;
        // data-* carry clip identity + current values so the direct-edit
        // pointer handlers can compute ops without a re-fetch.
        return `<div class="${cls}" data-clip-id="${clip.id}" data-track-id="${clip.track_id}"`
          + ` data-start="${clip.start}" data-duration="${clip.duration}" data-media-kind="${clip.media_kind}"`
          + ` data-source-in="${clip.source_in ?? 0}" data-source-out="${clip.source_out ?? 0}"`
          + ` style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%" title="${title}">`
          + `<span>${label}</span><div class="pt-clip-handle right" data-handle="right"></div></div>`;
      }).join("");
      const bodyCls = `pt-track-body${isOverlay ? " overlay" : ""}`;
      return `<div class="pt-track-row">
        <div class="pt-track-label"><span>${track.id}</span></div>
        <div class="${bodyCls}" data-track-id="${track.id}">${clipHtml}</div>
      </div>`;
    }).join("");
  }

  function fmtSec(s) {
    if (!isFinite(s)) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  // SMPTE-style timecode for an integer frame at a given fps: MM:SS:FF where
  // FF is the within-second frame index (0 .. round(fps)-1). Deterministic and
  // integer-only so the panel readout matches the ruler's frame model exactly.
  function fmtTimecode(frame, fps) {
    const f = Math.max(1, Math.round(Number(fps) || 30));
    const fr = Math.max(0, Math.round(Number(frame) || 0));
    const totalSec = Math.floor(fr / f);
    const ff = fr % f;
    const mm = Math.floor(totalSec / 60);
    const ss = totalSec % 60;
    return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}:${String(ff).padStart(2, "0")}`;
  }

  // Full SMPTE-style timecode HH:MM:SS:FF for an integer frame at a given fps.
  // FF is the within-second frame index (0 .. round(fps)-1); HH/MM/SS derive
  // from total seconds. Integer-only and deterministic, mirroring fmtTimecode
  // but with an explicit hours field so professional-length sequences read as a
  // proper NLE timecode. Exposed as window.__lumeriTimecode for tests/DevTools.
  //   frame 90 @ 30fps -> "00:00:03:00"
  //   frame 47 @ 24fps -> "00:00:01:23"
  function fmtTimecodeFull(frame, fps) {
    const f = Math.max(1, Math.round(Number(fps) || 30));
    const fr = Math.max(0, Math.round(Number(frame) || 0));
    const totalSec = Math.floor(fr / f);
    const ff = fr % f;
    const hh = Math.floor(totalSec / 3600);
    const mm = Math.floor((totalSec % 3600) / 60);
    const ss = totalSec % 60;
    const p = (n) => String(n).padStart(2, "0");
    return `${p(hh)}:${p(mm)}:${p(ss)}:${p(ff)}`;
  }

  // Derive keyframe tracks for a clip the round-2 editor can render. The backend
  // serializes per-clip animation data as either clip.keyframes or
  // clip.effects.keyframes, shaped { property: { frameIndex(string): value } }
  // (e.g. { opacity: { "0": 0, "12": 0.42 } }) or { property: { points: [...] } }.
  // We convert each frame index to seconds (t = frame / fps) so marker.left ==
  // t * pxPerSec lines up with the frame ruler. When a clip carries no keyframe
  // data we synthesize a single "clip" track with boundary markers at the clip's
  // own start/end (clip-relative t = 0 and t = duration) so a selected clip
  // always shows a meaningful, frame-snapped track.
  function clipKeyframeTracks(clip, fps) {
    const f = Math.max(1, Math.round(Number(fps) || 30));
    const out = [];
    const sources = [clip && clip.keyframes, clip && clip.effects && clip.effects.keyframes];
    for (const src of sources) {
      if (!src || typeof src !== "object") continue;
      for (const [property, spec] of Object.entries(src)) {
        const kfs = keyframePairsToSeconds(spec, f);
        if (kfs.length) out.push({ property, keyframes: kfs });
      }
    }
    if (out.length) return out;

    // Fallback: boundary markers from the clip's own time bounds.
    const dur = Math.max(0, Number(clip && clip.duration) || 0);
    const kfs = dur > 0
      ? [{ t: 0, value: 0 }, { t: dur, value: 1 }]
      : [{ t: 0, value: 0 }];
    return [{ property: "clip", keyframes: kfs }];
  }

  // Normalize one property's keyframe spec into [{ t(seconds), value }] sorted by
  // t. Accepts { frameIndex: value } maps and { points: [{ frame|t, value }] }.
  function keyframePairsToSeconds(spec, fps) {
    const f = Math.max(1, Math.round(Number(fps) || 30));
    const pairs = [];
    if (spec && Array.isArray(spec.points)) {
      for (const p of spec.points) {
        if (!p || typeof p !== "object") continue;
        const t = p.t != null ? Number(p.t)
          : (p.frame != null ? Number(p.frame) / f : null);
        if (t == null || !isFinite(t)) continue;
        pairs.push({ t, value: p.value });
      }
    } else if (spec && typeof spec === "object") {
      for (const [k, value] of Object.entries(spec)) {
        const frame = Number(k);
        if (!isFinite(frame)) continue;
        pairs.push({ t: frame / f, value });
      }
    }
    return pairs.sort((a, b) => a.t - b.t);
  }

  // ── frame ruler / playhead / scrubber / frame-step ──────────────────
  // Self-contained, imperative DOM (not part of render()'s innerHTML), so it is
  // testable in isolation and never clobbered by streaming events. Mirrors the
  // ask-modal pattern: a debug/test hook (window.__lumeriFrameRuler) drives the
  // same build()/seekToFrame()/step() the panel render wires up.
  //
  // Frame model (all integer-frame, deterministic):
  //   totalFrames = round(durationSec * fps)            (>= 1)
  //   one tick per frame, frames 0 .. totalFrames-1     → tickCount == totalFrames
  //   major tick (labeled) every `majorEvery` frames    (frame 0 always major)
  //   playhead.left(px) == currentFrame * pxPerFrame
  //   scrubber: frame = clamp(round(offsetX / pxPerFrame), 0, totalFrames-1)
  //   step(d):  frame = clamp(currentFrame + d, 0, totalFrames-1)

  const FRAME_RULER_DEFAULT_PX_PER_FRAME = 6;

  function clampFrame(n, totalFrames) {
    n = Math.round(Number(n) || 0);
    if (n < 0) return 0;
    const hi = Math.max(0, totalFrames - 1);
    return n > hi ? hi : n;
  }

  // Pick a labeled-tick stride so labels stay readable regardless of zoom:
  // aim for a major tick roughly every ~64px, snapped to a "nice" frame count.
  function frameRulerMajorEvery(pxPerFrame) {
    const target = pxPerFrame > 0 ? Math.round(64 / pxPerFrame) : 10;
    const nice = [1, 2, 5, 10, 15, 20, 25, 30, 50, 60, 100, 150, 300, 600];
    for (const n of nice) if (n >= target) return n;
    return nice[nice.length - 1];
  }

  function buildFrameRuler(container, opts) {
    if (!container) return null;
    opts = opts || {};
    const durationSec = Number(opts.durationSec) || 0;
    const fps = Number(opts.fps) || 30;
    const pxPerFrame = Number(opts.pxPerFrame) > 0
      ? Number(opts.pxPerFrame) : FRAME_RULER_DEFAULT_PX_PER_FRAME;
    const totalFrames = Math.max(1, Math.round(durationSec * fps));
    const majorEvery = frameRulerMajorEvery(pxPerFrame);

    // tear down any prior instance in this container
    if (state.frameRuler && state.frameRuler.root && state.frameRuler.root.parentNode === container) {
      container.removeChild(state.frameRuler.root);
    }

    const root = el("div", { class: "frame-ruler" });
    root.style.position = "relative";
    root.style.width = `${totalFrames * pxPerFrame}px`;

    // Tick marks: one per frame; majors are labeled with the frame number.
    const ticksWrap = el("div", { class: "frame-ruler-ticks" });
    for (let f = 0; f < totalFrames; f++) {
      const isMajor = (f % majorEvery) === 0;
      const tick = el("div", { class: isMajor ? "frame-tick major" : "frame-tick" });
      tick.style.left = `${f * pxPerFrame}px`;
      tick.setAttribute("data-frame", String(f));
      if (isMajor) {
        const lbl = el("span", { class: "frame-tick-label", text: String(f) });
        tick.appendChild(lbl);
      }
      ticksWrap.appendChild(tick);
    }
    root.appendChild(ticksWrap);

    // Playhead: a vertical line at currentFrame * pxPerFrame.
    const playhead = el("div", { class: "playhead" });
    root.appendChild(playhead);

    // Frame-step controls (prev / next frame), clamped to [0, totalFrames-1].
    const stepWrap = el("div", { class: "frame-step" });
    const prevBtn = el("button", { type: "button", class: "frame-step-btn", "data-step": "prev", text: "◀" });
    const readout = el("span", { class: "frame-step-readout" });
    const nextBtn = el("button", { type: "button", class: "frame-step-btn", "data-step": "next", text: "▶" });
    stepWrap.appendChild(prevBtn);
    stepWrap.appendChild(readout);
    stepWrap.appendChild(nextBtn);

    const inst = {
      root, ticksWrap, playhead, stepWrap, readout,
      durationSec, fps, pxPerFrame, totalFrames, majorEvery,
      currentFrame: clampFrame(opts.currentFrame != null ? opts.currentFrame : 0, totalFrames),
      container,
    };

    inst.applyPlayhead = () => {
      playhead.style.left = `${inst.currentFrame * inst.pxPerFrame}px`;
      readout.textContent = `frame ${inst.currentFrame} / ${inst.totalFrames - 1}`;
      // Mirror the move into the panel header timecode readout when present.
      updatePlayheadReadout();
    };
    inst.seekToFrame = (n) => {
      inst.currentFrame = clampFrame(n, inst.totalFrames);
      inst.applyPlayhead();
      return inst.currentFrame;
    };
    inst.step = (delta) => inst.seekToFrame(inst.currentFrame + (Math.round(Number(delta)) || 0));

    // Scrubber: pointerdown + drag on the ruler snaps to the nearest frame.
    const offsetXOf = (ev) => {
      if (typeof ev.offsetX === "number") return ev.offsetX;
      const rect = root.getBoundingClientRect ? root.getBoundingClientRect() : { left: 0 };
      return (ev.clientX || 0) - (rect.left || 0);
    };
    const seekFromEvent = (ev) => {
      const frame = Math.round(offsetXOf(ev) / inst.pxPerFrame);
      inst.seekToFrame(frame);
    };
    root.addEventListener("pointerdown", (ev) => {
      inst.scrubbing = true;
      try { root.setPointerCapture && root.setPointerCapture(ev.pointerId); } catch {}
      seekFromEvent(ev);
      if (ev.preventDefault) ev.preventDefault();
    });
    root.addEventListener("pointermove", (ev) => { if (inst.scrubbing) seekFromEvent(ev); });
    const endScrub = () => { inst.scrubbing = false; };
    root.addEventListener("pointerup", endScrub);
    root.addEventListener("pointercancel", endScrub);

    prevBtn.addEventListener("click", () => inst.step(-1));
    nextBtn.addEventListener("click", () => inst.step(1));

    inst.applyPlayhead();

    const wrap = el("div", { class: "frame-ruler-wrap" }, [root, stepWrap]);
    inst.wrap = wrap;
    container.appendChild(wrap);

    state.frameRuler = inst;
    return inst;
  }

  // Debug/test hook (mirrors window.__lumeriAsk): build() constructs the UI in a
  // container; seekToFrame()/step() drive the active instance.
  window.__lumeriFrameRuler = {
    build: (container, opts) => buildFrameRuler(container, opts),
    seekToFrame: (n) => (state.frameRuler ? state.frameRuler.seekToFrame(n) : null),
    step: (delta) => (state.frameRuler ? state.frameRuler.step(delta) : null),
    current: () => (state.frameRuler ? state.frameRuler.currentFrame : null),
  };

  // Computed-behavior hook: full SMPTE timecode HH:MM:SS:FF for (frame, fps).
  // Pure function, no DOM — lets DevTools/tests assert the formatting directly
  // (e.g. (90, 30) -> "00:00:03:00", (47, 24) -> "00:00:01:23").
  window.__lumeriTimecode = (frame, fps) => fmtTimecodeFull(frame, fps);

  // ── keyframe-track editor (curve-editing visual basis) ──────────────
  // Self-contained, imperative DOM (like buildFrameRuler): a horizontal track
  // with one marker per keyframe, the visual foundation for full curve editing.
  // Mirrors the frame-ruler pattern, including a debug/test hook
  // (window.__lumeriKeyframeEditor) that drives the same build/add/move the
  // panel render wires up. Bezier value curves come later — this slice nails
  // marker POSITIONS, add/move, and frame snapping.
  //
  // Position model (all deterministic, time-in-seconds):
  //   marker.left(px)   == keyframe.t * pxPerSec
  //   track width(px)   == max(durationSec, last keyframe t) * pxPerSec
  //   keyframes kept sorted ascending by t (addKeyframe inserts in order)
  //   frame snap         t -> round(t * fps) / fps   (move snaps to frame grid)

  const KEYFRAME_EDITOR_DEFAULT_PX_PER_SEC = 80;

  // Snap a time (seconds) to the nearest frame boundary on the fps grid.
  function snapTimeToFrame(t, fps) {
    const f = Number(fps) > 0 ? Number(fps) : 30;
    return Math.round((Number(t) || 0) * f) / f;
  }

  function clampTime(t, durationSec) {
    t = Number(t) || 0;
    if (t < 0) return 0;
    const hi = Number(durationSec) > 0 ? Number(durationSec) : t;
    return t > hi ? hi : t;
  }

  function buildKeyframeEditor(container, opts) {
    if (!container) return null;
    opts = opts || {};
    const property = opts.property != null ? String(opts.property) : "value";
    const durationSec = Number(opts.durationSec) || 0;
    const fps = Number(opts.fps) > 0 ? Number(opts.fps) : 30;
    const pxPerSec = Number(opts.pxPerSec) > 0
      ? Number(opts.pxPerSec) : KEYFRAME_EDITOR_DEFAULT_PX_PER_SEC;

    // Normalize + sort the incoming keyframes (ascending by t).
    const keyframes = (Array.isArray(opts.keyframes) ? opts.keyframes : [])
      .map((k) => ({ t: Number(k.t) || 0, value: k.value }))
      .sort((a, b) => a.t - b.t);

    const inst = {
      property, durationSec, fps, pxPerSec, keyframes, container,
    };

    // Track width spans at least the project duration, growing if a keyframe
    // sits past the end (so out-of-range markers stay visible).
    inst.trackWidth = () => {
      const lastT = inst.keyframes.length ? inst.keyframes[inst.keyframes.length - 1].t : 0;
      return Math.max(durationSec, lastT) * inst.pxPerSec;
    };

    const root = el("div", { class: "kf-track", "data-property": property });
    root.style.position = "relative";

    const lane = el("div", { class: "kf-track-lane" });
    root.appendChild(lane);

    const label = el("span", { class: "kf-track-label", text: property });
    root.appendChild(label);

    inst.root = root;
    inst.lane = lane;

    // Build one marker element for a keyframe at the given list index.
    inst.makeMarker = (kf, index) => {
      const marker = el("div", { class: "kf-marker" });
      marker.style.left = `${kf.t * inst.pxPerSec}px`;
      marker.setAttribute("data-index", String(index));
      marker.setAttribute("data-t", String(kf.t));
      if (kf.value != null) marker.setAttribute("data-value", String(kf.value));
      marker.setAttribute("title", `${property} @ ${kf.t.toFixed(3)}s`);
      return marker;
    };

    // Re-render all markers from inst.keyframes (clears prior markers first).
    inst.renderMarkers = () => {
      const old = lane.querySelectorAll(".kf-marker");
      for (const m of old) if (m.parentNode) m.parentNode.removeChild(m);
      inst.keyframes.forEach((kf, i) => lane.appendChild(inst.makeMarker(kf, i)));
      root.style.width = `${inst.trackWidth()}px`;
    };

    // addKeyframe: insert keeping the list sorted by t, then re-render.
    inst.addKeyframe = (t, value) => {
      t = clampTime(t, inst.durationSec);
      const kf = { t, value };
      let i = 0;
      while (i < inst.keyframes.length && inst.keyframes[i].t <= t) i++;
      inst.keyframes.splice(i, 0, kf);
      inst.renderMarkers();
      return inst.keyframes.indexOf(kf);
    };

    // moveKeyframe: update a marker's t (snapped to the frame grid), keep the
    // list sorted, re-render, and return the keyframe's NEW index.
    inst.moveKeyframe = (index, newT) => {
      if (index < 0 || index >= inst.keyframes.length) return -1;
      const kf = inst.keyframes[index];
      kf.t = clampTime(snapTimeToFrame(newT, inst.fps), inst.durationSec);
      inst.keyframes.sort((a, b) => a.t - b.t);
      inst.renderMarkers();
      return inst.keyframes.indexOf(kf);
    };

    // markers(): computed positions for callers/tests.
    inst.markers = () => inst.keyframes.map((kf, i) => ({
      index: i, t: kf.t, value: kf.value, left: kf.t * inst.pxPerSec,
    }));

    inst.renderMarkers();
    container.appendChild(root);

    state.keyframeEditor = inst;
    return inst;
  }

  // Debug/test hook (mirrors window.__lumeriFrameRuler): build() constructs the
  // strip in a container; addKeyframe/moveKeyframe/markers drive the instance.
  window.__lumeriKeyframeEditor = {
    build: (container, opts) => buildKeyframeEditor(container, opts),
    addKeyframe: (t, value) => (state.keyframeEditor ? state.keyframeEditor.addKeyframe(t, value) : null),
    moveKeyframe: (index, newT) => (state.keyframeEditor ? state.keyframeEditor.moveKeyframe(index, newT) : null),
    markers: () => (state.keyframeEditor ? state.keyframeEditor.markers() : null),
  };

  // Integration-level debug/test hook (mirrors __lumeriFrameRuler/
  // __lumeriKeyframeEditor): drives the WHOLE project-timeline panel render path
  // — frame ruler wired above the tracks, selection-driven keyframe strip below
  // it, and the header timecode readout — so DevTools and tests can assert the
  // computed wiring without standing up a browser.
  window.__lumeriTimelineUI = {
    // Render the panel from a timeline payload (same shape as GET /timeline).
    renderPanel: (data) => {
      state.projectTimeline = data;
      state.selectedClipId = null;
      renderProjectTimeline(data);
      return data;
    },
    // Seek the panel's frame ruler and return the resulting frame.
    seekToFrame: (n) => (state.frameRuler ? state.frameRuler.seekToFrame(n) : null),
    // Step the panel's frame ruler by +/-1 (clamped); returns the new frame.
    step: (delta) => (state.frameRuler ? state.frameRuler.step(delta) : null),
    // Current playhead frame on the panel ruler.
    currentFrame: () => (state.frameRuler ? state.frameRuler.currentFrame : null),
    // The header timecode readout text (frame + SMPTE), for assertions.
    readout: () => {
      const tc = document.getElementById("pt-timecode");
      return tc ? tc.textContent : null;
    },
    // Select a clip by id (drives the keyframe strip), returns marker positions.
    selectClip: (clipId) => {
      selectClip(clipId);
      return state.keyframeEditor ? state.keyframeEditor.markers() : null;
    },
    // Derived keyframe tracks for a clip (no DOM): [{ property, keyframes }].
    clipKeyframeTracks: (clip, fps) => clipKeyframeTracks(clip, fps),
  };

  // ── clip / layer inspector panel ────────────────────────────────────
  // Self-contained, imperative DOM (like buildFrameRuler / buildKeyframeEditor):
  // when a clip/layer is selected the panel lists its transform (x/y/scale/
  // rotation), opacity, blend mode, and any extra effects with readable values,
  // plus LIGHT controls — an opacity slider and a reset button — that emit the
  // existing edit op path (postTimelineOp -> { op: "set_effects", … }), the same
  // /timeline/op endpoint the model's verbs and direct-edit gestures use. No new
  // backend endpoints are invented.
  //
  // Value model (all read off the layer's effects map, with sensible defaults):
  //   transform : x=0, y=0, scale=1, rotation=0
  //   opacity   : 1 (clamped to the backend's [0,1] domain on the slider)
  //   blend     : layer.blend / layer.blend_mode / effects.blend, else "normal"
  //   effects   : any remaining keys on effects beyond the named transform set
  //
  // Mirrors the frame-ruler/keyframe-editor debug hook so DevTools and node tests
  // can assert the COMPUTED panel values without a browser:
  //   window.__lumeriInspector = { build(container, layer), readControls() }

  // Inspector transform keys that get their own labelled rows (everything else
  // on the effects map shows up under the generic "effects" list).
  const INSPECTOR_TRANSFORM_KEYS = ["x", "y", "scale", "rotation"];
  // Default values so an un-keyframed/untouched layer still reads sensibly.
  const INSPECTOR_DEFAULTS = { x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 };

  // ── compositing ops (additive) ──────────────────────────────────────
  // The 14 blend modes the backend accepts (lumenframe.model.BLEND_MODES /
  // the `set_blend` op). Ordered for the inspector <select>: normal first,
  // then the common groups (multiply/screen/overlay, lighten/darken, the
  // light blends, difference/exclusion, the dodge/burn pair, add/subtract).
  // Kept in lockstep with the backend; an unknown mode is rejected by set_blend.
  const COMPOSITING_BLEND_MODES = [
    "normal", "multiply", "screen", "overlay", "add", "lighten", "darken",
    "soft_light", "hard_light", "difference", "exclusion",
    "color_dodge", "color_burn", "subtract",
  ];
  // PiP defaults (mirror the backend pip op's own defaults: br corner, 0.3
  // scale, a small rounded radius so the inset reads as a card).
  const COMPOSITING_PIP_DEFAULTS = { corner: "br", scale: 0.3, margin: 0.04, radius: 24 };

  // The op every compositing emitter last sent through postTimelineOp — exposed
  // via window.__lumeriCompositing.lastOp() so DevTools / node tests can assert
  // the exact op structure without a network round-trip.
  state.lastCompositingOp = null;

  // All compositing emitters funnel through here: record the op for testability,
  // then dispatch it down the EXISTING edit path (postTimelineOp -> /timeline/op).
  // No new backend endpoint is invented.
  function emitCompositingOp(opBody) {
    state.lastCompositingOp = opBody;
    return postTimelineOp(opBody);
  }

  function blendOptions() {
    return COMPOSITING_BLEND_MODES.slice();
  }

  // set a layer's blend mode (sugar over the `set_blend` op which rejects an
  // unknown mode up front, unlike the raw set_blend_mode field).
  function emitSetBlend(clipId, mode) {
    if (!clipId) return null;
    return emitCompositingOp({ op: "set_blend", layer_id: clipId, mode: String(mode) });
  }

  // turn a layer into a picture-in-picture inset using the backend's own pip
  // defaults (br corner, 0.3 scale, rounded radius).
  function emitPip(clipId, opts) {
    if (!clipId) return null;
    const o = opts || {};
    return emitCompositingOp({
      op: "pip",
      layer_id: clipId,
      corner: o.corner || COMPOSITING_PIP_DEFAULTS.corner,
      scale: o.scale != null ? o.scale : COMPOSITING_PIP_DEFAULTS.scale,
      margin: o.margin != null ? o.margin : COMPOSITING_PIP_DEFAULTS.margin,
      radius: o.radius != null ? o.radius : COMPOSITING_PIP_DEFAULTS.radius,
    });
  }

  // add a default 2-stop linear gradient layer (black -> blue), per the
  // add_gradient schema (stops sorted/clamped to [0,1] on the backend).
  function emitAddGradient() {
    return emitCompositingOp({
      op: "add_gradient",
      mode: "linear",
      stops: [[0.0, "#000000"], [1.0, "#3344ff"]],
      angle: 90,
    });
  }

  // add a centred rounded rectangle shape layer, per the add_shape schema.
  function emitAddShape() {
    return emitCompositingOp({
      op: "add_shape",
      kind: "rect",
      fill: "#ff0044",
      rect: [0.1, 0.1, 0.9, 0.9],
      radius: 12,
    });
  }

  // cross-dissolve from clip A into clip B over a default 1s overlap.
  function emitCrossfade(fromId, toId) {
    if (!fromId || !toId) return null;
    return emitCompositingOp({
      op: "crossfade",
      from_id: fromId,
      to_id: toId,
      duration: 1.0,
    });
  }

  // The first two distinct selectable clips on the project timeline (in track
  // order), used to seed the inspector Crossfade button. Selected clip first.
  function compositingCrossfadeCandidates() {
    const tl = state.projectTimeline || {};
    const ids = [];
    for (const tr of tl.tracks || []) {
      for (const c of tr.clips || []) {
        if (c && c.id != null && !ids.includes(c.id)) ids.push(c.id);
      }
    }
    const sel = state.selectedClipId;
    if (sel && ids.includes(sel)) {
      const rest = ids.filter((id) => id !== sel);
      return [sel, ...rest];
    }
    return ids;
  }

  function _num(v, fallback) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }
  function clamp01(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return 0;
    return n < 0 ? 0 : n > 1 ? 1 : n;
  }
  // Trim trailing zeros for a compact-but-honest numeric readout (3 dp max).
  function fmtNum(n) {
    if (!Number.isFinite(n)) return "—";
    const s = (Math.round(n * 1000) / 1000).toString();
    return s;
  }

  // Pull the inspector's value model off a layer/clip object. Effects (x/y/scale/
  // rotation/opacity/…) ride on layer.effects (see lumerai/patches.py _EFFECT_KEYS);
  // we read them with defaults so the panel is always populated.
  function inspectorValues(layer) {
    const fx = (layer && typeof layer.effects === "object" && layer.effects) || {};
    const transform = {
      x: _num(fx.x, INSPECTOR_DEFAULTS.x),
      y: _num(fx.y, INSPECTOR_DEFAULTS.y),
      scale: _num(fx.scale, INSPECTOR_DEFAULTS.scale),
      rotation: _num(fx.rotation, INSPECTOR_DEFAULTS.rotation),
    };
    const opacity = clamp01(_num(fx.opacity, INSPECTOR_DEFAULTS.opacity));
    const blend = (layer && (layer.blend || layer.blend_mode)) || fx.blend || "normal";
    // Extra effects: anything on the map that isn't a named transform/opacity/blend.
    const known = new Set([...INSPECTOR_TRANSFORM_KEYS, "opacity", "blend"]);
    const effects = {};
    for (const [k, v] of Object.entries(fx)) {
      if (!known.has(k)) effects[k] = v;
    }
    return { transform, opacity, blend: String(blend), effects };
  }

  function inspectorRow(label, value) {
    const row = el("div", { class: "inspector-row" });
    row.appendChild(el("span", { class: "inspector-key", text: label }));
    row.appendChild(el("span", { class: "inspector-val", text: value }));
    return row;
  }

  function buildInspector(container, layer) {
    if (!container) return null;
    const vals = inspectorValues(layer);

    const root = el("div", { class: "inspector" });
    if (layer && layer.id != null) root.setAttribute("data-clip-id", String(layer.id));

    // Header: layer name / media kind.
    const name = (layer && (layer.name || layer.id)) || "—";
    const kind = (layer && (layer.media_kind || layer.kind)) || "";
    const head = el("div", { class: "inspector-head" });
    head.appendChild(el("span", { class: "inspector-title", text: String(name) }));
    if (kind) head.appendChild(el("span", { class: "inspector-kind", text: String(kind) }));
    root.appendChild(head);

    // ── Transform section (read-only readouts) ──
    const tform = el("div", { class: "inspector-section", "data-section": "transform" });
    tform.appendChild(el("div", { class: "inspector-section-title", text: "Transform" }));
    const tGrid = el("div", { class: "inspector-grid" });
    tGrid.appendChild(inspectorRow("X", fmtNum(vals.transform.x)));
    tGrid.appendChild(inspectorRow("Y", fmtNum(vals.transform.y)));
    tGrid.appendChild(inspectorRow("Scale", fmtNum(vals.transform.scale)));
    tGrid.appendChild(inspectorRow("Rotation", `${fmtNum(vals.transform.rotation)}°`));
    tform.appendChild(tGrid);
    root.appendChild(tform);

    // ── Compositing section: opacity (with slider) + blend ──
    const comp = el("div", { class: "inspector-section", "data-section": "compositing" });
    comp.appendChild(el("div", { class: "inspector-section-title", text: "Compositing" }));
    const cGrid = el("div", { class: "inspector-grid" });
    cGrid.appendChild(inspectorRow("Blend", vals.blend));
    comp.appendChild(cGrid);

    // Opacity control: a LIGHT slider [0,1] mirroring backend validation, with a
    // live numeric output; releasing it emits the existing set_effects op.
    const opWrap = el("div", { class: "inspector-control" });
    opWrap.appendChild(el("label", { class: "inspector-control-label", text: "Opacity" }));
    const slider = el("input", {
      class: "inspector-opacity",
      type: "range",
      min: "0",
      max: "1",
      step: "0.01",
    });
    slider.value = String(vals.opacity);
    const out = el("output", { class: "inspector-opacity-val", text: fmtNum(vals.opacity) });
    slider.addEventListener("input", () => { out.textContent = fmtNum(clamp01(slider.value)); });
    // Commit on change (pointer release / keyboard commit) through the real op path.
    slider.addEventListener("change", () => {
      const clipId = layer && layer.id;
      if (!clipId) return;
      postTimelineOp({ op: "set_effects", clip_id: clipId, effects: { opacity: clamp01(slider.value) } });
    });
    opWrap.appendChild(slider);
    opWrap.appendChild(out);
    comp.appendChild(opWrap);

    // ── Blend-mode <select> (additive): lists the 14 backend blend modes and
    // emits the existing set_blend op on change. The read-only "Blend" row above
    // stays as the at-a-glance value; this is the editable control.
    const blendWrap = el("div", { class: "inspector-control" });
    blendWrap.appendChild(el("label", { class: "inspector-control-label", text: "Blend mode" }));
    const blendSel = el("select", { class: "inspector-blend" });
    for (const mode of COMPOSITING_BLEND_MODES) {
      const opt = el("option", { value: mode, text: mode });
      if (mode === vals.blend) opt.selected = true;
      blendSel.appendChild(opt);
    }
    blendSel.value = COMPOSITING_BLEND_MODES.includes(vals.blend) ? vals.blend : "normal";
    blendSel.addEventListener("change", () => {
      const clipId = layer && layer.id;
      if (!clipId) return;
      emitSetBlend(clipId, blendSel.value);
    });
    blendWrap.appendChild(blendSel);
    comp.appendChild(blendWrap);

    // ── Compositing actions (additive): each emits an existing op through the
    // same postTimelineOp path the rest of the inspector uses.
    // NOTE: a distinct class (.inspector-comp-btn, NOT .inspector-btn) so the
    // existing inspector test's `querySelector(".inspector-btn")` still resolves
    // to the Reset-opacity action in the actions row below — additive only.
    const compActions = el("div", { class: "inspector-comp-actions" });

    const pipBtn = el("button", { class: "inspector-comp-btn", "data-action": "pip", text: "Make PiP" });
    pipBtn.addEventListener("click", () => {
      const clipId = layer && layer.id;
      if (!clipId) return;
      emitPip(clipId);
    });
    compActions.appendChild(pipBtn);

    const gradBtn = el("button", { class: "inspector-comp-btn", "data-action": "add-gradient", text: "Add gradient layer" });
    gradBtn.addEventListener("click", () => { emitAddGradient(); });
    compActions.appendChild(gradBtn);

    const shapeBtn = el("button", { class: "inspector-comp-btn", "data-action": "add-shape", text: "Add shape" });
    shapeBtn.addEventListener("click", () => { emitAddShape(); });
    compActions.appendChild(shapeBtn);

    // Crossfade needs a second clip; enabled only when the timeline has two
    // selectable clips. Fades the selected clip into the next available one.
    const cands = compositingCrossfadeCandidates();
    const xfadeBtn = el("button", { class: "inspector-comp-btn", "data-action": "crossfade", text: "Crossfade" });
    if (cands.length < 2) xfadeBtn.disabled = true;
    xfadeBtn.addEventListener("click", () => {
      const c2 = compositingCrossfadeCandidates();
      if (c2.length < 2) return;
      emitCrossfade(c2[0], c2[1]);
    });
    compActions.appendChild(xfadeBtn);

    comp.appendChild(compActions);
    root.appendChild(comp);

    // ── Effects section: any remaining effects keys, readable values ──
    const fxKeys = Object.keys(vals.effects);
    const fxSection = el("div", { class: "inspector-section", "data-section": "effects" });
    fxSection.appendChild(el("div", { class: "inspector-section-title", text: "Effects" }));
    const fxGrid = el("div", { class: "inspector-grid" });
    if (fxKeys.length === 0) {
      fxGrid.appendChild(el("div", { class: "inspector-empty", text: "none" }));
    } else {
      for (const k of fxKeys) {
        const v = vals.effects[k];
        const txt = typeof v === "number" ? fmtNum(v) : String(v);
        fxGrid.appendChild(inspectorRow(k, txt));
      }
    }
    fxSection.appendChild(fxGrid);
    root.appendChild(fxSection);

    // ── Actions: reset opacity to 1 through the same op path ──
    const actions = el("div", { class: "inspector-actions" });
    const resetBtn = el("button", { class: "inspector-btn", "data-action": "reset", text: "Reset opacity" });
    resetBtn.addEventListener("click", () => {
      slider.value = "1";
      out.textContent = fmtNum(1);
      const clipId = layer && layer.id;
      if (!clipId) return;
      postTimelineOp({ op: "set_effects", clip_id: clipId, effects: { opacity: 1 } });
    });
    actions.appendChild(resetBtn);
    root.appendChild(actions);

    // Instance API + computed-value accessors for the debug/test hook.
    const inst = {
      root,
      container,
      layer,
      values: vals,
      slider,
      output: out,
      resetBtn,
      // readControls(): the panel's CURRENT (possibly edited) computed values.
      readControls: () => ({
        opacity: clamp01(slider.value),
        transform: { ...vals.transform },
        blend: vals.blend,
        effects: { ...vals.effects },
      }),
    };

    container.appendChild(root);
    state.inspector = inst;
    return inst;
  }

  // Render the inspector into the panel host for the currently selected clip. With
  // no selection the host is emptied (panel layout stays stable). Lazily creates
  // the #project-timeline-inspector host below the keyframe strip.
  function renderInspector() {
    let host = document.getElementById("project-timeline-inspector");
    if (!host) {
      const kfHost = document.getElementById("project-timeline-keyframes");
      const panel = document.getElementById("project-timeline-panel");
      const anchor = kfHost || panel;
      if (!anchor) return;
      host = document.createElement("div");
      host.id = "project-timeline-inspector";
      host.className = "project-timeline-inspector";
      if (kfHost && kfHost.parentNode) {
        kfHost.parentNode.insertBefore(host, kfHost.nextSibling);
      } else if (panel) {
        panel.appendChild(host);
      }
    }
    host.innerHTML = "";
    const clip = selectedClip();
    if (!clip) { state.inspector = null; return; }
    buildInspector(host, clip);
  }

  // Debug/test hook (mirrors __lumeriFrameRuler / __lumeriKeyframeEditor):
  // build() constructs the inspector in a container for a given layer/clip;
  // readControls() returns the active panel's computed (post-edit) values.
  window.__lumeriInspector = {
    build: (container, layer) => buildInspector(container, layer),
    readControls: () => (state.inspector ? state.inspector.readControls() : null),
    values: () => (state.inspector ? state.inspector.values : null),
  };

  // Debug/test hook for the compositing controls (mirrors __lumeriInspector):
  // pure op emitters that route through the existing postTimelineOp path, plus
  // blendOptions() (the 14 modes) and lastOp() (the op last emitted), so DevTools
  // and node tests can assert the exact op structure without a network call.
  window.__lumeriCompositing = {
    blendOptions: () => blendOptions(),
    emitSetBlend: (clipId, mode) => emitSetBlend(clipId, mode),
    emitPip: (clipId, opts) => emitPip(clipId, opts),
    emitAddGradient: () => emitAddGradient(),
    emitAddShape: () => emitAddShape(),
    emitCrossfade: (aId, bId) => emitCrossfade(aId, bId),
    lastOp: () => state.lastCompositingOp,
  };

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
    document.querySelectorAll("#project-timeline-tracks .pt-clip").forEach((el) => {
      el.classList.toggle("selected", el.dataset.clipId === clipId);
    });
    updateEditHint();
    renderSelectedClipKeyframes();
    renderInspector();
  }

  // Rebuild the keyframe-track strip from the currently selected clip's derived
  // keyframe tracks (round-2 editor). With no selection the strip falls back to
  // an empty "value" track so the panel layout stays stable. Markers land at
  // marker.left == t * pxPerSec, matching the frame ruler above.
  function renderSelectedClipKeyframes() {
    const kfHost = document.getElementById("project-timeline-keyframes");
    if (!kfHost) return;
    const tl = state.projectTimeline || {};
    const dur = Number(tl.duration) || 0;
    if (dur <= 0) return;   // ruler/strip only exist once duration is known
    const fps = Number(tl.fps) || 30;
    const pxPerSec = fps * FRAME_RULER_DEFAULT_PX_PER_FRAME;

    const clip = selectedClip();
    const tracks = clip
      ? clipKeyframeTracks(clip, fps)
      : [{ property: "value", keyframes: [] }];

    // Track-span the editor at least to the project duration so positions line
    // up with the ruler; clip-relative keyframes are offset by the clip start so
    // a marker sits under the same frame on the ruler.
    const offset = clip ? (Number(clip.start) || 0) : 0;
    kfHost.innerHTML = "";
    let firstInst = null;
    tracks.forEach((trk) => {
      const sub = document.createElement("div");
      sub.className = "kf-track-host";
      kfHost.appendChild(sub);
      const inst = buildKeyframeEditor(sub, {
        property: trk.property,
        keyframes: trk.keyframes.map((k) => ({ t: offset + k.t, value: k.value })),
        durationSec: dur,
        fps,
        pxPerSec,
      });
      if (!firstInst) firstInst = inst;
    });
    // state.keyframeEditor (set by buildKeyframeEditor) points at the LAST track;
    // keep the test/debug hook pointed at the first track for stable assertions.
    if (firstInst) state.keyframeEditor = firstInst;
  }

  // Frame + SMPTE timecode readout for the current playhead, shown in the panel
  // header. Created lazily next to the meta line; updated on every ruler move
  // (seek/scrub/step) via the frame ruler's applyPlayhead callback.
  function updatePlayheadReadout() {
    const metaEl = document.getElementById("project-timeline-meta");
    if (!metaEl || !metaEl.parentNode) return;
    let tc = document.getElementById("pt-timecode");
    if (!tc) {
      tc = document.createElement("span");
      tc.id = "pt-timecode";
      tc.className = "pt-timecode";
      metaEl.parentNode.appendChild(tc);
    }
    const fr = state.frameRuler;
    if (!fr) { tc.textContent = ""; return; }
    tc.textContent = `${fmtTimecode(fr.currentFrame, fr.fps)} · f${fr.currentFrame}`;
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
    const hint = document.getElementById("pt-edit-hint");
    if (!hint) return;
    const c = selectedClip();
    hint.textContent = c
      ? `${c.id} · ${fmtSec(c.start)}–${fmtSec(c.start + c.duration)}`
      : "select a clip to edit";
  }

  function setupTimelineDirectEdit() {
    const root = document.getElementById("project-timeline-tracks");
    if (!root) return;

    root.addEventListener("pointerdown", (ev) => {
      const clipEl = ev.target.closest(".pt-clip");
      if (!clipEl) return;
      selectClip(clipEl.dataset.clipId);            // always select on press
      if (!editingEnabled()) return;                // busy gate: select only mid-turn

      const body = clipEl.closest(".pt-track-body");
      const dur = (state.projectTimeline && state.projectTimeline.duration) || 0;
      if (!body || dur <= 0) return;
      const rect = body.getBoundingClientRect();
      if (rect.width <= 0) return;
      const handle = ev.target.closest(".pt-clip-handle");
      state.ptDrag = {
        clipId: clipEl.dataset.clipId,
        mode: handle ? handle.dataset.handle : "move",   // "right" | "move"
        startX: ev.clientX,
        bodyW: rect.width,
        dur,
        origStart: parseFloat(clipEl.dataset.start) || 0,
        origDur: parseFloat(clipEl.dataset.duration) || 0,
        sourceIn: parseFloat(clipEl.dataset.sourceIn) || 0,
        mediaKind: clipEl.dataset.mediaKind,
        el: clipEl,
      };
      clipEl.classList.add("dragging");
      try { clipEl.setPointerCapture(ev.pointerId); } catch {}
      ev.preventDefault();
    });

    root.addEventListener("pointermove", (ev) => {
      const d = state.ptDrag;
      if (!d) return;
      const deltaSec = ((ev.clientX - d.startX) / d.bodyW) * d.dur;
      if (d.mode === "right") {
        // Snap the trailing edge to neighbour edges / grid, derive duration.
        const newEnd = snapSeconds(d.origStart + d.origDur + deltaSec, d);
        const newDur = Math.max(0.1, newEnd - d.origStart);
        d.pendingDur = newDur;
        d.el.style.width = `${Math.max((newDur / d.dur) * 100, 0.3).toFixed(2)}%`;
      } else {
        const newStart = snapSeconds(d.origStart + deltaSec, d);
        d.pendingStart = newStart;
        d.el.style.left = `${((newStart / d.dur) * 100).toFixed(2)}%`;
      }
    });

    const finish = () => {
      const d = state.ptDrag;
      if (!d) return;
      state.ptDrag = null;
      d.el.classList.remove("dragging");
      if (d.mode === "right" && d.pendingDur != null) {
        if (d.mediaKind === "video" || d.mediaKind === "audio") {
          postTimelineOp({ op: "trim", clip_id: d.clipId, source_out: +(d.sourceIn + d.pendingDur).toFixed(6) });
        } else {                                  // image/text: duration via set_time
          postTimelineOp({ op: "set_time", clip_id: d.clipId, duration: +d.pendingDur.toFixed(6) });
        }
      } else if (d.mode === "move" && d.pendingStart != null) {
        postTimelineOp({ op: "move", clip_id: d.clipId, start: +d.pendingStart.toFixed(6) });
      }
    };
    root.addEventListener("pointerup", finish);
    root.addEventListener("pointercancel", finish);

    // DE-C: split / delete / undo controls + Delete key (all via /timeline/op).
    document.getElementById("pt-split-btn")?.addEventListener("click", () => {
      const c = selectedClip();
      if (!c || !editingEnabled()) return;
      postTimelineOp({ op: "split", clip_id: c.id, at_time: +(c.start + c.duration / 2).toFixed(6) });
    });
    document.getElementById("pt-delete-btn")?.addEventListener("click", () => {
      const c = selectedClip();
      if (!c || !editingEnabled()) return;
      postTimelineOp({ op: "delete", clip_id: c.id }).then(() => { state.selectedClipId = null; updateEditHint(); });
    });
    document.getElementById("pt-undo-btn")?.addEventListener("click", () => {
      if (!editingEnabled()) return;
      postTimelineOp({ op: "undo", steps: 1 });
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Delete" && ev.key !== "Backspace") return;
      const t = ev.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const c = selectedClip();
      if (!c || !editingEnabled()) return;
      ev.preventDefault();
      postTimelineOp({ op: "delete", clip_id: c.id }).then(() => { state.selectedClipId = null; updateEditHint(); });
    });
  }

  // DE-D snapping: snap a timeline second to nearby edges of OTHER clips
  // (within ~6px) and otherwise to a coarse 0.1s grid; clamp >= 0.
  function snapSeconds(sec, d) {
    sec = Math.max(0, sec);
    if (!d || d.dur <= 0 || d.bodyW <= 0) return sec;
    const tolSec = (6 / d.bodyW) * d.dur;
    let best = sec, bestDist = tolSec;
    document.querySelectorAll("#project-timeline-tracks .pt-clip").forEach((el) => {
      if (el.dataset.clipId === d.clipId) return;
      const s = parseFloat(el.dataset.start) || 0;
      const e = s + (parseFloat(el.dataset.duration) || 0);
      for (const edge of [s, e]) {
        const dist = Math.abs(sec - edge);
        if (dist < bestDist) { best = edge; bestDist = dist; }
      }
    });
    if (bestDist === tolSec) best = Math.round(sec * 10) / 10;   // no edge → 0.1s grid
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
    state.turns = [];
    state.currentTurn = null;
    state.assets = [];
    state.errors = [];
    state.turnInProgress = false;
    state.lastEventId = null;
    state.projectTimeline = null;
    connectSse(state.sessionId);
    startTimelinePoll();
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
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
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
