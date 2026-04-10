import { computed, defineComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import {
  archiveConversationDirectory,
  apiBase,
  createScheduledTask,
  createConversationFileDirectory,
  createConversationTextFile,
  deleteScheduledTask,
  deleteConversationFileNode,
  extractConversationArchive,
  getAppSettings,
  getConversation,
  getModels,
  getSandbox,
  listScheduledTaskRuns,
  listScheduledTasks,
  listConversationFilesTree,
  listMessages,
  listToolEvents,
  readConversationTextFile,
  refreshToken,
  renameConversationFileNode,
  restartSandbox,
  runScheduledTaskNow,
  updateScheduledTask,
  uploadConversationAttachments,
  uploadConversationFilesToDirectory,
  writeConversationTextFile,
} from "../../services/api";
import { useI18n } from "../../i18n";
import { useAuthStore } from "../../stores/auth";
import MonacoTextEditor from "../../components/MonacoTextEditor.vue";
import type {
  ChatEvent,
  ConversationAttachment,
  ConversationFileNode,
  MessageListResponse,
  ModelsResponse,
  ProviderItem,
  SandboxInstance,
  ScheduledTask,
  ScheduledTaskRun,
  ToolEventListResponse,
} from "../../types/models";
import { renderMarkdown } from "../../utils/markdown";

type Role = "user" | "assistant" | "system";
type FilePreviewMode = "text" | "image";
type FileManagerRoot = "uploads" | "skills";
type FileSortField = "name" | "modified_at" | "created_at";
type FileSortDirection = "asc" | "desc";
type SettingsTab = "chat-settings" | "files" | "skills" | "scheduled-tasks";
type ScheduledTaskDialogMode = "create" | "edit";

interface ToolCallItem {
  id: string;
  name: string;
  display: string;
  command?: string;
  startedAt?: number;
  finishedAt?: number;
  createdAt?: number;
  output: string;
  status: string;
  hasResult: boolean;
  expanded: boolean;
}

interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  interrupted: boolean;
  attachments: ConversationAttachment[];
  loading: boolean;
  createdAt: number;
  durationSeconds?: number;
  toolCalls: ToolCallItem[];
  scheduledTag: "start" | "result" | null;
}

interface FlatFileNodeRow {
  node: ConversationFileNode;
  depth: number;
}

type FileOperationDialogMode = "new-folder" | "new-file" | "rename" | "archive";

const OUTPUT_LINE_PREVIEW = 14;
const OUTPUT_CHAR_PREVIEW = 1800;
const INTERRUPTED_MARKER = "[interrupted]";
const SCHEDULED_START_MARKER = "[scheduled-task:start]";
const SCHEDULED_RESULT_MARKER = "[scheduled-task:result]";
const MESSAGE_FOCUS_HIGHLIGHT_MS = 2400;

