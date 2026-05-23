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
