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

  // ── Project timeline ────────────────────────────────────────────────

  async function fetchProjectTimeline() {
    if (!state.sessionId) return;
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
        const newDur = Math.max(0.1, d.origDur + deltaSec);
        d.pendingDur = newDur;
        d.el.style.width = `${Math.max((newDur / d.dur) * 100, 0.3).toFixed(2)}%`;
      } else {
        const newStart = posSeconds(d.origStart + deltaSec, d.dur, d.bodyW, d.clipId);
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

  // px→seconds positioning; DE-D overrides snapping behaviour. Base: clamp >= 0.
  function posSeconds(sec, _dur, _bodyW, _excludeClipId) {
    return Math.max(0, sec);
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