function createClientId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }

  if (cryptoApi && typeof cryptoApi.getRandomValues === "function") {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  return `msg-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

export default defineComponent({
  name: "ChatPage",
  components: {
    MonacoTextEditor,
  },
  setup() {
    const route = useRoute();
    const router = useRouter();
    const auth = useAuthStore();
    const { t, dateLocale } = useI18n();

    const runtimeWsBase = (
      (globalThis as { __TEAMCLAW_RUNTIME__?: { WS_BASE?: string } }).__TEAMCLAW_RUNTIME__?.WS_BASE ?? ""
    ).trim();
    const wsBase = (runtimeWsBase || (import.meta.env.VITE_WS_BASE as string | undefined) || apiBase().replace(/^http/, "ws")).replace(/\/$/, "");

    const providers = ref<ProviderItem[]>([]);
    const selectedProvider = ref("");
    const selectedModel = ref("");
    const draft = ref("");
    const pendingAttachments = ref<ConversationAttachment[]>([]);
    const uploadingAttachments = ref(false);
    const attachmentMenuOpen = ref(false);
    const busy = ref(false);
    const interrupting = ref(false);
    const socketState = ref<"open" | "connecting" | "closed">("closed");
    const messages = ref<ChatMessage[]>([]);
    const errorText = ref("");
    const tipsOpen = ref(false);
    const tipsText = ref("");
    const tipsColor = ref<"success" | "error" | "info" | "warning">("info");
    const sandboxInfo = ref<SandboxInstance | null>(null);
    const showToolMessages = ref(true);
    const settingsTab = ref<SettingsTab>("chat-settings");
    const fileTreeItems = ref<ConversationFileNode[]>([]);
    const fileSortField = ref<FileSortField>("name");
    const fileSortDirection = ref<FileSortDirection>("asc");
    const filesTreeLoading = ref(false);
    const filesActionLoading = ref(false);
    const selectedFilePath = ref("");
    const expandedFilePaths = ref<string[]>([]);
    const fileManagerUploadInput = ref<HTMLInputElement | null>(null);
    const deleteFileDialogOpen = ref(false);
    const deleteTargetNode = ref<ConversationFileNode | null>(null);
    const deleteConfirmName = ref("");
    const editFileDialogOpen = ref(false);
    const editFilePath = ref("");
    const editFileLanguage = ref("plaintext");
    const editFileContent = ref("");
    const editFileLoading = ref(false);
    const editFileSaving = ref(false);
    const viewFileDialogOpen = ref(false);
    const viewFilePath = ref("");
    const viewFileName = ref("");
    const viewFileMode = ref<FilePreviewMode>("text");
    const viewFileLanguage = ref("plaintext");
    const viewFileTextContent = ref("");
    const viewFileLoading = ref(false);
    const fileOperationDialogOpen = ref(false);
    const fileOperationDialogMode = ref<FileOperationDialogMode>("new-folder");
    const fileOperationInput = ref("");
    const fileOperationSubmitting = ref(false);
    const scheduledTasks = ref<ScheduledTask[]>([]);
    const scheduledTasksLoading = ref(false);
    const scheduledTaskActionLoading = ref(false);
    const selectedScheduledTaskId = ref("");
    const scheduledTaskRuns = ref<ScheduledTaskRun[]>([]);
    const scheduledTaskRunsLoading = ref(false);
    const scheduledRunDetailDialogOpen = ref(false);
    const selectedScheduledRun = ref<ScheduledTaskRun | null>(null);
    const scheduledRunDetailContent = ref("");
    const scheduledRunDetailToolCalls = ref<ToolCallItem[]>([]);
    const focusedMessageId = ref("");
    const scheduledTaskDialogOpen = ref(false);
    const scheduledTaskDialogMode = ref<ScheduledTaskDialogMode>("create");
    const scheduledTaskDefaultTimezone = ref("UTC");
    const scheduledTaskFormName = ref("");
    const scheduledTaskFormTaskType = ref<"hybrid_task" | "skill_task">("hybrid_task");
    const scheduledTaskFormEnabled = ref(true);
    const scheduledTaskFormScheduleType = ref<"cron" | "interval">("cron");
    const scheduledTaskFormTimezone = ref(scheduledTaskDefaultTimezone.value);
    const scheduledTaskFormCronExpr = ref("0 9 * * *");
    const scheduledTaskFormIntervalMinutes = ref(60);
    const scheduledTaskFormScriptCommand = ref("");
    const scheduledTaskFormSkillName = ref("");
    const scheduledTaskFormSkillInput = ref("");
    const scheduledTaskFormSummaryPrompt = ref("");
    const scheduledTaskFormMaxRuns = ref<number | null | "">(null);

    const socket = ref<WebSocket | null>(null);
    const activeAssistantId = ref<string | null>(null);
    const reconnectTimer = ref<number | null>(null);
    const sandboxRefreshTimer = ref<number | null>(null);
    const uploadInput = ref<HTMLInputElement | null>(null);
    const chatViewport = ref<HTMLElement | null>(null);
    const shuttingDown = ref(false);
    const thinkingTicker = ref<number | null>(null);
    const thinkingStartedAt = ref<number | null>(null);
    const thinkingSeconds = ref(0);
    const bootstrapVersion = ref(0);
    const eventCursorByConversation = ref<Record<string, number>>({});
    const socketAuthRefreshing = ref(false);
    let messageFocusTimer: number | null = null;
    let redirectingToLogin = false;

    const conversationId = computed(() => String(route.params.id ?? ""));
    const providerItems = computed(() => providers.value);
    const modelItems = computed(() => {
      const provider = providers.value.find((entry) => entry.name === selectedProvider.value);
      return provider?.models ?? [];
    });
    const canSend = computed(
      () =>
        socketState.value === "open" &&
        !busy.value &&
        !uploadingAttachments.value &&
        (draft.value.trim().length > 0 || pendingAttachments.value.length > 0),
    );
    const canInterrupt = computed(
      () => socketState.value === "open" && busy.value && !interrupting.value && Boolean(conversationId.value),
    );
    const sandboxLabel = computed(() => {
      if (!sandboxInfo.value) {
        return t("chat.pending");
      }
      const containerPart = sandboxInfo.value.container_name ? ` · ${sandboxInfo.value.container_name}` : "";
      const hostPart = sandboxInfo.value.docker_host ? ` · ${sandboxInfo.value.docker_host}` : "";
      return `${sandboxInfo.value.status}${containerPart}${hostPart}`;
    });
    const sandboxCreatedAtLabel = computed(() => {
      if (!sandboxInfo.value) {
        return "-";
      }
      return formatDateTime(sandboxInfo.value.created_at);
    });

    const statusLabel = computed(() => {
      if (socketState.value === "open") {
        return busy.value ? t("chat.runningSeconds", { seconds: thinkingSeconds.value }) : t("chat.connected");
      }
      if (socketState.value === "connecting") {
        return t("chat.connecting");
      }
      return t("chat.disconnected");
    });

    const statusColor = computed(() => {
      if (socketState.value === "open") {
        return busy.value ? "warning" : "success";
      }
      if (socketState.value === "connecting") {
        return "info";
      }
      return "error";
    });

    const selectedFileNode = computed(() => findFileNodeByPath(fileTreeItems.value, selectedFilePath.value));
    const fileSortFieldItems = computed(() => [
      { title: t("chat.filesSortName"), value: "name" },
      { title: t("chat.filesSortModified"), value: "modified_at" },
      { title: t("chat.filesSortCreated"), value: "created_at" },
    ]);
    const sortedFileTreeItems = computed<ConversationFileNode[]>(() =>
      sortFileNodes(fileTreeItems.value, fileSortField.value, fileSortDirection.value),
    );
    const flatFileRows = computed<FlatFileNodeRow[]>(() => flattenFileRows(sortedFileTreeItems.value, expandedFilePaths.value));
    const fileManagerRoot = computed<FileManagerRoot>(() => (settingsTab.value === "skills" ? "skills" : "uploads"));
    const filesEmptyText = computed(() =>
      fileManagerRoot.value === "skills" ? t("chat.filesEmptySkills") : t("chat.filesEmptyUploads"),
    );
    const selectedUploadDirectory = computed(() => {
      const selected = selectedFileNode.value;
      const root = fileManagerRoot.value;
      if (!selected) {
        return root;
      }
      if (selected.path !== root && !selected.path.startsWith(`${root}/`)) {
        return root;
      }
      if (selected.node_type === "directory") {
        return selected.path;
      }
      const idx = selected.path.lastIndexOf("/");
      return idx > 0 ? selected.path.slice(0, idx) : root;
    });
    const canEditSelectedTextFile = computed(
      () => selectedFileNode.value?.node_type === "file" && selectedFileNode.value?.is_text === true,
    );
    const canPreviewSelectedFile = computed(() => {
      const selected = selectedFileNode.value;
      if (!selected || selected.node_type !== "file") {
        return false;
      }
      if (selected.is_text) {
        return true;
      }
      return isImageFileName(selected.name);
    });
    const canExtractSelectedArchive = computed(() => {
      const selected = selectedFileNode.value;
      if (!selected || selected.node_type !== "file") {
        return false;
      }
      const lowered = selected.name.toLowerCase();
      return lowered.endsWith(".zip") || lowered.endsWith(".tar") || lowered.endsWith(".tgz") || lowered.endsWith(".tar.gz") || lowered.endsWith(".tar.bz2") || lowered.endsWith(".tbz") || lowered.endsWith(".tar.xz") || lowered.endsWith(".txz");
    });
    const canArchiveSelectedDirectory = computed(
      () => selectedFileNode.value?.node_type === "directory" && selectedFileNode.value.path !== fileManagerRoot.value,
    );
    const canRenameSelectedNode = computed(
      () => Boolean(selectedFileNode.value) && selectedFileNode.value?.path !== fileManagerRoot.value,
    );
    const canDeleteSelectedNode = computed(
      () => Boolean(selectedFileNode.value) && selectedFileNode.value?.path !== fileManagerRoot.value,
    );
    const canDownloadSelectedFile = computed(() => selectedFileNode.value?.node_type === "file");
    const requiresDeleteNameConfirm = computed(() => {
      const selected = deleteTargetNode.value;
      return Boolean(selected && selected.node_type === "directory" && selected.children.length > 0);
    });
    const deleteConfirmDisabled = computed(() => {
      if (!requiresDeleteNameConfirm.value) {
        return false;
      }
      const selected = deleteTargetNode.value;
      if (!selected) {
        return true;
      }
      return deleteConfirmName.value.trim() !== selected.name;
    });
    const fileOperationDialogTitle = computed(() => {
      if (fileOperationDialogMode.value === "new-folder") return t("chat.filesNewFolder");
      if (fileOperationDialogMode.value === "new-file") return t("chat.filesNewFile");
      if (fileOperationDialogMode.value === "rename") return t("chat.filesRename");
      return t("chat.filesArchive");
    });
    const fileOperationDialogHint = computed(() => {
      if (fileOperationDialogMode.value === "new-folder") return t("chat.filesNewFolderPrompt");
      if (fileOperationDialogMode.value === "new-file") return t("chat.filesNewFilePrompt");
      if (fileOperationDialogMode.value === "rename") return t("chat.filesRenamePrompt");
      return t("chat.filesArchiveNamePrompt");
    });
    const fileOperationInputLabel = computed(() => {
      if (fileOperationDialogMode.value === "new-folder") return t("chat.filesNameLabelFolder");
      if (fileOperationDialogMode.value === "new-file") return t("chat.filesNameLabelFile");
      if (fileOperationDialogMode.value === "rename") return t("chat.filesNameLabelRename");
      return t("chat.filesNameLabelArchive");
    });
    const fileOperationConfirmDisabled = computed(
      () => fileOperationSubmitting.value || !fileOperationInput.value.trim(),
    );
    const selectedScheduledTask = computed(() =>
      scheduledTasks.value.find((item) => item.id === selectedScheduledTaskId.value) ?? null,
    );
    const scheduledTaskDialogConfirmDisabled = computed(() => {
      if (!scheduledTaskFormName.value.trim() || !scheduledTaskFormTimezone.value.trim()) {
        return true;
      }
      const rawMaxRuns = scheduledTaskFormMaxRuns.value;
      if (rawMaxRuns !== null && rawMaxRuns !== undefined && rawMaxRuns !== "") {
        const parsed = Number(rawMaxRuns);
        if (!Number.isFinite(parsed) || parsed < 1) {
          return true;
        }
      }
      if (scheduledTaskFormTaskType.value === "hybrid_task") {
        if (!scheduledTaskFormScriptCommand.value.trim()) {
          return true;
        }
      } else if (!scheduledTaskFormSkillName.value.trim()) {
        return true;
      }
      if (scheduledTaskFormScheduleType.value === "cron") {
        return !scheduledTaskFormCronExpr.value.trim();
      }
      return !(Number.isFinite(scheduledTaskFormIntervalMinutes.value) && scheduledTaskFormIntervalMinutes.value >= 1);
    });
    const scheduledTaskTaskTypeHelpText = computed(() =>
      scheduledTaskFormTaskType.value === "skill_task"
        ? t("chat.scheduledTaskTaskTypeSkillHint")
        : t("chat.scheduledTaskTaskTypeHybridHint"),
    );
    const normalizedScheduledTaskMaxRuns = computed<number | null>(() => {
      const rawValue = scheduledTaskFormMaxRuns.value;
      if (rawValue === null || rawValue === undefined || rawValue === "") {
        return null;
      }
      const parsed = Number(rawValue);
      if (!Number.isFinite(parsed)) {
        return null;
      }
      return Math.trunc(parsed);
    });

    function normalizeManagedPath(path: string, root: FileManagerRoot = fileManagerRoot.value): string {
      if (!path) {
        return root;
      }
      const cleaned = path.replace(/\\/g, "/").replace(/^\/+/, "");
      if (cleaned === root || cleaned.startsWith(`${root}/`)) {
        return cleaned;
      }
      return `${root}/${cleaned}`;
    }

    function joinManagedPath(parentPath: string, name: string, root: FileManagerRoot = fileManagerRoot.value): string {
      const safeName = name.trim().replace(/^\/+/, "").replace(/\/+$/, "");
      const parent = normalizeManagedPath(parentPath, root);
      if (!safeName) {
        return parent;
      }
      if (parent === root) {
        return `${root}/${safeName}`;
      }
      return `${parent}/${safeName}`;
    }

    function findFileNodeByPath(nodes: ConversationFileNode[], path: string): ConversationFileNode | null {
      if (!path) {
        return null;
      }
      for (const node of nodes) {
        if (node.path === path) {
          return node;
        }
        if (node.node_type === "directory" && node.children.length > 0) {
          const found = findFileNodeByPath(node.children, path);
          if (found) {
            return found;
          }
        }
      }
      return null;
    }

    function flattenFileRows(nodes: ConversationFileNode[], expandedPaths: string[], depth = 0): FlatFileNodeRow[] {
      const rows: FlatFileNodeRow[] = [];
      const expandedSet = new Set(expandedPaths);
      for (const node of nodes) {
        rows.push({ node, depth });
        if (node.node_type === "directory" && expandedSet.has(node.path) && node.children.length > 0) {
          rows.push(...flattenFileRows(node.children, expandedPaths, depth + 1));
        }
      }
      return rows;
    }

    function sortFileNodes(
      nodes: ConversationFileNode[],
      sortField: FileSortField,
      sortDirection: FileSortDirection,
    ): ConversationFileNode[] {
      const sign = sortDirection === "asc" ? 1 : -1;
      const cloned = nodes.map((node) => ({
        ...node,
        children:
          node.node_type === "directory"
            ? sortFileNodes(node.children ?? [], sortField, sortDirection)
            : [],
      }));
      cloned.sort((a, b) => {
        if (a.node_type !== b.node_type) {
          return a.node_type === "directory" ? -1 : 1;
        }
        const compared = compareFileNodes(a, b, sortField);
        if (compared !== 0) {
          return compared * sign;
        }
        return a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) * sign;
      });
      return cloned;
    }

    function compareFileNodes(a: ConversationFileNode, b: ConversationFileNode, sortField: FileSortField): number {
      if (sortField === "name") {
        return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
      }
      const aValue = Date.parse(sortField === "modified_at" ? a.modified_at : a.created_at);
      const bValue = Date.parse(sortField === "modified_at" ? b.modified_at : b.created_at);
      const aSafe = Number.isFinite(aValue) ? aValue : 0;
      const bSafe = Number.isFinite(bValue) ? bValue : 0;
      if (aSafe === bSafe) {
        return 0;
      }
      return aSafe < bSafe ? -1 : 1;
    }

    function buildSocketUrl(): string {
      const token = encodeURIComponent(auth.state.accessToken);
      return `${wsBase}/ws/chat?token=${token}`;
    }

    function getConversationEventCursor(cid: string): number | undefined {
      const value = eventCursorByConversation.value[cid];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return undefined;
      }
      return value;
    }

    function updateEventCursor(cid: string, seq: number): boolean {
      const current = getConversationEventCursor(cid);
      if (typeof current === "number" && seq <= current) {
        return false;
      }
      eventCursorByConversation.value = {
        ...eventCursorByConversation.value,
        [cid]: seq,
      };
      return true;
    }

    function sendSubscribeRequest() {
      if (!socket.value || socket.value.readyState !== WebSocket.OPEN || !conversationId.value) {
        return;
      }
      const payload: Record<string, unknown> = {
        type: "subscribe",
        session_id: conversationId.value,
      };
      const cursor = getConversationEventCursor(conversationId.value);
      if (typeof cursor === "number") {
        payload.cursor = cursor;
      }
      socket.value.send(JSON.stringify(payload));
    }

    function bubbleColor(role: Role): string {
      if (role === "user") {
        return "#dbf4ef";
      }
      if (role === "assistant") {
        return "#fff8ec";
      }
      return "#eef2ff";
    }

    function currentUserInitials(): string {
      const raw = auth.state.user?.display_name || auth.state.user?.username || auth.state.user?.email || "U";
      const cleaned = raw.trim();
      if (!cleaned) {
        return "U";
      }
      const parts = cleaned.split(/\s+/).filter(Boolean);
      if (parts.length >= 2) {
        return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
      }
      return cleaned.slice(0, 2).toUpperCase();
    }

    function messageAvatarText(role: Role): string {
      if (role === "user") {
        return currentUserInitials();
      }
      if (role === "assistant") {
        return "TC";
      }
      return "SYS";
    }

    function messageAvatarSrc(role: Role): string | null {
      if (role === "user") {
        const raw = auth.state.user?.avatar_url;
        if (typeof raw === "string" && raw.trim()) {
          if (/^https?:\/\//i.test(raw)) {
            return raw;
          }
          const base = apiBase().replace(/\/$/, "");
          return `${base}${raw.startsWith("/") ? raw : `/${raw}`}`;
        }
      }
      if (role === "assistant") {
        return "/images/teamclaw_logo.png";
      }
      return null;
    }

    function appendMessage(
      role: Role,
      content: string,
      loading = false,
      attachments: ConversationAttachment[] = [],
      interrupted = false,
      messageId?: string,
    ): string {
      const normalized = normalizeScheduledMessage(content);
      const explicitId = messageId && messageId.trim() ? messageId.trim() : "";
      if (explicitId) {
        const existing = messages.value.find((item) => item.id === explicitId);
        if (existing) {
          return existing.id;
        }
      }
      const id = explicitId || createClientId();
      messages.value.push({
        id,
        role,
        content: normalized.content,
        interrupted,
        attachments: attachments.slice(),
        loading,
        createdAt: Date.now(),
        toolCalls: [],
        scheduledTag: normalized.tag,
      });
      scrollToBottomSoon();
      return id;
    }

    function ensureActiveAssistantMessage(): ChatMessage {
      if (activeAssistantId.value) {
        const existing = messages.value.find((item) => item.id === activeAssistantId.value);
        if (existing) {
          return existing;
        }
      }
      const id = appendMessage("assistant", "", true);
      activeAssistantId.value = id;
      const created = messages.value.find((item) => item.id === id);
      if (!created) {
        throw new Error("Unable to create assistant message");
      }
      return created;
    }

    function messageContent(message: ChatMessage): string {
      if (message.content) {
        return message.content;
      }
      if (message.loading) {
        return t("chat.thinkingSeconds", { seconds: thinkingSeconds.value });
      }
      if (message.role === "assistant" && message.interrupted) {
        return t("chat.interruptedNoOutput");
      }
      return "";
    }

    function messageHtml(message: ChatMessage): string {
      return renderMarkdown(messageContent(message));
    }

    function scheduledRunDetailHtml(): string {
      return renderMarkdown(scheduledRunDetailContent.value || "");
    }

    function normalizeScheduledMessage(raw: string): { content: string; tag: "start" | "result" | null } {
      const source = String(raw ?? "");
      if (source.startsWith(SCHEDULED_START_MARKER)) {
        const content = source.slice(SCHEDULED_START_MARKER.length).trim();
        return { content, tag: "start" };
      }
      if (source.startsWith(SCHEDULED_RESULT_MARKER)) {
        const content = source.slice(SCHEDULED_RESULT_MARKER.length).trim();
        return { content, tag: "result" };
      }
      return { content: source, tag: null };
    }

    function parseTimestamp(value: unknown): number | undefined {
      if (typeof value === "number" && Number.isFinite(value)) {
        return value;
      }
      if (typeof value === "string" && value.trim()) {
        const parsed = Date.parse(value);
        if (!Number.isNaN(parsed)) {
          return parsed;
        }
      }
      return undefined;
    }

    function formatMessageTime(timestamp: number): string {
      return new Date(timestamp).toLocaleString(dateLocale.value, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }

    function messageMeta(message: ChatMessage): string {
      const timeText = formatMessageTime(message.createdAt);
      if (message.role !== "assistant") {
        return timeText;
      }
      if (message.loading) {
        return `${timeText} · ${t("chat.elapsed", { seconds: thinkingSeconds.value })}...`;
      }
      if (typeof message.durationSeconds === "number") {
        const elapsed = `${timeText} · ${t("chat.elapsed", { seconds: message.durationSeconds })}`;
        if (message.interrupted) {
          return `${elapsed} · ${t("chat.interrupted")}`;
        }
        return elapsed;
      }
      if (message.interrupted) {
        return `${timeText} · ${t("chat.interrupted")}`;
      }
      return timeText;
    }

    function scheduledTagLabel(tag: "start" | "result" | null): string {
      if (tag === "start") {
        return t("chat.scheduledTaskStartTag");
      }
      if (tag === "result") {
        return t("chat.scheduledTaskResultTag");
      }
      return "";
    }

    function startThinkingTimer() {
      stopThinkingTimer();
      thinkingStartedAt.value = Date.now();
      thinkingSeconds.value = 0;
      thinkingTicker.value = window.setInterval(() => {
        if (!thinkingStartedAt.value) {
          return;
        }
        const elapsedMs = Date.now() - thinkingStartedAt.value;
        thinkingSeconds.value = Math.max(0, Math.floor(elapsedMs / 1000));
      }, 200);
    }

    function stopThinkingTimer() {
      if (thinkingTicker.value !== null) {
        window.clearInterval(thinkingTicker.value);
        thinkingTicker.value = null;
      }
    }

    function markAssistantDone(options?: { interrupted?: boolean }) {
      const wasInterrupted = options?.interrupted === true;
      stopThinkingTimer();
      interrupting.value = false;
      if (!activeAssistantId.value) {
        busy.value = false;
        return;
      }
      const target = messages.value.find((item) => item.id === activeAssistantId.value);
      if (target) {
        target.loading = false;
        target.interrupted = wasInterrupted;
        target.durationSeconds = Math.max(0, Math.floor((Date.now() - target.createdAt) / 1000));
        if (!target.content.trim() && target.toolCalls.length === 0 && wasInterrupted) {
          target.content = t("chat.interruptedNoOutput");
        } else if (!target.content.trim() && target.toolCalls.length === 0) {
          target.content = t("chat.noTextOutput");
        }
      }
      activeAssistantId.value = null;
      busy.value = false;
    }

    function ensureStreamRunning() {
      if (!busy.value) {
        busy.value = true;
        startThinkingTimer();
      }
      ensureActiveAssistantMessage();
    }

    function appendAssistantText(delta: string) {
      const target = ensureActiveAssistantMessage();
      target.content += delta;
      if (!target.scheduledTag) {
        const normalized = normalizeScheduledMessage(target.content);
        if (normalized.tag) {
          target.scheduledTag = normalized.tag;
          target.content = normalized.content;
        }
      }
    }

    function isCollapsible(tool: ToolCallItem): boolean {
      if (tool.output.length > OUTPUT_CHAR_PREVIEW) {
        return true;
      }
      return tool.output.split("\n").length > OUTPUT_LINE_PREVIEW;
    }

    function visibleToolOutput(tool: ToolCallItem): string {
      if (tool.expanded || !isCollapsible(tool)) {
        return tool.output;
      }
      const lines = tool.output.split("\n").slice(0, OUTPUT_LINE_PREVIEW).join("\n");
      const clipped = lines.length > OUTPUT_CHAR_PREVIEW ? lines.slice(0, OUTPUT_CHAR_PREVIEW) : lines;
      return `${clipped}\n\n${t("chat.outputTruncated")}`;
    }

    function toolCallTimeText(tool: ToolCallItem): string {
      const timestamp = tool.finishedAt ?? tool.startedAt ?? tool.createdAt;
      if (typeof timestamp !== "number" || !Number.isFinite(timestamp)) {
        return "";
      }
      return formatMessageTime(timestamp);
    }

    function toolCallDurationSeconds(tool: ToolCallItem): number | null {
      const startedAt = tool.startedAt ?? tool.createdAt;
      if (typeof startedAt !== "number" || !Number.isFinite(startedAt)) {
        return null;
      }
      const endAt =
        typeof tool.finishedAt === "number" && Number.isFinite(tool.finishedAt)
          ? tool.finishedAt
          : !tool.hasResult
            ? Date.now()
            : startedAt;
      return Math.max(0, Math.floor((endAt - startedAt) / 1000));
    }

    function toolCallDurationText(tool: ToolCallItem): string {
      const seconds = toolCallDurationSeconds(tool);
      if (seconds === null) {
        return "";
      }
      return t("chat.elapsed", { seconds });
    }

    function toolCallMetaText(tool: ToolCallItem): string {
      const timeText = toolCallTimeText(tool);
      const durationText = toolCallDurationText(tool);
      if (timeText && durationText) {
        return `${timeText} · ${durationText}`;
      }
      return timeText || durationText;
    }

    function upsertToolCall(event: ChatEvent, withResult: boolean) {
      const target = ensureActiveAssistantMessage();
      const name = String(event.name ?? "tool");
      const toolCallId = String(event.tool_call_id ?? `${name}-${target.toolCalls.length}`);
      const display = String(event.display ?? `${name}()`);
      const command = typeof event.command === "string" ? event.command : undefined;
      const startedAt = parseTimestamp(event.started_at);
      const finishedAt = parseTimestamp(event.finished_at);
      const createdAt = parseTimestamp(event.created_at);

      let tool = target.toolCalls.find((item) => item.id === toolCallId);
      if (!tool) {
        const now = Date.now();
        tool = {
          id: toolCallId,
          name,
          display,
          command,
          startedAt: startedAt ?? createdAt ?? now,
          createdAt: createdAt ?? now,
          output: "",
          status: "running",
          hasResult: false,
          expanded: false,
        };
        target.toolCalls.push(tool);
      }

      tool.name = name;
      tool.display = display;
      tool.command = command;
      if (typeof startedAt === "number") {
        tool.startedAt = startedAt;
      }
      if (typeof createdAt === "number") {
        tool.createdAt = createdAt;
      }

      if (withResult) {
        tool.output = String(event.output ?? "");
        tool.status = String(event.status ?? "success");
        tool.hasResult = true;
        tool.finishedAt = finishedAt ?? Date.now();
        if (typeof tool.startedAt !== "number") {
          tool.startedAt = tool.createdAt ?? tool.finishedAt;
        }
      }
    }

    function attachHistoricalToolEvents(
      toolItems: Array<{
        message_id: string | null;
        tool_name: string;
        tool_call_id: string | null;
        display_text: string | null;
        command: string | null;
        output_text: string | null;
        status: string;
        started_at: string;
        finished_at: string | null;
        created_at: string;
      }>,
    ) {
      if (toolItems.length === 0) {
        return;
      }

      const messageMap = new Map<string, ChatMessage>();
      for (const message of messages.value) {
        messageMap.set(message.id, message);
      }

      for (const item of toolItems) {
        if (!item.message_id) {
          continue;
        }
        const target = messageMap.get(item.message_id);
        if (!target) {
          continue;
        }
        const toolCallId = item.tool_call_id || `${item.tool_name}-${target.toolCalls.length}`;
        const startedAt = parseTimestamp(item.started_at);
        const finishedAt = parseTimestamp(item.finished_at);
        const createdAt = parseTimestamp(item.created_at);
        const now = Date.now();
        target.toolCalls.push({
          id: toolCallId,
          name: item.tool_name,
          display: item.display_text || `${item.tool_name}()`,
          command: item.command || undefined,
          startedAt: startedAt ?? createdAt ?? now,
          finishedAt,
          createdAt: createdAt ?? startedAt ?? now,
          output: item.output_text || "",
          status: item.status || "success",
          hasResult: Boolean(item.output_text) || item.status !== "running",
          expanded: false,
        });
      }
    }

    function isStaleBootstrap(version: number | null, targetConversationId: string): boolean {
      return version !== null && (version !== bootstrapVersion.value || conversationId.value !== targetConversationId);
    }

    async function loadSandbox(targetConversationId?: string, version: number | null = null) {
      const cid = targetConversationId ?? conversationId.value;
      if (!auth.state.accessToken || !cid) {
        sandboxInfo.value = null;
        return;
      }
      try {
        const payload = await getSandbox(auth.state.accessToken, cid);
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        sandboxInfo.value = payload;
      } catch {
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        sandboxInfo.value = null;
      }
    }

    function openAttachmentPicker() {
      attachmentMenuOpen.value = false;
      uploadInput.value?.click();
    }

    async function uploadSelectedFiles(files: File[]) {
      if (files.length === 0 || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      if (uploadingAttachments.value) {
        return;
      }

      uploadingAttachments.value = true;
      try {
        const uploaded = await uploadConversationAttachments(auth.state.accessToken, conversationId.value, files);
        pendingAttachments.value = pendingAttachments.value.concat(uploaded);
        void loadFilesTree();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.attachmentUploadFailed");
      } finally {
        uploadingAttachments.value = false;
      }
    }

    async function onAttachmentInputChange(event: Event) {
      const target = event.target as HTMLInputElement;
      const selected = Array.from(target.files ?? []);
      target.value = "";
      if (selected.length === 0) {
        return;
      }
      await uploadSelectedFiles(selected);
    }

    async function onComposerPaste(event: ClipboardEvent) {
      const clipboardData = event.clipboardData;
      if (!clipboardData) {
        return;
      }

      const fileCandidates: File[] = [];
      for (const item of Array.from(clipboardData.items ?? [])) {
        if (item.kind !== "file") {
          continue;
        }
        const file = item.getAsFile();
        if (file) {
          fileCandidates.push(file);
        }
      }
      for (const file of Array.from(clipboardData.files ?? [])) {
        fileCandidates.push(file);
      }

      if (fileCandidates.length === 0) {
        return;
      }

      const seen = new Set<string>();
      const uniqueFiles: File[] = [];
      for (const file of fileCandidates) {
        const key = `${file.name}|${file.size}|${file.type}|${file.lastModified}`;
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        uniqueFiles.push(file);
      }

      if (uniqueFiles.length === 0) {
        return;
      }

      event.preventDefault();
      await uploadSelectedFiles(uniqueFiles);
    }

    function removePendingAttachment(path: string) {
      pendingAttachments.value = pendingAttachments.value.filter((item) => item.path !== path);
    }

    function formatAttachmentSize(size: number): string {
      if (size < 1024) {
        return `${size}B`;
      }
      if (size < 1024 * 1024) {
        return `${Math.max(1, Math.round(size / 1024))}KB`;
      }
      return `${(size / (1024 * 1024)).toFixed(1)}MB`;
    }

    function encodeAttachmentPath(path: string): string {
      return path
        .split("/")
        .filter((segment) => segment.length > 0)
        .map((segment) => encodeURIComponent(segment))
        .join("/");
    }

    function attachmentUrl(item: ConversationAttachment, inline: boolean): string {
      if (!auth.state.accessToken || !conversationId.value || !item.path) {
        return "#";
      }
      const encodedConversationId = encodeURIComponent(conversationId.value);
      const encodedPath = encodeAttachmentPath(item.path);
      const query = new URLSearchParams({
        token: auth.state.accessToken,
        name: item.name,
      });
      if (inline) {
        query.set("inline", "1");
      }
      const base = apiBase().replace(/\/$/, "");
      return `${base}/api/v1/conversations/${encodedConversationId}/attachments/${encodedPath}?${query.toString()}`;
    }

    function attachmentPreviewUrl(item: ConversationAttachment): string {
      return attachmentUrl(item, true);
    }

    function attachmentDownloadUrl(item: ConversationAttachment): string {
      return attachmentUrl(item, false);
    }

    async function loadFilesTree(
      targetConversationId?: string | Event,
      version: number | null = null,
    ) {
      const cid = typeof targetConversationId === "string" ? targetConversationId : conversationId.value;
      if (!auth.state.accessToken || !cid) {
        fileTreeItems.value = [];
        selectedFilePath.value = "";
        return;
      }
      const root = fileManagerRoot.value;
      filesTreeLoading.value = true;
      try {
        const tree = await listConversationFilesTree(auth.state.accessToken, cid, root);
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        fileTreeItems.value = Array.isArray(tree.items) ? tree.items : [];
        if (!selectedFilePath.value && fileTreeItems.value.length > 0) {
          selectedFilePath.value = fileTreeItems.value[0].path;
        } else if (selectedFilePath.value && !findFileNodeByPath(fileTreeItems.value, selectedFilePath.value)) {
          selectedFilePath.value = "";
        }
      } catch (error) {
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        errorText.value = error instanceof Error ? error.message : t("chat.filesLoadFailed");
      } finally {
        if (!isStaleBootstrap(version, cid)) {
          filesTreeLoading.value = false;
        }
      }
    }

    async function loadScheduledTasks(
      targetConversationId?: string | Event,
      version: number | null = null,
    ) {
      const cid = typeof targetConversationId === "string" ? targetConversationId : conversationId.value;
      if (!auth.state.accessToken || !cid) {
        scheduledTasks.value = [];
        selectedScheduledTaskId.value = "";
        scheduledTaskRuns.value = [];
        return;
      }
      scheduledTasksLoading.value = true;
      try {
        const items = await listScheduledTasks(auth.state.accessToken, cid);
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        scheduledTasks.value = Array.isArray(items) ? items : [];
        if (!selectedScheduledTaskId.value || !scheduledTasks.value.some((item) => item.id === selectedScheduledTaskId.value)) {
          selectedScheduledTaskId.value = scheduledTasks.value[0]?.id ?? "";
        }
      } catch (error) {
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        errorText.value = error instanceof Error ? error.message : t("chat.scheduledTasksLoadFailed");
      } finally {
        if (!isStaleBootstrap(version, cid)) {
          scheduledTasksLoading.value = false;
        }
      }
      await loadSelectedScheduledTaskRuns();
    }

    async function loadSelectedScheduledTaskRuns() {
      if (!auth.state.accessToken || !conversationId.value || !selectedScheduledTaskId.value) {
        scheduledTaskRuns.value = [];
        return;
      }
      scheduledTaskRunsLoading.value = true;
      try {
        const runs = await listScheduledTaskRuns(
          auth.state.accessToken,
          conversationId.value,
          selectedScheduledTaskId.value,
          { limit: 20 },
        );
        scheduledTaskRuns.value = Array.isArray(runs) ? runs : [];
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.scheduledTaskRunsLoadFailed");
      } finally {
        scheduledTaskRunsLoading.value = false;
      }
    }

    function messageDomId(messageId: string): string {
      return `chat-message-${messageId}`;
    }

    function clearFocusedMessage() {
      focusedMessageId.value = "";
      if (messageFocusTimer !== null) {
        window.clearTimeout(messageFocusTimer);
        messageFocusTimer = null;
      }
    }

    function focusMessage(messageId: string): boolean {
      const normalized = String(messageId || "").trim();
      if (!normalized) {
        return false;
      }
      const target = document.getElementById(messageDomId(normalized));
      if (!target) {
        return false;
      }
      target.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
      focusedMessageId.value = normalized;
      if (messageFocusTimer !== null) {
        window.clearTimeout(messageFocusTimer);
      }
      messageFocusTimer = window.setTimeout(() => {
        focusedMessageId.value = "";
        messageFocusTimer = null;
      }, MESSAGE_FOCUS_HIGHLIGHT_MS);
      return true;
    }

    function closeScheduledRunDetailDialog() {
      scheduledRunDetailDialogOpen.value = false;
      selectedScheduledRun.value = null;
      scheduledRunDetailContent.value = "";
      scheduledRunDetailToolCalls.value = [];
    }

    function findRunLinkedMessage(run: ScheduledTaskRun): ChatMessage | null {
      const messageId = String(run.result_message_id || run.start_message_id || "").trim();
      if (!messageId) {
        return null;
      }
      return messages.value.find((item) => item.id === messageId) ?? null;
    }

    function buildScheduledRunDetailContent(run: ScheduledTaskRun): string {
      const linkedMessage = findRunLinkedMessage(run);
      const linkedContent = linkedMessage?.content?.trim();
      if (linkedContent) {
        return linkedContent;
      }
      const summary = String(run.summary_text || "").trim();
      if (summary) {
        return summary;
      }
      const errorText = String(run.error_text || "").trim();
      if (errorText) {
        return `**Error**\n\n${errorText}`;
      }
      return t("chat.scheduledTaskRunDetailEmpty");
    }

    function buildScheduledRunDetailToolCalls(run: ScheduledTaskRun): ToolCallItem[] {
      const linkedMessage = findRunLinkedMessage(run);
      if (linkedMessage && linkedMessage.toolCalls.length > 0) {
        return linkedMessage.toolCalls.map((tool) => ({
          ...tool,
          expanded: false,
        }));
      }
      const output = String(run.script_output_text || "").trim();
      if (!output) {
        return [];
      }
      const isError = typeof run.script_exit_code === "number" && run.script_exit_code !== 0;
      return [
        {
          id: `scheduled-run-${run.id}`,
          name: "scheduled_script",
          display: "scheduled_script(...)",
          startedAt: parseTimestamp(run.started_at) ?? parseTimestamp(run.created_at),
          finishedAt: parseTimestamp(run.finished_at),
          createdAt: parseTimestamp(run.created_at),
          output,
          status: isError ? "error" : "success",
          hasResult: true,
          expanded: false,
        },
      ];
    }

    function openScheduledRunDetailDialog(run: ScheduledTaskRun) {
      selectedScheduledRun.value = run;
      scheduledRunDetailContent.value = buildScheduledRunDetailContent(run);
      scheduledRunDetailToolCalls.value = buildScheduledRunDetailToolCalls(run);
      scheduledRunDetailDialogOpen.value = true;
    }

    function jumpToScheduledRunMessage(run: ScheduledTaskRun) {
      const targetMessageId = run.result_message_id || run.start_message_id;
      if (targetMessageId && focusMessage(targetMessageId)) {
        showTips(t("chat.scheduledTaskRunLocateSuccess"), "success");
        return;
      }
      openScheduledRunDetailDialog(run);
      showTips(t("chat.scheduledTaskRunLocateFallback"), "info");
    }

    function selectScheduledTask(taskId: string) {
      selectedScheduledTaskId.value = taskId;
      void loadSelectedScheduledTaskRuns();
    }

    function formatDateTime(value: string | null | undefined): string {
      if (!value) {
        return "-";
      }
      const ts = Date.parse(value);
      if (Number.isNaN(ts)) {
        return value;
      }
      return new Date(ts).toLocaleString(dateLocale.value, {
        hour12: false,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }

    function showTips(text: string, color: "success" | "error" | "info" | "warning" = "info") {
      tipsText.value = text;
      tipsColor.value = color;
      tipsOpen.value = false;
      nextTick(() => {
        tipsOpen.value = true;
      });
    }

    function scheduledTaskRunLimitText(task: ScheduledTask): string {
      const count = Number.isFinite(task.run_count) ? task.run_count : 0;
      if (typeof task.max_runs === "number" && Number.isFinite(task.max_runs) && task.max_runs > 0) {
        return t("chat.scheduledTaskRunCountLimited", { count, max: task.max_runs });
      }
      return t("chat.scheduledTaskRunCountUnlimited", { count });
    }

    function openCreateScheduledTaskDialog() {
      scheduledTaskDialogMode.value = "create";
      scheduledTaskFormName.value = "";
      scheduledTaskFormTaskType.value = "hybrid_task";
      scheduledTaskFormEnabled.value = true;
      scheduledTaskFormScheduleType.value = "cron";
      scheduledTaskFormTimezone.value = scheduledTaskDefaultTimezone.value;
      scheduledTaskFormCronExpr.value = "0 9 * * *";
      scheduledTaskFormIntervalMinutes.value = 60;
      scheduledTaskFormScriptCommand.value = "";
      scheduledTaskFormSkillName.value = "";
      scheduledTaskFormSkillInput.value = "";
      scheduledTaskFormSummaryPrompt.value = "";
      scheduledTaskFormMaxRuns.value = null;
      scheduledTaskDialogOpen.value = true;
    }

    function openEditScheduledTaskDialog() {
      const task = selectedScheduledTask.value;
      if (!task) {
        return;
      }
      scheduledTaskDialogMode.value = "edit";
      scheduledTaskFormName.value = task.name;
      scheduledTaskFormTaskType.value = task.task_type === "skill_task" ? "skill_task" : "hybrid_task";
      scheduledTaskFormEnabled.value = task.enabled;
      scheduledTaskFormScheduleType.value = task.schedule_type === "interval" ? "interval" : "cron";
      scheduledTaskFormTimezone.value = task.timezone || scheduledTaskDefaultTimezone.value;
      scheduledTaskFormCronExpr.value = task.cron_expr || "";
      scheduledTaskFormIntervalMinutes.value = task.interval_minutes && task.interval_minutes >= 1 ? task.interval_minutes : 60;
      scheduledTaskFormScriptCommand.value = task.script_command || "";
      scheduledTaskFormSkillName.value = task.skill_name || "";
      scheduledTaskFormSkillInput.value = task.skill_input || "";
      scheduledTaskFormSummaryPrompt.value = task.summary_prompt || "";
      scheduledTaskFormMaxRuns.value = typeof task.max_runs === "number" ? task.max_runs : null;
      scheduledTaskDialogOpen.value = true;
    }

    function closeScheduledTaskDialog() {
      scheduledTaskDialogOpen.value = false;
    }

    async function submitScheduledTaskDialog() {
      if (!auth.state.accessToken || !conversationId.value || scheduledTaskDialogConfirmDisabled.value) {
        return;
      }
      const payload = {
        name: scheduledTaskFormName.value.trim(),
        task_type: scheduledTaskFormTaskType.value,
        enabled: scheduledTaskFormEnabled.value,
        schedule_type: scheduledTaskFormScheduleType.value,
        timezone: scheduledTaskFormTimezone.value.trim(),
        cron_expr: scheduledTaskFormScheduleType.value === "cron" ? scheduledTaskFormCronExpr.value.trim() : undefined,
        interval_minutes:
          scheduledTaskFormScheduleType.value === "interval"
            ? Number(scheduledTaskFormIntervalMinutes.value)
            : undefined,
        script_command:
          scheduledTaskFormTaskType.value === "hybrid_task"
            ? scheduledTaskFormScriptCommand.value.trim()
            : undefined,
        skill_name:
          scheduledTaskFormTaskType.value === "skill_task"
            ? scheduledTaskFormSkillName.value.trim()
            : undefined,
        skill_input:
          scheduledTaskFormTaskType.value === "skill_task"
            ? scheduledTaskFormSkillInput.value.trim() || undefined
            : undefined,
        summary_prompt: scheduledTaskFormSummaryPrompt.value.trim() || undefined,
        max_runs: normalizedScheduledTaskMaxRuns.value,
      };
      scheduledTaskActionLoading.value = true;
      try {
        if (scheduledTaskDialogMode.value === "create") {
          const created = await createScheduledTask(auth.state.accessToken, conversationId.value, payload);
          selectedScheduledTaskId.value = created.id;
        } else {
          const currentTask = selectedScheduledTask.value;
          if (!currentTask) {
            return;
          }
          await updateScheduledTask(auth.state.accessToken, conversationId.value, currentTask.id, payload);
        }
        closeScheduledTaskDialog();
        await loadScheduledTasks();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.scheduledTaskSaveFailed");
      } finally {
        scheduledTaskActionLoading.value = false;
      }
    }

    async function removeSelectedScheduledTask() {
      const task = selectedScheduledTask.value;
      if (!task || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      scheduledTaskActionLoading.value = true;
      try {
        await deleteScheduledTask(auth.state.accessToken, conversationId.value, task.id);
        selectedScheduledTaskId.value = "";
        await loadScheduledTasks();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.scheduledTaskDeleteFailed");
      } finally {
        scheduledTaskActionLoading.value = false;
      }
    }

    async function setSelectedScheduledTaskEnabled(enabled: boolean) {
      const task = selectedScheduledTask.value;
      if (!task || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      if (task.enabled === enabled) {
        return;
      }
      scheduledTaskActionLoading.value = true;
      try {
        await updateScheduledTask(auth.state.accessToken, conversationId.value, task.id, {
          enabled,
        });
        await loadScheduledTasks();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.scheduledTaskSaveFailed");
      } finally {
        scheduledTaskActionLoading.value = false;
      }
    }

    async function executeSelectedScheduledTaskNow() {
      const task = selectedScheduledTask.value;
      if (!task || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      scheduledTaskActionLoading.value = true;
      try {
        await runScheduledTaskNow(auth.state.accessToken, conversationId.value, task.id);
        await loadScheduledTasks();
        await loadSelectedScheduledTaskRuns();
        showTips(t("chat.scheduledTaskRunNowQueuedTip"), "success");
      } catch (error) {
        const message = error instanceof Error ? error.message : t("chat.scheduledTaskSaveFailed");
        errorText.value = message;
        showTips(message, "error");
      } finally {
        scheduledTaskActionLoading.value = false;
      }
    }

    function isFileNodeExpanded(path: string): boolean {
      return expandedFilePaths.value.includes(path);
    }

    function toggleFileNodeExpand(path: string) {
      const index = expandedFilePaths.value.indexOf(path);
      if (index >= 0) {
        expandedFilePaths.value.splice(index, 1);
      } else {
        expandedFilePaths.value.push(path);
      }
    }

    function selectFileNode(node: ConversationFileNode) {
      selectedFilePath.value = node.path;
    }

    function fileNodeIndentStyle(depth: number): { paddingLeft: string } {
      return { paddingLeft: `${8 + depth * 16}px` };
    }

    function fileNodeIcon(node: ConversationFileNode): string {
      if (node.node_type === "directory") {
        return isFileNodeExpanded(node.path) ? "mdi-folder-open-outline" : "mdi-folder-outline";
      }
      if (node.is_text) {
        return "mdi-file-document-outline";
      }
      const lowered = node.name.toLowerCase();
      if (lowered.endsWith(".zip") || lowered.endsWith(".tar") || lowered.endsWith(".tar.gz") || lowered.endsWith(".tgz")) {
        return "mdi-folder-zip-outline";
      }
      if (lowered.endsWith(".png") || lowered.endsWith(".jpg") || lowered.endsWith(".jpeg") || lowered.endsWith(".gif") || lowered.endsWith(".webp")) {
        return "mdi-file-image-outline";
      }
      return "mdi-file-outline";
    }

    function isImageFileName(name: string): boolean {
      const lowered = name.toLowerCase();
      return (
        lowered.endsWith(".png") ||
        lowered.endsWith(".jpg") ||
        lowered.endsWith(".jpeg") ||
        lowered.endsWith(".gif") ||
        lowered.endsWith(".webp") ||
        lowered.endsWith(".bmp") ||
        lowered.endsWith(".svg")
      );
    }

    function detectEditorLanguage(path: string): string {
      const lowered = path.toLowerCase();
      const extension = lowered.includes(".") ? lowered.slice(lowered.lastIndexOf(".")) : "";
      if (extension === ".py") return "python";
      if (extension === ".js" || extension === ".mjs" || extension === ".cjs") return "javascript";
      if (extension === ".ts" || extension === ".mts" || extension === ".cts") return "typescript";
      if (extension === ".jsx") return "javascript";
      if (extension === ".tsx") return "typescript";
      if (extension === ".json" || extension === ".jsonl") return "json";
      if (extension === ".yaml" || extension === ".yml") return "yaml";
      if (extension === ".toml") return "ini";
      if (extension === ".md" || extension === ".markdown") return "markdown";
      if (extension === ".sh" || extension === ".bash" || extension === ".zsh") return "shell";
      if (extension === ".sql") return "sql";
      if (extension === ".html" || extension === ".htm") return "html";
      if (extension === ".css") return "css";
      if (extension === ".scss") return "scss";
      if (extension === ".less") return "less";
      if (extension === ".xml" || extension === ".svg") return "xml";
      if (extension === ".go") return "go";
      if (extension === ".rs") return "rust";
      if (extension === ".java") return "java";
      if (extension === ".kt") return "kotlin";
      if (extension === ".php") return "php";
      if (extension === ".rb") return "ruby";
      if (extension === ".c") return "c";
      if (extension === ".h") return "cpp";
      if (extension === ".cpp" || extension === ".cc" || extension === ".cxx" || extension === ".hpp" || extension === ".hh") return "cpp";
      if (extension === ".swift") return "swift";
      if (extension === ".dockerfile" || lowered.endsWith("/dockerfile")) return "dockerfile";
      return "plaintext";
    }

    function formatFileNodeSize(size: number | null): string {
      if (typeof size !== "number" || size < 0) {
        return "-";
      }
      return formatAttachmentSize(size);
    }

    function formatFileNodeMtime(value: string): string {
      const ts = Date.parse(value);
      if (Number.isNaN(ts)) {
        return value;
      }
      return new Date(ts).toLocaleString(dateLocale.value, {
        hour12: false,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    }

    function formatFileNodeCtime(value: string): string {
      const ts = Date.parse(value);
      if (Number.isNaN(ts)) {
        return value;
      }
      return new Date(ts).toLocaleString(dateLocale.value, {
        hour12: false,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    }

    function toggleFileSortDirection() {
      fileSortDirection.value = fileSortDirection.value === "asc" ? "desc" : "asc";
    }

    function openFileManagerUploadPicker() {
      fileManagerUploadInput.value?.click();
    }

    async function onFileManagerUploadInputChange(event: Event) {
      const target = event.target as HTMLInputElement;
      const selected = Array.from(target.files ?? []);
      target.value = "";
      if (selected.length === 0 || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      filesActionLoading.value = true;
      try {
        await uploadConversationFilesToDirectory(
          auth.state.accessToken,
          conversationId.value,
          selected,
          normalizeManagedPath(selectedUploadDirectory.value),
          fileManagerRoot.value,
        );
        await loadFilesTree();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.filesUploadFailed");
      } finally {
        filesActionLoading.value = false;
      }
    }

    function openFileOperationDialog(mode: FileOperationDialogMode) {
      const selected = selectedFileNode.value;
      if (mode === "rename" && (!selected || selected.path === fileManagerRoot.value)) {
        return;
      }
      if (mode === "archive" && (!selected || selected.node_type !== "directory")) {
        return;
      }

      fileOperationDialogMode.value = mode;
      if (mode === "new-folder") {
        fileOperationInput.value = "";
      } else if (mode === "new-file") {
        fileOperationInput.value = "untitled.txt";
      } else if (mode === "rename" && selected) {
        fileOperationInput.value = selected.name;
      } else if (mode === "archive" && selected) {
        fileOperationInput.value = `${selected.name}.zip`;
      }
      fileOperationDialogOpen.value = true;
    }

    function closeFileOperationDialog() {
      fileOperationDialogOpen.value = false;
      fileOperationInput.value = "";
      fileOperationSubmitting.value = false;
    }

    function createDirectoryInSelectedPath() {
      openFileOperationDialog("new-folder");
    }

    function createTextFileInSelectedPath() {
      openFileOperationDialog("new-file");
    }

    function renameSelectedFileNode() {
      openFileOperationDialog("rename");
    }

    function openDeleteFileNodeDialog() {
      if (!selectedFileNode.value || selectedFileNode.value.path === fileManagerRoot.value) {
        return;
      }
      deleteTargetNode.value = selectedFileNode.value;
      deleteConfirmName.value = "";
      deleteFileDialogOpen.value = true;
    }

    function closeDeleteFileNodeDialog() {
      deleteFileDialogOpen.value = false;
      deleteTargetNode.value = null;
      deleteConfirmName.value = "";
    }

    async function confirmDeleteFileNode() {
      const node = deleteTargetNode.value;
      if (!node || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      if (requiresDeleteNameConfirm.value && deleteConfirmDisabled.value) {
        return;
      }

      filesActionLoading.value = true;
      try {
        await deleteConversationFileNode(auth.state.accessToken, conversationId.value, {
          path: node.path,
          recursive: node.node_type === "directory",
          confirm_name: requiresDeleteNameConfirm.value ? deleteConfirmName.value.trim() : undefined,
        });
        closeDeleteFileNodeDialog();
        if (selectedFilePath.value === node.path) {
          selectedFilePath.value = "";
        }
        await loadFilesTree();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.filesDeleteFailed");
      } finally {
        filesActionLoading.value = false;
      }
    }

    async function extractSelectedArchiveFile() {
      const node = selectedFileNode.value;
      if (!node || node.node_type !== "file" || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      filesActionLoading.value = true;
      try {
        const result = await extractConversationArchive(auth.state.accessToken, conversationId.value, {
          archive_path: node.path,
        });
        selectedFilePath.value = result.target_path;
        await loadFilesTree();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.filesExtractFailed");
      } finally {
        filesActionLoading.value = false;
      }
    }

    function archiveSelectedDirectoryNode() {
      openFileOperationDialog("archive");
    }

    async function submitFileOperationDialog() {
      const name = fileOperationInput.value.trim();
      if (!name || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      const selected = selectedFileNode.value;

      fileOperationSubmitting.value = true;
      filesActionLoading.value = true;
      try {
        if (fileOperationDialogMode.value === "new-folder") {
          await createConversationFileDirectory(auth.state.accessToken, conversationId.value, {
            directory_path: joinManagedPath(selectedUploadDirectory.value, name),
          });
        } else if (fileOperationDialogMode.value === "new-file") {
          await createConversationTextFile(auth.state.accessToken, conversationId.value, {
            file_path: joinManagedPath(selectedUploadDirectory.value, name),
            content: "",
          });
        } else if (fileOperationDialogMode.value === "rename") {
          if (!selected) {
            return;
          }
          const renamed = await renameConversationFileNode(auth.state.accessToken, conversationId.value, {
            path: selected.path,
            new_name: name,
          });
          selectedFilePath.value = renamed.path;
        } else if (fileOperationDialogMode.value === "archive") {
          if (!selected || selected.node_type !== "directory") {
            return;
          }
          const archived = await archiveConversationDirectory(auth.state.accessToken, conversationId.value, {
            directory_path: selected.path,
            output_name: name,
          });
          selectedFilePath.value = archived.path;
        }
        closeFileOperationDialog();
        await loadFilesTree();
      } catch (error) {
        if (fileOperationDialogMode.value === "new-folder") {
          errorText.value = error instanceof Error ? error.message : t("chat.filesCreateFolderFailed");
        } else if (fileOperationDialogMode.value === "new-file") {
          errorText.value = error instanceof Error ? error.message : t("chat.filesCreateFileFailed");
        } else if (fileOperationDialogMode.value === "rename") {
          errorText.value = error instanceof Error ? error.message : t("chat.filesRenameFailed");
        } else {
          errorText.value = error instanceof Error ? error.message : t("chat.filesArchiveFailed");
        }
      } finally {
        fileOperationSubmitting.value = false;
        filesActionLoading.value = false;
      }
    }

    function fileNodeDownloadUrl(path: string, name: string, inline = false): string {
      if (!auth.state.accessToken || !conversationId.value || !path) {
        return "#";
      }
      const encodedConversationId = encodeURIComponent(conversationId.value);
      const encodedPath = encodeAttachmentPath(path);
      const query = new URLSearchParams({
        token: auth.state.accessToken,
        name,
      });
      if (inline) {
        query.set("inline", "1");
      }
      const base = apiBase().replace(/\/$/, "");
      return `${base}/api/v1/conversations/${encodedConversationId}/attachments/${encodedPath}?${query.toString()}`;
    }

    async function openTextEditorForSelectedFile() {
      const node = selectedFileNode.value;
      if (!node || node.node_type !== "file" || !node.is_text || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      editFileLoading.value = true;
      editFilePath.value = node.path;
      editFileLanguage.value = detectEditorLanguage(node.path);
      editFileContent.value = "";
      try {
        const payload = await readConversationTextFile(auth.state.accessToken, conversationId.value, node.path);
        editFileContent.value = payload.content;
        editFileDialogOpen.value = true;
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.filesReadTextFailed");
      } finally {
        editFileLoading.value = false;
      }
    }

    function closeViewFileDialog() {
      viewFileDialogOpen.value = false;
      viewFilePath.value = "";
      viewFileName.value = "";
      viewFileTextContent.value = "";
      viewFileLoading.value = false;
      viewFileMode.value = "text";
      viewFileLanguage.value = "plaintext";
    }

    async function openViewDialogForSelectedFile() {
      const node = selectedFileNode.value;
      if (!node || node.node_type !== "file") {
        return;
      }
      if (!node.is_text && !isImageFileName(node.name)) {
        return;
      }
      viewFilePath.value = node.path;
      viewFileName.value = node.name;

      if (node.is_text) {
        if (!auth.state.accessToken || !conversationId.value) {
          return;
        }
        viewFileMode.value = "text";
        viewFileLanguage.value = detectEditorLanguage(node.path);
        viewFileTextContent.value = "";
        viewFileLoading.value = true;
        viewFileDialogOpen.value = true;
        try {
          const payload = await readConversationTextFile(auth.state.accessToken, conversationId.value, node.path);
          viewFileTextContent.value = payload.content;
        } catch (error) {
          closeViewFileDialog();
          errorText.value = error instanceof Error ? error.message : t("chat.filesReadTextFailed");
        } finally {
          viewFileLoading.value = false;
        }
        return;
      }

      viewFileMode.value = "image";
      viewFileLanguage.value = "plaintext";
      viewFileTextContent.value = "";
      viewFileLoading.value = false;
      viewFileDialogOpen.value = true;
    }

    function closeTextEditorDialog() {
      editFileDialogOpen.value = false;
      editFilePath.value = "";
      editFileLanguage.value = "plaintext";
      editFileContent.value = "";
    }

    async function saveTextEditorContent() {
      if (!editFilePath.value || !auth.state.accessToken || !conversationId.value) {
        return;
      }
      editFileSaving.value = true;
      try {
        await writeConversationTextFile(auth.state.accessToken, conversationId.value, {
          path: editFilePath.value,
          content: editFileContent.value,
        });
        await loadFilesTree();
        closeTextEditorDialog();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.filesWriteTextFailed");
      } finally {
        editFileSaving.value = false;
      }
    }

    function handleStreamEvent(event: ChatEvent) {
      const eventConversationId =
        typeof event.session_id === "string" && event.session_id.trim() ? event.session_id : conversationId.value;
      if (eventConversationId !== conversationId.value && event.type !== "pong") {
        return;
      }

      const rawSeq = event.seq;
      if (typeof rawSeq === "number" && Number.isFinite(rawSeq)) {
        if (!updateEventCursor(eventConversationId, rawSeq)) {
          return;
        }
      } else if (typeof rawSeq === "string" && rawSeq.trim()) {
        const parsed = Number(rawSeq);
        if (Number.isFinite(parsed)) {
          if (!updateEventCursor(eventConversationId, parsed)) {
            return;
          }
        }
      }

      if (event.type === "subscribed") {
        const running = event.running === true;
        if (!running && busy.value) {
          markAssistantDone({ interrupted: true });
        }
        return;
      }
      if (event.type === "accepted" || event.type === "pong") {
        return;
      }
      if (event.type === "interrupt_ack") {
        const accepted = event.accepted === true;
        if (!accepted) {
          interrupting.value = false;
        }
        return;
      }

      if (event.type === "status") {
        const status = String(event.status ?? "");
        if (status === "running") {
          ensureStreamRunning();
        }
        return;
      }
      if (event.type === "text") {
        ensureStreamRunning();
        appendAssistantText(String(event.delta ?? ""));
        scrollToBottomSoon();
        return;
      }
      if (event.type === "tool_call") {
        ensureStreamRunning();
        upsertToolCall(event, false);
        scrollToBottomSoon();
        return;
      }
      if (event.type === "tool_result") {
        ensureStreamRunning();
        upsertToolCall(event, true);
        scrollToBottomSoon();
        return;
      }
      if (event.type === "warning") {
        appendMessage("system", String(event.message ?? t("chat.warning")));
        return;
      }
      if (event.type === "system_message") {
        appendMessage(
          "system",
          String(event.message ?? ""),
          false,
          [],
          false,
          typeof event.message_id === "string" ? event.message_id : undefined,
        );
        return;
      }
      if (event.type === "error") {
        interrupting.value = false;
        const text = String(event.message ?? t("chat.unknownError"));
        if (activeAssistantId.value) {
          appendAssistantText(`\n\n[error] ${text}`);
          markAssistantDone();
        } else {
          appendMessage("system", `[error] ${text}`);
          busy.value = false;
        }
        void loadSandbox();
        return;
      }
      if (event.type === "done") {
        markAssistantDone({
          interrupted: event.interrupted === true,
        });
        void loadSandbox();
      }
    }

    function scheduleReconnect() {
      if (shuttingDown.value) {
        return;
      }
      if (reconnectTimer.value !== null) {
        return;
      }
      reconnectTimer.value = window.setTimeout(() => {
        reconnectTimer.value = null;
        connectSocket();
      }, 1200);
    }

    function connectSocket() {
      if (!auth.state.accessToken) {
        return;
      }
      if (shuttingDown.value) {
        return;
      }
      const current = socket.value;
      if (current && (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING)) {
        return;
      }

      socketState.value = "connecting";
      const ws = new WebSocket(buildSocketUrl());
      socket.value = ws;

      ws.onopen = () => {
        socketState.value = "open";
        sendSubscribeRequest();
      };

      ws.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as ChatEvent;
          handleStreamEvent(event);
        } catch {
          appendMessage("system", t("chat.malformedEvent"));
          markAssistantDone();
        }
      };

      ws.onerror = () => {
        socketState.value = "closed";
      };

      ws.onclose = (event) => {
        socketState.value = "closed";
        interrupting.value = false;
        if (event.code === 4401) {
          void handleSocketUnauthorized();
          return;
        }
        scheduleReconnect();
      };
    }

    async function handleSocketUnauthorized() {
      if (socketAuthRefreshing.value) {
        return;
      }
      const refresh = auth.state.refreshToken?.trim();
      if (refresh) {
        socketAuthRefreshing.value = true;
        try {
          const payload = await refreshToken({ refresh_token: refresh });
          auth.setAuth({
            accessToken: payload.access_token,
            refreshToken: payload.refresh_token,
            user: payload.user,
          });
          connectSocket();
          return;
        } catch {
          // fall through to hard logout
        } finally {
          socketAuthRefreshing.value = false;
        }
      }
      auth.clearAuth();
      if (!redirectingToLogin && route.path !== "/login") {
        redirectingToLogin = true;
        void router.push("/login").finally(() => {
          redirectingToLogin = false;
        });
      }
    }

    async function loadModelsAndConversation(targetConversationId?: string, version: number | null = null) {
      const cid = targetConversationId ?? conversationId.value;
      if (!auth.state.accessToken || !cid) {
        return;
      }
      errorText.value = "";
      try {
        const token = auth.state.accessToken;
        const [modelsPayload, conversation, history, toolHistory] = await Promise.all([
          getModels(),
          getConversation(token, cid),
          (async (): Promise<MessageListResponse> => {
            const pageSize = 200;
            let page = 1;
            let total = 0;
            const items: MessageListResponse["items"] = [];
            do {
              const chunk = await listMessages(token, cid, { page, page_size: pageSize });
              if (page === 1) {
                total = chunk.total;
              }
              if (Array.isArray(chunk.items) && chunk.items.length > 0) {
                items.push(...chunk.items);
              }
              if (!Array.isArray(chunk.items) || chunk.items.length < pageSize) {
                break;
              }
              page += 1;
            } while (items.length < total);
            return {
              items,
              total: items.length,
              page: 1,
              page_size: items.length || pageSize,
            };
          })(),
          (async (): Promise<ToolEventListResponse> => {
            const pageSize = 300;
            let page = 1;
            let total = 0;
            const items: ToolEventListResponse["items"] = [];
            do {
              const chunk = await listToolEvents(token, cid, { page, page_size: pageSize });
              if (page === 1) {
                total = chunk.total;
              }
              if (Array.isArray(chunk.items) && chunk.items.length > 0) {
                items.push(...chunk.items);
              }
              if (!Array.isArray(chunk.items) || chunk.items.length < pageSize) {
                break;
              }
              page += 1;
            } while (items.length < total);
            return {
              items,
              total: items.length,
              page: 1,
              page_size: items.length || pageSize,
            };
          })(),
        ]);
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        const models = modelsPayload as ModelsResponse;
        providers.value = models.providers;
        selectedProvider.value = conversation.default_provider || models.default_provider;
        const allowedModels =
          providers.value.find((entry) => entry.name === selectedProvider.value)?.models ?? [];
        selectedModel.value =
          conversation.default_model && allowedModels.includes(conversation.default_model)
            ? conversation.default_model
            : (allowedModels[0] ?? models.default_model);

        messages.value = history.items.map((item) => {
          const role: Role = item.role === "user" || item.role === "assistant" ? item.role : "system";
          const persistedContent =
            role === "assistant"
              ? parsePersistedAssistantContent(item.content || "")
              : {
                  ...normalizeScheduledMessage(item.content || ""),
                  interrupted: false,
                };
          return {
            id: item.id,
            role,
            content: persistedContent.content,
            interrupted: persistedContent.interrupted,
            attachments: Array.isArray(item.attachments) ? item.attachments : [],
            loading: false,
            createdAt: Date.parse(item.created_at) || Date.now(),
            durationSeconds:
              typeof item.duration_ms === "number" && item.duration_ms > 0
                ? Math.max(0, Math.floor(item.duration_ms / 1000))
                : undefined,
            toolCalls: [],
            scheduledTag: persistedContent.tag,
          };
        });
        if (messages.value.length === 0) {
          appendMessage("system", t("chat.readyToAsk"));
        }
        attachHistoricalToolEvents(toolHistory.items);
      } catch (error) {
        if (isStaleBootstrap(version, cid)) {
          return;
        }
        errorText.value = error instanceof Error ? error.message : t("chat.loadConversationFailed");
      }
      await Promise.all([loadSandbox(cid, version), loadFilesTree(cid, version), loadScheduledTasks(cid, version)]);
    }

    function sendMessage() {
      const text = draft.value.trim();
      const attachments = pendingAttachments.value.slice();
      if ((!text && attachments.length === 0) || !canSend.value || !socket.value || !conversationId.value) {
        return;
      }
      appendMessage("user", text, false, attachments);
      const assistantId = appendMessage("assistant", "", true);
      activeAssistantId.value = assistantId;
      interrupting.value = false;
      busy.value = true;
      startThinkingTimer();

      socket.value.send(
        JSON.stringify({
          type: "chat",
          session_id: conversationId.value,
          message: text,
          attachments: attachments.map((item) => ({
            path: item.path,
            name: item.name,
            mime_type: item.mime_type,
            size: item.size,
          })),
          provider: selectedProvider.value || undefined,
          model: selectedModel.value || undefined,
        }),
      );
      draft.value = "";
      pendingAttachments.value = [];
    }

    function interruptRun() {
      if (!socket.value || !conversationId.value || !canInterrupt.value) {
        return;
      }
      interrupting.value = true;
      socket.value.send(
        JSON.stringify({
          type: "interrupt",
          session_id: conversationId.value,
        }),
      );
    }

    function parsePersistedAssistantContent(
      raw: string,
    ): { content: string; interrupted: boolean; tag: "start" | "result" | null } {
      const normalized = normalizeScheduledMessage(raw);
      const source = normalized.content;
      const trimmedEnd = source.trimEnd();
      if (!trimmedEnd.endsWith(INTERRUPTED_MARKER)) {
        return { content: source, interrupted: false, tag: normalized.tag };
      }
      const contentWithoutMarker = trimmedEnd
        .slice(0, trimmedEnd.length - INTERRUPTED_MARKER.length)
        .replace(/\n+$/u, "");
      return { content: contentWithoutMarker, interrupted: true, tag: normalized.tag };
    }

    function scrollToBottomSoon() {
      nextTick(() => {
        if (!chatViewport.value) {
          return;
        }
        chatViewport.value.scrollTop = chatViewport.value.scrollHeight;
      });
    }

    function stopSandboxRefreshPolling() {
      if (sandboxRefreshTimer.value !== null) {
        window.clearTimeout(sandboxRefreshTimer.value);
        sandboxRefreshTimer.value = null;
      }
    }

    function startSandboxRefreshPolling(targetConversationId: string, attempts = 20) {
      stopSandboxRefreshPolling();
      const version = bootstrapVersion.value;
      const tick = async (remaining: number) => {
        if (shuttingDown.value || conversationId.value !== targetConversationId) {
          stopSandboxRefreshPolling();
          return;
        }
        await loadSandbox(targetConversationId, version);
        const current = sandboxInfo.value;
        const ready = current?.status === "running" && Boolean(current.container_name);
        if (ready || remaining <= 1) {
          stopSandboxRefreshPolling();
          return;
        }
        sandboxRefreshTimer.value = window.setTimeout(() => {
          void tick(remaining - 1);
        }, 1000);
      };
      void tick(attempts);
    }

    async function restartSandboxNow() {
      if (!auth.state.accessToken || !conversationId.value) {
        return;
      }
      try {
        const currentConversationId = conversationId.value;
        sandboxInfo.value = await restartSandbox(auth.state.accessToken, currentConversationId);
        startSandboxRefreshPolling(currentConversationId);
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.restartSandboxFailed");
      }
    }

    function toolStatusLabel(status: string): string {
      return status === "error" ? t("chat.failed") : t("chat.done");
    }

    async function bootstrap() {
      const version = bootstrapVersion.value + 1;
      bootstrapVersion.value = version;
      const targetConversationId = conversationId.value;

      socket.value?.close();
      socket.value = null;
      stopSandboxRefreshPolling();
      activeAssistantId.value = null;
      busy.value = false;
      interrupting.value = false;
      pendingAttachments.value = [];
      uploadingAttachments.value = false;
      fileTreeItems.value = [];
      selectedFilePath.value = "";
      expandedFilePaths.value = [];
      scheduledTasks.value = [];
      selectedScheduledTaskId.value = "";
      scheduledTaskRuns.value = [];
      closeScheduledRunDetailDialog();
      clearFocusedMessage();
      scheduledTaskDialogOpen.value = false;
      closeFileOperationDialog();
      closeDeleteFileNodeDialog();
      closeTextEditorDialog();
      closeViewFileDialog();
      await loadModelsAndConversation(targetConversationId, version);
      if (isStaleBootstrap(version, targetConversationId)) {
        return;
      }
      connectSocket();
    }

    async function loadAppSettingsForTimezone() {
      try {
        const settings = await getAppSettings();
        const tz =
          typeof settings.sandbox_timezone === "string"
            ? settings.sandbox_timezone.trim()
            : "";
        if (!tz) {
          return;
        }
        scheduledTaskDefaultTimezone.value = tz;
        if (!scheduledTaskDialogOpen.value || scheduledTaskDialogMode.value === "create") {
          scheduledTaskFormTimezone.value = tz;
        }
      } catch {
        // Keep client fallback when backend settings are unavailable.
      }
    }

    watch(selectedProvider, () => {
      const available = modelItems.value;
      if (!available.includes(selectedModel.value)) {
        selectedModel.value = available[0] ?? "";
      }
    });

    watch(
      () => messages.value.length,
      () => {
        scrollToBottomSoon();
      },
    );

    watch(
      () => route.params.id,
      () => {
        void bootstrap();
      },
    );

    watch(
      () => settingsTab.value,
      (tab) => {
        if (tab === "files" || tab === "skills") {
          selectedFilePath.value = "";
          expandedFilePaths.value = [];
          void loadFilesTree();
          return;
        }
        if (tab === "scheduled-tasks") {
          void loadScheduledTasks();
        }
      },
    );

    onMounted(() => {
      void (async () => {
        await loadAppSettingsForTimezone();
        await bootstrap();
      })();
    });

    onBeforeUnmount(() => {
      shuttingDown.value = true;
      stopThinkingTimer();
      clearFocusedMessage();
      stopSandboxRefreshPolling();
      if (reconnectTimer.value !== null) {
        window.clearTimeout(reconnectTimer.value);
      }
      socket.value?.close();
    });

    return {
      conversationId,
      selectedProvider,
      selectedModel,
      providerItems,
      modelItems,
      statusLabel,
      statusColor,
      showToolMessages,
      settingsTab,
      sandboxLabel,
      sandboxCreatedAtLabel,
      fileTreeItems,
      fileSortField,
      fileSortDirection,
      fileSortFieldItems,
      flatFileRows,
      fileManagerRoot,
      filesEmptyText,
      filesTreeLoading,
      filesActionLoading,
      selectedFilePath,
      selectedFileNode,
      selectedUploadDirectory,
      canEditSelectedTextFile,
      canPreviewSelectedFile,
      canExtractSelectedArchive,
      canArchiveSelectedDirectory,
      canRenameSelectedNode,
      canDeleteSelectedNode,
      canDownloadSelectedFile,
      fileManagerUploadInput,
      deleteFileDialogOpen,
      deleteTargetNode,
      deleteConfirmName,
      deleteConfirmDisabled,
      requiresDeleteNameConfirm,
      editFileDialogOpen,
      editFilePath,
      editFileLanguage,
      editFileContent,
      editFileLoading,
      editFileSaving,
      viewFileDialogOpen,
      viewFilePath,
      viewFileName,
      viewFileMode,
      viewFileLanguage,
      viewFileTextContent,
      viewFileLoading,
      fileOperationDialogOpen,
      fileOperationDialogTitle,
      fileOperationDialogHint,
      fileOperationInputLabel,
      fileOperationInput,
      fileOperationSubmitting,
      fileOperationConfirmDisabled,
      scheduledTasks,
      scheduledTasksLoading,
      scheduledTaskActionLoading,
      selectedScheduledTaskId,
      selectedScheduledTask,
      scheduledTaskRuns,
      scheduledTaskRunsLoading,
      scheduledRunDetailDialogOpen,
      selectedScheduledRun,
      scheduledRunDetailToolCalls,
      focusedMessageId,
      scheduledTaskDialogOpen,
      scheduledTaskDialogMode,
      scheduledTaskFormName,
      scheduledTaskFormTaskType,
      scheduledTaskFormEnabled,
      scheduledTaskFormScheduleType,
      scheduledTaskFormTimezone,
      scheduledTaskFormCronExpr,
      scheduledTaskFormIntervalMinutes,
      scheduledTaskFormScriptCommand,
      scheduledTaskFormSkillName,
      scheduledTaskFormSkillInput,
      scheduledTaskFormSummaryPrompt,
      scheduledTaskFormMaxRuns,
      scheduledTaskDialogConfirmDisabled,
      scheduledTaskTaskTypeHelpText,
      errorText,
      tipsOpen,
      tipsText,
      tipsColor,
      messages,
      draft,
      pendingAttachments,
      uploadingAttachments,
      attachmentMenuOpen,
      busy,
      canInterrupt,
      interrupting,
      canSend,
      uploadInput,
      chatViewport,
      bubbleColor,
      messageAvatarText,
      messageAvatarSrc,
      messageHtml,
      scheduledRunDetailHtml,
      messageMeta,
      toolCallTimeText,
      toolCallMetaText,
      scheduledTagLabel,
      attachmentPreviewUrl,
      attachmentDownloadUrl,
      formatAttachmentSize,
      formatFileNodeSize,
      formatFileNodeMtime,
      formatFileNodeCtime,
      formatDateTime,
      scheduledTaskRunLimitText,
      showTips,
      messageDomId,
      fileNodeIndentStyle,
      fileNodeIcon,
      isFileNodeExpanded,
      visibleToolOutput,
      isCollapsible,
      toolStatusLabel,
      t,
      openAttachmentPicker,
      onAttachmentInputChange,
      onComposerPaste,
      removePendingAttachment,
      loadFilesTree,
      loadScheduledTasks,
      loadSelectedScheduledTaskRuns,
      jumpToScheduledRunMessage,
      openScheduledRunDetailDialog,
      toggleFileNodeExpand,
      selectFileNode,
      selectScheduledTask,
      openFileManagerUploadPicker,
      onFileManagerUploadInputChange,
      toggleFileSortDirection,
      createDirectoryInSelectedPath,
      createTextFileInSelectedPath,
      openCreateScheduledTaskDialog,
      openEditScheduledTaskDialog,
      closeScheduledTaskDialog,
      submitScheduledTaskDialog,
      removeSelectedScheduledTask,
      setSelectedScheduledTaskEnabled,
      executeSelectedScheduledTaskNow,
      renameSelectedFileNode,
      openDeleteFileNodeDialog,
      closeDeleteFileNodeDialog,
      confirmDeleteFileNode,
      extractSelectedArchiveFile,
      archiveSelectedDirectoryNode,
      fileNodeDownloadUrl,
      openTextEditorForSelectedFile,
      closeTextEditorDialog,
      saveTextEditorContent,
      openViewDialogForSelectedFile,
      closeViewFileDialog,
      closeFileOperationDialog,
      submitFileOperationDialog,
      closeScheduledRunDetailDialog,
      sendMessage,
      interruptRun,
      restartSandboxNow,
    };
  },
});
