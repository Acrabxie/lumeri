import { useReducer, useCallback, useMemo } from "react";
import type {
  MediaClip,
  MediaKind,
  ProjectAsset,
  ProjectState,
  TimelineMarker,
  TimelineTrack,
  TrackKind,
} from "../types";

// ── id helper ───────────────────────────────────────────────────────────────
let _counter = 0;
export function uid(prefix: string): string {
  _counter += 1;
  const rnd =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().slice(0, 8)
      : (Date.now().toString(36) + _counter.toString(36));
  return `${prefix}_${rnd}_${_counter}`;
}

const DEFAULT_CLIP_DURATION = 5;
const IMAGE_DURATION = 4;
const MARKER_COLORS = ["#ff3b4e", "#ffb13b", "#4ea1ff", "#7a5cff", "#2fd178"];

// ── initial project ──────────────────────────────────────────────────────────
export function createInitialProject(): ProjectState {
  return {
    projectId: uid("project"),
    title: "未命名项目",
    fps: 30,
    width: 1920,
    height: 1080,
    tracks: [
      { id: "V1", kind: "video", name: "视频", locked: false, muted: false, hidden: false },
      { id: "A1", kind: "audio", name: "音频", locked: false, muted: false, hidden: false },
    ],
    clips: [],
    markers: [],
    playhead: 0,
    zoom: 80,
    snapEnabled: true,
    selectedClipId: null,
  };
}

// ── clip construction ────────────────────────────────────────────────────────
function trackKindForMedia(kind: MediaKind): TrackKind {
  if (kind === "audio") return "audio";
  if (kind === "text") return "text";
  return "video"; // video + image live on visual tracks
}

function trackEnd(state: ProjectState, trackId: string): number {
  let end = 0;
  for (const c of state.clips) {
    if (c.trackId === trackId) end = Math.max(end, c.start + c.duration);
  }
  return end;
}

function pickTrack(state: ProjectState, mediaKind: MediaKind): string {
  const want = trackKindForMedia(mediaKind);
  const match = state.tracks.find((t) => t.kind === want || (want === "video" && t.kind === "overlay"));
  return match?.id ?? state.tracks[0]?.id ?? "V1";
}

export interface AssetClipOptions {
  trackId?: string;
  atTime?: number;
  previewSrc?: string | null;
}

export function clipFromAsset(asset: ProjectAsset, opts: AssetClipOptions = {}): MediaClip {
  const mediaKind = asset.media_kind;
  const dur =
    mediaKind === "image"
      ? IMAGE_DURATION
      : Number(asset.duration) > 0
        ? Number(asset.duration)
        : DEFAULT_CLIP_DURATION;
  return {
    id: uid("clip"),
    name: asset.name,
    trackId: opts.trackId ?? "V1",
    assetId: asset.asset_id ?? asset.id,
    mediaKind,
    mimeType: asset.mime_type,
    serverPath: asset.source_path,
    previewSrc: opts.previewSrc ?? asset.preview_src ?? null,
    thumbnailSrc: asset.thumbnail_src ?? null,
    start: opts.atTime ?? 0,
    duration: dur,
    inPoint: 0,
    outPoint: dur,
    sourceDuration: Number(asset.duration) > 0 ? Number(asset.duration) : dur,
    keep: true,
    thumbnailStrip: asset.thumbnails,
    waveformPeaks: asset.waveform_peaks,
    metadata: asset.metadata ?? {},
  };
}

