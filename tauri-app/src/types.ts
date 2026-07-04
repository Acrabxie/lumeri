export type AppStatus =
  | "starting"
  | "ready"
  | "planning"
  | "executing"
  | "done"
  | "error"
  | "asking";

export interface ChatMessage {
  id: string;
  role: "user" | "status";
  content: string;
  statusType?: AppStatus;
  timestamp: number;
}

export interface Skill {
  name: string;
}

export interface MediaAsset {
  id?: string;
  asset_id: string;
  name: string;
  media_kind: "video" | "image" | "audio" | string;
  mime?: string;
  duration?: number;
  width?: number;
  height?: number;
  status?: string;
  preview_src?: string;
  thumbnail_src?: string;
  thumbnails?: string[];
}

export interface SessionSnapshot {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
  clip_count: number;
}

export interface AskQuestion {
  id: string;
  text: string;
  input_type: "choices" | "slider" | "text";
  // choices
  choices?: string[];
  // slider
  min?: number;
  max?: number;
  default?: number;
  step?: number;
  unit?: string;
  // text
  placeholder?: string;
}

// ── Timeline / Project model ────────────────────────────────────────────────
// Local, front-end editing state that drives the timeline editor. Mirrors what
// the engine timeline contract exposes, plus visual caches (filmstrip frames,
// audio peaks) computed client-side. `projectModel.ts` lowers this state into a
// canonical project payload for the render engine.

export type MediaKind = "video" | "image" | "audio" | "text";
export type TrackKind = "video" | "overlay" | "audio" | "text";

export interface TextConfig {
  content: string;
  font_size: number;
  color: string;
}

/** One source media item available in the media pool. */
export interface ProjectAsset {
  id: string;
  asset_id: string;
  name: string;
  media_kind: MediaKind;
  mime_type: string;
  source_path: string;
  preview_src: string | null;
  thumbnail_src?: string | null;
  thumbnails?: string[];
  waveform_peaks?: number[];
  duration: number;
  width?: number;
  height?: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

/** One placed clip on a timeline track (front-end editing shape). */
export interface MediaClip {
  id: string;
  name: string;
  trackId: string;
  assetId?: string | null;
  mediaKind?: MediaKind;
  mimeType?: string;
  /** server-relative source path, e.g. "inputs/foo.mp4" */
  serverPath?: string;
  /** playable URL (blob: preferred) used for frame extraction / preview sync */
  previewSrc?: string | null;
  thumbnailSrc?: string | null;
  /** timeline position, seconds */
  start: number;
  /** timeline duration, seconds */
  duration: number;
  /** in-point within the source media, seconds */
  inPoint: number;
  /** out-point within the source media, seconds */
  outPoint: number;
  /** full intrinsic source duration, seconds (for trim clamping) */
  sourceDuration?: number;
  /** kept in the cut (enabled) */
  keep: boolean;
  trimmed?: boolean;
  effects?: unknown[];
  transitionAfter?: string | null;
  summary?: string | null;
  /** extracted filmstrip frames (data URLs) */
  thumbnailStrip?: string[];
  /** normalized 0..1 waveform peaks */
  waveformPeaks?: number[];
  textConfig?: TextConfig | null;
  metadata?: Record<string, unknown>;
}

export interface TimelineTrack {
  id: string;
  kind: TrackKind;
  name: string;
  locked: boolean;
  muted: boolean;
  hidden: boolean;
}

export interface TimelineMarker {
  id: string;
  time: number;
  label: string;
  color: string;
}

export interface ProjectState {
  projectId: string;
  title: string;
  fps: number;
  width: number;
  height: number;
  tracks: TimelineTrack[];
  clips: MediaClip[];
  markers: TimelineMarker[];
  playhead: number;
  /** pixels per second */
  zoom: number;
  snapEnabled: boolean;
  selectedClipId: string | null;
  timeReferences?: unknown[];
  createdAt?: string;
  updatedAt?: string;
}

// ── Canonical project (engine contract lowering target) ─────────────────────

export interface ProjectTimelineClip {
  id: string;
  asset_id: string;
  track_id: string;
  name: string;
  media_kind: MediaKind;
  start: number;
  duration: number;
  source_in: number;
  source_out: number;
  enabled: boolean;
  effects?: unknown[];
  transition_after?: string | null;
  summary?: string | null;
  thumbnails?: string[];
  waveform_peaks?: number[];
}

export interface CanonicalProject {
  schema: "gemia.project";
  version: number;
  project_id: string;
  account_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
  assets: ProjectAsset[];
  timeline: {
    fps: number;
    width: number;
    height: number;
    duration: number;
    tracks: Array<{
      id: string;
      kind: string;
      name: string;
      index: number;
      locked: boolean;
      muted: boolean;
    }>;
    clips: ProjectTimelineClip[];
    markers: TimelineMarker[];
  };
  render_settings: Record<string, unknown>;
  ui_state: Record<string, unknown>;
  metadata: Record<string, unknown>;
}
