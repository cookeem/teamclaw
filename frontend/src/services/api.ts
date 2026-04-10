import type {
  AuditLog,
  AuthTokenResponse,
  Conversation,
  ConversationAttachment,
  ConversationFileNode,
  ConversationFileTextContent,
  ConversationFileTree,
  ConversationListResponse,
  MessageListResponse,
  ModelsResponse,
  PagedResponse,
  SandboxInstance,
  ScheduledTask,
  ScheduledTaskRun,
  ToolEventListResponse,
  UserPublic,
} from "../types/models";
import { useAuthStore } from "../stores/auth";

type TeamClawRuntimeEnv = {
  API_BASE?: string;
  WS_BASE?: string;
};

const runtimeEnv = (globalThis as { __TEAMCLAW_RUNTIME__?: TeamClawRuntimeEnv }).__TEAMCLAW_RUNTIME__;
const runtimeApiBase = typeof runtimeEnv?.API_BASE === "string" ? runtimeEnv.API_BASE.trim() : "";
const API_BASE = runtimeApiBase || (import.meta.env.VITE_API_BASE as string | undefined) || "http://127.0.0.1:8000";

let unauthorizedHandler: (() => void) | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function hasAuthorizationHeader(headers?: HeadersInit): boolean {
  if (!headers) {
    return false;
  }
  if (headers instanceof Headers) {
    return Boolean(headers.get("Authorization"));
  }
  if (Array.isArray(headers)) {
    return headers.some(
      ([key, value]) => key.toLowerCase() === "authorization" && String(value).trim().length > 0,
    );
  }
  return Object.entries(headers).some(
    ([key, value]) => key.toLowerCase() === "authorization" && String(value).trim().length > 0,
  );
}

function notifyUnauthorized() {
  if (!unauthorizedHandler) {
    return;
  }
  try {
    unauthorizedHandler();
  } catch {
    // Ignore side-effect failures to preserve original API error behavior.
  }
}

export function registerUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function withAuthorizationHeader(headers: HeadersInit | undefined, token: string): Headers {
  const merged = new Headers(headers);
  merged.set("Authorization", `Bearer ${token}`);
  return merged;
}

async function fetchWithAuthRetry(path: string, init?: RequestInit, allowRefreshRetry = true): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (response.status === 401 && hasAuthorizationHeader(init?.headers)) {
    if (allowRefreshRetry) {
      const refreshedAccessToken = await tryRefreshAccessToken();
      if (refreshedAccessToken) {
        const retryInit: RequestInit = {
          ...init,
          headers: withAuthorizationHeader(init?.headers, refreshedAccessToken),
        };
        return fetchWithAuthRetry(path, retryInit, false);
      }
    }
    notifyUnauthorized();
  }
  return response;
}

async function tryRefreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) {
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    const auth = useAuthStore();
    const refresh = auth.state.refreshToken?.trim();
    if (!refresh) {
      return null;
    }
    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) {
        return null;
      }
      const payload = (await response.json()) as AuthTokenResponse;
      if (
        !payload ||
        typeof payload.access_token !== "string" ||
        !payload.access_token.trim() ||
        typeof payload.refresh_token !== "string" ||
        !payload.refresh_token.trim() ||
        !payload.user
      ) {
        return null;
      }
      auth.setAuth({
        accessToken: payload.access_token,
        refreshToken: payload.refresh_token,
        user: payload.user,
      });
      return payload.access_token;
    } catch {
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

function authHeaders(token?: string): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function requestJson<T>(path: string, init?: RequestInit, allowRefreshRetry = true): Promise<T> {
  const response = await fetchWithAuthRetry(path, init, allowRefreshRetry);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `HTTP ${response.status}`;
    let message = `HTTP ${response.status}`;
    if (typeof detail === "string" && detail.trim()) {
      message = detail;
    } else if (Array.isArray(detail) && detail.length > 0) {
      const parsed = detail
        .map((entry) => {
          if (typeof entry === "string") return entry;
          if (entry && typeof entry === "object" && "msg" in entry) return String(entry.msg);
          return "";
        })
        .filter(Boolean);
      message = parsed.length > 0 ? parsed.join("; ") : JSON.stringify(detail);
    } else if (detail && typeof detail === "object") {
      message = JSON.stringify(detail);
    }
    throw new ApiError(message, response.status);
  }
  return payload as T;
}

export function apiBase(): string {
  return API_BASE;
}

export async function getModels(): Promise<ModelsResponse> {
  return requestJson<ModelsResponse>("/api/models");
}

