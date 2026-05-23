import type { CanonicalProject, MediaClip, MediaKind, ProjectAsset, ProjectState, ProjectTimelineClip } from "../types";

const IMAGE_DURATION = 3;
const DEFAULT_WIDTH = 1920;
const DEFAULT_HEIGHT = 1080;
const DEFAULT_FPS = 30;

export function canonicalProjectFromState(state: ProjectState, accountId: string | null): CanonicalProject {
  const now = new Date().toISOString();
  const title = state.clips[0]?.name || state.title || "Untitled Project";
  const assetsByPath = new Map<string, ProjectAsset>();
  const clips: ProjectTimelineClip[] = [];
  let cursor = 0;

  for (const clip of state.clips) {
    const asset = assetFromClip(clip, now);
    const key = asset.source_path || asset.id;
    if (!assetsByPath.has(key)) assetsByPath.set(key, asset);
    const canonicalAsset = assetsByPath.get(key) ?? asset;
    const mediaKind = clip.mediaKind || canonicalAsset.media_kind;
    const duration = clipDuration(clip, mediaKind);
    clips.push({
      id: clip.id,
      asset_id: canonicalAsset.id,
      track_id: clip.trackId,
      name: clip.name,
      media_kind: mediaKind,
      start: round(cursor),
      duration,
      source_in: mediaKind === "image" ? 0 : round(clip.inPoint),
      source_out: mediaKind === "image" ? IMAGE_DURATION : round(clipSourceOut(clip, mediaKind)),
      enabled: clip.keep,
      effects: clip.effects,
      transition_after: clip.transitionAfter ?? null,
      summary: clip.summary ?? null,
      thumbnails: clip.thumbnailStrip,
      waveform_peaks: clip.waveformPeaks,
    });
    cursor += duration;
  }

  return {
    schema: "gemia.project",
    version: 1,
    project_id: state.projectId || `project_${hashString(`${accountId ?? "local"}:${title}`)}`,
    account_id: accountId,
    title,
    created_at: state.createdAt || now,
    updated_at: state.updatedAt || now,
    assets: Array.from(assetsByPath.values()),
    timeline: {
      fps: DEFAULT_FPS,
      width: DEFAULT_WIDTH,
      height: DEFAULT_HEIGHT,
      duration: round(cursor),
      tracks: [
        { id: "V1", kind: "video", name: "Video 1", index: 0, locked: false, muted: false },
        { id: "A1", kind: "audio", name: "Audio 1", index: 1, locked: false, muted: false },
      ],
      clips,
      markers: [],
    },
    render_settings: {
      format: "mp4",
      video_codec: "h264",
      audio_codec: "aac",
      width: DEFAULT_WIDTH,
      height: DEFAULT_HEIGHT,
      fps: DEFAULT_FPS,
    },
    ui_state: {
      selected_clip_id: state.selectedClipId,
      playhead: round(state.playhead),
      zoom: state.zoom,
      snap_enabled: state.snapEnabled,
    },
    metadata: {
      generator: "gemia-tauri",
      agent_time_references: state.timeReferences ?? [],
    },
  };
}

function assetFromClip(clip: MediaClip, now: string): ProjectAsset {
  const mediaKind = clip.mediaKind || mediaKindForName(clip.name, clip.mimeType || clip.metadata?.mime_type || "");
  const duration = clipDuration(clip, mediaKind);
  return {
    id: clip.assetId || `asset_${hashString(clip.serverPath || clip.name)}`,
    asset_id: clip.assetId || `asset_${hashString(clip.serverPath || clip.name)}`,
    name: clip.name,
    media_kind: mediaKind,
    mime_type: clip.mimeType || clip.metadata?.mime_type || "",
    source_path: clip.serverPath,
    preview_src: clip.previewSrc && !clip.previewSrc.startsWith("blob:") ? clip.previewSrc : null,
    thumbnail_src: clip.thumbnailSrc,
    thumbnails: clip.thumbnailStrip,
    waveform_peaks: clip.waveformPeaks,
    duration,
    metadata: clip.metadata ?? {},
    created_at: now,
  };
}

function clipDuration(clip: MediaClip, mediaKind: MediaKind) {
  if (mediaKind === "image") return IMAGE_DURATION;
  const realDuration = Number(clip.duration) || 0;
  const inPoint = Number(clip.inPoint) || 0;
  const outPoint = Number(clip.outPoint) || 0;
  const trimmed = Boolean((clip as MediaClip & { trimmed?: boolean }).trimmed) || inPoint > 0.01;
  const trimDuration = outPoint > inPoint ? outPoint - inPoint : 0;
  return Math.max(0.1, round(trimmed ? trimDuration || realDuration : realDuration || trimDuration));
}

function clipSourceOut(clip: MediaClip, mediaKind: MediaKind) {
  if (mediaKind === "image") return IMAGE_DURATION;
  const realDuration = Number(clip.duration) || 0;
  const inPoint = Number(clip.inPoint) || 0;
  const outPoint = Number(clip.outPoint) || 0;
  const trimmed = Boolean((clip as MediaClip & { trimmed?: boolean }).trimmed) || inPoint > 0.01;
  return trimmed ? outPoint : inPoint + (realDuration || Math.max(0.1, outPoint - inPoint));
}

function mediaKindForName(name: string, mimeType = ""): MediaKind {
  const mime = mimeType.toLowerCase();
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("audio/")) return "audio";
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "webp", "gif"].includes(ext)) return "image";
  if (["flac", "wav", "mp3", "m4a", "aac"].includes(ext)) return "audio";
  return "video";
}

function hashString(value: string) {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function round(value: number) {
  return Math.round(value * 1_000_000) / 1_000_000;
}
