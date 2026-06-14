import { useState, useEffect, useRef, useCallback } from "react";

interface TimelineClip {
  id: string;
  name: string;
  start: number;
  duration: number;
  media_kind: string;
  track_id: string;
  enabled: boolean;
  text_config: { content: string; font_size: number; color: string } | null;
}

interface TimelineTrack {
  id: string;
  kind: "video" | "overlay" | "audio";
  name: string;
  clips: TimelineClip[];
}

interface ProjectTimeline {
  session_id: string;
  project_id: string;
  patch_seq: number;
  duration: number;
  fps: number;
  width: number;
  height: number;
  tracks: TimelineTrack[];
}

interface Props {
  videoRef: React.RefObject<HTMLVideoElement>;
  hasVideo: boolean;
  sessionId?: string;
  serverBase?: string;
}

function fmt(s: number) {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function clipColor(kind: string): { bg: string; border: string; text: string } {
  switch (kind) {
    case "video":
      return { bg: "rgba(0,200,100,0.25)", border: "#0a7a40", text: "#40e090" };
    case "image":
      return { bg: "rgba(100,100,255,0.25)", border: "#3030aa", text: "#8888ff" };
    case "text":
      return { bg: "rgba(255,150,50,0.25)", border: "#aa5500", text: "#ffaa44" };
    default:
      return { bg: "rgba(120,120,120,0.2)", border: "#444", text: "#aaa" };
  }
}

function trackBg(kind: string): string {
  switch (kind) {
    case "video": return "rgba(0,255,100,0.04)";
    case "overlay": return "rgba(100,100,255,0.04)";
    default: return "rgba(255,255,255,0.02)";
  }
}

const TRACK_HEIGHT = 32;
const RULER_HEIGHT = 18;
const MIN_DISPLAY_SEC = 3;

export default function Timeline({ videoRef, hasVideo, sessionId, serverBase }: Props) {
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDuration, setVideoDuration] = useState(0);
  const [projectTimeline, setProjectTimeline] = useState<ProjectTimeline | null>(null);
  const [lastPatchSeq, setLastPatchSeq] = useState(-1);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  // Poll project timeline from server when sessionId is available
  useEffect(() => {
    if (!sessionId || !serverBase) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${serverBase}/sessions/${sessionId}/timeline`);
        if (!res.ok) return;
        const data: ProjectTimeline = await res.json();
        if (!cancelled) {
          setProjectTimeline(data);
          setLastPatchSeq(data.patch_seq);
        }
      } catch { /* network error, retry later */ }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(id); };
  }, [sessionId, serverBase]);

  // Also poll when a new patch arrives (faster re-fetch after edit)
  useEffect(() => {
    if (!sessionId || !serverBase || lastPatchSeq < 0) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const res = await fetch(`${serverBase}/sessions/${sessionId}/timeline`);
        if (!res.ok || cancelled) return;
        const data: ProjectTimeline = await res.json();
        if (!cancelled && data.patch_seq !== lastPatchSeq) {
          setProjectTimeline(data);
          setLastPatchSeq(data.patch_seq);
        }
      } catch { /* ignore */ }
    };
    const id = setTimeout(refresh, 300);
    return () => { cancelled = true; clearTimeout(id); };
  }, [lastPatchSeq, sessionId, serverBase]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => setCurrentTime(v.currentTime);
    const onMeta = () => setVideoDuration(isFinite(v.duration) ? v.duration : 0);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("loadedmetadata", onMeta);
    return () => {
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("loadedmetadata", onMeta);
    };
  }, [videoRef]);

  const effectiveDuration = Math.max(
    projectTimeline?.duration || 0,
    videoDuration,
    MIN_DISPLAY_SEC,
  );

  const seekToX = useCallback((clientX: number) => {
    const el = trackRef.current;
    if (!el || !effectiveDuration) return;
    const rect = el.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const t = ratio * effectiveDuration;
    setCurrentTime(t);
    if (videoRef.current) videoRef.current.currentTime = t;
  }, [effectiveDuration, videoRef]);

  function handleMouseDown(e: React.MouseEvent) {
    dragging.current = true;
    seekToX(e.clientX);
    const onMove = (ev: MouseEvent) => { if (dragging.current) seekToX(ev.clientX); };
    const onUp = () => {
      dragging.current = false;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  const pct = effectiveDuration > 0 ? Math.min((currentTime / effectiveDuration) * 100, 100) : 0;
  const ticks = buildRulerTicks(effectiveDuration);
  const hasTracks = (projectTimeline?.tracks ?? []).some(t => t.clips.length > 0);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        background: "var(--surface)",
        borderTop: "1px solid var(--border)",
        borderBottom: "1px solid var(--border)",
        flexShrink: 0,
        userSelect: "none",
      }}
    >
      {/* Time display row */}
      <div style={{ display: "flex", alignItems: "center", padding: "4px 12px 0", gap: 8 }}>
        <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--accent)", minWidth: 34 }}>
          {fmt(currentTime)}
        </span>
        <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text3)" }}>/</span>
        <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text3)" }}>
          {fmt(effectiveDuration)}
        </span>
        {projectTimeline && (
          <span style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text3)", marginLeft: "auto" }}>
            {projectTimeline.width}×{projectTimeline.height} · {projectTimeline.fps}fps · seq {projectTimeline.patch_seq}
          </span>
        )}
      </div>

      {/* Ruler + track area */}
      <div
        ref={trackRef}
        onMouseDown={handleMouseDown}
        style={{ position: "relative", margin: "4px 12px 6px", cursor: "pointer" }}
      >
        {/* Time ruler */}
        <div style={{ position: "relative", height: RULER_HEIGHT, marginBottom: 2 }}>
          {ticks.map(({ pct: tp, label }) => (
            <div key={tp} style={{ position: "absolute", left: `${tp}%`, top: 0, transform: "translateX(-50%)" }}>
              <div style={{ width: 1, height: 4, background: "var(--border2)", margin: "0 auto" }} />
              <div style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text3)", marginTop: 1, whiteSpace: "nowrap" }}>
                {label}
              </div>
            </div>
          ))}
        </div>

        {/* Track rows: show real project tracks when available */}
        {hasTracks ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {(projectTimeline?.tracks ?? []).map(track => (
              <TrackRow
                key={track.id}
                track={track}
                duration={effectiveDuration}
                pct={pct}
              />
            ))}
          </div>
        ) : (
          /* Fallback: single empty track when no project data */
          <div
            style={{
              position: "relative",
              height: TRACK_HEIGHT,
              borderRadius: 3,
              overflow: "hidden",
              background: "linear-gradient(180deg, #1a2a1a 0%, #0d1a0d 100%)",
              border: "1px solid #2a3a2a",
              opacity: hasVideo ? 1 : 0.3,
            }}
          >
            <EmptyTrackWave pct={pct} />
          </div>
        )}

        {/* Playhead needle — spans all track rows */}
        <div
          style={{
            position: "absolute",
            left: `${pct}%`,
            top: 0,
            bottom: 0,
            width: 2,
            background: "var(--accent)",
            borderRadius: 1,
            pointerEvents: "none",
            boxShadow: "0 0 6px var(--accent)",
            transform: "translateX(-50%)",
          }}
        />
        <div
          style={{
            position: "absolute",
            left: `${pct}%`,
            top: -4,
            width: 8,
            height: 8,
            background: "var(--accent)",
            borderRadius: "50%",
            transform: "translateX(-50%)",
            pointerEvents: "none",
            boxShadow: "0 0 4px var(--accent)",
          }}
        />
      </div>
    </div>
  );
}

function TrackRow({ track, duration, pct }: { track: TimelineTrack; duration: number; pct: number }) {
  const bg = trackBg(track.kind);
  return (
    <div style={{ display: "flex", alignItems: "stretch", gap: 0 }}>
      {/* Track label */}
      <div
        style={{
          width: 48,
          flexShrink: 0,
          background: "var(--surface2)",
          borderRadius: "3px 0 0 3px",
          border: "1px solid var(--border)",
          borderRight: "none",
          display: "flex",
          alignItems: "center",
          padding: "0 4px",
        }}
      >
        <span style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {track.id}
        </span>
      </div>

      {/* Clip strip */}
      <div
        style={{
          flex: 1,
          position: "relative",
          height: TRACK_HEIGHT,
          borderRadius: "0 3px 3px 0",
          overflow: "hidden",
          background: bg,
          border: "1px solid var(--border)",
        }}
      >
        {track.clips.map(clip => (
          <ClipBlock key={clip.id} clip={clip} duration={duration} />
        ))}
      </div>
    </div>
  );
}

function ClipBlock({ clip, duration }: { clip: TimelineClip; duration: number }) {
  if (!duration) return null;
  const left = (clip.start / duration) * 100;
  const width = Math.max((clip.duration / duration) * 100, 0.5);
  const { bg, border, text } = clipColor(clip.media_kind);
  const label = clip.media_kind === "text"
    ? (clip.text_config?.content?.slice(0, 20) ?? clip.name)
    : clip.name;

  return (
    <div
      title={`${clip.name} (${clip.media_kind}) ${clip.start.toFixed(2)}s–${(clip.start + clip.duration).toFixed(2)}s`}
      style={{
        position: "absolute",
        left: `${left}%`,
        width: `${width}%`,
        top: 2,
        bottom: 2,
        background: bg,
        border: `1px solid ${border}`,
        borderRadius: 2,
        display: "flex",
        alignItems: "center",
        overflow: "hidden",
        opacity: clip.enabled ? 1 : 0.4,
      }}
    >
      <span
        style={{
          fontSize: 8,
          fontFamily: "var(--font-mono)",
          color: text,
          padding: "0 3px",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          pointerEvents: "none",
        }}
      >
        {label}
      </span>
    </div>
  );
}

function EmptyTrackWave({ pct }: { pct: number }) {
  return (
    <>
      <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", gap: 0 }}>
        {Array.from({ length: 120 }, (_, i) => (
          <div
            key={i}
            style={{
              flex: 1,
              height: `${(0.3 + ((((i * 1664525 + 1013904223) & 0xffffffff) >>> 0) / 0xffffffff) * 0.7) * 70}%`,
              background: i / 120 < pct / 100 ? "rgba(0,255,100,0.55)" : "rgba(0,255,100,0.18)",
              borderRadius: 1,
            }}
          />
        ))}
      </div>
      <div
        style={{
          position: "absolute",
          left: 0, top: 0, bottom: 0,
          width: `${pct}%`,
          background: "rgba(0,200,80,0.06)",
          pointerEvents: "none",
        }}
      />
    </>
  );
}

function buildRulerTicks(duration: number): { pct: number; label: string }[] {
  if (!duration || !isFinite(duration) || duration <= 0) return [];
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300];
  const step = steps.find(s => duration / s <= 8) ?? 300;
  const ticks: { pct: number; label: string }[] = [];
  for (let t = 0; t <= duration; t += step) {
    ticks.push({ pct: (t / duration) * 100, label: fmt(t) });
  }
  return ticks;
}