// ── actions ──────────────────────────────────────────────────────────────────
export type EditorAction =
  | { type: "LOAD_PROJECT"; project: ProjectState }
  | { type: "ADD_CLIP"; clip: MediaClip; select?: boolean }
  | { type: "ADD_ASSET"; asset: ProjectAsset; trackId?: string; atTime?: number; previewSrc?: string | null }
  | { type: "MOVE_CLIP"; id: string; trackId: string; start: number }
  | { type: "TRIM_CLIP"; id: string; start: number; duration: number; inPoint: number; outPoint: number }
  | { type: "SPLIT_CLIP"; id: string; atTime: number }
  | { type: "DELETE_CLIP"; id: string }
  | { type: "PATCH_CLIP"; id: string; patch: Partial<MediaClip> }
  | { type: "SELECT_CLIP"; id: string | null }
  | { type: "RENAME_CLIP"; id: string; name: string }
  | { type: "ADD_MARKER"; time: number; label?: string }
  | { type: "DELETE_MARKER"; id: string }
  | { type: "ADD_TRACK"; kind: TrackKind }
  | { type: "TOGGLE_TRACK"; id: string; field: "locked" | "muted" | "hidden" }
  | { type: "UNDO" }
  | { type: "REDO" };

/** Actions that mutate view/selection/cache state and must NOT enter undo history. */
const EPHEMERAL = new Set<EditorAction["type"]>(["SELECT_CLIP", "PATCH_CLIP"]);

// ── core (present) reducer ───────────────────────────────────────────────────
function projectReducer(state: ProjectState, action: EditorAction): ProjectState {
  switch (action.type) {
    case "ADD_CLIP": {
      return {
        ...state,
        clips: [...state.clips, action.clip],
        selectedClipId: action.select === false ? state.selectedClipId : action.clip.id,
      };
    }

    case "ADD_ASSET": {
      const trackId = action.trackId ?? pickTrack(state, action.asset.media_kind);
      const atTime = action.atTime ?? trackEnd(state, trackId);
      const clip = clipFromAsset(action.asset, { trackId, atTime, previewSrc: action.previewSrc });
      return { ...state, clips: [...state.clips, clip], selectedClipId: clip.id };
    }

    case "MOVE_CLIP": {
      return {
        ...state,
        clips: state.clips.map((c) =>
          c.id === action.id ? { ...c, trackId: action.trackId, start: Math.max(0, action.start) } : c,
        ),
      };
    }

    case "TRIM_CLIP": {
      return {
        ...state,
        clips: state.clips.map((c) =>
          c.id === action.id
            ? {
                ...c,
                start: Math.max(0, action.start),
                duration: Math.max(0.1, action.duration),
                inPoint: Math.max(0, action.inPoint),
                outPoint: action.outPoint,
                trimmed: true,
              }
            : c,
        ),
      };
    }

    case "SPLIT_CLIP": {
      const clip = state.clips.find((c) => c.id === action.id);
      if (!clip) return state;
      const at = action.atTime;
      if (at <= clip.start + 0.04 || at >= clip.start + clip.duration - 0.04) return state;
      const leftDur = at - clip.start;
      const rightDur = clip.duration - leftDur;
      const isStatic = clip.mediaKind === "image" || clip.mediaKind === "text";
      const splitSource = isStatic ? clip.inPoint : clip.inPoint + leftDur;
      const left: MediaClip = {
        ...clip,
        duration: leftDur,
        outPoint: isStatic ? clip.outPoint : splitSource,
        trimmed: true,
      };
      const right: MediaClip = {
        ...clip,
        id: uid("clip"),
        start: at,
        duration: rightDur,
        inPoint: isStatic ? clip.inPoint : splitSource,
        outPoint: clip.outPoint,
        trimmed: true,
      };
      const idx = state.clips.findIndex((c) => c.id === action.id);
      const next = state.clips.slice();
      next.splice(idx, 1, left, right);
      return { ...state, clips: next, selectedClipId: right.id };
    }

    case "DELETE_CLIP": {
      return {
        ...state,
        clips: state.clips.filter((c) => c.id !== action.id),
        selectedClipId: state.selectedClipId === action.id ? null : state.selectedClipId,
      };
    }

    case "PATCH_CLIP": {
      return {
        ...state,
        clips: state.clips.map((c) => (c.id === action.id ? { ...c, ...action.patch } : c)),
      };
    }

    case "RENAME_CLIP": {
      return {
        ...state,
        clips: state.clips.map((c) => (c.id === action.id ? { ...c, name: action.name } : c)),
      };
    }

    case "SELECT_CLIP": {
      return { ...state, selectedClipId: action.id };
    }

    case "ADD_MARKER": {
      const marker: TimelineMarker = {
        id: uid("marker"),
        time: Math.max(0, action.time),
        label: action.label ?? `标记 ${state.markers.length + 1}`,
        color: MARKER_COLORS[state.markers.length % MARKER_COLORS.length],
      };
      return { ...state, markers: [...state.markers, marker].sort((a, b) => a.time - b.time) };
    }

    case "DELETE_MARKER": {
      return { ...state, markers: state.markers.filter((m) => m.id !== action.id) };
    }

    case "ADD_TRACK": {
      const kind = action.kind;
      const count = state.tracks.filter((t) => t.kind === kind).length + 1;
      const prefix = kind === "audio" ? "A" : kind === "text" ? "T" : kind === "overlay" ? "O" : "V";
      const label =
        kind === "audio" ? "音频" : kind === "text" ? "文本" : kind === "overlay" ? "叠加" : "视频";
      const track: TimelineTrack = {
        id: `${prefix}${count}_${uid("t")}`,
        kind,
        name: `${label} ${count}`,
        locked: false,
        muted: false,
        hidden: false,
      };
      // Visual tracks (overlay/text) stack above the main row; audio sinks below.
      const tracks =
        kind === "audio"
          ? [...state.tracks, track]
          : kind === "overlay" || kind === "text"
            ? [track, ...state.tracks]
            : [...state.tracks, track];
      return { ...state, tracks };
    }

    case "TOGGLE_TRACK": {
      return {
        ...state,
        tracks: state.tracks.map((t) =>
          t.id === action.id ? { ...t, [action.field]: !t[action.field] } : t,
        ),
      };
    }

    default:
      return state;
  }
}

