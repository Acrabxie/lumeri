(function () {
  const state = { task: null, open: true, feedbackFor: null, feedbackError: "", copiedPath: "" };
  const css = `
    #lumeri-crt-panel{position:fixed;right:14px;bottom:92px;width:min(360px,calc(100vw - 28px));max-height:min(54vh,560px);z-index:80;background:rgba(14,17,20,.96);color:#eef5f8;border-radius:16px;box-shadow:0 18px 56px rgba(0,0,0,.42);font:13px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Noto Sans SC",sans-serif;overflow:hidden;display:none}
    #lumeri-crt-panel.visible{display:block}
    #lumeri-crt-panel.collapsed .crt-body{display:none}
    .crt-head{display:flex;align-items:center;gap:8px;padding:10px 12px;background:rgba(255,255,255,.04);cursor:pointer}
    .crt-dot{width:7px;height:7px;border-radius:999px;background:#8bdfff;box-shadow:0 0 18px rgba(139,223,255,.65)}
    .crt-title{font-weight:650;font-size:13px;flex:1}
    .crt-sub{font-size:11px;color:#9ba9b4}
    .crt-body{padding:10px;display:flex;flex-direction:column;gap:10px;overflow:auto;max-height:calc(min(62vh,620px) - 42px)}
    .crt-card{background:rgba(255,255,255,.055);border-radius:14px;padding:10px;display:flex;flex-direction:column;gap:8px}
    .crt-card video,.crt-card img{width:100%;max-height:190px;object-fit:contain;background:#07090b;border-radius:10px}
    .crt-row{display:flex;align-items:center;gap:8px;min-width:0}
    .crt-kind{font-size:11px;color:#8bdfff;white-space:nowrap}
    .crt-file{font-size:12px;color:#c7d2d9;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .crt-note{font-size:12px;color:#dbe4e9;white-space:pre-wrap}
    .crt-actions{display:flex;gap:8px}
    .crt-btn{border:0;border-radius:10px;background:rgba(139,223,255,.13);color:#9be5ff;padding:6px 10px;cursor:pointer;font-size:12px}
    .crt-btn:hover{background:rgba(139,223,255,.2)}
    .crt-feedback{display:flex;gap:6px}
    .crt-feedback input{flex:1;min-width:0;border:0;border-radius:10px;background:rgba(255,255,255,.08);color:#eef5f8;padding:7px 9px;outline:none}
    .crt-feedback-note{font-size:12px;color:#b9c8d1;background:rgba(139,223,255,.08);border-radius:10px;padding:7px 8px;white-space:pre-wrap}
    .crt-feedback-error{font-size:12px;color:#ffb6b6;background:rgba(255,96,96,.1);border-radius:10px;padding:7px 8px;white-space:pre-wrap}
    .crt-log-box{background:rgba(3,5,7,.72);border:1px solid rgba(255,255,255,.07);border-radius:12px;padding:9px;display:flex;flex-direction:column;gap:8px}
    .crt-log-head{display:flex;align-items:center;gap:8px;color:#c9d7df;font-size:12px;font-weight:650}
    .crt-log-count{margin-left:auto;color:#8a98a4;font-size:11px;font-weight:500}
    .crt-log-stream{margin:0;max-height:230px;overflow:auto;white-space:pre-wrap;word-break:break-word;color:#aebdc7;font:11px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;user-select:text}
    .crt-log-line{display:block;padding:0 0 5px}
    .crt-log-ts{color:#6f7d88}
    .crt-log-phase{color:#8bdfff}
    .crt-log-source{color:#cfd9df}
    .crt-report{background:linear-gradient(180deg,rgba(139,223,255,.10),rgba(255,255,255,.045));border:1px solid rgba(139,223,255,.18);border-radius:12px;padding:10px;display:flex;flex-direction:column;gap:7px}
    .crt-report-title{display:flex;align-items:center;gap:7px;color:#e9f8ff;font-size:12px;font-weight:700}
    .crt-report-state{border:1px solid rgba(139,223,255,.25);border-radius:999px;color:#8bdfff;padding:1px 7px;font:10px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
    .crt-report-summary,.crt-report-next{font-size:12px;color:#cbd7de;white-space:pre-wrap}
    .crt-report-path{font:11px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#9be5ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .crt-report-stats{display:flex;flex-wrap:wrap;gap:5px}
    .crt-report-chip{font-size:10px;color:#98aab5;background:rgba(255,255,255,.06);border-radius:999px;padding:2px 7px}
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  function fileName(value) {
    return String(value || "").split("/").filter(Boolean).pop() || "preview";
  }

  function assetUrl(value) {
    const path = String(value || "");
    for (const root of ["outputs", "frames", "styled", "demo", "inputs", "uploads", "temp", "timeline"]) {
      const marker = "/" + root + "/";
      const idx = path.indexOf(marker);
      if (idx >= 0) return "/file/" + root + "/" + path.slice(idx + marker.length).split("/").map(encodeURIComponent).join("/");
      if (path.startsWith(root + "/")) return "/file/" + path.split("/").map(encodeURIComponent).join("/");
    }
    return "";
  }

  function mediaNode(path) {
    const url = assetUrl(path);
    if (!url) return "";
    const lower = path.toLowerCase();
    if (/\.(mp4|mov|m4v|webm)$/i.test(lower)) {
      return `<video src="${url}" controls muted playsinline></video>`;
    }
    if (/\.(png|jpe?g|webp|gif)$/i.test(lower)) {
      return `<img src="${url}" alt="">`;
    }
    return `<a class="crt-btn" href="${url}" target="_blank" rel="noreferrer">打开输出</a>`;
  }

  function isPreviewableMedia(path) {
    return /\.(mp4|mov|m4v|webm|png|jpe?g|webp|gif)$/i.test(String(path || "").split("?")[0]);
  }

  function reviewFor(passId) {
    const notes = Array.isArray(state.task?.review_notes) ? state.task.review_notes : [];
    return notes.find((item) => item && item.render_pass_id === passId) || null;
  }

  function feedbackFor(passId) {
    const feedbacks = Array.isArray(state.task?.human_feedback) ? state.task.human_feedback : [];
    return feedbacks.filter((item) => item && String(item.render_pass_id || "") === String(passId || ""));
  }

  function revisionPlanFor(passId) {
    const events = Array.isArray(state.task?.agent_events) ? state.task.agent_events : [];
    return events.filter((item) => (
      item
      && String(item.phase || "") === "revision_plan"
      && String(item.render_pass_id || "") === String(passId || "")
    )).pop() || null;
  }

  function logsForTask() {
    const logs = Array.isArray(state.task?.execution_logs) ? state.task.execution_logs : [];
    if (logs.length) return logs;
    const events = Array.isArray(state.task?.agent_events) ? state.task.agent_events : [];
    return events.map((event, index) => ({
      index: index + 1,
      timestamp: event?.created_at || "",
      source: event?.voice || "lumeri",
      phase: event?.phase || "event",
      status: event?.status || "",
      label: event?.label || "",
      message: event?.body || event?.meta || event?.detail || "",
      raw: event || {},
    }));
  }

  function renderLogLine(log, index) {
    const ts = String(log.timestamp || "").replace("T", " ").replace(/\.\d+.*$/, "Z");
    const source = String(log.source || "lumeri");
    const phase = String(log.phase || "event");
    const status = String(log.status || "");
    const label = String(log.label || "");
    const message = String(log.message || log.detail || log.meta || "");
    const raw = log.raw ? "\n" + JSON.stringify(log.raw, null, 2) : "";
    return `<span class="crt-log-line"><span class="crt-log-ts">#${index + 1} ${escapeHtml(ts)}</span> <span class="crt-log-source">${escapeHtml(source)}</span> <span class="crt-log-phase">${escapeHtml(phase)}</span> ${escapeHtml(status)}\n${escapeHtml(label)}${message ? "\n" + escapeHtml(message) : ""}${escapeHtml(raw)}</span>`;
  }

  function renderReportCard() {
    const report = state.task?.agent_report || state.task?.report || {};
    const brief = report?.brief || {};
    const summary = report?.summary || {};
    if (!brief.title && !brief.summary && !summary.status) return "";
    const primaryPath = String(brief.primary_path || "");
    const chips = [
      summary.status ? `status ${summary.status}` : "",
      Number.isFinite(summary.log_count) ? `logs ${summary.log_count}` : "",
      Number.isFinite(summary.failure_count) && summary.failure_count ? `failures ${summary.failure_count}` : "",
      Number.isFinite(summary.output_count) ? `previews ${summary.output_count}` : "",
      Number.isFinite(summary.artifact_count) && summary.artifact_count ? `artifacts ${summary.artifact_count}` : "",
    ].filter(Boolean);
    const primaryUrl = isPreviewableMedia(primaryPath) ? assetUrl(primaryPath) : "";
    const copyNote = state.copiedPath && state.copiedPath === primaryPath
      ? `<div class="crt-report-next">Copied path.</div>`
      : "";
    const actions = [
      primaryUrl ? `<a class="crt-btn" href="${primaryUrl}" target="_blank" rel="noreferrer">Open preview</a>` : "",
      primaryPath ? `<button class="crt-btn" data-copy-primary="${escapeHtml(primaryPath)}">Copy path</button>` : "",
    ].filter(Boolean).join("");
    return `<section class="crt-report">
      <div class="crt-report-title">
        <span>Agent report</span>
        <span class="crt-report-state">${escapeHtml(brief.state || summary.status || "unknown")}</span>
      </div>
      <div class="crt-report-summary">${escapeHtml(brief.summary || brief.title || "")}</div>
      ${primaryPath ? `<div class="crt-report-path">${escapeHtml(primaryPath)}</div>` : ""}
      ${actions ? `<div class="crt-actions">${actions}</div>` : ""}
      ${copyNote}
      ${brief.next_action ? `<div class="crt-report-next">${escapeHtml(brief.next_action)}</div>` : ""}
      ${chips.length ? `<div class="crt-report-stats">${chips.map((chip) => `<span class="crt-report-chip">${escapeHtml(chip)}</span>`).join("")}</div>` : ""}
    </section>`;
  }

  async function copyText(value) {
    const text = String(value || "");
    if (!text) return;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const input = document.createElement("textarea");
      input.value = text;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    state.copiedPath = text;
    render();
  }

  async function refreshTask(taskId) {
    const response = await fetch(`/task/${encodeURIComponent(taskId)}`);
    if (!response.ok) return null;
    const payload = await response.json();
    if (payload && Array.isArray(payload.render_passes)) {
      state.task = payload;
      return payload;
    }
    return null;
  }

  function taskIdFromSession(payload) {
    const taskId = String(payload?.creative_runtime_task_id || "").trim();
    return taskId || "";
  }

  async function hydrateSessionTask(payload) {
    const taskId = taskIdFromSession(payload);
    if (!taskId) {
      clearRuntimeTask();
      return;
    }
    if (String(state.task?.task_id || "") === taskId && Array.isArray(state.task?.render_passes)) {
      render();
      return;
    }
    await refreshTask(taskId);
    render();
  }

  function clearRuntimeTask() {
    state.task = null;
    state.feedbackFor = null;
    state.feedbackError = "";
    render();
  }

  function attachTaskToSessionSave(args) {
    const taskId = String(state.task?.task_id || "").trim();
    if (!taskId) return;
    try {
      const url = typeof args[0] === "string" ? args[0] : String(args[0]?.url || "");
      const pathname = new URL(url, location.href).pathname;
      const init = args[1] && typeof args[1] === "object" ? { ...args[1] } : {};
      const method = String(init.method || "GET").toUpperCase();
      if (pathname !== "/session-history" || method !== "POST") return;
      const body = typeof init.body === "string" && init.body ? JSON.parse(init.body) : {};
      body.creative_runtime_task_id = taskId;
      init.body = JSON.stringify(body);
      args[1] = init;
    } catch (_) {}
  }

  async function sendFeedback(taskId, passId, layerId, text) {
    const feedback = String(text || "").trim();
    if (!feedback) return;
    const response = await fetch(`/task/${encodeURIComponent(taskId)}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback, render_pass_id: passId || "", layer_id: layerId || "" }),
      });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {}
    if (!response.ok) {
      state.feedbackError = String(payload?.error || "反馈没有保存成功");
      render();
      return;
    }
    state.feedbackError = "";
    if (payload?.task && Array.isArray(payload.task.render_passes)) {
      state.task = payload.task;
    } else {
      await refreshTask(taskId);
    }
    state.feedbackFor = null;
    render();
  }

  function render() {
    let panel = document.getElementById("lumeri-crt-panel");
    if (!panel) {
      panel = document.createElement("aside");
      panel.id = "lumeri-crt-panel";
      document.body.appendChild(panel);
    }
    const passes = Array.isArray(state.task?.render_passes) ? state.task.render_passes : [];
    const logs = logsForTask();
    const reportCard = renderReportCard();
    if (!state.task || (passes.length === 0 && logs.length === 0 && !reportCard)) {
      panel.className = "";
      panel.innerHTML = "";
      return;
    }
    panel.className = "visible" + (state.open ? "" : " collapsed");
    const cards = passes.slice(-6).reverse().map((pass) => {
      const passId = String(pass.render_pass_id || "");
      const rawOutput = String(pass.output_path || "");
      const output = String(pass.preview_path || (isPreviewableMedia(rawOutput) ? rawOutput : "") || pass.artifact_path || rawOutput);
      const note = reviewFor(passId);
      const feedbacks = feedbackFor(passId).slice(-3);
      const revision = revisionPlanFor(passId);
      const layerId = Array.isArray(pass.layer_ids) && pass.layer_ids.length ? String(pass.layer_ids[0]) : "";
      const inputId = "crt-feedback-" + passId;
      const feedback = state.feedbackFor === passId
        ? `<div class="crt-feedback"><input id="${inputId}" placeholder="直接说哪里不对"><button class="crt-btn" data-send="${passId}" data-layer="${layerId}">发送</button></div>`
        : "";
      const feedbackNotes = feedbacks.length
        ? `<div class="crt-feedback-note">${feedbacks.map((item) => `已收到反馈：${escapeHtml(item.feedback || "")}`).join("<br>")}</div>`
        : "";
      const revisionNote = revision?.body
        ? `<div class="crt-feedback-note">${escapeHtml(revision.body)}</div>`
        : "";
      const errorNote = state.feedbackError
        ? `<div class="crt-feedback-error">${escapeHtml(state.feedbackError)}</div>`
        : "";
      return `
        <section class="crt-card">
          ${mediaNode(output)}
          <div class="crt-row"><span class="crt-kind">${escapeHtml(pass.kind || "preview")}</span><span class="crt-file">${escapeHtml(fileName(output))}</span></div>
          ${note?.note ? `<div class="crt-note">${escapeHtml(note.note)}</div>` : ""}
          ${feedbackNotes}
          ${revisionNote}
          ${errorNote}
          <div class="crt-actions"><button class="crt-btn" data-feedback="${passId}">反馈这一段</button></div>
          ${feedback}
        </section>
      `;
    }).join("");
    const logBlock = logs.length
      ? `<section class="crt-log-box">
          <div class="crt-log-head">Gemini / execution logs <span class="crt-log-count">${logs.length} entries</span></div>
          <pre class="crt-log-stream">${logs.map(renderLogLine).join("")}</pre>
        </section>`
      : "";
    panel.innerHTML = `
      <div class="crt-head" data-toggle="1">
        <span class="crt-dot"></span>
        <span class="crt-title">Creative Runtime · Logs</span>
        <span class="crt-sub">${passes.length} 个小样 · ${logs.length} logs</span>
      </div>
      <div class="crt-body">${reportCard}${logBlock}${cards}</div>
    `;
    const stream = panel.querySelector(".crt-log-stream");
    if (stream) stream.scrollTop = stream.scrollHeight;
    panel.querySelector("[data-toggle]")?.addEventListener("click", () => {
      state.open = !state.open;
      render();
    });
    panel.querySelectorAll("[data-copy-primary]").forEach((node) => {
      node.addEventListener("click", () => {
        copyText(node.getAttribute("data-copy-primary") || "").catch(() => {});
      });
    });
    panel.querySelectorAll("[data-feedback]").forEach((node) => {
      node.addEventListener("click", () => {
        state.feedbackFor = node.getAttribute("data-feedback");
        render();
      });
    });
    panel.querySelectorAll("[data-send]").forEach((node) => {
      node.addEventListener("click", () => {
        const passId = node.getAttribute("data-send") || "";
        const input = document.getElementById("crt-feedback-" + passId);
        sendFeedback(state.task.task_id, passId, node.getAttribute("data-layer") || "", input?.value || "");
      });
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async function (...args) {
    attachTaskToSessionSave(args);
    const response = await originalFetch(...args);
    try {
      const url = typeof args[0] === "string" ? args[0] : String(args[0]?.url || "");
      const pathname = new URL(url, location.href).pathname;
      if (/\/task\/[^/]+$/.test(pathname)) {
        response.clone().json().then((payload) => {
          if (payload && Array.isArray(payload.render_passes)) {
            state.task = payload;
            render();
          }
        }).catch(() => {});
      }
      if (pathname === "/session-history" || /^\/session-history\/[^/]+$/.test(pathname)) {
        response.clone().json().then(hydrateSessionTask).catch(() => {});
      }
    } catch (_) {}
    return response;
  };
  window.addEventListener("lumeri:new-session", clearRuntimeTask);
  window.__lumeriCreativeRuntimeClear = clearRuntimeTask;
})();
