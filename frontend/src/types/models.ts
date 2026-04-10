export interface UserPublic {
  id: string;
  email: string;
  username: string;
  display_name: string | null;
  avatar_url?: string | null;
  is_admin: boolean;
  is_blocked: boolean;
  conversation_limit: number | null;
  last_login_at: string | null;
  last_active_at: string | null;
  created_at: string;
}

export interface AuthTokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  user: UserPublic;
}

export interface ProviderItem {
  name: string;
  models: string[];
}

export interface ModelsResponse {
  providers: ProviderItem[];
  default_provider: string;
  default_model: string;
}

export interface Conversation {
  id: string;
  user_id: string;
  title: string;
  default_provider: string | null;
  default_model: string | null;
  is_pinned: boolean;
  workspace_host_path: string;
  workspace_mount_path: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  items: Conversation[];
  total: number;
  page: number;
  page_size: number;
}

export interface MessageRecord {
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  attachments: ConversationAttachment[];
  provider: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  duration_ms: number | null;
  created_at: string;
}

export interface MessageListResponse {
  items: MessageRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface ToolEventRecord {
  id: string;
  conversation_id: string;
  message_id: string | null;
  tool_call_id: string | null;
  tool_name: string;
  display_text: string | null;
  args_json: Record<string, unknown> | null;
  command: string | null;
  output_text: string | null;
  status: string;
  exit_code: number | null;
  started_at: string;
  finished_at: string | null;
  created_at: string;
}

export interface ToolEventListResponse {
  items: ToolEventRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface SandboxInstance {
  id: string;
  conversation_id: string;
  docker_host: string | null;
  image: string | null;
  container_id: string | null;
  container_name: string | null;
  status: string;
  last_heartbeat_at: string | null;
  created_at: string;
  updated_at: string;
  destroyed_at: string | null;
}

export interface ConversationAttachment {
  name: string;
  path: string;
  mime_type: string | null;
  size: number;
  kind: "image" | "file";
  workspace_path: string;
}

export interface ConversationFileNode {
  path: string;
  name: string;
  node_type: "directory" | "file";
  size: number | null;
  mime_type: string | null;
  is_text: boolean;
  created_at: string;
  modified_at: string;
  children: ConversationFileNode[];
}

export interface ConversationFileTree {
  root_path: string;
  items: ConversationFileNode[];
}

export interface ConversationFileTextContent {
  path: string;
  size: number;
  content: string;
  is_text: boolean;
}

export interface ScheduledTask {
  id: string;
  conversation_id: string;
  user_id: string;
  name: string;
  task_type: "hybrid_task" | "skill_task" | string;
  enabled: boolean;
  schedule_type: "cron" | "interval" | string;
  timezone: string;
  cron_expr: string | null;
  interval_minutes: number | null;
  script_command: string | null;
  skill_name: string | null;
  skill_input: string | null;
  summary_prompt: string | null;
  max_runs: number | null;
  run_count: number;
  next_run_at: string;
  run_now_requested_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduledTaskRun {
  id: string;
  task_id: string;
  conversation_id: string;
  user_id: string;
  status: string;
  scheduled_for: string;
  started_at: string | null;
  finished_at: string | null;
  start_message_id: string | null;
  result_message_id: string | null;
  script_exit_code: number | null;
  script_output_text: string | null;
  summary_text: string | null;
  error_text: string | null;
  created_at: string;
}

export interface AuditLog {
  id: string;
  actor_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  result: string;
  detail_json: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface PagedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type ChatEvent = {
  type: string;
  [key: string]: unknown;
};
