import { useRef, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import type React from "react";
import type { MediaAsset, SessionSnapshot } from "../types";

interface Props {
  assets: MediaAsset[];
  selectedAssetId: string | null;
  sessions: SessionSnapshot[];
  disabled?: boolean;
  onSelectAsset: (assetId: string) => void;
  onAddAssetToTimeline: (assetId: string) => void | Promise<void>;
  onDeleteAsset: (assetId: string) => void | Promise<void>;
  onUploadSources: (sources: Array<string | File>) => Promise<void>;
  onNewSession?: () => void;
  onOpenSession?: (sessionId: string) => void | Promise<void>;
}

const API_ORIGIN = "http://127.0.0.1:7788";
const assetUrl = (value: string | null | undefined) => {
  const raw = String(value || "");
  if (!raw) return "";
  if (/^(https?:|blob:|data:)/.test(raw)) return raw;
  if (raw.startsWith("/")) return `${API_ORIGIN}${raw}`;
  return raw;
};
const hasTauri = () => Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
const MEDIA_ACCEPT = [
  "video/mp4",
  "video/quicktime",
  "video/x-msvideo",
  "video/x-matroska",
  "video/webm",
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "audio/flac",
  "audio/wav",
  "audio/mpeg",
  "audio/mp4",
  "audio/aac",
  ".m4a",
].join(",");
const MEDIA_EXTENSIONS = ["mp4", "mov", "m4v", "avi", "mkv", "webm", "png", "jpg", "jpeg", "webp", "gif", "flac", "wav", "mp3", "m4a", "aac"];

function fmtSeconds(value: number) {
  if (!isFinite(value)) return "00:00";
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60);
  return `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

function fmtDate(value: string) {
  const date = new Date(value);
  if (!isFinite(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function MediaHistorySidebar({
  assets,
  selectedAssetId,
  sessions,
  disabled = false,
  onSelectAsset,
  onAddAssetToTimeline,
  onDeleteAsset,
  onUploadSources,
  onNewSession,
  onOpenSession,
}: Props) {
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function upload(sources: Array<string | File>) {
    const items = sources.filter(Boolean);
    if (disabled || uploading || items.length === 0) return;
    setUploading(true);
    try {
      await onUploadSources(items);
    } finally {
      setUploading(false);
      setDragOver(false);
    }
  }

  async function pickMedia() {
    if (disabled || uploading) return;
    if (!hasTauri()) {
      inputRef.current?.click();
      return;
    }
    const selection = await openDialog({
      multiple: true,
      filters: [{ name: "Media", extensions: MEDIA_EXTENSIONS }],
    });
    if (Array.isArray(selection)) {
      await upload(selection.filter((item): item is string => typeof item === "string"));
    } else if (typeof selection === "string") {
      await upload([selection]);
    }
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files);
    const sources = files
      .map((file) => (file as unknown as { path?: string }).path || file)
      .filter((item): item is string | File => Boolean(item));
    void upload(sources);
  }

  function handleNewSessionClick() {
    window.dispatchEvent(new CustomEvent("lumeri:new-session"));
    onNewSession?.();
  }

  return (
    <aside
      style={{
        width: 238,
        borderRight: "1px solid var(--border)",
        background: "var(--surface)",
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        overflow: "hidden",
      }}
    >
      <Panel title="媒体素材池" height="54%">
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={MEDIA_ACCEPT}
          style={{ display: "none" }}
          onChange={(event) => {
            void upload(Array.from(event.currentTarget.files ?? []));
            event.currentTarget.value = "";
          }}
        />
        <button
          onClick={pickMedia}
          disabled={disabled || uploading}
          onDragOver={(event) => {
            event.preventDefault();
            if (!disabled && !uploading) setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          style={uploadButtonStyle(dragOver, disabled || uploading)}
        >
          <span style={{ fontSize: 15, lineHeight: 1 }}>+</span>
          <span>{uploading ? "导入中..." : "导入媒体"}</span>
          <span style={{ marginLeft: "auto", color: "var(--text3)", fontSize: 10 }}>多选</span>
        </button>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
          {assets.length === 0 ? (
            <EmptyText text="拖入或多选媒体" compact />
          ) : (
            assets.map((asset) => (
              <MediaRow
                key={asset.asset_id}
                asset={asset}
                selected={asset.asset_id === selectedAssetId || asset.id === selectedAssetId}
                disabled={disabled}
                onSelect={() => onSelectAsset(asset.asset_id)}
                onAdd={() => onAddAssetToTimeline(asset.asset_id)}
                onDelete={() => onDeleteAsset(asset.asset_id)}
              />
            ))
          )}
        </div>
      </Panel>

      <Panel
        title="会话历史"
        height="46%"
        action={
          onNewSession ? (
            <button
              type="button"
              title="新会话"
              aria-label="新会话"
              disabled={disabled}
              onClick={handleNewSessionClick}
              style={newSessionButtonStyle(disabled)}
            >
              <span style={{ fontSize: 14, lineHeight: 1 }}>+</span>
              <span>新会话</span>
            </button>
          ) : null
        }
      >
        {sessions.length === 0 ? (
          <EmptyText text="暂无历史" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {sessions.map((item) => (
              <SessionRow key={item.id} item={item} disabled={disabled} onOpen={onOpenSession} />
            ))}
          </div>
        )}
      </Panel>
    </aside>
  );
}

function uploadButtonStyle(active: boolean, disabled: boolean): React.CSSProperties {
  return {
    width: "100%",
    minHeight: 36,
    display: "flex",
    alignItems: "center",
    gap: 7,
    border: `1px dashed ${active ? "var(--accent)" : "var(--border2)"}`,
    background: active ? "var(--accent-dim)" : "var(--surface2)",
    borderRadius: "var(--r-sm)",
    color: disabled ? "var(--text3)" : active ? "var(--accent)" : "var(--text2)",
    padding: "0 10px",
    fontSize: 12,
    cursor: disabled ? "default" : "pointer",
  };
}

function Panel({
  title,
  height,
  action,
  children,
}: {
  title: string;
  height: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section style={{ height, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div
        style={{
          height: 34,
          padding: "0 12px",
          display: "flex",
          alignItems: "center",
          borderBottom: "1px solid var(--border)",
          color: "var(--text2)",
          fontSize: 12,
          fontWeight: 600,
          flexShrink: 0,
        }}
      >
        <span style={{ flex: 1, minWidth: 0 }}>{title}</span>
        {action}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 8 }}>{children}</div>
    </section>
  );
}

function newSessionButtonStyle(disabled?: boolean): React.CSSProperties {
  return {
    height: 24,
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    border: "1px solid var(--border)",
    borderRadius: "var(--r-sm)",
    background: "var(--surface2)",
    color: disabled ? "var(--text3)" : "var(--text2)",
    padding: "0 7px",
    fontSize: 11,
    fontWeight: 500,
    cursor: disabled ? "default" : "pointer",
  };
}

function MediaRow({
  asset,
  selected,
  disabled,
  onSelect,
  onAdd,
  onDelete,
}: {
  asset: MediaAsset;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  onAdd: () => void | Promise<void>;
  onDelete: () => void | Promise<void>;
}) {
  const thumb = assetUrl(asset.thumbnail_src || asset.thumbnails?.[0] || (asset.media_kind === "image" ? asset.preview_src : ""));
  const duration = Math.max(0.1, asset.duration || 0);
  const resolution = asset.width && asset.height ? `${asset.width}x${asset.height}` : "";
  const typeLabel = asset.media_kind === "image" ? "图片" : asset.media_kind === "audio" ? "音频" : "视频";
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const showActions = !disabled && (hovered || focused);
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false);
      }}
      style={{
        position: "relative",
        width: "100%",
        display: "grid",
        gridTemplateColumns: "1fr",
        minHeight: 92,
        border: `1px solid ${selected ? "var(--accent-border)" : "var(--border)"}`,
        background: selected ? "var(--accent-dim)" : "var(--surface2)",
        borderRadius: "var(--r-sm)",
        padding: 8,
        textAlign: "left",
        color: "var(--text)",
      }}
    >
      <button
        disabled={disabled}
        onClick={onSelect}
        style={{
          minWidth: 0,
          display: "grid",
          gridTemplateColumns: "58px 1fr",
          gap: 8,
          alignItems: "center",
          border: 0,
          background: "transparent",
          color: "inherit",
          padding: "0 46px 0 0",
          textAlign: "left",
          cursor: disabled ? "default" : "pointer",
        }}
      >
        <div
          style={{
            width: 58,
            height: 40,
            borderRadius: "var(--r-sm)",
            background: thumb ? `center / cover no-repeat url(${thumb})` : asset.media_kind === "audio" ? "var(--accent-dim)" : "var(--surface3)",
            border: "1px solid var(--border)",
            overflow: "hidden",
            display: "grid",
            placeItems: "center",
            color: "var(--accent-strong)",
            fontSize: 16,
          }}
        >
          {!thumb && (asset.media_kind === "audio" ? "♪" : typeLabel.slice(0, 1))}
        </div>
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", justifyContent: "center", gap: 2 }}>
          <div style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {asset.name}
          </div>
          <div style={{ display: "flex", gap: 6, color: "var(--text3)", fontSize: 10 }}>
            <span>{typeLabel}</span>
            <span>{fmtSeconds(duration)}</span>
            {resolution && asset.media_kind !== "audio" && <span>{resolution}</span>}
            <span style={{ color: asset.status === "ready" ? "var(--accent)" : "var(--text3)" }}>{asset.status === "ready" ? "就绪" : asset.status}</span>
          </div>
        </div>
      </button>
      <div
        style={{
          position: "absolute",
          right: 8,
          top: 8,
          bottom: 8,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: 10,
          opacity: showActions ? 1 : 0,
          pointerEvents: showActions ? "auto" : "none",
          transform: showActions ? "translateX(0)" : "translateX(4px)",
          transition: "opacity 120ms ease, transform 120ms ease",
        }}
      >
        <button
          type="button"
          aria-label={`添加 ${asset.name} 到时间轴`}
          title="添加到时间轴"
          disabled={disabled}
          onClick={() => void onAdd()}
          style={{
            width: 34,
            height: 34,
            display: "grid",
            placeItems: "center",
            border: 0,
            borderRadius: "var(--r-sm)",
            background: selected ? "var(--accent)" : "var(--surface3)",
            color: selected ? "var(--ink)" : "var(--text2)",
            cursor: disabled ? "default" : "pointer",
            fontSize: 16,
            lineHeight: 1,
            opacity: disabled ? 0.45 : 1,
          }}
        >
          +
        </button>
        <button
          type="button"
          aria-label={`删除素材 ${asset.name}`}
          title="删除素材"
          disabled={disabled}
          onClick={(event) => {
            event.stopPropagation();
            void onDelete();
          }}
          style={{
            width: 34,
            height: 34,
            display: "grid",
            placeItems: "center",
            border: 0,
            borderRadius: "var(--r-sm)",
            background: "var(--surface3)",
            color: "var(--error)",
            cursor: disabled ? "default" : "pointer",
            fontSize: 16,
            lineHeight: 1,
            opacity: disabled ? 0.45 : 1,
          }}
        >
          ×
        </button>
      </div>
    </div>
  );
}

function SessionRow({
  item,
  disabled,
  onOpen,
}: {
  item: SessionSnapshot;
  disabled?: boolean;
  onOpen?: (sessionId: string) => void | Promise<void>;
}) {
  const canOpen = Boolean(onOpen) && !disabled;
  return (
    <button
      type="button"
      title={item.title}
      aria-label={`打开会话：${item.title}`}
      disabled={!canOpen}
      onClick={() => {
        if (canOpen) void onOpen?.(item.id);
      }}
      style={{
        width: "100%",
        border: "1px solid var(--border)",
        background: "var(--surface2)",
        color: "inherit",
        borderRadius: "var(--r-sm)",
        padding: "8px 9px",
        textAlign: "left",
        cursor: canOpen ? "pointer" : "default",
        opacity: disabled ? 0.55 : 1,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {item.title}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 4, color: "var(--text3)", fontSize: 10 }}>
        <span>{fmtDate(item.updated_at)}</span>
        <span>{item.message_count} 条</span>
        <span>{item.clip_count} 媒体</span>
      </div>
    </button>
  );
}

function EmptyText({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div
      style={{
        height: compact ? 72 : "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text3)",
        fontSize: 12,
      }}
    >
      {text}
    </div>
  );
}
