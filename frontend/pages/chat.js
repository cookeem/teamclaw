const NewChatPage = {
  template: `
    <v-container fluid class="page-shell">
      <h2 class="page-title">{{ $t('chat.new_chat') }}</h2>
      <v-card class="panel-card" rounded="xl">
        <v-card-text>
          <v-row>
            <v-col cols="12" md="4">
              <v-text-field :label="$t('chat.conversation_title')" v-model="title" variant="outlined" hide-details />
            </v-col>
            <v-col cols="12" md="4">
              <v-select :label="$t('chat.model')" v-model="model" :items="models" variant="outlined" hide-details />
            </v-col>
            <v-col cols="12" md="4" class="d-flex align-center justify-end">
              <v-btn :loading="loading" color="secondary" @click="createConversation">{{ $t('chat.create_and_enter') }}</v-btn>
            </v-col>
          </v-row>
        </v-card-text>
      </v-card>
      <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
    </v-container>
  `,
  setup() {
    const { t } = window.TeamClawI18n;
    const router = useRouter();
    const makeSuffix = () => {
      const now = new Date();
      const pad = (num) => String(num).padStart(2, "0");
      const suffix = `${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(
        now.getMinutes()
      )}`;
      return suffix;
    };
    const title = ref(`${t("app.new_conversation")} - ${makeSuffix()}`);
    const model = ref("default");
    const models = ["default"];
    const loading = ref(false);
    const error = ref("");

    const createConversation = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await apiFetch("/conversations", {
          method: "POST",
          body: JSON.stringify({ title: title.value, model: model.value, skills: [], tools: [] }),
        });
        router.push(`/chat/${data.id}`);
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    };

    return { title, model, models, loading, error, createConversation };
  },
};

