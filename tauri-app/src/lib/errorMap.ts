export interface ApiError {
  error_code?: string;
  user_message?: string;
  detail?: string;
  // legacy field from older server responses
  error?: string;
}

const ERROR_MESSAGES: Record<string, string> = {
  E_CONFIG: "请先在设置中填入有效的 API Key",
  E_AI: "AI 服务暂时不可用，请稍后重试",
  E_MEDIA: "媒体处理失败，请检查文件格式是否正确",
  E_PLAN: "任务执行出错，请重试或换一个描述方式",
  E_INPUT: "输入文件无效，请检查文件格式或路径",
  E_GEMIA: "服务器内部错误，请稍后重试",
};

export function friendlyError(raw: unknown): string {
  if (!raw) return "未知错误";

  // Structured ApiError from new server
  if (typeof raw === "object" && raw !== null) {
    const e = raw as ApiError;
    if (e.user_message) return e.user_message;
    if (e.error_code && ERROR_MESSAGES[e.error_code]) {
      return ERROR_MESSAGES[e.error_code];
    }
    // Legacy: {error: "..."}
    if (typeof e.error === "string" && e.error) return e.error;
  }

  if (raw instanceof Error) return raw.message || "未知错误";
  if (typeof raw === "string") return raw || "未知错误";

  return "未知错误";
}

export function parseApiError(body: unknown): ApiError | null {
  if (typeof body === "object" && body !== null) return body as ApiError;
  if (typeof body === "string") {
    try {
      return JSON.parse(body) as ApiError;
    } catch {
      return { error: body };
    }
  }
  return null;
}
