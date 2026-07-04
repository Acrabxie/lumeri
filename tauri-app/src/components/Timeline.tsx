import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { MediaClip, TimelineTrack, TrackKind } from "../types";
import type { EditorAction } from "../lib/projectStore";
import { extractFilmstrip } from "../lib/thumbnails";
import { extractWaveform } from "../lib/waveform";

// ── geometry ─────────────────────────────────────────────────────────────────
const HEADER_W = 138;
const RULER_H = 30;
const TRACK_H = 60;
const CLIP_INSET = 5;
const CLIP_H = TRACK_H - CLIP_INSET * 2;
const HANDLE_W = 9;
const MIN_PX = 12;
const MAX_PX = 600;
const TAIL_SEC = 4;
const MIN_TIMELINE_SEC = 16;
const FILMSTRIP_FRAMES = 14;

const STEP_CANDIDATES = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

interface TimelineProps {
  project: import("../types").ProjectState;
  dispatch: React.Dispatch<EditorAction>;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  videoRef: React.RefObject<HTMLVideoElement>;
  hasVideo: boolean;
}

interface DragState {
  mode: "move" | "trim-left" | "trim-right";
  clipId: string;
  startClientX: number;
  origStart: number;
  origDuration: number;
  origIn: number;
  origOut: number;
  origTrackId: string;
}

interface ClipPreview {
  clipId: string;
  start: number;
  duration: number;
  inPoint: number;
  outPoint: number;
  trackId: string;
}

// ── helpers ──────────────────────────────────────────────────────────────────
function chooseStep(px: number): number {
  for (const s of STEP_CANDIDATES) {
    if (s * px >= 68) return s;
  }
  return STEP_CANDIDATES[STEP_CANDIDATES.length - 1];
}