const ChatPage = {
  template: `
    <v-container fluid class="chat-shell pa-0">
      <v-row no-gutters class="chat-row">
        <v-col cols="12" md="8" class="chat-main">
          <div class="chat-header">
            <div>
              <div class="chat-title">
                {{ conversationTitle }}
                <span v-if="conversationModel" class="chat-model">model: {{ conversationModel }}</span>
              </div>
              <div class="chat-sub">{{ $t('chat.container_status') }}：<span class="status-pill">running</span></div>
            </div>
            <v-chip color="secondary" variant="tonal">{{ $t('chat.message_count') }}：{{ messages.length }}</v-chip>
          </div>

          <div class="messages-wrap" ref="messagesWrapRef">
            <div v-for="msg in messages" :key="msg.id" class="msg-row" :class="msg.sender_role === 'human' ? 'human' : 'assistant'">
              <div class="chat-avatar">
                <img
                  v-if="msg.sender_role === 'human' && session.user?.avatar_url"
                  :src="toAssetUrl(session.user.avatar_url)"
                  alt="avatar"
                />
                <span v-else>{{ msg.sender_role === 'human' ? userInitials(session.user?.display_name, session.user?.username) : 'TC' }}</span>
              </div>
              <div class="msg-bubble">
                <div class="msg-meta">
                  <span>{{ msg.message_type }}</span>
                  <span v-if="msg.tool_name">tool={{ msg.tool_name }}</span>
                  <span>{{ msg.message_status }}</span>
                  <span>{{ formatMessageTime(msg.created_at) }}</span>
                </div>
                <div class="msg-content" v-html="renderMarkdown(msg.content_md)"></div>
                <div class="msg-attachments" v-if="msg.attachments_json && msg.attachments_json.items && msg.attachments_json.items.length">
                  <div v-for="(att, idx) in msg.attachments_json.items" :key="att.saved_name || idx" class="attachment-card">
                    <div class="attachment-title">{{ att.original_name || att.saved_name }}</div>
                    <div class="attachment-meta">
                      <span v-if="att.size_bytes">{{ $t('chat.size') }}: {{ formatBytes(att.size_bytes) }}</span>
                      <span v-if="att.content_type">{{ $t('chat.type') }}: {{ att.content_type }}</span>
                      <span v-if="att.workspace_path">{{ $t('chat.path') }}: {{ att.workspace_path }}</span>
                    </div>
                    <div class="attachment-actions">
                      <v-btn v-if="att.workspace_path" size="x-small" variant="tonal" @click="downloadAttachment(att.workspace_path, att.original_name || att.saved_name)">{{ $t('chat.download_original') }}</v-btn>
                      <v-btn v-if="att.markdown && att.markdown.workspace_path" size="x-small" variant="tonal" @click="downloadAttachment(att.markdown.workspace_path, (att.original_name || att.saved_name || 'document') + '.md')">{{ $t('chat.download_markdown') }}</v-btn>
                      <v-btn v-if="att.markdown && att.markdown.workspace_path" size="x-small" variant="tonal" @click="openMarkdown(att)">{{ $t('chat.view_markdown') }}</v-btn>
                      <span v-else-if="att.markdown && att.markdown.error" class="attachment-error">{{ $t('chat.parse_failed', { error: att.markdown.error }) }}</span>
                      <span v-else-if="att.markdown && att.markdown.warnings && att.markdown.warnings.length" class="attachment-warn">{{ $t('chat.warning', { warning: att.markdown.warnings.join('; ') }) }}</span>
                    </div>
                  </div>
                </div>
                <div class="msg-foot" v-if="msg.sender_role === 'assistant' && (hasTokenUsage(msg) || (msg.run_duration_ms || 0) > 0)">
                  <span v-if="hasTokenUsage(msg)">{{ $t('chat.tokens', { input: msg.input_tokens, output: msg.output_tokens, total: msg.total_tokens }) }}</span>
                  <span v-if="(msg.run_duration_ms || 0) > 0">{{ $t('chat.duration', { ms: msg.run_duration_ms }) }}</span>
                </div>
              </div>
            </div>
            <v-alert v-if="pendingInterruptId" type="warning" variant="tonal" class="mt-2">
              {{ $t('chat.interrupt_detected', { id: pendingInterruptId }) }}
              <div class="d-flex ga-2 mt-2">
                <v-btn size="small" color="success" variant="tonal" :loading="deciding" @click="decide('allow')">allow</v-btn>
                <v-btn size="small" color="error" variant="tonal" :loading="deciding" @click="decide('reject')">reject</v-btn>
                <v-btn size="small" color="secondary" variant="flat" :loading="deciding" @click="decide('allow_all')">allow_all</v-btn>
              </div>
            </v-alert>
            <v-alert v-if="error" type="error" variant="tonal" class="mt-2">{{ error }}</v-alert>
          </div>

          <div class="composer">
            <v-textarea v-model="input" rows="2" auto-grow variant="outlined" hide-details :placeholder="$t('chat.input_placeholder')"></v-textarea>
            <div class="composer-actions">
              <div class="composer-left">
                <v-file-input
                  v-model="attachmentFiles"
                  class="composer-upload"
                  density="compact"
                  variant="outlined"
                  hide-details
                  prepend-icon="mdi-paperclip"
                  show-size
                  accept=".doc,.docx,.xls,.xlsx,.ppt,.pptx,.pdf"
                  multiple
                  :label="$t('chat.upload_label')"
                />
                <v-btn size="small" variant="tonal" :loading="uploadingAttachments" :disabled="!hasAttachmentFiles" @click="uploadAttachments">{{ $t('chat.upload') }}</v-btn>
              </div>
              <v-btn :loading="sending" color="secondary" prepend-icon="mdi-send" :disabled="sending || uploadingAttachments" @click="send">{{ $t('chat.send') }}</v-btn>
            </div>
          </div>

          <v-dialog v-model="markdownDialog" max-width="860">
            <v-card>
              <v-card-title>{{ $t('chat.markdown_title') }}</v-card-title>
              <v-card-text>
                <div v-if="markdownLoading" class="text-medium-emphasis">{{ $t('common.loading') }}</div>
                <div v-else-if="markdownError" class="text-error">{{ markdownError }}</div>
                <div v-else class="markdown-view" v-html="renderMarkdown(markdownContent)"></div>
                <div v-if="markdownTruncated" class="text-medium-emphasis mt-2">{{ $t('chat.content_truncated') }}</div>
              </v-card-text>
              <v-card-actions>
                <v-spacer></v-spacer>
                <v-btn variant="text" @click="markdownDialog=false">{{ $t('common.close') }}</v-btn>
              </v-card-actions>
            </v-card>
          </v-dialog>
        </v-col>

        <v-col cols="12" md="4" class="task-panel-col">
          <div class="task-panel">
            <v-tabs v-model="sideTab" color="secondary" density="compact" class="mb-2">
              <v-tab value="tasks">{{ $t('chat.tasks_panel') }}</v-tab>
              <v-tab value="files">{{ $t('chat.my_files') }}</v-tab>
              <v-tab value="agent-skills">{{ $t('chat.agent_skills') }}</v-tab>
            </v-tabs>
            <v-window v-model="sideTab">
              <v-window-item value="tasks">
                <v-list density="compact" class="bg-transparent">
                  <v-list-item title="deepagents" subtitle="stream + interrupt/resume"></v-list-item>
                  <v-list-item :title="$t('chat.approval_status')" :subtitle="pendingInterruptId ? $t('chat.waiting_decision') : $t('chat.no_pending_interrupt')"></v-list-item>
                </v-list>
                <v-divider class="my-3"></v-divider>
                <div class="text-subtitle-2 mb-2">{{ $t('chat.steps') }}</div>
                <div v-if="taskItems.length === 0" class="text-medium-emphasis">{{ $t('chat.no_tasks') }}</div>
                <div v-for="task in taskItems" :key="task.id" class="task-item">
                  <div class="task-head">
                    <strong>{{ task.title }}</strong>
                    <span class="task-state" :class="task.status">{{ task.status }}</span>
                  </div>
                  <div class="task-meta">{{ task.subtitle }}</div>
                </div>
              </v-window-item>
              <v-window-item value="files">
                <div class="files-header">
                  <div class="text-subtitle-2">{{ $t('chat.attachments') }}</div>
                  <v-btn size="x-small" variant="tonal" @click="loadFiles" :loading="filesLoading">{{ $t('chat.refresh') }}</v-btn>
                </div>
                <div v-if="filesError" class="text-error text-caption mt-2">{{ filesError }}</div>
                <div v-if="filesLoading" class="text-medium-emphasis">{{ $t('common.loading') }}</div>
                <div v-else-if="filesList.length === 0" class="text-medium-emphasis">{{ $t('chat.no_attachments') }}</div>
                <div v-else class="file-list">
                  <div v-for="file in filesList" :key="file.workspace_path" class="file-card">
                    <div class="file-title">{{ file.original_name || file.saved_name }}</div>
                    <div class="file-meta">
                      <span v-if="file.uploaded_at">{{ $t('chat.uploaded_at', { time: formatMessageTime(file.uploaded_at) }) }}</span>
                      <span v-if="file.content_type">{{ $t('chat.type') }}: {{ file.content_type }}</span>
                      <span v-if="file.size_bytes">{{ $t('chat.size') }}: {{ formatBytes(file.size_bytes) }}</span>
                    </div>
                    <div class="file-actions">
                      <v-btn v-if="file.workspace_path" size="x-small" variant="tonal" @click="downloadAttachment(file.workspace_path, file.original_name || file.saved_name)">{{ $t('chat.download_original') }}</v-btn>
                      <v-btn v-if="file.markdown && file.markdown.workspace_path" size="x-small" variant="tonal" @click="downloadAttachment(file.markdown.workspace_path, (file.original_name || file.saved_name || 'document') + '.md')">{{ $t('chat.download_markdown') }}</v-btn>
                      <v-btn v-if="file.markdown && file.markdown.workspace_path" size="x-small" variant="tonal" @click="openMarkdown(file)">{{ $t('chat.view_markdown') }}</v-btn>
                      <span v-else-if="file.markdown && file.markdown.error" class="attachment-error">{{ $t('chat.parse_failed', { error: file.markdown.error }) }}</span>
                    </div>
                  </div>
                </div>
              </v-window-item>
              <v-window-item value="agent-skills">
                <div class="files-header">
                  <div class="text-subtitle-2">{{ $t('chat.agent_skills_title') }}</div>
                  <v-btn size="x-small" variant="tonal" @click="refreshAgentSkills" :loading="agentSkillsLoading || mySkillsLoading">{{ $t('chat.refresh') }}</v-btn>
                </div>
                <div v-if="agentSkillsError" class="text-error text-caption mt-2">{{ agentSkillsError }}</div>
                <div v-if="agentSkillsLoading" class="text-medium-emphasis">{{ $t('common.loading') }}</div>
                <div v-else-if="agentSkillsList.length === 0" class="text-medium-emphasis">{{ $t('chat.no_agent_skills') }}</div>
                <div v-else class="file-list">
                  <div v-for="skill in agentSkillsList" :key="skill.dir_name || skill.name" class="file-card">
                    <div class="file-title">{{ skill.display_name || skill.name || skill.dir_name }}</div>
                    <div class="file-meta">
                      <span v-if="skill.name">name: {{ skill.name }}</span>
                      <span v-if="skill.description">{{ skill.description }}</span>
                    </div>
                    <div class="file-actions">
                      <v-btn size="x-small" variant="tonal" @click="moveAgentSkillToMine(skill)">{{ $t('chat.move_to_my_skills') }}</v-btn>
                    </div>
                  </div>
                </div>

                <v-divider class="my-3"></v-divider>
                <div class="files-header">
                  <div class="text-subtitle-2">{{ $t('chat.my_draft_skills') }}</div>
                </div>
                <div v-if="mySkillsError" class="text-error text-caption mt-2">{{ mySkillsError }}</div>
                <div v-if="mySkillsLoading" class="text-medium-emphasis">{{ $t('common.loading') }}</div>
                <div v-else-if="mySkillsForAgent.length === 0" class="text-medium-emphasis">{{ $t('chat.no_movable_skills') }}</div>
                <div v-else class="file-list">
                  <div v-for="skill in mySkillsForAgent" :key="skill.id" class="file-card">
                    <div class="file-title">{{ skill.display_name || skill.name }}</div>
                    <div class="file-meta">
                      <span v-if="skill.name">name: {{ skill.name }}</span>
                      <span v-if="skill.description">{{ skill.description }}</span>
                    </div>
                    <div class="file-actions">
                      <v-btn size="x-small" variant="tonal" @click="moveMySkillToAgent(skill)">{{ $t('chat.move_to_agent_skills') }}</v-btn>
                    </div>
                  </div>
                </div>
              </v-window-item>
            </v-window>
          </div>
        </v-col>
      </v-row>
    </v-container>
  `,
  setup() {
    const route = useRoute();
    const messages = ref([]);
    const input = ref("");
    const attachmentFiles = ref([]);
    const uploadingAttachments = ref(false);
    const sending = ref(false);
    const deciding = ref(false);
    const error = ref("");
    const filesList = ref([]);
    const filesLoading = ref(false);
    const filesError = ref("");
    const agentSkillsList = ref([]);
    const agentSkillsLoading = ref(false);
    const agentSkillsError = ref("");
    const mySkillsList = ref([]);
    const mySkillsLoading = ref(false);
    const mySkillsError = ref("");
    const sideTab = ref("tasks");
    const conversationTitle = ref(t("chat.conversation"));
    const conversationModel = ref("");
    const pendingInterruptId = ref("");
    const messagesWrapRef = ref(null);
    const localThinkingId = ref("");
    const markdownDialog = ref(false);
    const markdownContent = ref("");
    const markdownError = ref("");
    const markdownLoading = ref(false);
    const markdownTruncated = ref(false);
    const hasAttachmentFiles = computed(() => {
      if (!attachmentFiles.value) return false;
      return Array.isArray(attachmentFiles.value) ? attachmentFiles.value.length > 0 : true;
    });
    const mySkillsForAgent = computed(() =>
      mySkillsList.value.filter((skill) => skill.status === "draft")
    );
    const taskItems = computed(() => {
      const items = [];
      for (const msg of messages.value) {
        if (msg.message_type === "ToolMessage") {
          const failed = msg.message_status === "failed";
          const toolLabel = msg.tool_name
            ? `${t("chat.tool_execution")} · ${msg.tool_name}`
            : t("chat.tool_execution");
          items.push({
            id: `task-tool-${msg.id}`,
            title: toolLabel,
            status: failed ? "failed" : "done",
            subtitle: (msg.content_md || "").slice(0, 120),
          });
        } else if (msg.message_type === "SystemMessage" && msg.message_status === "pending") {
          items.push({
            id: `task-interrupt-${msg.id}`,
            title: t("chat.approval_status"),
            status: "pending",
            subtitle: (msg.content_md || "").slice(0, 120),
          });
        } else if (msg.message_type === "AIMessage") {
          items.push({
            id: `task-ai-${msg.id}`,
            title: "AI",
            status: msg.message_status === "done" ? "done" : "running",
            subtitle: (msg.content_md || "").slice(0, 120),
          });
        }
      }
      return items.slice(-12).reverse();
    });

    const scrollToBottom = async () => {
      await nextTick();
      const el = messagesWrapRef.value;
      if (!el) return;
      el.scrollTop = el.scrollHeight;
    };

    const loadMessages = async () => {
      try {
        const data = await apiFetch(`/conversations/${route.params.id}/messages`);
        messages.value = data.items || [];
        await loadFiles();
        await loadPendingInterrupt();
        await scrollToBottom();
      } catch (e) {
        error.value = e.message;
      }
    };

    const loadConversationMeta = async () => {
      try {
        const data = await apiFetch("/conversations");
        const conv = (data.items || []).find((item) => item.id === route.params.id);
        conversationTitle.value = conv?.title || t("chat.conversation");
        conversationModel.value = conv?.model || "";
      } catch {
        conversationTitle.value = t("chat.conversation");
        conversationModel.value = "";
      }
    };

    const send = async () => {
      if (!input.value.trim()) return;
      sending.value = true;
      error.value = "";
      const content = input.value;
      input.value = "";
      const optimisticMessage = {
        id: `local-${Date.now()}`,
        conversation_id: route.params.id,
        sender_role: "human",
        message_type: "human_text",
        message_status: "done",
        content_md: content,
        total_tokens: 0,
        run_duration_ms: 0,
        created_at: new Date().toISOString(),
      };
      messages.value.push(optimisticMessage);

      const thinkingId = `thinking-${Date.now()}`;
      localThinkingId.value = thinkingId;
      messages.value.push({
        id: thinkingId,
        conversation_id: route.params.id,
        sender_role: "assistant",
        message_type: "SystemMessage",
        message_status: "streaming",
        content_md: t("chat.ai_thinking"),
        total_tokens: 0,
        run_duration_ms: 0,
        created_at: new Date().toISOString(),
      });
      await scrollToBottom();
      try {
        const resp = await apiFetch(`/conversations/${route.params.id}/messages`, {
          method: "POST",
          body: JSON.stringify({ content }),
        });
        if (resp.requires_interrupt_decision) {
          pendingInterruptId.value = resp.interrupt_id;
        }
        await loadMessages();
      } catch (e) {
        error.value = e.message;
        if (localThinkingId.value) {
          messages.value = messages.value.map((m) =>
            m.id === localThinkingId.value
              ? {
                  ...m,
                  message_status: "failed",
                  content_md: t("chat.ai_failed", { error: e.message }),
                }
              : m
          );
        }
      } finally {
        if (localThinkingId.value && !error.value) {
          messages.value = messages.value.filter((m) => m.id !== localThinkingId.value);
          localThinkingId.value = "";
        } else if (localThinkingId.value && error.value) {
          localThinkingId.value = "";
        }
        sending.value = false;
      }
    };

    const normalizeAttachmentFiles = () => {
      if (!attachmentFiles.value) return [];
      return Array.isArray(attachmentFiles.value) ? attachmentFiles.value : [attachmentFiles.value];
    };

    const uploadAttachments = async () => {
      const files = normalizeAttachmentFiles().filter(Boolean);
      if (!files.length) return;
      uploadingAttachments.value = true;
      error.value = "";
      try {
        for (const file of files) {
          const formData = new FormData();
          formData.append("file", file);
          formData.append("convert_to_markdown", "true");
          await apiUpload(`/conversations/${route.params.id}/attachments`, formData);
        }
        attachmentFiles.value = [];
        await loadMessages();
      } catch (e) {
        error.value = e.message;
      } finally {
        uploadingAttachments.value = false;
      }
    };

    const loadFiles = async () => {
      filesLoading.value = true;
      filesError.value = "";
      try {
        const data = await apiFetch(`/conversations/${route.params.id}/attachments`);
        filesList.value = data.items || [];
      } catch (e) {
        filesError.value = e.message;
      } finally {
        filesLoading.value = false;
      }
    };

    const loadAgentSkills = async () => {
      agentSkillsLoading.value = true;
      agentSkillsError.value = "";
      try {
        const data = await apiFetch("/skills/agent_skills");
        agentSkillsList.value = data.items || [];
      } catch (e) {
        agentSkillsError.value = e.message;
      } finally {
        agentSkillsLoading.value = false;
      }
    };

    const loadMySkillsForAgent = async () => {
      mySkillsLoading.value = true;
      mySkillsError.value = "";
      try {
        const data = await apiFetch("/skills/mine");
        mySkillsList.value = data.items || [];
      } catch (e) {
        mySkillsError.value = e.message;
      } finally {
        mySkillsLoading.value = false;
      }
    };

    const refreshAgentSkills = async () => {
      await Promise.all([loadAgentSkills(), loadMySkillsForAgent()]);
    };

    const loadPendingInterrupt = async () => {
      try {
        const data = await apiFetch(`/conversations/${route.params.id}/interrupts/pending`);
        pendingInterruptId.value = data.interrupt_id || "";
      } catch {
        pendingInterruptId.value = "";
      }
    };

    const decide = async (decision) => {
      if (!pendingInterruptId.value) return;
      deciding.value = true;
      error.value = "";
      try {
        const resp = await apiFetch(`/conversations/${route.params.id}/interrupts/${pendingInterruptId.value}/decision`, {
          method: "POST",
          body: JSON.stringify({ decision }),
        });
        if (resp.requires_interrupt_decision) {
          pendingInterruptId.value = resp.interrupt_id;
        } else {
          pendingInterruptId.value = "";
        }
        await loadMessages();
      } catch (e) {
        error.value = e.message;
      } finally {
        deciding.value = false;
      }
    };

    const moveAgentSkillToMine = async (skill) => {
      const name = skill?.dir_name || skill?.name;
      if (!name) return;
      const ok = await confirmDialog(
        t("dialogs.move_to_my_skills", { name: skill.display_name || name })
      );
      if (!ok) return;
      error.value = "";
      try {
        await apiFetch(`/skills/agent_skills/${encodeURIComponent(name)}/move_to_user`, { method: "POST" });
        await refreshAgentSkills();
      } catch (e) {
        error.value = e.message;
      }
    };

    const moveMySkillToAgent = async (skill) => {
      if (!skill?.id) return;
      const ok = await confirmDialog(
        t("dialogs.move_to_agent_skills", { name: skill.display_name || skill.name })
      );
      if (!ok) return;
      error.value = "";
      try {
        await apiFetch(`/skills/${skill.id}/move_to_agent`, { method: "POST" });
        await refreshAgentSkills();
      } catch (e) {
        error.value = e.message;
      }
    };

    const escapeHtml = (text) =>
      String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#039;");

    const renderMarkdown = (text) => {
      if (!text) return "";
      if (window.marked && typeof window.marked.parse === "function") {
        return window.marked.parse(text, { breaks: true, gfm: true });
      }
      return escapeHtml(text).replace(/\n/g, "<br>");
    };

    const formatBytes = (value) => {
      const num = Number(value || 0);
      if (!num) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      let idx = 0;
      let size = num;
      while (size >= 1024 && idx < units.length - 1) {
        size /= 1024;
        idx += 1;
      }
      return `${size.toFixed(size >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
    };

    const openMarkdown = async (attachment) => {
      const path = attachment?.markdown?.workspace_path;
      if (!path) return;
      markdownDialog.value = true;
      markdownLoading.value = true;
      markdownError.value = "";
      markdownContent.value = "";
      markdownTruncated.value = false;
      try {
        const resp = await apiFetch(
          `/conversations/${route.params.id}/attachments/markdown?path=${encodeURIComponent(path)}`
        );
        markdownContent.value = resp.content || "";
        markdownTruncated.value = !!resp.truncated;
      } catch (e) {
        markdownError.value = e.message;
      } finally {
        markdownLoading.value = false;
      }
    };

    const downloadAttachment = async (path, filename) => {
      if (!path) return;
      try {
        const headers = {};
        if (session.token) headers.Authorization = `Bearer ${session.token}`;
        const res = await fetch(
          `${API_BASE}/conversations/${route.params.id}/attachments/download?path=${encodeURIComponent(path)}`,
          { headers }
        );
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || data.error || `HTTP ${res.status}`);
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename || path.split("/").pop() || "download";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        error.value = e.message;
      }
    };

    const formatMessageTime = (value) => {
      if (!value) return t("chat.just_now");
      const d = new Date(value);
      if (Number.isNaN(d.getTime())) return t("chat.just_now");
      return d.toLocaleString();
    };

    const hasTokenUsage = (msg) => {
      const i = Number(msg?.input_tokens ?? 0);
      const o = Number(msg?.output_tokens ?? 0);
      const t = Number(msg?.total_tokens ?? 0);
      return i > 0 || o > 0 || t > 0;
    };

    let eventSource = null;

    const stopEventStream = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    };

    const startEventStream = () => {
      stopEventStream();
      if (!session.token) return;
      const url = `${API_BASE}/conversations/${route.params.id}/events?token=${encodeURIComponent(session.token)}`;
      eventSource = new EventSource(url);
      eventSource.addEventListener("message.created", async () => {
        await loadMessages();
      });
      eventSource.addEventListener("message.updated", async () => {
        await loadMessages();
      });
      eventSource.addEventListener("system.connected", () => {
        // no-op
      });
      eventSource.onerror = () => {
        // Browser will auto-reconnect. We keep the instance.
      };
    };

    onMounted(async () => {
      await loadConversationMeta();
      await loadMessages();
      startEventStream();
    });
    watch(
      () => messages.value.length,
      async () => {
        await scrollToBottom();
      }
    );
    watch(() => route.params.id, async () => {
      pendingInterruptId.value = "";
      localThinkingId.value = "";
      attachmentFiles.value = [];
      filesList.value = [];
      filesError.value = "";
      agentSkillsList.value = [];
      agentSkillsError.value = "";
      mySkillsList.value = [];
      mySkillsError.value = "";
      sideTab.value = "tasks";
      markdownDialog.value = false;
      markdownContent.value = "";
      markdownError.value = "";
      markdownTruncated.value = false;
      conversationTitle.value = t("chat.conversation");
      conversationModel.value = "";
      await loadConversationMeta();
      await loadMessages();
      startEventStream();
    });

    onBeforeUnmount(() => {
      stopEventStream();
    });
    watch(
      () => sideTab.value,
      async (tab) => {
        if (tab === "agent-skills") {
          await refreshAgentSkills();
        }
      }
    );

    return {
      messages,
      input,
      attachmentFiles,
      uploadingAttachments,
      hasAttachmentFiles,
      filesList,
      filesLoading,
      filesError,
      agentSkillsList,
      agentSkillsLoading,
      agentSkillsError,
      mySkillsForAgent,
      mySkillsLoading,
      mySkillsError,
      sideTab,
      sending,
      deciding,
      error,
      send,
      uploadAttachments,
      loadFiles,
      refreshAgentSkills,
      loadPendingInterrupt,
      loadConversationMeta,
      decide,
      moveAgentSkillToMine,
      moveMySkillToAgent,
      startEventStream,
      stopEventStream,
      renderMarkdown,
      formatBytes,
      openMarkdown,
      downloadAttachment,
      markdownDialog,
      markdownContent,
      markdownError,
      markdownLoading,
      markdownTruncated,
      formatMessageTime,
      hasTokenUsage,
      conversationTitle,
      conversationModel,
      pendingInterruptId,
      messagesWrapRef,
      taskItems,
      session,
      toAssetUrl,
      userInitials,
    };
  },
};