// ── undo/redo wrapper ────────────────────────────────────────────────────────
interface EditorHistory {
  past: ProjectState[];
  present: ProjectState;
  future: ProjectState[];
}

const HISTORY_LIMIT = 120;

function editorReducer(state: EditorHistory, action: EditorAction): EditorHistory {
  if (action.type === "LOAD_PROJECT") {
    return { past: [], present: action.project, future: [] };
  }
  if (action.type === "UNDO") {
    if (state.past.length === 0) return state;
    const previous = state.past[state.past.length - 1];
    return {
      past: state.past.slice(0, -1),
      present: previous,
      future: [state.present, ...state.future].slice(0, HISTORY_LIMIT),
    };
  }
  if (action.type === "REDO") {
    if (state.future.length === 0) return state;
    const next = state.future[0];
    return {
      past: [...state.past, state.present].slice(-HISTORY_LIMIT),
      present: next,
      future: state.future.slice(1),
    };
  }

  const present = projectReducer(state.present, action);
  if (present === state.present) return state;
  if (EPHEMERAL.has(action.type)) return { ...state, present };
  return {
    past: [...state.past, state.present].slice(-HISTORY_LIMIT),
    present,
    future: [],
  };
}

// ── hook ─────────────────────────────────────────────────────────────────────
export interface EditorApi {
  project: ProjectState;
  dispatch: (action: EditorAction) => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

export function useEditor(initial?: ProjectState): EditorApi {
  const [history, dispatch] = useReducer(editorReducer, undefined, () => ({
    past: [],
    present: initial ?? createInitialProject(),
    future: [],
  }));

  const undo = useCallback(() => dispatch({ type: "UNDO" }), []);
  const redo = useCallback(() => dispatch({ type: "REDO" }), []);

  return useMemo(
    () => ({
      project: history.present,
      dispatch,
      undo,
      redo,
      canUndo: history.past.length > 0,
      canRedo: history.future.length > 0,
    }),
    [history, undo, redo],
  );
}