function fmtRuler(t: number, step: number): string {
  if (step < 1) return `${t.toFixed(step < 0.5 ? 2 : 1)}s`;
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtTimecode(t: number, fps: number): string {
  const total = Math.max(0, t);
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  const f = Math.floor((total - Math.floor(total)) * fps);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
}

function clipFill(kind: MediaClip["mediaKind"]): { bg: string; edge: string } {
  switch (kind) {
    case "audio":
      return { bg: "var(--clip-audio)", edge: "var(--clip-audio-edge)" };
    case "image":
      return { bg: "var(--clip-image)", edge: "#5a4a9a" };
    case "text":
      return { bg: "var(--clip-text)", edge: "var(--clip-text-edge)" };
    default:
      return { bg: "var(--clip-video)", edge: "var(--clip-video-edge)" };
  }
}

// ── component ────────────────────────────────────────────────────────────────
export default function Timeline({
  project,
  dispatch,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  videoRef,
  hasVideo,
}: TimelineProps) {
  const { tracks, clips, markers, fps } = project;

  const [px, setPx] = useState(project.zoom || 80);
  const [snap, setSnap] = useState(project.snapEnabled);
  const [playhead, setPlayhead] = useState(0);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [viewportW, setViewportW] = useState(800);

  const [preview, setPreview] = useState<ClipPreview | null>(null);
  const [snapGuideX, setSnapGuideX] = useState<number | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const headerInnerRef = useRef<HTMLDivElement>(null);
  const rulerRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const extractStarted = useRef<Set<string>>(new Set());

  const selectedId = project.selectedClipId;

  // ── derived geometry ──────────────────────────────────────────────────────
  const contentDuration = useMemo(() => {
    let end = MIN_TIMELINE_SEC;
    for (const c of clips) end = Math.max(end, c.start + c.duration);
    return end + TAIL_SEC;
  }, [clips]);

  const contentWidth = contentDuration * px;
  const tracksHeight = Math.max(tracks.length * TRACK_H, 3 * TRACK_H);

  const trackIndex = useCallback(
    (trackId: string) => tracks.findIndex((t) => t.id === trackId),
    [tracks],
  );

  const geomOf = useCallback(
    (clip: MediaClip) => {
      if (preview && preview.clipId === clip.id) {
        return { start: preview.start, duration: preview.duration, inPoint: preview.inPoint, outPoint: preview.outPoint, trackId: preview.trackId };
      }
      return { start: clip.start, duration: clip.duration, inPoint: clip.inPoint, outPoint: clip.outPoint, trackId: clip.trackId };
    },
    [preview],
  );

  // ── viewport size ──────────────────────────────────────────────────────────
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const update = () => setViewportW(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ── ruler drawing ──────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = rulerRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = viewportW;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.floor(RULER_H * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${RULER_H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const css = getComputedStyle(document.documentElement);
    const bg = css.getPropertyValue("--tl-header").trim() || "#161b22";
    const line = css.getPropertyValue("--tl-line").trim() || "#272d36";
    const textDim = css.getPropertyValue("--tl-text-dim").trim() || "#8b95a3";
    const textFaint = css.getPropertyValue("--tl-text-faint").trim() || "#5b6573";

    ctx.clearRect(0, 0, w, RULER_H);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, RULER_H);
    ctx.fillStyle = line;
    ctx.fillRect(0, RULER_H - 1, w, 1);

    const step = chooseStep(px);
    const minor = step / 5;
    const firstMajor = Math.floor(scrollLeft / px / step) * step;
    ctx.font = "10px ui-monospace, monospace";
    ctx.textBaseline = "middle";

    ctx.strokeStyle = textFaint;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    for (let t = firstMajor; t * px - scrollLeft < w + step * px; t += minor) {
      const x = Math.round(t * px - scrollLeft) + 0.5;
      if (x < -2 || x > w + 2) continue;
      ctx.moveTo(x, RULER_H - 6);
      ctx.lineTo(x, RULER_H - 1);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.strokeStyle = textDim;
    ctx.fillStyle = textDim;
    ctx.beginPath();
    for (let t = firstMajor; t * px - scrollLeft < w + step * px; t += step) {
      if (t < 0) continue;
      const x = Math.round(t * px - scrollLeft) + 0.5;
      ctx.moveTo(x, RULER_H - 11);
      ctx.lineTo(x, RULER_H - 1);
      ctx.fillText(fmtRuler(t, step), x + 4, RULER_H / 2 - 3);
    }
    ctx.stroke();

    for (const mk of markers) {
      const x = Math.round(mk.time * px - scrollLeft);
      if (x < -6 || x > w + 6) continue;
      ctx.fillStyle = mk.color;
      ctx.beginPath();
      ctx.moveTo(x, 2);
      ctx.lineTo(x + 5, 7);
      ctx.lineTo(x, 12);
      ctx.lineTo(x - 5, 7);
      ctx.closePath();
      ctx.fill();
    }
  }, [px, scrollLeft, viewportW, markers]);

  // ── scroll sync ────────────────────────────────────────────────────────────
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setScrollLeft(el.scrollLeft);
    if (headerInnerRef.current) headerInnerRef.current.scrollTop = el.scrollTop;
  }, []);

  // ── playhead follows preview video ─────────────────────────────────────────
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    let raf = 0;
    const tick = () => {
      setPlayhead(v.currentTime || 0);
      raf = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    const onPause = () => {
      cancelAnimationFrame(raf);
      setPlayhead(v.currentTime || 0);
    };
    const onTime = () => setPlayhead(v.currentTime || 0);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("seeked", onTime);
    v.addEventListener("timeupdate", onTime);
    return () => {
      cancelAnimationFrame(raf);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("seeked", onTime);
      v.removeEventListener("timeupdate", onTime);
    };
  }, [videoRef]);

  // keep playhead in view while playing
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || videoRef.current?.paused) return;
    const x = playhead * px;
    if (x < scrollLeft + 40) el.scrollLeft = Math.max(0, x - 40);
    else if (x > scrollLeft + el.clientWidth - 60) el.scrollLeft = x - el.clientWidth + 60;
  }, [playhead, px, scrollLeft, videoRef]);

  // ── frame / waveform extraction ────────────────────────────────────────────
  useEffect(() => {
    for (const clip of clips) {
      if (!clip.previewSrc) continue;
      const filmKey = `film:${clip.id}`;
      if (
        clip.mediaKind === "video" &&
        (!clip.thumbnailStrip || clip.thumbnailStrip.length === 0) &&
        !extractStarted.current.has(filmKey)
      ) {
        extractStarted.current.add(filmKey);
        extractFilmstrip(clip.previewSrc, {
          count: FILMSTRIP_FRAMES,
          fromSec: clip.inPoint,
          toSec: clip.outPoint || clip.duration,
          height: CLIP_H,
        }).then((frames) => {
          if (frames.length) dispatch({ type: "PATCH_CLIP", id: clip.id, patch: { thumbnailStrip: frames } });
        });
      }

      const waveKey = `wave:${clip.id}`;
      if (
        (clip.mediaKind === "audio" || clip.mediaKind === "video") &&
        (!clip.waveformPeaks || clip.waveformPeaks.length === 0) &&
        !extractStarted.current.has(waveKey)
      ) {
        extractStarted.current.add(waveKey);
        extractWaveform(clip.previewSrc, 320).then((peaks) => {
          if (peaks.length) dispatch({ type: "PATCH_CLIP", id: clip.id, patch: { waveformPeaks: peaks } });
        });
      }
    }
  }, [clips, dispatch]);

  // ── snapping ───────────────────────────────────────────────────────────────
  const snapEdge = useCallback(
    (edge: number, ignoreId: string): { time: number; snapped: boolean } => {
      if (!snap) return { time: edge, snapped: false };
      const tol = 8 / px;
      const targets = [0, playhead];
      for (const m of markers) targets.push(m.time);
      for (const c of clips) {
        if (c.id === ignoreId) continue;
        targets.push(c.start, c.start + c.duration);
      }
      let best = edge;
      let bestD = tol;
      let hit = false;
      for (const t of targets) {
        const d = Math.abs(edge - t);
        if (d < bestD) {
          bestD = d;
          best = t;
          hit = true;
        }
      }
      return { time: best, snapped: hit };
    },
    [snap, px, clips, markers, playhead],
  );

  // ── pointer drag ───────────────────────────────────────────────────────────
  const onDragMove = useCallback(
    (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const clip = clips.find((c) => c.id === d.clipId);
      if (!clip) return;
      const dsec = (e.clientX - d.startClientX) / px;
      const isStatic = clip.mediaKind === "image" || clip.mediaKind === "text";
      const sourceMax = clip.sourceDuration ?? d.origOut;

      if (d.mode === "move") {
        let start = Math.max(0, d.origStart + dsec);
        const sStart = snapEdge(start, d.clipId);
        const sEnd = snapEdge(start + d.origDuration, d.clipId);
        if (sStart.snapped && (!sEnd.snapped || Math.abs(sStart.time - start) <= Math.abs(sEnd.time - (start + d.origDuration)))) {
          start = sStart.time;
          setSnapGuideX(sStart.time * px);
        } else if (sEnd.snapped) {
          start = sEnd.time - d.origDuration;
          setSnapGuideX(sEnd.time * px);
        } else {
          setSnapGuideX(null);
        }
        start = Math.max(0, start);

        let trackId = d.origTrackId;
        const sc = scrollRef.current;
        if (sc) {
          const y = e.clientY - sc.getBoundingClientRect().top + sc.scrollTop;
          const idx = Math.max(0, Math.min(tracks.length - 1, Math.floor(y / TRACK_H)));
          const target = tracks[idx];
          const wantAudio = clip.mediaKind === "audio";
          if (target && !target.locked && wantAudio === (target.kind === "audio")) trackId = target.id;
        }
        setPreview({ clipId: d.clipId, start, duration: d.origDuration, inPoint: d.origIn, outPoint: d.origOut, trackId });
      } else if (d.mode === "trim-left") {
        let start = d.origStart + dsec;
        const s = snapEdge(start, d.clipId);
        if (s.snapped) {
          start = s.time;
          setSnapGuideX(s.time * px);
        } else setSnapGuideX(null);
        start = Math.min(start, d.origStart + d.origDuration - 0.1);
        let delta = start - d.origStart;
        let inPoint = d.origIn + (isStatic ? 0 : delta);
        if (!isStatic && inPoint < 0) {
          inPoint = 0;
          delta = -d.origIn;
          start = d.origStart + delta;
        }
        start = Math.max(0, start);
        const duration = d.origStart + d.origDuration - start;
        setPreview({ clipId: d.clipId, start, duration: Math.max(0.1, duration), inPoint, outPoint: d.origOut, trackId: d.origTrackId });
      } else {
        let end = d.origStart + d.origDuration + dsec;
        const s = snapEdge(end, d.clipId);
        if (s.snapped) {
          end = s.time;
          setSnapGuideX(s.time * px);
        } else setSnapGuideX(null);
        let duration = Math.max(0.1, end - d.origStart);
        let outPoint = d.origOut + (isStatic ? 0 : duration - d.origDuration);
        if (!isStatic && outPoint > sourceMax) {
          outPoint = sourceMax;
          duration = d.origDuration + (sourceMax - d.origOut);
        }
        setPreview({ clipId: d.clipId, start: d.origStart, duration: Math.max(0.1, duration), inPoint: d.origIn, outPoint, trackId: d.origTrackId });
      }
    },
    [clips, px, tracks, snapEdge],
  );

  const onDragEnd = useCallback(() => {
    window.removeEventListener("pointermove", onDragMove);
    const d = dragRef.current;
    dragRef.current = null;
    setSnapGuideX(null);
    setPreview((prev) => {
      if (d && prev && prev.clipId === d.clipId) {
        if (d.mode === "move") {
          dispatch({ type: "MOVE_CLIP", id: d.clipId, trackId: prev.trackId, start: prev.start });
        } else {
          dispatch({ type: "TRIM_CLIP", id: d.clipId, start: prev.start, duration: prev.duration, inPoint: prev.inPoint, outPoint: prev.outPoint });
        }
      }
      return null;
    });
  }, [onDragMove, dispatch]);

  const beginDrag = useCallback(
    (e: React.PointerEvent, clip: MediaClip, mode: DragState["mode"]) => {
      const track = tracks.find((t) => t.id === clip.trackId);
      if (track?.locked) return;
      e.preventDefault();
      e.stopPropagation();
      dispatch({ type: "SELECT_CLIP", id: clip.id });
      dragRef.current = {
        mode,
        clipId: clip.id,
        startClientX: e.clientX,
        origStart: clip.start,
        origDuration: clip.duration,
        origIn: clip.inPoint,
        origOut: clip.outPoint,
        origTrackId: clip.trackId,
      };
      window.addEventListener("pointermove", onDragMove);
      window.addEventListener("pointerup", onDragEnd, { once: true });
    },
    [tracks, dispatch, onDragMove, onDragEnd],
  );

  // ── seek ───────────────────────────────────────────────────────────────────
  const seekTo = useCallback(
    (clientX: number) => {
      const sc = scrollRef.current;
      if (!sc) return;
      const x = clientX - sc.getBoundingClientRect().left + sc.scrollLeft;
      const t = Math.max(0, x / px);
      setPlayhead(t);
      if (videoRef.current) {
        try {
          videoRef.current.currentTime = t;
        } catch {
          /* ignore */
        }
      }
    },
    [px, videoRef],
  );

  const onRulerPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const move = (ev: PointerEvent) => seekTo(ev.clientX);
      seekTo(e.clientX);
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    [seekTo],
  );

  const onEmptyPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.target !== e.currentTarget) return;
      dispatch({ type: "SELECT_CLIP", id: null });
      seekTo(e.clientX);
    },
    [dispatch, seekTo],
  );

  // ── zoom ───────────────────────────────────────────────────────────────────
  const zoomAt = useCallback((factor: number, anchorClientX?: number) => {
    setPx((prev) => {
      const next = Math.max(MIN_PX, Math.min(MAX_PX, prev * factor));
      const sc = scrollRef.current;
      if (sc) {
        const anchorX = anchorClientX != null ? anchorClientX - sc.getBoundingClientRect().left : sc.clientWidth / 2;
        const timeAtAnchor = (sc.scrollLeft + anchorX) / prev;
        requestAnimationFrame(() => {
          sc.scrollLeft = Math.max(0, timeAtAnchor * next - anchorX);
          setScrollLeft(sc.scrollLeft);
        });
      }
      return next;
    });
  }, []);

  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX);
      }
    },
    [zoomAt],
  );

  // ── editing ops ────────────────────────────────────────────────────────────
  const splitSelected = useCallback(() => {
    if (selectedId) dispatch({ type: "SPLIT_CLIP", id: selectedId, atTime: playhead });
  }, [selectedId, playhead, dispatch]);

  const deleteSelected = useCallback(() => {
    if (selectedId) dispatch({ type: "DELETE_CLIP", id: selectedId });
  }, [selectedId, dispatch]);

  const addMarker = useCallback(() => {
    dispatch({ type: "ADD_MARKER", time: playhead });
  }, [dispatch, playhead]);

  // ── keyboard ───────────────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      const tag = (el?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || el?.isContentEditable) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) onRedo();
        else onUndo();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (selectedId) {
          e.preventDefault();
          deleteSelected();
        }
      } else if (e.key.toLowerCase() === "s") {
        if (selectedId) {
          e.preventDefault();
          splitSelected();
        }
      } else if (e.key.toLowerCase() === "m") {
        e.preventDefault();
        addMarker();
      } else if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        zoomAt(1.2);
      } else if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        zoomAt(1 / 1.2);
      } else if (e.key === " ") {
        const v = videoRef.current;
        if (v && hasVideo) {
          e.preventDefault();
          if (v.paused) v.play().catch(() => {});
          else v.pause();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, deleteSelected, splitSelected, addMarker, onUndo, onRedo, zoomAt, videoRef, hasVideo]);

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        background: "var(--tl-bg)",
        borderTop: "1px solid #000",
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
        color: "var(--tl-text)",
      }}
    >
      <Toolbar
        playhead={playhead}
        total={contentDuration - TAIL_SEC}
        fps={fps}
        px={px}
        snap={snap}
        canUndo={canUndo}
        canRedo={canRedo}
        hasSelection={!!selectedId}
        onUndo={onUndo}
        onRedo={onRedo}
        onSplit={splitSelected}
        onDelete={deleteSelected}
        onMarker={addMarker}
        onToggleSnap={() => setSnap((s) => !s)}
        onZoomIn={() => zoomAt(1.2)}
        onZoomOut={() => zoomAt(1 / 1.2)}
        onZoomSet={(v) => setPx(v)}
        onAddTrack={(k) => dispatch({ type: "ADD_TRACK", kind: k })}
      />

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* track headers */}
        <div style={{ width: HEADER_W, flexShrink: 0, display: "flex", flexDirection: "column", background: "var(--tl-header)", borderRight: "1px solid var(--tl-line)" }}>
          <div style={{ height: RULER_H, borderBottom: "1px solid var(--tl-line)", display: "flex", alignItems: "center", padding: "0 10px", fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--tl-text-faint)", letterSpacing: "0.08em" }}>
            轨道
          </div>
          <div ref={headerInnerRef} style={{ flex: 1, overflow: "hidden" }}>
            <div style={{ height: tracksHeight }}>
              {tracks.map((t) => (
                <TrackHeader key={t.id} track={t} onToggle={(field) => dispatch({ type: "TOGGLE_TRACK", id: t.id, field })} />
              ))}
            </div>
          </div>
        </div>

        {/* content */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <canvas ref={rulerRef} onPointerDown={onRulerPointerDown} style={{ display: "block", cursor: "ew-resize", flexShrink: 0 }} />
          <div
            ref={scrollRef}
            className="tl-scroll"
            onScroll={handleScroll}
            onWheel={onWheel}
            style={{ flex: 1, overflow: "auto", position: "relative", minHeight: 0 }}
          >
            <div onPointerDown={onEmptyPointerDown} style={{ position: "relative", width: contentWidth, height: tracksHeight, minWidth: "100%" }}>
              {tracks.map((t, i) => (
                <div
                  key={t.id}
                  style={{
                    position: "absolute",
                    left: 0,
                    top: i * TRACK_H,
                    width: "100%",
                    height: TRACK_H,
                    borderBottom: "1px solid var(--tl-line-soft)",
                    background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.012)",
                    pointerEvents: "none",
                  }}
                />
              ))}

              {clips.length === 0 && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--tl-text-faint)", fontSize: 12, pointerEvents: "none" }}>
                  上传或从左侧媒体池拖入素材，即可在此编辑
                </div>
              )}

              {clips.map((clip) => {
                const g = geomOf(clip);
                const idx = trackIndex(g.trackId);
                if (idx < 0) return null;
                return (
                  <ClipView
                    key={clip.id}
                    clip={clip}
                    left={g.start * px}
                    width={Math.max(2, g.duration * px)}
                    top={idx * TRACK_H + CLIP_INSET}
                    selected={selectedId === clip.id}
                    dragging={preview?.clipId === clip.id}
                    onBeginMove={(e) => beginDrag(e, clip, "move")}
                    onBeginTrimLeft={(e) => beginDrag(e, clip, "trim-left")}
                    onBeginTrimRight={(e) => beginDrag(e, clip, "trim-right")}
                  />
                );
              })}

              {markers.map((mk) => (
                <div key={mk.id} style={{ position: "absolute", top: 0, left: mk.time * px, width: 1, height: tracksHeight, background: mk.color, opacity: 0.5, pointerEvents: "none" }} />
              ))}

              {snapGuideX != null && (
                <div style={{ position: "absolute", top: 0, left: snapGuideX, width: 1, height: tracksHeight, background: "var(--tl-snap)", boxShadow: "0 0 6px var(--tl-snap)", pointerEvents: "none", zIndex: 30 }} />
              )}

              <div style={{ position: "absolute", top: 0, left: playhead * px, height: tracksHeight, pointerEvents: "none", zIndex: 40 }}>
                <div style={{ position: "absolute", top: 0, left: -6, width: 12, height: 10, background: "var(--tl-playhead)", clipPath: "polygon(0 0, 100% 0, 50% 100%)" }} />
                <div style={{ width: 2, height: "100%", background: "var(--tl-playhead)", marginLeft: -1, boxShadow: "0 0 4px rgba(255,59,78,0.6)" }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── toolbar ──────────────────────────────────────────────────────────────────
interface ToolbarProps {
  playhead: number;
  total: number;
  fps: number;
  px: number;
  snap: boolean;
  canUndo: boolean;
  canRedo: boolean;
  hasSelection: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onSplit: () => void;
  onDelete: () => void;
  onMarker: () => void;
  onToggleSnap: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onZoomSet: (v: number) => void;
  onAddTrack: (kind: TrackKind) => void;
}

function Toolbar(p: ToolbarProps) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, height: 38, padding: "0 10px", background: "var(--tl-panel)", borderBottom: "1px solid var(--tl-line)", flexShrink: 0, userSelect: "none" }}>
      <TBtn label="撤销" onClick={p.onUndo} disabled={!p.canUndo} icon={<UndoIcon />} />
      <TBtn label="重做" onClick={p.onRedo} disabled={!p.canRedo} icon={<RedoIcon />} />
      <Sep />
      <TBtn label="分割" onClick={p.onSplit} disabled={!p.hasSelection} icon={<SplitIcon />} />
      <TBtn label="删除" onClick={p.onDelete} disabled={!p.hasSelection} icon={<TrashIcon />} danger />
      <TBtn label="标记" onClick={p.onMarker} icon={<FlagIcon />} />
      <TBtn label="吸附" onClick={p.onToggleSnap} active={p.snap} icon={<MagnetIcon />} />
      <Sep />
      <TBtn label="视频轨" onClick={() => p.onAddTrack("overlay")} icon={<PlusIcon />} small />
      <TBtn label="音频轨" onClick={() => p.onAddTrack("audio")} icon={<PlusIcon />} small />

      <div style={{ flex: 1 }} />
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--tl-text)", letterSpacing: "0.04em", display: "flex", gap: 6, alignItems: "baseline" }}>
        <span>{fmtTimecode(p.playhead, p.fps)}</span>
        <span style={{ color: "var(--tl-text-faint)", fontSize: 11 }}>/ {fmtTimecode(p.total, p.fps)}</span>
      </div>
      <div style={{ flex: 1 }} />

      <button onClick={p.onZoomOut} title="缩小 (-)" style={zoomBtnStyle}><MinusIcon /></button>
      <input type="range" className="tl-range" min={MIN_PX} max={MAX_PX} value={p.px} onChange={(e) => p.onZoomSet(Number(e.target.value))} style={{ width: 110 }} />
      <button onClick={p.onZoomIn} title="放大 (+)" style={zoomBtnStyle}><PlusIcon /></button>
    </div>
  );
}

const zoomBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 26,
  height: 24,
  background: "transparent",
  border: "1px solid var(--tl-line)",
  borderRadius: "var(--tl-radius-sm)",
  color: "var(--tl-text-dim)",
};

function Sep() {
  return <div style={{ width: 1, height: 18, background: "var(--tl-line)", margin: "0 2px" }} />;
}

function TBtn({
  label,
  icon,
  onClick,
  disabled,
  active,
  danger,
  small,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  danger?: boolean;
  small?: boolean;
}) {
  return (
    <button
      title={label}
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 5,
        height: 26,
        padding: small ? "0 7px" : "0 9px",
        background: active ? "var(--tl-accent-soft)" : "transparent",
        border: `1px solid ${active ? "var(--tl-accent)" : "var(--tl-line)"}`,
        borderRadius: "var(--tl-radius-sm)",
        color: disabled ? "var(--tl-text-faint)" : danger ? "#ff6b78" : active ? "var(--tl-accent)" : "var(--tl-text-dim)",
        opacity: disabled ? 0.45 : 1,
        cursor: disabled ? "default" : "pointer",
        fontSize: 11,
        fontFamily: "var(--font-mono)",
        whiteSpace: "nowrap",
        transition: "all 0.12s",
      }}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

// ── track header ─────────────────────────────────────────────────────────────
const TrackHeader = memo(function TrackHeader({
  track,
  onToggle,
}: {
  track: TimelineTrack;
  onToggle: (field: "locked" | "muted" | "hidden") => void;
}) {
  const kindLabel = track.kind === "audio" ? "♪" : track.kind === "text" ? "T" : track.kind === "overlay" ? "▣" : "▶";
  return (
    <div style={{ height: TRACK_H, display: "flex", flexDirection: "column", justifyContent: "center", gap: 4, padding: "0 10px", borderBottom: "1px solid var(--tl-line-soft)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 11, color: "var(--tl-text-dim)", width: 14, textAlign: "center" }}>{kindLabel}</span>
        <span style={{ fontSize: 12, color: "var(--tl-text)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{track.name}</span>
      </div>
      <div style={{ display: "flex", gap: 4, paddingLeft: 20 }}>
        <HeaderToggle on={track.locked} label="锁定" onClick={() => onToggle("locked")}>
          {track.locked ? <LockIcon /> : <UnlockIcon />}
        </HeaderToggle>
        {track.kind === "audio" ? (
          <HeaderToggle on={track.muted} label="静音" onClick={() => onToggle("muted")}>
            {track.muted ? <MuteIcon /> : <SoundIcon />}
          </HeaderToggle>
        ) : (
          <HeaderToggle on={track.hidden} label="隐藏" onClick={() => onToggle("hidden")}>
            {track.hidden ? <EyeOffIcon /> : <EyeIcon />}
          </HeaderToggle>
        )}
      </div>
    </div>
  );
});

function HeaderToggle({ on, label, onClick, children }: { on: boolean; label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      title={label}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 20,
        height: 18,
        background: on ? "var(--tl-accent-soft)" : "transparent",
        border: "1px solid transparent",
        borderRadius: 4,
        color: on ? "var(--tl-accent)" : "var(--tl-text-faint)",
      }}
    >
      {children}
    </button>
  );
}

// ── clip ─────────────────────────────────────────────────────────────────────
interface ClipViewProps {
  clip: MediaClip;
  left: number;
  width: number;
  top: number;
  selected: boolean;
  dragging: boolean;
  onBeginMove: (e: React.PointerEvent) => void;
  onBeginTrimLeft: (e: React.PointerEvent) => void;
  onBeginTrimRight: (e: React.PointerEvent) => void;
}

const ClipView = memo(function ClipView({
  clip,
  left,
  width,
  top,
  selected,
  dragging,
  onBeginMove,
  onBeginTrimLeft,
  onBeginTrimRight,
}: ClipViewProps) {
  const { bg, edge } = clipFill(clip.mediaKind);
  const showFilm = (clip.mediaKind === "video" || clip.mediaKind === "image") && width > 24;
  const showWave = clip.mediaKind === "audio" && !!clip.waveformPeaks && clip.waveformPeaks.length > 0;
  const showVideoWave = clip.mediaKind === "video" && !!clip.waveformPeaks && clip.waveformPeaks.length > 0 && width > 40;
  const strip = clip.thumbnailStrip;

  return (
    <div
      onPointerDown={onBeginMove}
      style={{
        position: "absolute",
        left,
        top,
        width,
        height: CLIP_H,
        background: bg,
        border: `1.5px solid ${selected ? "var(--tl-accent)" : edge}`,
        borderRadius: "var(--tl-radius)",
        overflow: "hidden",
        cursor: "grab",
        boxShadow: selected ? "0 0 0 1px var(--tl-accent), 0 2px 10px rgba(0,0,0,0.4)" : dragging ? "0 4px 14px rgba(0,0,0,0.5)" : "0 1px 2px rgba(0,0,0,0.3)",
        zIndex: selected || dragging ? 20 : 10,
        userSelect: "none",
        transition: dragging ? "none" : "box-shadow 0.12s, border-color 0.12s",
      }}
    >
      {showFilm && strip && strip.length > 0 && (
        <div style={{ position: "absolute", inset: 0, display: "flex", opacity: 0.92 }}>
          {strip.map((src, i) => (
            <div key={i} style={{ flex: 1, backgroundImage: `url(${src})`, backgroundSize: "cover", backgroundPosition: "center", borderRight: i < strip.length - 1 ? "1px solid rgba(0,0,0,0.18)" : "none" }} />
          ))}
        </div>
      )}
      {clip.mediaKind === "image" && (!strip || strip.length === 0) && clip.thumbnailSrc && (
        <div style={{ position: "absolute", inset: 0, backgroundImage: `url(${clip.thumbnailSrc})`, backgroundSize: "cover", backgroundPosition: "center", opacity: 0.85 }} />
      )}
      {showFilm && (!strip || strip.length === 0) && clip.mediaKind === "video" && (
        <div style={{ position: "absolute", inset: 0, background: "repeating-linear-gradient(90deg, rgba(255,255,255,0.04) 0 1px, transparent 1px 14px)" }} />
      )}

      {showWave && <WaveBars peaks={clip.waveformPeaks!} color="var(--clip-wave)" />}
      {showVideoWave && (
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 14, opacity: 0.7 }}>
          <WaveBars peaks={clip.waveformPeaks!} color="rgba(180,240,210,0.85)" />
        </div>
      )}

      {clip.mediaKind === "text" && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, color: "#ffe6c2", padding: "0 8px", overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
          {clip.textConfig?.content || clip.name}
        </div>
      )}

      <div style={{ position: "absolute", top: 0, left: 0, right: 0, display: "flex", alignItems: "center", gap: 4, padding: "2px 6px", background: "linear-gradient(180deg, rgba(0,0,0,0.55), transparent)", pointerEvents: "none" }}>
        <span style={{ fontSize: 10.5, color: "#fff", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}>
          {clip.name}
        </span>
        {!clip.keep && <span style={{ fontSize: 9, color: "#ff8b8b" }}>✕</span>}
      </div>

      {width > 56 && (
        <div style={{ position: "absolute", bottom: 3, right: 5, fontSize: 9.5, fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.85)", background: "rgba(0,0,0,0.4)", borderRadius: 4, padding: "0 4px", pointerEvents: "none" }}>
          {clip.duration.toFixed(1)}s
        </div>
      )}

      <div onPointerDown={onBeginTrimLeft} style={handleStyle(selected, "left")} />
      <div onPointerDown={onBeginTrimRight} style={handleStyle(selected, "right")} />
    </div>
  );
});

function handleStyle(selected: boolean, side: "left" | "right"): React.CSSProperties {
  return {
    position: "absolute",
    top: 0,
    [side]: 0,
    width: HANDLE_W,
    height: "100%",
    cursor: "ew-resize",
    background: selected ? "rgba(255,255,255,0.18)" : "transparent",
    borderLeft: side === "left" && selected ? "2px solid var(--tl-accent)" : undefined,
    borderRight: side === "right" && selected ? "2px solid var(--tl-accent)" : undefined,
    zIndex: 5,
  } as React.CSSProperties;
}

// ── waveform ─────────────────────────────────────────────────────────────────
const WaveBars = memo(function WaveBars({ peaks, color }: { peaks: number[]; color: string }) {
  const path = useMemo(() => {
    const N = Math.min(160, peaks.length);
    if (N < 2) return "";
    const stepIn = peaks.length / N;
    const top: string[] = [];
    const bot: string[] = [];
    for (let i = 0; i < N; i++) {
      const p = peaks[Math.floor(i * stepIn)] ?? 0;
      const x = (i / (N - 1)) * 100;
      const h = Math.max(1, p * 46);
      top.push(`${x.toFixed(2)},${(50 - h).toFixed(2)}`);
      bot.push(`${x.toFixed(2)},${(50 + h).toFixed(2)}`);
    }
    return `M${top.join(" L")} L${bot.reverse().join(" L")} Z`;
  }, [peaks]);

  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
      <path d={path} fill={color} opacity={0.85} />
    </svg>
  );
});