export async function getAppSettings(): Promise<{
  version: string;
  language: string;
  supported_languages: string[];
  sandbox_timezone: string;
  default_user_conversation_limit: number;
}> {
  return requestJson<{
    version: string;
    language: string;
    supported_languages: string[];
    sandbox_timezone: string;
    default_user_conversation_limit: number;
  }>("/api/settings");
}

export async function signup(payload: {
  email: string;
  username: string;
  password: string;
  display_name?: string;
}): Promise<AuthTokenResponse> {
  return requestJson<AuthTokenResponse>("/api/v1/auth/signup", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
}

export async function login(payload: {
  account: string;
  password: string;
}): Promise<AuthTokenResponse> {
  return requestJson<AuthTokenResponse>("/api/v1/auth/login", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
}

export async function refreshToken(payload: { refresh_token: string }): Promise<AuthTokenResponse> {
  return requestJson<AuthTokenResponse>("/api/v1/auth/refresh", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
}

export async function logout(
  token: string,
  payload: { refresh_token?: string; revoke_all?: boolean },
): Promise<{ ok: boolean }> {
  return requestJson<{ ok: boolean }>("/api/v1/auth/logout", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function forgotPassword(payload: { email: string }): Promise<{
  ok: boolean;
  delivery?: "email" | "debug_token" | "failed" | "none";
  message?: string;
  error?: string;
  reset_code?: string;
  reset_token?: string;
  expires_at?: string;
}> {
  return requestJson<{
    ok: boolean;
    delivery?: "email" | "debug_token" | "failed" | "none";
    message?: string;
    error?: string;
    reset_code?: string;
    reset_token?: string;
    expires_at?: string;
  }>("/api/v1/auth/forgot-password", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
}

export async function resetPassword(payload: {
  email: string;
  code: string;
  new_password: string;
}): Promise<{ ok: boolean }> {
  return requestJson<{ ok: boolean }>("/api/v1/auth/reset-password", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
}

export async function getMe(token: string): Promise<UserPublic> {
  return requestJson<UserPublic>("/api/v1/me", {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function updateMe(
  token: string,
  payload: {
    display_name?: string;
    email?: string;
    current_password?: string;
    new_password?: string;
  },
): Promise<UserPublic> {
  return requestJson<UserPublic>("/api/v1/me", {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function uploadMyAvatar(token: string, file: File): Promise<UserPublic> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetchWithAuthRetry("/api/v1/me/avatar", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `HTTP ${response.status}`;
    const message = typeof detail === "string" && detail.trim() ? detail : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }
  return payload as UserPublic;
}

export async function listConversations(
  token: string,
  params?: { page?: number; page_size?: number },
): Promise<ConversationListResponse> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", String(params.page));
  if (params?.page_size) query.set("page_size", String(params.page_size));
  const suffix = query.toString() ? `?${query.toString()}` : "";

  return requestJson<ConversationListResponse>(`/api/v1/conversations${suffix}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function createConversation(
  token: string,
  payload: { title?: string; default_provider?: string; default_model?: string },
): Promise<Conversation> {
  return requestJson<Conversation>("/api/v1/conversations", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function getConversation(token: string, id: string): Promise<Conversation> {
  return requestJson<Conversation>(`/api/v1/conversations/${id}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function updateConversation(
  token: string,
  id: string,
  payload: {
    title?: string;
    default_provider?: string;
    default_model?: string;
    is_pinned?: boolean;
    status?: string;
  },
): Promise<Conversation> {
  return requestJson<Conversation>(`/api/v1/conversations/${id}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function deleteConversation(token: string, id: string): Promise<{ ok: boolean }> {
  return requestJson<{ ok: boolean }>(`/api/v1/conversations/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export async function listMessages(
  token: string,
  conversationId: string,
  params?: { page?: number; page_size?: number },
): Promise<MessageListResponse> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", String(params.page));
  if (params?.page_size) query.set("page_size", String(params.page_size));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<MessageListResponse>(`/api/v1/conversations/${conversationId}/messages${suffix}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function listToolEvents(
  token: string,
  conversationId: string,
  params?: { page?: number; page_size?: number },
): Promise<ToolEventListResponse> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", String(params.page));
  if (params?.page_size) query.set("page_size", String(params.page_size));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<ToolEventListResponse>(`/api/v1/conversations/${conversationId}/tool-events${suffix}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function uploadConversationAttachments(
  token: string,
  conversationId: string,
  files: File[],
): Promise<ConversationAttachment[]> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }

  const response = await fetchWithAuthRetry(`/api/v1/conversations/${conversationId}/attachments`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `HTTP ${response.status}`;
    const message = typeof detail === "string" && detail.trim() ? detail : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }
  return payload as ConversationAttachment[];
}

export async function listConversationFilesTree(
  token: string,
  conversationId: string,
  root: "uploads" | "skills" = "uploads",
): Promise<ConversationFileTree> {
  const query = new URLSearchParams({ root });
  return requestJson<ConversationFileTree>(`/api/v1/conversations/${conversationId}/files/tree?${query.toString()}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function createConversationFileDirectory(
  token: string,
  conversationId: string,
  payload: { directory_path: string },
): Promise<ConversationFileNode> {
  return requestJson<ConversationFileNode>(`/api/v1/conversations/${conversationId}/files/mkdir`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function createConversationTextFile(
  token: string,
  conversationId: string,
  payload: { file_path: string; content?: string },
): Promise<ConversationFileNode> {
  return requestJson<ConversationFileNode>(`/api/v1/conversations/${conversationId}/files/create-text`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function uploadConversationFilesToDirectory(
  token: string,
  conversationId: string,
  files: File[],
  targetDir: string,
  root: "uploads" | "skills" = "uploads",
): Promise<ConversationFileNode[]> {
  const form = new FormData();
  form.append("target_dir", targetDir);
  form.append("root", root);
  for (const file of files) {
    form.append("files", file);
  }

  const response = await fetchWithAuthRetry(`/api/v1/conversations/${conversationId}/files/upload`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `HTTP ${response.status}`;
    const message = typeof detail === "string" && detail.trim() ? detail : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }
  return payload as ConversationFileNode[];
}

export async function renameConversationFileNode(
  token: string,
  conversationId: string,
  payload: { path: string; new_name: string },
): Promise<ConversationFileNode> {
  return requestJson<ConversationFileNode>(`/api/v1/conversations/${conversationId}/files/rename`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function deleteConversationFileNode(
  token: string,
  conversationId: string,
  payload: { path: string; recursive?: boolean; confirm_name?: string },
): Promise<{ ok: boolean; message?: string; path?: string }> {
  return requestJson<{ ok: boolean; message?: string; path?: string }>(
    `/api/v1/conversations/${conversationId}/files/delete`,
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(payload),
    },
  );
}

export async function extractConversationArchive(
  token: string,
  conversationId: string,
  payload: { archive_path: string; target_dir?: string },
): Promise<{ ok: boolean; target_path: string; extracted_count: number }> {
  return requestJson<{ ok: boolean; target_path: string; extracted_count: number }>(
    `/api/v1/conversations/${conversationId}/files/extract`,
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(payload),
    },
  );
}

export async function archiveConversationDirectory(
  token: string,
  conversationId: string,
  payload: { directory_path: string; target_dir?: string; output_name?: string },
): Promise<ConversationFileNode> {
  return requestJson<ConversationFileNode>(`/api/v1/conversations/${conversationId}/files/archive`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function readConversationTextFile(
  token: string,
  conversationId: string,
  path: string,
): Promise<ConversationFileTextContent> {
  const query = new URLSearchParams({ path });
  return requestJson<ConversationFileTextContent>(`/api/v1/conversations/${conversationId}/files/content?${query.toString()}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function writeConversationTextFile(
  token: string,
  conversationId: string,
  payload: { path: string; content: string },
): Promise<ConversationFileNode> {
  return requestJson<ConversationFileNode>(`/api/v1/conversations/${conversationId}/files/content`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function getSandbox(token: string, conversationId: string): Promise<SandboxInstance> {
  return requestJson<SandboxInstance>(`/api/v1/conversations/${conversationId}/sandbox`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function restartSandbox(token: string, conversationId: string): Promise<SandboxInstance> {
  return requestJson<SandboxInstance>(`/api/v1/conversations/${conversationId}/sandbox/restart`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export async function listScheduledTasks(token: string, conversationId: string): Promise<ScheduledTask[]> {
  return requestJson<ScheduledTask[]>(`/api/v1/conversations/${conversationId}/scheduled-tasks`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function createScheduledTask(
  token: string,
  conversationId: string,
  payload: {
    name: string;
    task_type?: "hybrid_task" | "skill_task";
    enabled?: boolean;
    schedule_type: "cron" | "interval";
    timezone: string;
    cron_expr?: string;
    interval_minutes?: number;
    script_command?: string;
    skill_name?: string;
    skill_input?: string;
    summary_prompt?: string;
    max_runs?: number | null;
  },
): Promise<ScheduledTask> {
  return requestJson<ScheduledTask>(`/api/v1/conversations/${conversationId}/scheduled-tasks`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function updateScheduledTask(
  token: string,
  conversationId: string,
  taskId: string,
  payload: {
    task_type?: "hybrid_task" | "skill_task";
    name?: string;
    enabled?: boolean;
    schedule_type?: "cron" | "interval";
    timezone?: string;
    cron_expr?: string;
    interval_minutes?: number;
    script_command?: string;
    skill_name?: string;
    skill_input?: string;
    summary_prompt?: string;
    max_runs?: number | null;
  },
): Promise<ScheduledTask> {
  return requestJson<ScheduledTask>(`/api/v1/conversations/${conversationId}/scheduled-tasks/${taskId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function deleteScheduledTask(
  token: string,
  conversationId: string,
  taskId: string,
): Promise<{ ok: boolean; message?: string; path?: string }> {
  return requestJson<{ ok: boolean; message?: string; path?: string }>(
    `/api/v1/conversations/${conversationId}/scheduled-tasks/${taskId}`,
    {
      method: "DELETE",
      headers: authHeaders(token),
    },
  );
}

export async function runScheduledTaskNow(
  token: string,
  conversationId: string,
  taskId: string,
): Promise<{ ok: boolean; message?: string; path?: string }> {
  return requestJson<{ ok: boolean; message?: string; path?: string }>(
    `/api/v1/conversations/${conversationId}/scheduled-tasks/${taskId}/run`,
    {
      method: "POST",
      headers: authHeaders(token),
    },
  );
}

export async function listScheduledTaskRuns(
  token: string,
  conversationId: string,
  taskId: string,
  params?: { limit?: number },
): Promise<ScheduledTaskRun[]> {
  const query = new URLSearchParams();
  if (typeof params?.limit === "number" && params.limit > 0) {
    query.set("limit", String(params.limit));
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<ScheduledTaskRun[]>(
    `/api/v1/conversations/${conversationId}/scheduled-tasks/${taskId}/runs${suffix}`,
    {
      method: "GET",
      headers: authHeaders(token),
    },
  );
}

export async function listUsers(token: string, params?: { search?: string; page?: number; page_size?: number }): Promise<PagedResponse<UserPublic>> {
  const query = new URLSearchParams();
  if (params?.search) query.set("search", params.search);
  if (params?.page) query.set("page", String(params.page));
  if (params?.page_size) query.set("page_size", String(params.page_size));
  const suffix = query.toString() ? `?${query.toString()}` : "";

  return requestJson<PagedResponse<UserPublic>>(`/api/v1/admin/users${suffix}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function createUser(
  token: string,
  payload: {
    email: string;
    username: string;
    password: string;
    display_name?: string;
    is_admin?: boolean;
    is_blocked?: boolean;
    conversation_limit?: number | null;
  },
): Promise<UserPublic> {
  return requestJson<UserPublic>("/api/v1/admin/users", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function updateUser(
  token: string,
  userId: string,
  payload: {
    display_name?: string;
    email?: string;
    new_password?: string;
    is_admin?: boolean;
    is_blocked?: boolean;
    conversation_limit?: number | null;
  },
): Promise<UserPublic> {
  return requestJson<UserPublic>(`/api/v1/admin/users/${userId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export async function deleteUser(token: string, userId: string): Promise<{ ok: boolean }> {
  return requestJson<{ ok: boolean }>(`/api/v1/admin/users/${userId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export async function uploadAdminUserAvatar(token: string, userId: string, file: File): Promise<UserPublic> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetchWithAuthRetry(`/api/v1/admin/users/${userId}/avatar`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `HTTP ${response.status}`;
    const message = typeof detail === "string" && detail.trim() ? detail : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }
  return payload as UserPublic;
}

export async function listAuditLogs(
  token: string,
  params?: { action?: string; result?: string; actor_user_id?: string; page?: number; page_size?: number },
): Promise<PagedResponse<AuditLog>> {
  const query = new URLSearchParams();
  if (params?.action) query.set("action", params.action);
  if (params?.result) query.set("result", params.result);
  if (params?.actor_user_id) query.set("actor_user_id", params.actor_user_id);
  if (params?.page) query.set("page", String(params.page));
  if (params?.page_size) query.set("page_size", String(params.page_size));
  const suffix = query.toString() ? `?${query.toString()}` : "";

  return requestJson<PagedResponse<AuditLog>>(`/api/v1/admin/audit-logs${suffix}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}

export async function getAuditLog(token: string, logId: string): Promise<AuditLog> {
  return requestJson<AuditLog>(`/api/v1/admin/audit-logs/${logId}`, {
    method: "GET",
    headers: authHeaders(token),
  });
}
