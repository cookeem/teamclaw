const SkillsPage = {
  template: `
    <v-container fluid class="page-shell skill-editor-page">
      <div class="page-head">
        <h2 class="page-title">{{ $t('skills.title') }}</h2>
        <v-btn color="secondary" @click="openCreate">{{ $t('skills.create') }}</v-btn>
      </div>

      <v-tabs v-model="tab" color="secondary" class="mb-3">
        <v-tab value="mine">{{ $t('skills.mine') }}</v-tab>
        <v-tab value="agent">{{ $t('skills.agent') }}</v-tab>
        <v-tab value="pending">{{ $t('skills.pending') }}</v-tab>
        <v-tab value="published">{{ $t('skills.published') }}</v-tab>
        <v-tab value="builtin">{{ $t('skills.builtin') }}</v-tab>
      </v-tabs>

      <v-window v-model="tab">
        <v-window-item value="mine">
          <div class="skills-grid">
            <div v-for="skill in myEditablePaged" :key="skill.id" class="skill-card">
              <div class="skill-title">{{ skill.display_name || skill.name }}</div>
              <div class="skill-meta">name: {{ skill.name }}</div>
              <div class="d-flex ga-2 mt-1 flex-wrap">
                <v-chip size="x-small" variant="tonal">{{ statusLabel(skill.status) }}</v-chip>
                <template v-if="skill.status === 'published'">
                  <v-chip
                    size="x-small"
                    :variant="skill.is_public ? 'flat' : 'tonal'"
                    rounded="pill"
                    class="tag-chip"
                    :color="skill.is_public ? 'blue' : undefined"
                    :class="skill.is_public ? 'text-white' : ''"
                  >
                    {{ skill.is_public ? $t('skills.status_public') : $t('skills.status_private') }}
                  </v-chip>
                  <v-chip
                    size="x-small"
                    :variant="skill.is_public_edit ? 'flat' : 'tonal'"
                    rounded="pill"
                    class="tag-chip"
                    :color="skill.is_public_edit ? 'success' : undefined"
                    :class="skill.is_public_edit ? 'text-white' : ''"
                  >
                    {{ skill.is_public_edit ? $t('skills.status_public_edit') : $t('skills.status_private_edit') }}
                  </v-chip>
                </template>
              </div>
              <v-alert
                v-if="skill.status === 'rejected'"
                type="error"
                variant="tonal"
                density="compact"
                class="mt-2"
              >
                {{ $t('skills.rejected', { reason: skill.rejected_reason || '-' }) }}
              </v-alert>
              <div class="skill-actions">
                <v-btn size="small" variant="tonal" @click="openEditor(skill)">{{ $t('skills.edit') }}</v-btn>
                <v-btn size="small" color="secondary" variant="tonal" @click="requestPublish(skill)">{{ $t('skills.request_publish') }}</v-btn>
                <v-btn size="small" color="error" variant="tonal" @click="deleteSkill(skill)">{{ $t('skills.delete') }}</v-btn>
              </div>
            </div>
            <div v-if="myEditableSkills.length === 0" class="text-medium-emphasis">{{ $t('skills.empty_mine') }}</div>
          </div>
          <div class="skills-pagination" v-if="minePageCount > 1">
            <v-pagination v-model="pages.mine" :length="minePageCount" :total-visible="7" color="secondary" density="comfortable" />
          </div>
        </v-window-item>

        <v-window-item value="agent">
          <div class="skills-grid">
            <div v-for="skill in agentPaged" :key="skill.id" class="skill-card">
              <div class="skill-title">{{ skill.display_name || skill.name }}</div>
              <div class="skill-meta">name: {{ skill.name }}</div>
              <div class="skill-meta">{{ $t('skills.source_agent') }}</div>
              <div class="skill-actions">
                <v-btn size="small" variant="tonal" @click="openEditor(skill)">{{ $t('skills.view') }}</v-btn>
                <v-btn size="small" color="secondary" variant="tonal" @click="saveAgentSkill(skill)">{{ $t('skills.save_to_mine') }}</v-btn>
              </div>
            </div>
            <div v-if="agentSkills.length === 0" class="text-medium-emphasis">{{ $t('skills.empty_agent') }}</div>
          </div>
          <div class="skills-pagination" v-if="agentPageCount > 1">
            <v-pagination v-model="pages.agent" :length="agentPageCount" :total-visible="7" color="secondary" density="comfortable" />
          </div>
        </v-window-item>

        <v-window-item value="pending">
          <div class="skills-grid">
            <div v-for="skill in pendingPaged" :key="skill.id" class="skill-card">
              <div class="skill-title">{{ skill.display_name || skill.name }}</div>
              <div class="skill-meta">name: {{ skill.name }}</div>
              <div class="skill-meta">{{ $t('skills.note', { note: skill.pending_comment || '-' }) }}</div>
              <div class="skill-actions">
                <v-btn size="small" variant="tonal" @click="openEditor(skill)">{{ $t('skills.view') }}</v-btn>
                <v-btn size="small" color="warning" variant="tonal" @click="withdrawSkill(skill)">{{ $t('skills.withdraw') }}</v-btn>
              </div>
            </div>
            <div v-if="pendingList.length === 0" class="text-medium-emphasis">{{ $t('skills.empty_pending') }}</div>
          </div>
          <div class="skills-pagination" v-if="pendingPageCount > 1">
            <v-pagination v-model="pages.pending" :length="pendingPageCount" :total-visible="7" color="secondary" density="comfortable" />
          </div>
        </v-window-item>

        <v-window-item value="published">
          <div class="d-flex justify-end mb-2">
            <v-btn-toggle v-model="publishedMode" color="secondary" density="comfortable" mandatory>
              <v-btn value="available">{{ $t('skills.available') }}</v-btn>
              <v-btn value="mine">{{ $t('skills.my_published') }}</v-btn>
            </v-btn-toggle>
          </div>
          <div class="skills-grid">
            <div v-for="skill in publishedPaged" :key="skill.id" class="skill-card">
              <div class="skill-title">{{ skill.display_name || skill.name }}</div>
              <div class="skill-meta">name: {{ skill.name }}</div>
              <div class="skill-meta">{{ $t('skills.usage', { count: usageCountOf(skill.name) }) }}</div>
              <div class="d-flex ga-2 mt-1 flex-wrap">
                <v-chip size="x-small" variant="tonal">{{ $t('skills.published_tag') }}</v-chip>
                <v-chip
                  size="x-small"
                  :variant="skill.is_public ? 'flat' : 'tonal'"
                  rounded="pill"
                  class="tag-chip"
                  :color="skill.is_public ? 'blue' : undefined"
                  :class="skill.is_public ? 'text-white' : ''"
                >
                    {{ skill.is_public ? $t('skills.status_public') : $t('skills.status_private') }}
                </v-chip>
                <v-chip
                  size="x-small"
                  :variant="skill.is_public_edit ? 'flat' : 'tonal'"
                  rounded="pill"
                  class="tag-chip"
                  :color="skill.is_public_edit ? 'success' : undefined"
                  :class="skill.is_public_edit ? 'text-white' : ''"
                >
                    {{ skill.is_public_edit ? $t('skills.status_public_edit') : $t('skills.status_private_edit') }}
                </v-chip>
              </div>
              <div class="skill-actions" v-if="publishedMode === 'available'">
                <v-btn
                  v-if="skill.is_public_edit"
                  size="small"
                  color="secondary"
                  variant="tonal"
                  @click="copySkill(skill)"
                >{{ $t('skills.copy_to_mine') }}</v-btn>
              </div>
            </div>
            <div v-if="publishedSkills.length === 0" class="text-medium-emphasis">{{ publishedEmptyText }}</div>
          </div>
          <div class="skills-pagination" v-if="publishedPageCount > 1">
            <v-pagination v-model="pages.published" :length="publishedPageCount" :total-visible="7" color="secondary" density="comfortable" />
          </div>
        </v-window-item>

        <v-window-item value="builtin">
          <div class="skills-grid">
            <div v-for="skill in builtinPaged" :key="skill.name" class="skill-card">
              <div class="skill-title">{{ skill.display_name || skill.name }}</div>
              <div class="skill-meta">name: {{ skill.name }}</div>
              <div class="skill-meta">{{ $t('skills.usage', { count: usageCountOf(skill.name) }) }}</div>
              <div class="skill-meta">{{ $t('skills.description') }}：{{ skill.description || '-' }}</div>
              <div class="d-flex ga-2 mt-1 flex-wrap">
                <v-chip size="x-small" variant="tonal">{{ $t('skills.builtin_tag') }}</v-chip>
              </div>
            </div>
            <div v-if="builtinSkills.length === 0" class="text-medium-emphasis">{{ $t('skills.empty_builtin') }}</div>
          </div>
          <div class="skills-pagination" v-if="builtinPageCount > 1">
            <v-pagination v-model="pages.builtin" :length="builtinPageCount" :total-visible="7" color="secondary" density="comfortable" />
          </div>
        </v-window-item>
      </v-window>

      <v-dialog v-model="createDialog" max-width="480">
        <v-card>
          <v-card-title>{{ $t('skills.create_title') }}</v-card-title>
          <v-card-text>
            <v-text-field
              v-model="createForm.name"
              :label="$t('skills.name_label')"
              variant="outlined"
              :error="!!nameError"
              :error-messages="nameError"
            />
            <v-text-field v-model="createForm.display_name" :label="$t('skills.display_name')" variant="outlined" />
            <v-textarea v-model="createForm.description" :label="$t('skills.description')" variant="outlined" rows="3" />
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" @click="createDialog=false">{{ $t('skills.cancel') }}</v-btn>
            <v-btn color="secondary" :loading="creating" :disabled="!canCreate" @click="createSkill">{{ $t('skills.create_confirm') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
    </v-container>
  `,
  setup() {
    const { t } = window.TeamClawI18n;
    const router = useRouter();
    const route = useRoute();
    const tab = ref("mine");
    const error = ref("");
    const createDialog = ref(false);
    const creating = ref(false);
    const createForm = reactive({ name: "", display_name: "", description: "" });
    const mySkills = ref([]);
    const agentSkills = ref([]);
    const publishedSkills = ref([]);
    const publishedMode = ref("available");
    const usageCounts = ref({});
    const builtinSkills = ref([]);
    const loadingAgents = ref(false);
    const namePattern = /^[a-zA-Z0-9-]+$/;
    const pageSize = 8;
    const pages = reactive({ mine: 1, agent: 1, pending: 1, published: 1, builtin: 1 });

    const nameError = computed(() => {
      if (!createForm.name) return "";
      return namePattern.test(createForm.name.trim()) ? "" : t("skills.name_rule");
    });
    const canCreate = computed(() => !!createForm.name.trim() && !nameError.value);

    const myEditableSkills = computed(() =>
      mySkills.value.filter((s) => s.source_type !== "agent" && (s.status === "draft" || s.status === "rejected"))
    );
    const pendingList = computed(() =>
      mySkills.value.filter((s) => s.source_type !== "agent" && s.status === "pending")
    );
    const minePageCount = computed(() => Math.max(1, Math.ceil(myEditableSkills.value.length / pageSize)));
    const agentPageCount = computed(() => Math.max(1, Math.ceil(agentSkills.value.length / pageSize)));
    const pendingPageCount = computed(() => Math.max(1, Math.ceil(pendingList.value.length / pageSize)));
    const publishedPageCount = computed(() => Math.max(1, Math.ceil(publishedSkills.value.length / pageSize)));
    const builtinPageCount = computed(() => Math.max(1, Math.ceil(builtinSkills.value.length / pageSize)));

    const slicePage = (items, page) => {
      const start = (page - 1) * pageSize;
      return items.slice(start, start + pageSize);
    };

    const myEditablePaged = computed(() => slicePage(myEditableSkills.value, pages.mine));
    const agentPaged = computed(() => slicePage(agentSkills.value, pages.agent));
    const pendingPaged = computed(() => slicePage(pendingList.value, pages.pending));
    const publishedPaged = computed(() => slicePage(publishedSkills.value, pages.published));
    const builtinPaged = computed(() => slicePage(builtinSkills.value, pages.builtin));

    const clampPage = (key, count) => {
      if (pages[key] > count) pages[key] = count;
      if (pages[key] < 1) pages[key] = 1;
    };

    const loadMine = async () => {
      const data = await apiFetch("/skills/mine");
      mySkills.value = data.items || [];
    };

    const loadAgents = async () => {
      loadingAgents.value = true;
      try {
        const data = await apiFetch("/skills/agents");
        agentSkills.value = data.items || [];
      } catch {
        agentSkills.value = mySkills.value.filter((s) => s.source_type === "agent");
      } finally {
        loadingAgents.value = false;
      }
    };

    const loadPublished = async () => {
      if (publishedMode.value === "mine") {
        const data = await apiFetch("/skills/mine");
        publishedSkills.value = (data.items || []).filter((s) => s.status === "published");
      } else {
        const data = await apiFetch("/skills/published");
        publishedSkills.value = data.items || [];
      }
    };

    const loadBuiltin = async () => {
      const data = await apiFetch("/skills/builtin");
      builtinSkills.value = data.items || [];
      clampPage("builtin", builtinPageCount.value);
    };

    const loadUsageCounts = async () => {
      try {
        const data = await apiFetch("/skills/usage");
        const map = {};
        for (const item of data.items || []) {
          if (!item?.tool_name) continue;
          map[item.tool_name] = Number(item.count || 0);
        }
        usageCounts.value = map;
      } catch {
        usageCounts.value = {};
      }
    };

    const usageCountOf = (name) => usageCounts.value[name] ?? 0;

    const refresh = async () => {
      try {
        error.value = "";
        await loadMine();
        await Promise.all([loadAgents(), loadPublished(), loadBuiltin(), loadUsageCounts()]);
      } catch (e) {
        error.value = e.message;
      }
    };

    const openCreate = () => {
      createForm.name = "";
      createForm.display_name = "";
      createForm.description = "";
      createDialog.value = true;
    };

    const createSkill = async () => {
      if (!canCreate.value) return;
      creating.value = true;
      try {
        await apiFetch("/skills", { method: "POST", body: JSON.stringify(createForm) });
        createDialog.value = false;
        await refresh();
      } catch (e) {
        error.value = e.message;
      } finally {
        creating.value = false;
      }
    };

    const openEditor = (skill) => {
      router.push(`/skills/${skill.id}`);
    };

    const requestPublish = async (skill) => {
      const comment = await promptDialog(t("skills.publish_comment_prompt"), { title: t("skills.publish_comment_title") });
      if (comment === null) return;
      try {
        await apiFetch(`/skills/${skill.id}/publish`, {
          method: "POST",
          body: JSON.stringify({ comment }),
        });
        await refresh();
      } catch (e) {
        error.value = e.message;
      }
    };

    const deleteSkill = async (skill) => {
      const ok = await confirmDialog(t("skills.delete_confirm", { name: skill.display_name || skill.name }));
      if (!ok) return;
      try {
        await apiFetch(`/skills/${skill.id}`, { method: "DELETE" });
        await refresh();
      } catch (e) {
        error.value = e.message;
      }
    };

    const withdrawSkill = async (skill) => {
      const ok = await confirmDialog(t("skills.withdraw_confirm", { name: skill.display_name || skill.name }));
      if (!ok) return;
      try {
        await apiFetch(`/skills/${skill.id}/withdraw`, { method: "POST" });
        await refresh();
      } catch (e) {
        error.value = e.message;
      }
    };

    const saveAgentSkill = async (skill) => {
      const ok = await confirmDialog(t("skills.save_agent_confirm", { name: skill.display_name || skill.name }));
      if (!ok) return;
      try {
        await apiFetch(`/skills/${skill.id}/save_to_mine`, { method: "POST" });
        await refresh();
      } catch (e) {
        error.value = e.message;
      }
    };

    const copySkill = async (skill) => {
      const ok = await confirmDialog(t("skills.copy_confirm", { name: skill.display_name || skill.name }));
      if (!ok) return;
      try {
        await apiFetch(`/skills/${skill.id}/copy`, { method: "POST" });
        await refresh();
      } catch (e) {
        error.value = e.message;
      }
    };

    const publishedEmptyText = computed(() =>
      publishedMode.value === "mine" ? t("skills.empty_published_mine") : t("skills.empty_published_available")
    );

    const statusLabel = (status) => {
      if (status === "draft") return t("skills.status_draft");
      if (status === "pending") return t("skills.status_pending");
      if (status === "published") return t("skills.status_published");
      if (status === "rejected") return t("skills.status_rejected");
      return status || "-";
    };

    const syncTabFromRoute = () => {
      const tabValue = route.query.tab;
      if (["mine", "agent", "pending", "published", "builtin"].includes(tabValue)) {
        tab.value = tabValue;
      }
    };

    onMounted(async () => {
      syncTabFromRoute();
      await refresh();
    });
    watch(publishedMode, loadPublished);
    watch(() => route.query.tab, syncTabFromRoute);
    watch(myEditableSkills, () => clampPage("mine", minePageCount.value));
    watch(agentSkills, () => clampPage("agent", agentPageCount.value));
    watch(pendingList, () => clampPage("pending", pendingPageCount.value));
    watch(publishedSkills, () => clampPage("published", publishedPageCount.value));
    // builtin list is static; clamp page on load to avoid pagination flicker

    return {
      tab,
      error,
      creating,
      createDialog,
      createForm,
      nameError,
      canCreate,
      mySkills,
      agentSkills,
      publishedSkills,
      publishedMode,
      publishedEmptyText,
      usageCountOf,
      builtinSkills,
      myEditableSkills,
      myEditablePaged,
      agentPaged,
      pendingPaged,
      publishedPaged,
      builtinPaged,
      pages,
      minePageCount,
      agentPageCount,
      pendingPageCount,
      publishedPageCount,
      builtinPageCount,
      pendingList,
      loadingAgents,
      openCreate,
      createSkill,
      openEditor,
      requestPublish,
      deleteSkill,
      withdrawSkill,
      saveAgentSkill,
      copySkill,
      statusLabel,
      session,
    };
  },
};
const SkillEditorPage = {
  template: `
    <v-container fluid class="page-shell">
      <div class="page-head">
        <div class="d-flex align-center ga-2">
          <v-btn icon="mdi-arrow-left" variant="text" @click="goBack"></v-btn>
          <h2 class="page-title">{{ skill.display_name || skill.name || 'Skill' }}</h2>
          <v-chip size="small" variant="tonal">{{ skill.status || '-' }}</v-chip>
        </div>
        <div class="d-flex ga-2">
          <v-btn size="small" variant="tonal" @click="loadTree" :loading="loadingTree">{{ $t('skills.load_tree') }}</v-btn>
          <v-btn
            v-if="canRequestPublish"
            size="small"
            color="secondary"
            variant="tonal"
            @click="requestPublishFromEditor"
          >{{ $t('skills.request_publish') }}</v-btn>
          <v-btn
            v-if="canDeleteSkill"
            size="small"
            color="error"
            variant="tonal"
            @click="deleteSkill"
          >{{ $t('skills.delete') }}</v-btn>
          <v-btn
            v-if="canWithdrawSkill"
            size="small"
            color="warning"
            variant="tonal"
            @click="withdrawSkill"
          >{{ $t('skills.withdraw_publish') }}</v-btn>
        </div>
      </div>

      <v-row class="skill-editor-row">
        <v-col cols="12" md="4" class="skill-editor-col">
          <v-card class="panel-card mb-3 skill-file-panel" rounded="xl">
            <v-card-title>{{ $t('skills.info_title') }}</v-card-title>
            <v-card-text>
              <div class="d-flex ga-2 mb-2 flex-wrap">
                <v-chip
                  size="x-small"
                  variant="tonal"
                >{{ $t('skills.source', { source: skill.source_type === 'agent' ? $t('skills.source_agent_label') : $t('skills.source_user_label') }) }}</v-chip>
                <v-chip size="x-small" variant="tonal">{{ $t('skills.status', { status: skill.status || '-' }) }}</v-chip>
              </div>
              <v-text-field
                v-model="metaForm.name"
                label="name"
                variant="outlined"
                density="comfortable"
                :disabled="!canRename"
              />
              <v-text-field
                v-model="metaForm.display_name"
                :label="$t('skills.display_name')"
                variant="outlined"
                density="comfortable"
                :disabled="!canEdit"
              />
              <v-textarea
                v-model="metaForm.description"
                :label="$t('skills.description')"
                variant="outlined"
                rows="3"
                :disabled="!canEdit"
              />
              <v-checkbox
                v-if="skill.status === 'published'"
                v-model="metaForm.is_public"
                :label="$t('skills.public_skill')"
                hide-details
                :disabled="!canTogglePublic"
              />
              <v-checkbox
                v-if="skill.status === 'published'"
                v-model="metaForm.is_public_edit"
                :label="$t('skills.public_edit')"
                hide-details
                :disabled="!canTogglePublicEdit"
              />
              <v-alert
                v-if="skill.status === 'rejected'"
                type="error"
                variant="tonal"
                density="compact"
                class="mt-2"
              >
                {{ $t('skills.rejected', { reason: skill.rejected_reason || '-' }) }}
              </v-alert>
              <div class="d-flex ga-2 mt-2">
                <v-btn size="small" color="secondary" :loading="savingMeta" :disabled="!canEdit" @click="saveMeta">{{ $t('skills.save_info') }}</v-btn>
              </div>
            </v-card-text>
          </v-card>
          <v-card class="panel-card skill-tree-panel" rounded="xl">
            <v-card-title>{{ $t('skills.files_title') }}</v-card-title>
            <v-card-text>
              <div class="file-tree-scroll" style="max-height: 320px; overflow-y: auto;">
                <div v-if="loadingTree" class="text-medium-emphasis">{{ $t('skills.loading') }}</div>
                <v-list density="compact" class="bg-transparent">
                  <v-list-item
                    v-for="item in displayItems"
                    :key="item.path"
                    :title="item.label"
                    @click="selectItem(item)"
                    :active="selectedPath === item.path"
                    :style="{ paddingLeft: (12 + item.depth * 16) + 'px' }"
                  >
                    <template #prepend>
                      <v-btn
                        v-if="item.is_dir && item.hasChildren"
                        icon
                        size="x-small"
                        variant="text"
                        class="tree-toggle"
                        @click.stop="toggleFolder(item.path)"
                      >
                        <v-icon size="16">{{ isExpanded(item.path) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
                      </v-btn>
                      <span v-else class="tree-spacer"></span>
                      <v-icon size="18" class="ml-1">{{ item.is_dir ? 'mdi-folder-outline' : 'mdi-file-document-outline' }}</v-icon>
                    </template>
                  </v-list-item>
                </v-list>
              </div>
            </v-card-text>
          </v-card>
          <div class="mt-3 d-flex ga-2 flex-wrap">
            <v-btn size="small" variant="tonal" :disabled="!canEdit" @click="createFile">{{ $t('skills.new_file') }}</v-btn>
            <v-btn size="small" variant="tonal" :disabled="!canEdit" @click="createDir">{{ $t('skills.new_dir') }}</v-btn>
            <v-btn
              v-if="selectedPath !== 'SKILL.md'"
              size="small"
              variant="tonal"
              :disabled="!canEdit || !canRenamePath"
              @click="renamePath"
            >{{ $t('skills.rename') }}</v-btn>
            <v-btn
              v-if="selectedPath !== 'SKILL.md'"
              size="small"
              color="error"
              variant="tonal"
              :disabled="!canEdit || !canDeletePath"
              @click="deletePath"
            >{{ $t('skills.delete_path') }}</v-btn>
          </div>
        </v-col>
        <v-col cols="12" md="8" class="skill-editor-col">
          <v-card class="panel-card" rounded="xl">
            <v-card-title>{{ $t('skills.editor_title') }}</v-card-title>
            <v-card-text>
              <v-text-field :label="$t('skills.path')" :model-value="selectedPath || ''" variant="outlined" readonly />
              <v-textarea
                v-model="fileContent"
                :readonly="!canEdit || selectedIsDir"
                variant="outlined"
                rows="14"
              />
              <div class="d-flex ga-2 mt-2">
                <v-btn size="small" color="secondary" :disabled="!canEdit || selectedIsDir || !selectedPath" @click="saveFile">{{ $t('skills.save_file') }}</v-btn>
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
      <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
    </v-container>
  `,
  setup() {
    const route = useRoute();
    const router = useRouter();
    const { t } = window.TeamClawI18n;
    const skill = reactive({});
    const treeItems = ref([]);
    const selectedPath = ref("");
    const selectedIsDir = ref(false);
    const fileContent = ref("");
    const loadingTree = ref(false);
    const savingMeta = ref(false);
    const error = ref("");
    const metaReady = ref(false);
    const autoSaving = ref(false);
    const metaForm = reactive({ name: "", display_name: "", description: "", is_public: false, is_public_edit: false });
    const selectedDir = computed(() => {
      if (!selectedPath.value) return "";
      if (selectedIsDir.value) return selectedPath.value;
      const idx = selectedPath.value.lastIndexOf("/");
      return idx >= 0 ? selectedPath.value.slice(0, idx) : "";
    });
    const canDeletePath = computed(() => !!selectedPath.value && selectedPath.value !== "SKILL.md");
    const canRenamePath = computed(() => !!selectedPath.value && selectedPath.value !== "SKILL.md");
    const expandedPaths = ref(new Set());
    const hasInitExpand = ref(false);

    const canEdit = computed(() => {
      if (!skill.id) return false;
      if (skill.status === "draft" || skill.status === "rejected") {
        if (skill.source_type === "agent") return false;
        return skill.owner_user_id === session.user?.id;
      }
      if (skill.status === "pending" || skill.status === "published") {
        return !!session.user?.is_admin;
      }
      return false;
    });
    const canTogglePublic = computed(() => skill.status === "published" && !!session.user?.is_admin);
    const canTogglePublicEdit = computed(() => canTogglePublic.value && metaForm.is_public);
    const canRename = computed(() => canEdit.value && skill.status !== "published");
    const canDeleteSkill = computed(() => canEdit.value);
    const canWithdrawSkill = computed(() => skill.status === "pending" && skill.owner_user_id === session.user?.id);
    const canRequestPublish = computed(
      () =>
        skill.source_type !== "agent" &&
        (skill.status === "draft" || skill.status === "rejected") &&
        skill.owner_user_id === session.user?.id
    );
    const displayItems = computed(() => {
      const items = treeItems.value || [];
      const nodeMap = new Map();
      const root = { path: "", name: "", is_dir: true, children: [] };
      nodeMap.set("", root);

      const ensureNode = (path, isDir) => {
        if (!nodeMap.has(path)) {
          const parts = path.split("/").filter(Boolean);
          nodeMap.set(path, {
            path,
            name: parts[parts.length - 1] || path,
            is_dir: !!isDir,
            children: [],
          });
        } else if (isDir) {
          const node = nodeMap.get(path);
          node.is_dir = true;
        }
        return nodeMap.get(path);
      };

      const attachChild = (parentPath, childPath) => {
        const parent = ensureNode(parentPath, true);
        const child = nodeMap.get(childPath);
        if (parent && child && !parent.children.includes(child)) {
          parent.children.push(child);
        }
      };

      items.forEach((item) => {
        const parts = item.path.split("/").filter(Boolean);
        let currentPath = "";
        for (let i = 0; i < parts.length; i += 1) {
          const nextPath = currentPath ? `${currentPath}/${parts[i]}` : parts[i];
          const isDir = i < parts.length - 1 ? true : !!item.is_dir;
          ensureNode(nextPath, isDir);
          attachChild(currentPath, nextPath);
          currentPath = nextPath;
        }
      });

      const sortChildren = (children) => {
        return [...children].sort((a, b) => {
          if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
          return a.name.localeCompare(b.name);
        });
      };

      const flattened = [];
      const walk = (node, depth) => {
        sortChildren(node.children).forEach((child) => {
          flattened.push({
            path: child.path,
            is_dir: child.is_dir,
            depth,
            label: child.name,
            hasChildren: child.children.length > 0,
          });
          if (child.is_dir && expandedPaths.value.has(child.path)) {
            walk(child, depth + 1);
          }
        });
      };
      walk(root, 0);
      return flattened;
    });

    const loadSkill = async () => {
      const data = await apiFetch(`/skills/${route.params.id}`);
      Object.assign(skill, data.item || {});
      metaForm.name = skill.name || "";
      metaForm.display_name = skill.display_name || "";
      metaForm.description = skill.description || "";
      metaForm.is_public = !!skill.is_public;
      metaForm.is_public_edit = !!skill.is_public_edit;
      metaReady.value = true;
    };

    const loadTree = async () => {
      loadingTree.value = true;
      try {
        const data = await apiFetch(`/skills/${route.params.id}/tree`);
        treeItems.value = data.items || [];
        if (!hasInitExpand.value) {
          const dirs = treeItems.value.filter((i) => i.is_dir).map((i) => i.path);
          expandedPaths.value = new Set(dirs);
          hasInitExpand.value = true;
        }
      } catch (e) {
        error.value = e.message;
      } finally {
        loadingTree.value = false;
      }
    };

    const selectItem = async (item) => {
      selectedPath.value = item.path;
      selectedIsDir.value = !!item.is_dir;
      fileContent.value = "";
      if (item.is_dir) return;
      const data = await apiFetch(`/skills/${route.params.id}/file?path=${encodeURIComponent(item.path)}`);
      fileContent.value = data.content || "";
    };

    const saveFile = async () => {
      if (!selectedPath.value) return;
      await apiFetch(`/skills/${route.params.id}/file`, {
        method: "PUT",
        body: JSON.stringify({ path: selectedPath.value, content: fileContent.value }),
      });
      await loadTree();
    };

    const createFile = async () => {
      const hint = selectedDir.value ? `${selectedDir.value}/` : "";
      const input = await promptDialog(t("skills.new_file_prompt"), {
        title: t("skills.new_file"),
        defaultValue: hint,
      });
      if (!input) return;
      let path = input.trim();
      if (selectedDir.value && !path.includes("/") && !path.startsWith(`${selectedDir.value}/`)) {
        path = `${selectedDir.value}/${path}`;
      }
      await apiFetch(`/skills/${route.params.id}/file`, {
        method: "PUT",
        body: JSON.stringify({ path, content: "" }),
      });
      await loadTree();
    };

    const createDir = async () => {
      const hint = selectedDir.value ? `${selectedDir.value}/` : "";
      const input = await promptDialog(t("skills.new_dir_prompt"), {
        title: t("skills.new_dir"),
        defaultValue: hint,
      });
      if (!input) return;
      let path = input.trim();
      if (selectedDir.value && !path.includes("/") && !path.startsWith(`${selectedDir.value}/`)) {
        path = `${selectedDir.value}/${path}`;
      }
      await apiFetch(`/skills/${route.params.id}/dir`, {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      await loadTree();
    };

    const renamePath = async () => {
      if (!canRenamePath.value) return;
      const next = await promptDialog(t("skills.rename_prompt"), {
        title: t("skills.rename"),
        defaultValue: selectedPath.value,
      });
      if (!next || next === selectedPath.value) return;
      await apiFetch(`/skills/${route.params.id}/rename`, {
        method: "POST",
        body: JSON.stringify({ from_path: selectedPath.value, to_path: next }),
      });
      selectedPath.value = "";
      await loadTree();
    };

    const deletePath = async () => {
      if (!canDeletePath.value) return;
      const ok = await confirmDialog(t("skills.delete_path_confirm", { path: selectedPath.value }));
      if (!ok) return;
      await apiFetch(`/skills/${route.params.id}/path?path=${encodeURIComponent(selectedPath.value)}`, {
        method: "DELETE",
      });
      selectedPath.value = "";
      fileContent.value = "";
      await loadTree();
    };

    const deleteSkill = async () => {
      if (!canDeleteSkill.value) return;
      const ok = await confirmDialog(t("skills.delete_skill_confirm", { name: skill.display_name || skill.name }));
      if (!ok) return;
      await apiFetch(`/skills/${route.params.id}`, { method: "DELETE" });
      if (route.query.from === "admin-skills") {
        const tab = route.query.tab === "published" ? "published" : "pending";
        router.push({ path: "/admin/skills", query: { tab } });
        return;
      }
      router.push({ path: "/skills", query: { tab: "mine" } });
    };

    const requestPublishFromEditor = async () => {
      if (!canRequestPublish.value) return;
      const comment = await promptDialog(t("skills.publish_comment_prompt"), { title: t("skills.publish_comment_title") });
      if (comment === null) return;
      await apiFetch(`/skills/${route.params.id}/publish`, {
        method: "POST",
        body: JSON.stringify({ comment }),
      });
      router.push({ path: "/skills", query: { tab: "pending" } });
    };

    const withdrawSkill = async () => {
      if (!canWithdrawSkill.value) return;
      const ok = await confirmDialog(t("skills.withdraw_confirm", { name: skill.display_name || skill.name }));
      if (!ok) return;
      await apiFetch(`/skills/${route.params.id}/withdraw`, { method: "POST" });
      await loadSkill();
      await loadTree();
    };

    const toggleFolder = (path) => {
      const next = new Set(expandedPaths.value);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      expandedPaths.value = next;
    };

    const isExpanded = (path) => expandedPaths.value.has(path);

    const saveMeta = async () => {
      if (!canEdit.value) return;
      const payload = {};
      const trimmedName = metaForm.name.trim();
      if (canRename.value && trimmedName && trimmedName !== skill.name) {
        payload.name = trimmedName;
      }
      if (metaForm.display_name !== (skill.display_name || "")) {
        payload.display_name = metaForm.display_name;
      }
      if (metaForm.description !== (skill.description || "")) {
        payload.description = metaForm.description;
      }
      if (canTogglePublic.value && metaForm.is_public !== !!skill.is_public) {
        payload.is_public = metaForm.is_public;
      }
      if (canTogglePublic.value && !metaForm.is_public && skill.is_public_edit) {
        payload.is_public_edit = false;
      }
      if (canTogglePublicEdit.value && metaForm.is_public_edit !== !!skill.is_public_edit) {
        payload.is_public_edit = metaForm.is_public_edit;
      }
      if (!Object.keys(payload).length) return;
      savingMeta.value = true;
      try {
        const data = await apiFetch(`/skills/${route.params.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        Object.assign(skill, data.item || {});
        metaForm.name = skill.name || "";
        metaForm.display_name = skill.display_name || "";
        metaForm.description = skill.description || "";
        metaForm.is_public = !!skill.is_public;
        metaForm.is_public_edit = !!skill.is_public_edit;
        await loadTree();
      } catch (e) {
        error.value = e.message;
      } finally {
        savingMeta.value = false;
      }
    };

    const autoSavePublic = async () => {
      if (!metaReady.value || autoSaving.value) return;
      if (!canTogglePublic.value) return;
      autoSaving.value = true;
      try {
        await saveMeta();
      } finally {
        autoSaving.value = false;
      }
    };

    const goBack = () => {
      if (route.query.from === "admin-skills") {
        const tab = route.query.tab === "published" ? "published" : "pending";
        router.push({ path: "/admin/skills", query: { tab } });
        return;
      }
      router.push("/skills");
    };

    onMounted(async () => {
      try {
        await loadSkill();
        await loadTree();
      } catch (e) {
        error.value = e.message;
      }
    });

    watch(
      () => metaForm.is_public,
      (val) => {
        if (!val) metaForm.is_public_edit = false;
      }
    );
    watch(
      () => metaForm.is_public,
      () => {
        if (skill.status === "published") autoSavePublic();
      }
    );
    watch(
      () => metaForm.is_public_edit,
      () => {
        if (skill.status === "published") autoSavePublic();
      }
    );

    return {
      skill,
      treeItems,
      displayItems,
      selectedPath,
      selectedIsDir,
      fileContent,
      loadingTree,
      error,
      canEdit,
      canTogglePublic,
      canTogglePublicEdit,
      canRename,
      canDeleteSkill,
      canWithdrawSkill,
      canRequestPublish,
      canDeletePath,
      canRenamePath,
      selectedDir,
      metaForm,
      savingMeta,
      loadTree,
      selectItem,
      saveFile,
      createFile,
      createDir,
      renamePath,
      deletePath,
      deleteSkill,
      requestPublishFromEditor,
      withdrawSkill,
      toggleFolder,
      isExpanded,
      saveMeta,
      goBack,
    };
  },
};