// ── icons ────────────────────────────────────────────────────────────────────
const iconProps = {
  width: 13,
  height: 13,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};
const UndoIcon = () => <svg {...iconProps}><path d="M3 7v6h6" /><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13" /></svg>;
const RedoIcon = () => <svg {...iconProps}><path d="M21 7v6h-6" /><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3L21 13" /></svg>;
const SplitIcon = () => <svg {...iconProps}><path d="M12 3v18" /><path d="M5 8 8 12 5 16" /><path d="M19 8l-3 4 3 4" /></svg>;
const TrashIcon = () => <svg {...iconProps}><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M6 6l1 14h10l1-14" /></svg>;
const FlagIcon = () => <svg {...iconProps}><path d="M4 22V4" /><path d="M4 4h12l-2 4 2 4H4" /></svg>;
const MagnetIcon = () => <svg {...iconProps}><path d="M6 4v7a6 6 0 0 0 12 0V4" /><path d="M6 9h4" /><path d="M14 9h4" /></svg>;
const PlusIcon = () => <svg {...iconProps}><path d="M12 5v14" /><path d="M5 12h14" /></svg>;
const MinusIcon = () => <svg {...iconProps}><path d="M5 12h14" /></svg>;
const LockIcon = () => <svg {...iconProps}><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>;
const UnlockIcon = () => <svg {...iconProps}><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V7a4 4 0 0 1 7.5-2" /></svg>;
const SoundIcon = () => <svg {...iconProps}><path d="M4 9v6h4l5 4V5L8 9H4z" /><path d="M17 8a5 5 0 0 1 0 8" /></svg>;
const MuteIcon = () => <svg {...iconProps}><path d="M4 9v6h4l5 4V5L8 9H4z" /><path d="M22 9l-6 6" /><path d="M16 9l6 6" /></svg>;
const EyeIcon = () => <svg {...iconProps}><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></svg>;
const EyeOffIcon = () => <svg {...iconProps}><path d="M17.9 17.9A10.4 10.4 0 0 1 12 19c-7 0-11-7-11-7a18 18 0 0 1 5.1-5.9" /><path d="M9.9 4.2A10.6 10.6 0 0 1 12 5c7 0 11 7 11 7a18 18 0 0 1-2.2 3.2" /><path d="M1 1l22 22" /></svg>;
