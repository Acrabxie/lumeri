import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { invoke } from "@tauri-apps/api/core";
import VideoPreview from "./components/VideoPreview";
import Timeline from "./components/Timeline";
import QuickActions from "./components/QuickActions";
import ChatPanel from "./components/ChatPanel";
import SkillsPanel from "./components/SkillsPanel";
import MediaHistorySidebar from "./components/MediaHistorySidebar";
import type { AppStatus, AskQuestion, ChatMessage, MediaAsset, MediaKind, ProjectAsset, Skill } from "./types";
import { useEditor } from "./lib/projectStore";
import DevPanel from "./dev/DevPanel"; // [DEV] remove this line to disable dev panel
import { friendlyError } from "./lib/errorMap";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let _mid = 0;
const newId = () => `m${++_mid}_${Date.now()}`;
const PLAYABLE_VIDEO_RE = /\.(mp4|mov|m4v|webm)$/i;
const firstPlayableVideoOutput = (outputs?: string[]) =>
  Array.isArray(outputs) ? outputs.find((output) => PLAYABLE_VIDEO_RE.test(String(output).split("?")[0])) : undefined;
const basename = (value: string) => value.split(/[\\/]/).pop() || value;
const SERVER_FILE_RE = /(?:^|\/)(outputs|frames|styled|demo|inputs|uploads|temp|timeline)\/(.+)$/;
const serverRelativeOutputPath = (value: string) => {
  const match = value.replace(/\\/g, "/").match(SERVER_FILE_RE);
  return match ? `${match[1]}/${match[2]}` : `outputs/${basename(value)}`;
};

const MIME_BY_EXT: Record<string, string> = {
  mp4: "video/mp4",
  mov: "video/quicktime",
  m4v: "video/x-m4v",
  webm: "video/webm",
  mkv: "video/x-matroska",
  avi: "video/x-msvideo",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  gif: "image/gif",
  flac: "audio/flac",
  wav: "audio/wav",
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  aac: "audio/aac",
};
const extOf = (name: string) => name.split(".").pop()?.toLowerCase() ?? "";
const mimeFromName = (name: string) => MIME_BY_EXT[extOf(name)] ?? "application/octet-stream";
const kindFromName = (name: string): MediaKind => {
  const ext = extOf(name);
  if (["png", "jpg", "jpeg", "webp", "gif", "bmp"].includes(ext)) return "image";
  if (["flac", "wav", "mp3", "m4a", "aac", "ogg"].includes(ext)) return "audio";
  return "video";
};

type ApiResult = { ok: boolean; status: number; data: Record<string, unknown> };

const humanErrorMessage = (raw: unknown) => friendlyError(raw);
const taskArtifactSummary = (task: Record<string, unknown>, playableOutput?: string) => {
  const lines: string[] = [];
  if (playableOutput) {
    lines.push(`preview: ${basename(playableOutput)}`);
  }

  const artifacts = Array.isArray(task.artifact_outputs)
    ? task.artifact_outputs
    : Array.isArray(task.artifacts)
    ? task.artifacts
    : [];
  const artifactNames = artifacts
    .map((artifact) => {
      if (typeof artifact === "string") return basename(artifact);
      if (artifact && typeof artifact === "object") {
        const record = artifact as Record<string, unknown>;
        const path = record.path ?? record.output_path ?? record.artifact_path ?? record.name;
        return typeof path === "string" ? basename(path) : "";
      }
      return "";
    })
    .filter(Boolean)
    .slice(0, 3);
  if (artifactNames.length) {
    lines.push(`artifacts: ${artifactNames.join(", ")}`);
  }

  return lines.length ? `完成 ✓\n${lines.join("\n")}` : "完成 ✓";
};
const taskUserMessage = (task: Record<string, unknown>, fallback: string) => {
  if (typeof task.user_message === "string" && task.user_message) return task.user_message;
  const events = Array.isArray(task.agent_events) ? task.agent_events : [];
  const last = [...events].reverse().find((event) => typeof event === "object" && event !== null) as
    | Record<string, unknown>
    | undefined;
  if (last) {
    if (typeof last.user_message === "string" && last.user_message) return last.user_message;
    if (typeof last.body === "string" && last.body) return last.body;
  }
  return fallback;
};

