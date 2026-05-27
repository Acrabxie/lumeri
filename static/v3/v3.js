/* Lumeri v3 frontend — vanilla JS, no build step.
 *
 * Connects to the agent loop via:
 *   POST /sessions                              create session
 *   POST /sessions/{id}/assets                  raw body + X-Filename
 *   POST /sessions/{id}/turn                    {"message": "..."} (202)
 *   GET  /sessions/{id}/stream                  EventSource (auto Last-Event-ID)
 *   GET  /sessions/{id}/assets/{aid}            preview URL for the asset
 *   POST /sessions/{id}/close                   teardown
 *
 * Invariants (mirror the agent loop's promises):
 *   - Every event kind has a handler. Unknown kinds raise a visible
 *     banner — never silent drop.
 *   - tool_exec_progress shows real percent when present, indeterminate
 *     spinner when omitted. We never fabricate progress.
 *   - All asset previews load from /sessions/{id}/assets/{aid}. The
 *     preview_uri field in tool_exec_result.result is the on-disk path
 *     and is IGNORED for fetching; we use asset_id only.
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
  };

  /** @typedef {{ asset_id: string, kind: string, summary: string, source: "user"|"tool", final?: boolean }} AssetEntry */

  const state = {
    sessionId: null,
    eventSource: null,
    turnInProgress: false,
    turns: [],                  // array of TurnRecord
    currentTurn: null,          // TurnRecord (also last in turns[])
    /** @type {AssetEntry[]} */
    assets: [],
    /** @type {string[]} */
    errors: [],
    uploadStatus: null,
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

  function render() {
    els.sessionLabel.textContent = state.sessionId || "—";
    els.sendBtn.disabled = !state.sessionId || state.turnInProgress;
    els.uploadBtn.disabled = !state.sessionId || state.turnInProgress;

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
    const callsHtml = turn.orderedCallIds.map((cid) => renderToolCall(turn.toolCalls.get(cid))).join("");
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

  function renderToolCall(tc) {
    const argsHtml = tc.args
      ? `<div class="tool-args">${escapeHTML(JSON.stringify(tc.args, null, 2))}</div>`
      : "";
    const summaryHtml = tc.summary
      ? `<div class="tool-summary">${escapeHTML(tc.summary)}</div>`
      : "";
    const errorHtml = tc.error
      ? `<div class="tool-error">${escapeHTML(tc.error)}</div>`
      : "";
    const progressHtml = renderProgress(tc);
    const previewHtml = tc.previewAssetId && state.sessionId
      ? `<a class="tool-preview-link" href="/sessions/${state.sessionId}/assets/${tc.previewAssetId}" target="_blank" rel="noopener">open ${tc.previewAssetId} ↗</a>`
      : "";
    return `
      <div class="tool-card">
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
      t.toolCalls.set(ev.call_id, {
        call_id: ev.call_id,
        tool_name: ev.tool_name,
        status: "pending",
        args: null,
        progress: null,
        summary: null,
        error: null,
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
          kind: "video",   // milestone 1: all tool outputs are video
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
    turn_complete: (ev) => {
      const t = state.currentTurn;
      state.turnInProgress = false;
      if (!t) return;
      t.streaming = false;
      t.complete = true;
      const finals = ev.final_asset_ids || [];
      for (const aid of finals) {
        const existing = state.assets.find((a) => a.asset_id === aid);
        if (existing) existing.final = true;
      }
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

  // ── SSE connection ──────────────────────────────────────────────────

  function setConnPill(text, cls) {
    els.connPill.textContent = text;
    els.connPill.className = `status-pill ${cls}`;
  }

  function connectSse(sessionId) {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    const es = new EventSource(`/sessions/${sessionId}/stream`);
    es.onopen = () => setConnPill("live", "live");
    es.onerror = () => setConnPill("reconnecting", "reconnecting");
    es.onmessage = (e) => {
      try {
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

  // ── API calls ───────────────────────────────────────────────────────

  async function createSession() {
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
    connectSse(state.sessionId);
    render();
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
      kind: file.type?.startsWith("image/") ? "image" : "video",
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

  // boot
  createSession().catch((err) => {
    state.errors.push(`initial session failed: ${err.message}`);
    setConnPill("failed", "failed");
    render();
  });

  // teardown on page hide
  window.addEventListener("beforeunload", () => {
    if (state.sessionId) {
      navigator.sendBeacon?.(`/sessions/${state.sessionId}/close`);
    }
  });
})();