export default function App() {
  const [status, setStatus] = useState<AppStatus>("starting");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [videoSrc, setVideoSrc] = useState<string | null>(null);
  const [serverVideoPath, setServerVideoPath] = useState<string | null>(null);
  const [pendingAskId, setPendingAskId] = useState<string | null>(null);
  const [pendingAskQuestions, setPendingAskQuestions] = useState<AskQuestion[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [devPanelOpen, setDevPanelOpen] = useState(false); // [DEV]

  // ── Editor / media pool state ──────────────────────────────────────────
  const editor = useEditor();
  const [assets, setAssets] = useState<ProjectAsset[]>([]);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null!);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Helpers ───────────────────────────────────────────────────────────

  const api = useCallback(async (method: string, path: string, body?: unknown): Promise<ApiResult> => {
    const raw = await invoke<{ status: number; body: string }>("api_call", {
      method,
      path,
      body: body != null ? JSON.stringify(body) : null,
    });
    return { ok: raw.status < 400, status: raw.status, data: JSON.parse(raw.body) };
  }, []);

  const addMsg = useCallback((msg: Omit<ChatMessage, "id" | "timestamp">) => {
    const full: ChatMessage = { ...msg, id: newId(), timestamp: Date.now() };
    setMessages((prev) => [...prev, full]);
    return full.id;
  }, []);

  const updateLastStatus = useCallback((content: string, statusType: AppStatus) => {
    setMessages((prev) => {
      const idx = [...prev].reverse().findIndex((m) => m.role === "status");
      if (idx === -1) return prev;
      const at = prev.length - 1 - idx;
      const next = [...prev];
      next[at] = { ...next[at], content, statusType };
      return next;
    });
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  function b64ToBlobUrl(b64: string, mime: string): string {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return URL.createObjectURL(new Blob([bytes], { type: mime }));
  }

  const setPreviewSource = useCallback((src: string) => {
    setVideoSrc(src);
    if (videoRef.current) {
      videoRef.current.src = src;
      videoRef.current.load();
    }
  }, []);

  async function loadVideoPreview(relPath: string) {
    try {
      const b64 = await invoke<string>("fetch_video_b64", { serverRelPath: relPath });
      setPreviewSource(b64ToBlobUrl(b64, "video/mp4"));
    } catch {
      // preview failed, non-fatal
    }
  }

  // Probe duration of a blob media element (best-effort).
  function probeDuration(url: string, kind: MediaKind): Promise<number> {
    return new Promise((resolve) => {
      if (kind === "image") return resolve(4);
      const el = document.createElement(kind === "audio" ? "audio" : "video");
      el.preload = "metadata";
      el.src = url;
      const done = (v: number) => resolve(isFinite(v) && v > 0 ? v : 0);
      el.addEventListener("loadedmetadata", () => done(el.duration), { once: true });
      el.addEventListener("error", () => resolve(0), { once: true });
      setTimeout(() => resolve(0), 8000);
    });
  }

  // ── Media import (drives both the media pool and the timeline) ──────────
  const selectAsset = useCallback(
    (asset: ProjectAsset) => {
      setSelectedAssetId(asset.asset_id);
      setServerVideoPath(asset.source_path);
      if (asset.preview_src && asset.media_kind !== "audio") {
        setPreviewSource(asset.preview_src);
      }
    },
    [setPreviewSource],
  );

  const importMedia = useCallback(
    async (srcPath: string, opts: { select?: boolean; addToTimeline?: boolean } = {}): Promise<ProjectAsset | null> => {
      const raw = await invoke<{ status: number; body: string }>("upload_media", { srcPath });
      const data = JSON.parse(raw.body) as Record<string, unknown>;
      if (raw.status >= 400) throw new Error((data.error as string) ?? "上传失败");
      const name = (data.name as string) ?? basename(srcPath);
      const serverPath = (data.path as string) ?? `inputs/${name}`;
      const rel = `inputs/${name}`;
      const kind = kindFromName(name);

      let previewSrc: string | null = null;
      try {
        const b64 = await invoke<string>("fetch_video_b64", { serverRelPath: rel });
        previewSrc = b64ToBlobUrl(b64, mimeFromName(name));
      } catch {
        // preview/thumbnail extraction will simply be unavailable
      }
      const duration = previewSrc ? await probeDuration(previewSrc, kind) : 0;

      const asset: ProjectAsset = {
        id: serverPath,
        asset_id: serverPath,
        name,
        media_kind: kind,
        mime_type: mimeFromName(name),
        source_path: serverPath,
        preview_src: previewSrc,
        thumbnail_src: null,
        duration,
        metadata: {},
        created_at: new Date().toISOString(),
      };
      setAssets((prev) => [asset, ...prev.filter((a) => a.asset_id !== asset.asset_id)]);
      if (opts.select) selectAsset(asset);
      if (opts.addToTimeline) editor.dispatch({ type: "ADD_ASSET", asset, previewSrc });
      return asset;
    },
    [editor, selectAsset],
  );

  const handleUploadSources = useCallback(
    async (sources: Array<string | File>) => {
      const paths = sources.filter((s): s is string => typeof s === "string");
      let first = true;
      for (const p of paths) {
        try {
          await importMedia(p, { select: first });
          first = false;
        } catch (e) {
          addMsg({ role: "status", content: `导入失败: ${e instanceof Error ? e.message : String(e)}`, statusType: "error" });
        }
      }
    },
    [importMedia, addMsg],
  );

  const handleSelectAsset = useCallback(
    (assetId: string) => {
      const asset = assets.find((a) => a.asset_id === assetId);
      if (asset) selectAsset(asset);
    },
    [assets, selectAsset],
  );

  const handleAddAssetToTimeline = useCallback(
    (assetId: string) => {
      const asset = assets.find((a) => a.asset_id === assetId);
      if (asset) editor.dispatch({ type: "ADD_ASSET", asset, previewSrc: asset.preview_src });
    },
    [assets, editor],
  );

  const handleDeleteAsset = useCallback(
    (assetId: string) => {
      setAssets((prev) => {
        const target = prev.find((a) => a.asset_id === assetId);
        if (target?.preview_src?.startsWith("blob:")) URL.revokeObjectURL(target.preview_src);
        return prev.filter((a) => a.asset_id !== assetId);
      });
      setSelectedAssetId((cur) => (cur === assetId ? null : cur));
    },
    [],
  );

  // Adapt local ProjectAsset[] to the media pool's MediaAsset[] shape.
  const poolAssets = useMemo<MediaAsset[]>(
    () =>
      assets.map((a) => ({
        id: a.id,
        asset_id: a.asset_id,
        name: a.name,
        media_kind: a.media_kind,
        preview_src: a.preview_src ?? undefined,
        thumbnail_src: a.thumbnail_src ?? undefined,
        thumbnails: a.thumbnails,
        duration: a.duration,
        status: "ready",
      })),
    [assets],
  );

  // ── Dev panel toggle (Ctrl+Shift+D) ──────────────────────────────── [DEV]
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === "D") {
        e.preventDefault();
        setDevPanelOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  // ── End dev panel toggle ─────────────────────────────────────────────

  // ── Boot ──────────────────────────────────────────────────────────────

  useEffect(() => {
    (async () => {
      for (let i = 0; i < 40; i++) {
        try {
          const raw = await invoke<{ status: number; body: string }>("api_call", {
            method: "GET",
            path: "/skills",
            body: null,
          });
          if (raw.status < 500) {
            setStatus("ready");
            const data = JSON.parse(raw.body);
            if (data.skills) {
              setSkills((data.skills as string[]).map((name) => ({ name })));
            }
            return;
          }
        } catch {
          // still starting
        }
        await sleep(500);
      }
      setStatus("error");
    })();
  }, []);

  // ── Video upload (main preview drop/pick) ───────────────────────────────

  const handleVideoSelect = useCallback(
    async (filePath: string) => {
      try {
        await importMedia(filePath, { select: true, addToTimeline: true });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        addMsg({ role: "status", content: `上传失败: ${msg}`, statusType: "error" });
      }
    },
    [importMedia, addMsg],
  );

  // ── Polling ───────────────────────────────────────────────────────────

  const startPolling = useCallback(
    (taskId: string) => {
      pollRef.current = setInterval(async () => {
        try {
          const r = await api("GET", `/task/${taskId}`);
          const taskStatus = r.data.status as string;
          const outputs = r.data.outputs as string[] | undefined;

          if (taskStatus === "succeeded" || taskStatus === "preview_ready") {
            stopPolling();
            setStatus("done");
            setIsRunning(false);
            const playableOutput = firstPlayableVideoOutput(outputs);
            if (playableOutput) {
              updateLastStatus(taskArtifactSummary(r.data, playableOutput), "done");
              await loadVideoPreview(serverRelativeOutputPath(playableOutput));
              if (videoRef.current) videoRef.current.play().catch(() => {});
            } else {
              updateLastStatus(taskUserMessage(r.data, "生成了小样记录，但没有新的可播放视频。"), "done");
            }
          } else if (taskStatus === "artifact_ready") {
            stopPolling();
            setStatus("done");
            setIsRunning(false);
            updateLastStatus(taskUserMessage(r.data, "生成了文档或计划，没有可播放视频。"), "done");
          } else if (taskStatus === "failed") {
            stopPolling();
            setStatus("error");
            setIsRunning(false);
            updateLastStatus(taskUserMessage(r.data, "这一步没有完成。"), "error");
          }
        } catch {
          // ignore transient errors
        }
      }, 2000);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [api, stopPolling, updateLastStatus],
  );

  // ── Run prompt ────────────────────────────────────────────────────────

  const handleAnswerAsk = useCallback(
    async (answers: Record<string, string>) => {
      if (!pendingAskId) return;
      const askId = pendingAskId;
      setPendingAskId(null);
      setPendingAskQuestions([]);
      setIsRunning(true);
      setStatus("planning");
      const summary = Object.values(answers).join(" / ");
      addMsg({ role: "user", content: summary });
      addMsg({ role: "status", content: "正在规划...", statusType: "planning" });
      try {
        const r = await api("POST", `/answer-ask/${askId}`, { answers });
        if (r.data.ask) {
          setStatus("asking");
          setIsRunning(false);
          setPendingAskId(r.data.ask_id as string);
          setPendingAskQuestions((r.data.questions as AskQuestion[]) ?? []);
          updateLastStatus("需要更多信息", "asking");
          return;
        }
        if (!r.ok || !r.data.task_id) throw r.data;
        setStatus("executing");
        updateLastStatus("正在执行...", "executing");
        startPolling(r.data.task_id as string);
      } catch (e) {
        const msg = humanErrorMessage(e);
        setStatus("error");
        setIsRunning(false);
        updateLastStatus(msg, "error");
      }
    },
    [pendingAskId, addMsg, api, updateLastStatus, startPolling],
  );

  const handleSend = useCallback(
    async (text: string) => {
      if (!serverVideoPath) return;
      stopPolling();
      setIsRunning(true);
      setStatus("planning");
      addMsg({ role: "user", content: text });
      addMsg({ role: "status", content: "正在规划...", statusType: "planning" });
      try {
        const r = await api("POST", "/run-prompt", { prompt: text, video: serverVideoPath, stream_logs: true });
        if (r.data.ask) {
          setStatus("asking");
          setIsRunning(false);
          setPendingAskId(r.data.ask_id as string);
          setPendingAskQuestions((r.data.questions as AskQuestion[]) ?? []);
          updateLastStatus("需要更多信息", "asking");
          return;
        }
        if (!r.ok || !r.data.task_id) throw r.data;
        setStatus("executing");
        updateLastStatus("正在执行...", "executing");
        startPolling(r.data.task_id as string);
      } catch (e) {
        const msg = humanErrorMessage(e);
        setStatus("error");
        setIsRunning(false);
        updateLastStatus(msg, "error");
      }
    },
    [serverVideoPath, addMsg, api, stopPolling, updateLastStatus, startPolling],
  );

  // ── Run skill ─────────────────────────────────────────────────────────

  const handleRunSkill = useCallback(
    async (skillName: string) => {
      if (!serverVideoPath || isRunning) return;
      setIsRunning(true);
      setStatus("executing");
      addMsg({ role: "user", content: `▶ ${skillName}` });
      addMsg({ role: "status", content: "正在执行...", statusType: "executing" });
      try {
        const r = await api("POST", "/run-skill", {
          skill_id: skillName,
          inputs: { video: serverVideoPath },
        });
        if (!r.ok || !r.data.task_id) throw r.data;
        startPolling(r.data.task_id as string);
      } catch (e) {
        const msg = humanErrorMessage(e);
        setStatus("error");
        setIsRunning(false);
        updateLastStatus(msg, "error");
      }
    },
    [serverVideoPath, isRunning, addMsg, api, startPolling, updateLastStatus],
  );

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      {/* [DEV] remove next line to disable dev panel */}
      <DevPanel visible={devPanelOpen} onClose={() => setDevPanelOpen(false)} />
      <AppHeader status={status} />

      {/* Body: column = upper workspace + bottom full-width timeline */}
      <div style={{ display: "flex", flexDirection: "column", flex: 1, height: 0, overflow: "hidden" }}>
        {/* Upper workspace */}
        <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
          {/* Left: media pool */}
          <MediaHistorySidebar
            assets={poolAssets}
            selectedAssetId={selectedAssetId}
            sessions={[]}
            disabled={false}
            onSelectAsset={handleSelectAsset}
            onAddAssetToTimeline={handleAddAssetToTimeline}
            onDeleteAsset={handleDeleteAsset}
            onUploadSources={handleUploadSources}
          />

          {/* Center: preview + quick actions */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, overflow: "hidden" }}>
            <div style={{ flex: 1, minHeight: 0, overflow: "hidden", background: "#000" }}>
              <VideoPreview videoRef={videoRef} videoSrc={videoSrc} onFileSelect={handleVideoSelect} />
            </div>
            <QuickActions
              serverVideoPath={serverVideoPath}
              isRunning={isRunning}
              onTaskStart={(taskId) => {
                setIsRunning(true);
                setStatus("executing");
                addMsg({ role: "status", content: "正在执行...", statusType: "executing" });
                startPolling(taskId);
              }}
              onError={(msg) => {
                setStatus("error");
                addMsg({ role: "status", content: humanErrorMessage(msg), statusType: "error" });
              }}
            />
          </div>

          {/* Right: chat */}
          <div style={{ width: 344, display: "flex", flexDirection: "column", minHeight: 0, borderLeft: "1px solid var(--border)" }}>
            <ChatPanel
              messages={messages}
              isRunning={isRunning}
              hasVideo={!!serverVideoPath}
              pendingAskId={pendingAskId}
              pendingAskQuestions={pendingAskQuestions}
              onSend={handleSend}
              onAnswerAsk={handleAnswerAsk}
            />
          </div>

          {/* Far right: skills */}
          <SkillsPanel skills={skills} hasVideo={!!serverVideoPath} isRunning={isRunning} onRunSkill={handleRunSkill} />
        </div>

        {/* Bottom: full-width timeline */}
        <div style={{ height: 324, flexShrink: 0, minHeight: 0 }}>
          <Timeline
            project={editor.project}
            dispatch={editor.dispatch}
            onUndo={editor.undo}
            onRedo={editor.redo}
            canUndo={editor.canUndo}
            canRedo={editor.canRedo}
            videoRef={videoRef}
            hasVideo={!!videoSrc}
          />
        </div>
      </div>
    </div>
  );
}

// ── Header ────────────────────────────────────────────────────────────────

const STATUS_INFO: Record<AppStatus, { label: string; color: string; pulse: boolean }> = {
  starting: { label: "启动中", color: "var(--text3)",  pulse: false },
  ready:    { label: "就绪",   color: "var(--text3)",  pulse: false },
  planning: { label: "规划中", color: "var(--accent)", pulse: true  },
  executing:{ label: "执行中", color: "var(--accent)", pulse: true  },
  done:     { label: "完成",   color: "#22dd77",       pulse: false },
  error:    { label: "错误",   color: "var(--error)",  pulse: false },
  asking:   { label: "等待",   color: "var(--warn)",   pulse: true  },
};

function AppHeader({ status }: { status: AppStatus }) {
  const info = STATUS_INFO[status];
  const isPulsing = info.pulse;

  return (
    <header
      style={{
        height: 42,
        background: "var(--surface)",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        padding: "0 14px",
        gap: 12,
        WebkitAppRegion: "drag",
        flexShrink: 0,
      } as React.CSSProperties}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 13,
          fontWeight: 500,
          color: "var(--accent)",
          letterSpacing: "0.2em",
          WebkitAppRegion: "no-drag",
        } as React.CSSProperties}
      >
        GEMIA
      </span>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          WebkitAppRegion: "no-drag",
        } as React.CSSProperties}
      >
        <div
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: info.color,
            flexShrink: 0,
            animation: isPulsing ? "pulse-dot 1s ease-in-out infinite" : "none",
          }}
        />
        <span
          style={{
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            color: info.color,
            letterSpacing: "0.04em",
          }}
        >
          {info.label}
        </span>
      </div>
    </header>
  );
}
