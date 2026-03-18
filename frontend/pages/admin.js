const AdminSkillsPage = {
  template: `
    <v-container fluid class="page-shell">
      <div class="page-head">
        <h2 class="page-title">{{ $t('admin.skill_mgmt_title') }}</h2>
        <v-btn size="small" variant="tonal" @click="refreshAll">{{ $t('admin.refresh') }}</v-btn>
      </div>

      <v-tabs v-model="tab" color="secondary" class="mb-3">
        <v-tab value="pending">{{ $t('admin.pending_skills') }}</v-tab>
        <v-tab value="published">{{ $t('admin.published_skills') }}</v-tab>
      </v-tabs>

      <v-window v-model="tab">
        <v-window-item value="pending">
          <div class="skills-grid">
            <div v-for="req in pendingPaged" :key="req.id" class="skill-card">
              <div class="skill-title">{{ skillName(req.skill_id) }}</div>
              <div class="skill-meta">{{ $t('admin.requester', { name: userLabel(req.requester_user_id) }) }}</div>
              <div class="skill-meta">{{ $t('admin.comment', { comment: req.comment || '-' }) }}</div>
              <div class="skill-actions">
                <v-btn size="small" variant="tonal" @click="openEditorForRequest(req)">{{ $t('admin.edit') }}</v-btn>
                <v-btn size="small" color="success" variant="tonal" @click="approveRequest(req)">{{ $t('admin.approve') }}</v-btn>
                <v-btn size="small" color="error" variant="tonal" @click="rejectRequest(req)">{{ $t('admin.reject') }}</v-btn>
              </div>
            </div>
            <div v-if="publishRequests.length === 0" class="text-medium-emphasis">{{ $t('admin.empty_pending') }}</div>
          </div>
          <div class="skills-pagination" v-if="pendingPageCount > 1">
            <v-pagination v-model="pages.pending" :length="pendingPageCount" color="secondary" density="comfortable" />
          </div>
        </v-window-item>

        <v-window-item value="published">
          <div class="skills-grid">
            <div v-for="skill in publishedPaged" :key="skill.id" class="skill-card">
              <div class="skill-title">{{ skill.display_name || skill.name }}</div>
              <div class="skill-meta">name: {{ skill.name }}</div>
              <div class="d-flex flex-wrap ga-2 mt-2">
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
              <div class="skill-actions">
                <v-btn size="small" variant="tonal" @click="openEditorForPublished(skill)">{{ $t('admin.edit') }}</v-btn>
                <v-btn size="small" color="error" variant="tonal" @click="deletePublishedSkill(skill)">{{ $t('admin.delete') }}</v-btn>
              </div>
            </div>
            <div v-if="publishedSkills.length === 0" class="text-medium-emphasis">{{ $t('admin.empty_published') }}</div>
          </div>
          <div class="skills-pagination" v-if="publishedPageCount > 1">
            <v-pagination v-model="pages.published" :length="publishedPageCount" color="secondary" density="comfortable" />
          </div>
        </v-window-item>
      </v-window>
      <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
    </v-container>
  `,
  setup() {
    const { t } = window.TeamClawI18n;
    const router = useRouter();
    const route = useRoute();
    const tab = ref("pending");
    const error = ref("");
    const publishRequests = ref([]);
    const allSkills = ref([]);
    const allUsers = ref([]);
    const pageSize = 8;
    const pages = reactive({ pending: 1, published: 1 });
    const publishedSkills = computed(() => allSkills.value.filter((s) => s.status === "published"));
    const pendingPageCount = computed(() => Math.max(1, Math.ceil(publishRequests.value.length / pageSize)));
    const publishedPageCount = computed(() => Math.max(1, Math.ceil(publishedSkills.value.length / pageSize)));

    const slicePage = (items, page) => {
      const start = (page - 1) * pageSize;
      return items.slice(start, start + pageSize);
    };

    const pendingPaged = computed(() => slicePage(publishRequests.value, pages.pending));
    const publishedPaged = computed(() => slicePage(publishedSkills.value, pages.published));

    const clampPage = (key, count) => {
      if (pages[key] > count) pages[key] = count;
      if (pages[key] < 1) pages[key] = 1;
    };

    const refreshAll = async () => {
      try {
        error.value = "";
        const [reqs, skills, users] = await Promise.all([
          apiFetch("/skills/publish_requests?status=pending"),
          apiFetch("/skills/all"),
          apiFetch("/users"),
        ]);
        publishRequests.value = reqs.items || [];
        allSkills.value = skills.items || [];
        allUsers.value = users.items || [];
      } catch (e) {
        error.value = e.message;
      }
    };

    const skillName = (id) => {
      const s = allSkills.value.find((x) => x.id === id);
      return s?.display_name || s?.name || id;
    };

    const userLabel = (id) => {
      const u = allUsers.value.find((x) => x.id === id);
      return u ? `${u.display_name || u.username}` : id;
    };
    const openEditorById = (skillId, tabName) => {
      router.push({ path: `/skills/${skillId}`, query: { from: "admin-skills", tab: tabName || tab.value } });
    };
    const openEditorForRequest = (req) => {
      openEditorById(req.skill_id, "pending");
    };
    const openEditorForPublished = (skill) => {
      openEditorById(skill.id, "published");
    };

    const deletePublishedSkill = async (skill) => {
      const ok = await confirmDialog(t("dialogs.delete_skill_confirm", { name: skill.display_name || skill.name }));
      if (!ok) return;
      try {
        await apiFetch(`/skills/${skill.id}`, { method: "DELETE" });
        await refreshAll();
      } catch (e) {
        error.value = e.message;
      }
    };

    const approveRequest = async (req) => {
      await apiFetch(`/skills/publish_requests/${req.id}/approve`, { method: "POST", body: JSON.stringify({}) });
      await refreshAll();
    };

    const rejectRequest = async (req) => {
      const comment = await promptDialog(t("dialogs.reject_reason_prompt"), { title: t("admin.reject") });
      if (comment === null) return;
      await apiFetch(`/skills/publish_requests/${req.id}/reject`, { method: "POST", body: JSON.stringify({ comment }) });
      await refreshAll();
    };

    onMounted(() => {
      if (route.query.tab === "published") {
        tab.value = "published";
      } else if (route.query.tab === "pending") {
        tab.value = "pending";
      }
      refreshAll();
    });
    watch(() => route.query.tab, (next) => {
      if (next === "published" || next === "pending") {
        tab.value = next;
      }
    });
    watch(publishRequests, () => clampPage("pending", pendingPageCount.value));
    watch(publishedSkills, () => clampPage("published", publishedPageCount.value));

    return {
      tab,
      error,
      publishRequests,
      publishedSkills,
      pendingPaged,
      publishedPaged,
      pages,
      pendingPageCount,
      publishedPageCount,
      refreshAll,
      skillName,
      userLabel,
      openEditorById,
      openEditorForRequest,
      openEditorForPublished,
      deletePublishedSkill,
      approveRequest,
      rejectRequest,
    };
  },
};

const SkillGroupsPage = {
  template: `
    <v-container fluid class="page-shell">
      <div class="page-head">
        <h2 class="page-title">{{ $t('admin.group_mgmt_title') }}</h2>
        <div class="d-flex ga-2">
          <v-btn color="secondary" @click="openGroupDialog">{{ $t('admin.create_group') }}</v-btn>
          <v-btn size="small" variant="tonal" @click="refreshAll">{{ $t('admin.refresh') }}</v-btn>
        </div>
      </div>

      <v-row>
        <v-col cols="12">
          <v-card class="panel-card" rounded="xl">
            <v-card-title class="d-flex align-center">
              {{ $t('admin.group_name') }}
              <v-spacer></v-spacer>
            </v-card-title>
            <v-card-text>
              <div class="skills-grid">
                <div v-for="group in groups" :key="group.id" class="skill-card">
                  <div class="skill-title">{{ group.name }}</div>
                  <div class="skill-meta">{{ $t('admin.group_desc', { desc: group.description || '-' }) }}</div>
                  <div class="d-flex flex-wrap ga-2 mt-2">
                    <template v-if="(group.skills || []).length">
                      <v-chip
                        v-for="skill in group.skills"
                        :key="skill.id"
                        size="x-small"
                        variant="tonal"
                      >
                        {{ skill.display_name || skill.name }}
                      </v-chip>
                    </template>
                    <span v-else class="text-medium-emphasis">{{ $t('admin.no_skills') }}</span>
                  </div>
                  <div class="skill-actions">
                    <v-btn size="small" variant="tonal" @click="editGroup(group)">{{ $t('admin.group_edit') }}</v-btn>
                    <v-btn size="small" color="error" variant="tonal" @click="deleteGroup(group)">{{ $t('admin.group_delete') }}</v-btn>
                  </div>
                </div>
                <div v-if="groups.length === 0" class="text-medium-emphasis">{{ $t('admin.empty_groups') }}</div>
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
      <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>

      <v-dialog v-model="groupDialog" max-width="520">
        <v-card>
          <v-card-title>{{ groupDialogMode === 'create' ? $t('admin.group_dialog_create') : $t('admin.group_dialog_edit') }}</v-card-title>
          <v-card-text>
            <v-text-field v-model="groupForm.name" :label="$t('admin.name_label')" variant="outlined" />
            <v-textarea v-model="groupForm.description" :label="$t('admin.desc_label')" variant="outlined" rows="3" />
            <v-autocomplete
              v-model="groupFormSkillIds"
              :items="publishedSkillOptions"
              item-title="title"
              item-value="value"
              :loading="optionsLoading"
              :disabled="optionsLoading"
              :no-data-text="$t('admin.no_skills')"
              :label="$t('admin.select_skills')"
              variant="outlined"
              multiple
              chips
              closable-chips
            />
            <v-autocomplete
              v-model="groupFormUserIds"
              :items="userOptions"
              item-title="title"
              item-value="value"
              :loading="optionsLoading"
              :disabled="optionsLoading"
              :no-data-text="$t('admin.no_users')"
              :label="$t('admin.select_users')"
              variant="outlined"
              multiple
              chips
              closable-chips
            />
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" @click="groupDialog=false">{{ $t('common.cancel') }}</v-btn>
            <v-btn color="secondary" @click="saveGroup">{{ $t('admin.save') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </v-container>
  `,
  setup() {
    const error = ref("");
    const allSkills = ref([]);
    const allUsers = ref([]);
    const groups = ref([]);
    const editingGroupId = ref("");
    const groupDialog = ref(false);
    const groupDialogMode = ref("create");
    const groupForm = reactive({ name: "", description: "" });
    const groupFormSkillIds = ref([]);
    const groupFormUserIds = ref([]);
    const groupFormSkillOriginalIds = ref([]);
    const groupFormUserOriginalIds = ref([]);
    const optionsLoading = ref(false);
    const publishedSkillOptions = computed(() =>
      allSkills.value
        .filter((s) => s.status === "published" && !s.is_public)
        .map((s) => ({
          title: s.display_name || s.name,
          value: s.id,
        }))
    );
    const userOptions = computed(() =>
      allUsers.value.map((u) => ({ title: u.username, value: u.id }))
    );

    const refreshAll = async () => {
      try {
        error.value = "";
        optionsLoading.value = true;
        const [optionsRes, groupsRes] = await Promise.all([
          apiFetch("/skills/groups/options"),
          apiFetch("/skills/groups/list"),
        ]);
        allSkills.value = optionsRes.skills || [];
        allUsers.value = optionsRes.users || [];
        groups.value = groupsRes.items || [];
      } catch (e) {
        error.value = e.message;
      } finally {
        optionsLoading.value = false;
      }
    };

    const ensureOptionsLoaded = async () => {
      if (!allSkills.value.length || !allUsers.value.length) {
        await refreshAll();
      }
    };

    const openGroupDialog = async () => {
      await ensureOptionsLoaded();
      groupDialogMode.value = "create";
      groupForm.name = "";
      groupForm.description = "";
      groupFormSkillIds.value = [];
      groupFormUserIds.value = [];
      groupFormSkillOriginalIds.value = [];
      groupFormUserOriginalIds.value = [];
      editingGroupId.value = "";
      groupDialog.value = true;
    };

    const editGroup = async (group) => {
      if (!group) return;
      await ensureOptionsLoaded();
      groupDialogMode.value = "edit";
      editingGroupId.value = group.id;
      groupForm.name = group.name;
      groupForm.description = group.description || "";
      try {
        const [skills, users] = await Promise.all([
          apiFetch(`/skills/groups/${group.id}/skills`),
          apiFetch(`/skills/groups/${group.id}/users`),
        ]);
        groupFormSkillIds.value = (skills.items || []).map((s) => s.id);
        groupFormUserIds.value = (users.items || []).map((u) => u.id);
        groupFormSkillOriginalIds.value = [...groupFormSkillIds.value];
        groupFormUserOriginalIds.value = [...groupFormUserIds.value];
      } catch (e) {
        error.value = e.message;
      }
      groupDialog.value = true;
    };

    const normalizeIds = (items) =>
      (items || [])
        .map((item) => (item && typeof item === "object" ? item.value : item))
        .filter((val) => typeof val === "string" && val.length > 0);

    const saveGroup = async () => {
      try {
        error.value = "";
        const normalizedSkillIds = normalizeIds(groupFormSkillIds.value);
        const normalizedUserIds = normalizeIds(groupFormUserIds.value);
        groupFormSkillIds.value = normalizedSkillIds;
        groupFormUserIds.value = normalizedUserIds;
        if (groupDialogMode.value === "create") {
          const res = await apiFetch("/skills/groups", { method: "POST", body: JSON.stringify(groupForm) });
          const createdId = res?.item?.id;
          if (createdId) {
            const tasks = [];
            for (const skillId of normalizedSkillIds) {
              tasks.push(
                apiFetch(`/skills/groups/${createdId}/skills`, {
                  method: "POST",
                  body: JSON.stringify({ skill_id: skillId }),
                })
              );
            }
            for (const userId of normalizedUserIds) {
              tasks.push(
                apiFetch(`/skills/groups/${createdId}/users`, {
                  method: "POST",
                  body: JSON.stringify({ user_id: userId }),
                })
              );
            }
            if (tasks.length) {
              await Promise.all(tasks);
            }
          }
          groupDialog.value = false;
          await refreshAll();
        } else if (editingGroupId.value) {
          await apiFetch(`/skills/groups/${editingGroupId.value}`, {
            method: "PATCH",
            body: JSON.stringify(groupForm),
          });
          const toAddSkills = normalizedSkillIds.filter(
            (id) => !groupFormSkillOriginalIds.value.includes(id)
          );
          const toRemoveSkills = groupFormSkillOriginalIds.value.filter(
            (id) => !normalizedSkillIds.includes(id)
          );
          const toAddUsers = normalizedUserIds.filter(
            (id) => !groupFormUserOriginalIds.value.includes(id)
          );
          const toRemoveUsers = groupFormUserOriginalIds.value.filter(
            (id) => !normalizedUserIds.includes(id)
          );
          const tasks = [
            ...toAddSkills.map((skillId) =>
              apiFetch(`/skills/groups/${editingGroupId.value}/skills`, {
                method: "POST",
                body: JSON.stringify({ skill_id: skillId }),
              })
            ),
            ...toRemoveSkills.map((skillId) =>
              apiFetch(`/skills/groups/${editingGroupId.value}/skills/${skillId}`, { method: "DELETE" })
            ),
            ...toAddUsers.map((userId) =>
              apiFetch(`/skills/groups/${editingGroupId.value}/users`, {
                method: "POST",
                body: JSON.stringify({ user_id: userId }),
              })
            ),
            ...toRemoveUsers.map((userId) =>
              apiFetch(`/skills/groups/${editingGroupId.value}/users/${userId}`, { method: "DELETE" })
            ),
          ];
          if (tasks.length) {
            await Promise.all(tasks);
          }
          groupDialog.value = false;
          await refreshAll();
        }
      } catch (e) {
        error.value = e.message;
      }
    };

    const deleteGroup = async (group) => {
      if (!group) return;
      const ok = await confirmDialog(t("dialogs.delete_group_confirm", { name: group.name }));
      if (!ok) return;
      await apiFetch(`/skills/groups/${group.id}`, { method: "DELETE" });
      await refreshAll();
    };

    onMounted(refreshAll);

    return {
      error,
      groups,
      editingGroupId,
      publishedSkillOptions,
      userOptions,
      groupDialog,
      groupDialogMode,
      groupForm,
      groupFormSkillIds,
      groupFormUserIds,
      groupFormSkillOriginalIds,
      groupFormUserOriginalIds,
      optionsLoading,
      refreshAll,
      openGroupDialog,
      editGroup,
      saveGroup,
      deleteGroup,
    };
  },
};

const AdminPage = {
  template: `
    <v-container fluid class="page-shell">
      <div class="page-head">
        <h2 class="page-title">{{ $t('admin.user_mgmt_title') }}</h2>
        <div class="d-flex ga-2">
          <v-btn color="secondary" @click="openCreateDialog">{{ $t('admin.create_user') }}</v-btn>
          <v-btn size="small" variant="tonal" @click="loadUsers">{{ $t('admin.refresh') }}</v-btn>
        </div>
      </div>
      <v-row>
        <v-col cols="12">
          <v-card class="panel-card skill-file-panel" rounded="xl">
            <v-card-title class="d-flex align-center">
              {{ $t('admin.users_list') }}
              <v-spacer></v-spacer>
            </v-card-title>
            <v-card-text>
              <div class="d-flex flex-wrap ga-2 align-center mb-3">
                <v-autocomplete
                  v-model="bulkGroupIds"
                  :items="groupOptions"
                  item-title="title"
                  item-value="value"
                  :label="$t('admin.bulk_assign')"
                  variant="outlined"
                  multiple
                  chips
                  closable-chips
                  class="flex-grow-1"
                  :disabled="bulkLoading"
                  :no-data-text="$t('admin.no_groups')"
                />
                <v-btn color="secondary" :loading="bulkLoading" @click="applyBulkGroups">{{ $t('admin.apply_bulk') }}</v-btn>
                <v-btn variant="tonal" :disabled="bulkLoading" @click="clearBulkSelection">{{ $t('admin.clear_selection') }}</v-btn>
              </div>
              <v-table density="comfortable">
                <thead>
                  <tr>
                    <th style="width: 42px;">
                      <v-checkbox-btn
                        :model-value="isAllSelected"
                        :indeterminate="isPartSelected"
                        @update:modelValue="toggleSelectAll"
                      />
                    </th>
                    <th>{{ $t('admin.username') }}</th>
                    <th>{{ $t('admin.display_name') }}</th>
                    <th>{{ $t('admin.email') }}</th>
                    <th>{{ $t('admin.skill_groups') }}</th>
                    <th>{{ $t('admin.avatar') }}</th>
                    <th>{{ $t('admin.admin') }}</th>
                    <th>{{ $t('admin.status') }}</th>
                    <th>{{ $t('admin.actions') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="u in users" :key="u.id">
                    <td>
                      <v-checkbox-btn
                        :model-value="selectedUserIds.includes(u.id)"
                        @update:modelValue="toggleSelectUser(u.id)"
                      />
                    </td>
                    <td>{{ u.username }}</td>
                    <td>{{ u.display_name }}</td>
                    <td>{{ u.email }}</td>
                    <td>
                      <div class="d-flex flex-wrap ga-1">
                        <v-chip
                          v-for="group in userGroups(u.id)"
                          :key="group.id"
                          size="x-small"
                          variant="tonal"
                        >
                          {{ group.name }}
                        </v-chip>
                        <span v-if="userGroups(u.id).length === 0" class="text-medium-emphasis">-</span>
                      </div>
                    </td>
                    <td><v-avatar size="28"><v-img v-if="u.avatar_url" :src="toAssetUrl(u.avatar_url)" cover></v-img><span v-else>--</span></v-avatar></td>
                    <td>{{ u.is_admin ? $t('admin.yes') : $t('admin.no') }}</td>
                    <td>{{ u.is_blocked ? $t('admin.blocked') : $t('admin.active') }}</td>
                    <td>
                      <div class="d-flex ga-2">
                        <v-btn size="x-small" variant="tonal" @click="openEdit(u)">{{ $t('admin.edit_user') }}</v-btn>
                        <v-btn size="x-small" variant="tonal" @click="toggleAdmin(u)">{{ u.is_admin ? $t('admin.toggle_admin_off') : $t('admin.toggle_admin_on') }}</v-btn>
                        <v-btn size="x-small" variant="tonal" @click="toggleBlock(u)">{{ u.is_blocked ? $t('admin.unblock') : $t('admin.block') }}</v-btn>
                        <v-btn size="x-small" color="error" variant="tonal" @click="removeUser(u)">{{ $t('admin.delete_user') }}</v-btn>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </v-table>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
      <v-dialog v-model="createDialog" max-width="560">
        <v-card>
          <v-card-title>{{ $t('admin.create_user_title') }}</v-card-title>
          <v-card-text>
            <v-text-field :label="$t('admin.username')" v-model="createForm.username" variant="outlined" />
            <v-text-field :label="$t('admin.display_name')" v-model="createForm.display_name" variant="outlined" />
            <v-text-field :label="$t('admin.email')" v-model="createForm.email" variant="outlined" />
            <v-text-field :label="$t('admin.password')" v-model="createForm.password" type="password" variant="outlined" />
            <v-checkbox v-model="createForm.is_admin" :label="$t('admin.set_admin')" hide-details />
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" @click="createDialog=false">{{ $t('common.cancel') }}</v-btn>
            <v-btn color="secondary" :loading="loadingCreate" @click="createUser">{{ $t('admin.create_user') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
      <v-dialog v-model="editDialog" max-width="560">
        <v-card>
          <v-card-title>{{ $t('admin.edit_user_title') }}</v-card-title>
          <v-card-text>
            <v-text-field :label="$t('admin.username_readonly')" :model-value="editForm.username" variant="outlined" readonly />
            <v-text-field :label="$t('admin.display_name')" v-model="editForm.display_name" variant="outlined" />
            <v-text-field :label="$t('admin.email')" v-model="editForm.email" variant="outlined" />
            <v-select
              v-model="editGroupIds"
              :items="groupOptions"
              item-title="title"
              item-value="value"
              :label="$t('admin.allowed_groups')"
              variant="outlined"
              multiple
              chips
              closable-chips
              :loading="groupLoading"
            />
            <v-file-input
              :label="$t('admin.avatar_upload')"
              prepend-icon="mdi-camera"
              accept="image/*"
              v-model="editAvatarFile"
              @update:modelValue="onEditAvatarFileChange"
              variant="outlined"
              show-size
            />
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" @click="editDialog=false">{{ $t('common.cancel') }}</v-btn>
            <v-btn color="secondary" :loading="savingEdit" @click="saveEdit">{{ $t('admin.save') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
      <v-dialog v-model="editCropDialog" max-width="980" persistent>
        <v-card>
          <v-card-title>{{ $t('admin.crop_avatar') }}</v-card-title>
          <v-card-text>
            <div class="crop-stage" :style="{ width: editDisplay.width + 'px', height: editDisplay.height + 'px' }">
              <img class="crop-image" :src="editCropSourceUrl" alt="crop-source" />
              <div class="crop-square" :style="editCropBoxStyle" @mousedown="startEditMove">
                <div class="crop-handle tl" @mousedown.stop="startEditResize('tl', $event)"></div>
                <div class="crop-handle tr" @mousedown.stop="startEditResize('tr', $event)"></div>
                <div class="crop-handle bl" @mousedown.stop="startEditResize('bl', $event)"></div>
                <div class="crop-handle br" @mousedown.stop="startEditResize('br', $event)"></div>
              </div>
            </div>
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" @click="cancelEditCrop">{{ $t('common.cancel') }}</v-btn>
            <v-btn color="secondary" @click="confirmEditCrop">{{ $t('admin.confirm') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
      <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
      <v-alert v-if="notice" type="success" variant="tonal" class="mt-3">{{ notice }}</v-alert>
    </v-container>
  `,
  setup() {
    const router = useRouter();
    const { t } = window.TeamClawI18n;
    const users = ref([]);
    const error = ref("");
    const notice = ref("");
    const loadingCreate = ref(false);
    const createDialog = ref(false);
    const editDialog = ref(false);
    const savingEdit = ref(false);
    const editAvatarFile = ref(null);
    const groupLoading = ref(false);
    const allGroups = ref([]);
    const editGroupIds = ref([]);
    const editGroupOriginalIds = ref([]);
    const userGroupMap = ref({});
    const selectedUserIds = ref([]);
    const bulkGroupIds = ref([]);
    const bulkLoading = ref(false);
    const editCropDialog = ref(false);
    const editCropSourceUrl = ref("");
    const editCropImage = ref(null);
    const editCropReady = ref(false);
    const editDisplay = reactive({ width: 0, height: 0 });
    const editCrop = reactive({ x: 0, y: 0, size: 160, minSize: 64 });
    const editDrag = reactive({ mode: "", startX: 0, startY: 0, baseX: 0, baseY: 0, baseSize: 0, corner: "br" });
    const editForm = reactive({ id: "", username: "", display_name: "", email: "" });
    const createForm = reactive({
      username: "",
      display_name: "",
      email: "",
      password: "",
      is_admin: false,
    });
    const groupOptions = computed(() => allGroups.value.map((g) => ({ title: g.name, value: g.id })));
    const isAllSelected = computed(() => users.value.length > 0 && selectedUserIds.value.length === users.value.length);
    const isPartSelected = computed(
      () => selectedUserIds.value.length > 0 && selectedUserIds.value.length < users.value.length
    );

    const editCropBoxStyle = computed(() => ({
      left: `${editCrop.x}px`,
      top: `${editCrop.y}px`,
      width: `${editCrop.size}px`,
      height: `${editCrop.size}px`,
    }));

    const clampEditCrop = () => {
      const maxSize = Math.min(editDisplay.width, editDisplay.height);
      editCrop.size = Math.max(editCrop.minSize, Math.min(editCrop.size, maxSize));
      const maxX = Math.max(0, editDisplay.width - editCrop.size);
      const maxY = Math.max(0, editDisplay.height - editCrop.size);
      editCrop.x = Math.max(0, Math.min(editCrop.x, maxX));
      editCrop.y = Math.max(0, Math.min(editCrop.y, maxY));
    };

    const stopEditDrag = () => {
      editDrag.mode = "";
      window.removeEventListener("mousemove", onEditMouseMove);
      window.removeEventListener("mouseup", stopEditDrag);
    };

    const onEditMouseMove = (e) => {
      if (!editDrag.mode) return;
      const dx = e.clientX - editDrag.startX;
      const dy = e.clientY - editDrag.startY;
      if (editDrag.mode === "move") {
        editCrop.x = editDrag.baseX + dx;
        editCrop.y = editDrag.baseY + dy;
      } else if (editDrag.mode === "resize") {
        if (editDrag.corner === "br") {
          const delta = Math.max(dx, dy);
          const maxSize = Math.min(editDisplay.width - editDrag.baseX, editDisplay.height - editDrag.baseY);
          editCrop.size = Math.max(editCrop.minSize, Math.min(editDrag.baseSize + delta, maxSize));
        } else if (editDrag.corner === "tl") {
          const delta = Math.min(dx, dy);
          const targetSize = editDrag.baseSize - delta;
          const maxSize = Math.min(editDrag.baseX + editDrag.baseSize, editDrag.baseY + editDrag.baseSize);
          editCrop.size = Math.max(editCrop.minSize, Math.min(targetSize, maxSize));
          editCrop.x = editDrag.baseX + (editDrag.baseSize - editCrop.size);
          editCrop.y = editDrag.baseY + (editDrag.baseSize - editCrop.size);
        } else if (editDrag.corner === "tr") {
          const delta = Math.max(dx, -dy);
          const maxSize = Math.min(editDisplay.width - editDrag.baseX, editDrag.baseY + editDrag.baseSize);
          editCrop.size = Math.max(editCrop.minSize, Math.min(editDrag.baseSize + delta, maxSize));
          editCrop.y = editDrag.baseY + (editDrag.baseSize - editCrop.size);
        } else if (editDrag.corner === "bl") {
          const delta = Math.max(-dx, dy);
          const maxSize = Math.min(editDrag.baseX + editDrag.baseSize, editDisplay.height - editDrag.baseY);
          editCrop.size = Math.max(editCrop.minSize, Math.min(editDrag.baseSize + delta, maxSize));
          editCrop.x = editDrag.baseX + (editDrag.baseSize - editCrop.size);
        }
      }
      clampEditCrop();
    };

    const startEditMove = (e) => {
      editDrag.mode = "move";
      editDrag.startX = e.clientX;
      editDrag.startY = e.clientY;
      editDrag.baseX = editCrop.x;
      editDrag.baseY = editCrop.y;
      window.addEventListener("mousemove", onEditMouseMove);
      window.addEventListener("mouseup", stopEditDrag);
    };

    const startEditResize = (corner, e) => {
      editDrag.mode = "resize";
      editDrag.corner = corner;
      editDrag.startX = e.clientX;
      editDrag.startY = e.clientY;
      editDrag.baseX = editCrop.x;
      editDrag.baseY = editCrop.y;
      editDrag.baseSize = editCrop.size;
      window.addEventListener("mousemove", onEditMouseMove);
      window.addEventListener("mouseup", stopEditDrag);
    };

    const cancelEditCrop = () => {
      editCropDialog.value = false;
      editCropReady.value = false;
      editCropImage.value = null;
      editCropSourceUrl.value = "";
      editAvatarFile.value = null;
      stopEditDrag();
    };

    const confirmEditCrop = () => {
      editCropDialog.value = false;
      clampEditCrop();
      editCropReady.value = true;
    };

    const onEditAvatarFileChange = async (fileValue) => {
      const file = Array.isArray(fileValue) ? fileValue[0] : fileValue;
      if (!file) {
        cancelEditCrop();
        return;
      }
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error(t("admin.read_avatar_failed")));
        reader.readAsDataURL(file);
      });
      const image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error(t("admin.invalid_image")));
        img.src = dataUrl;
      });
      editCropImage.value = image;
      editCropSourceUrl.value = dataUrl;
      const maxW = 860;
      const maxH = 520;
      const scale = Math.min(maxW / image.width, maxH / image.height, 1);
      editDisplay.width = Math.round(image.width * scale);
      editDisplay.height = Math.round(image.height * scale);
      editCrop.size = Math.round(Math.min(editDisplay.width, editDisplay.height) * 0.7);
      editCrop.x = Math.round((editDisplay.width - editCrop.size) / 2);
      editCrop.y = Math.round((editDisplay.height - editCrop.size) / 2);
      clampEditCrop();
      editCropReady.value = true;
      editCropDialog.value = true;
    };

    const getEditCroppedAvatarBlob = async () => {
      const image = editCropImage.value;
      if (!image || editDisplay.width <= 0 || editDisplay.height <= 0) return null;
      const scaleX = image.width / editDisplay.width;
      const scaleY = image.height / editDisplay.height;
      const sx = Math.round(editCrop.x * scaleX);
      const sy = Math.round(editCrop.y * scaleY);
      const sSize = Math.round(Math.min(editCrop.size * scaleX, editCrop.size * scaleY));
      const output = document.createElement("canvas");
      output.width = 256;
      output.height = 256;
      const ctx = output.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(image, sx, sy, sSize, sSize, 0, 0, 256, 256);
      return await new Promise((resolve) => output.toBlob(resolve, "image/png", 0.95));
    };

    const loadUsers = async () => {
      error.value = "";
      try {
        const data = await apiFetch("/users");
        users.value = data.items || [];
        await loadUserGroupMap();
      } catch (e) {
        if (String(e.message || "").includes("Admin only")) {
          router.push("/new-chat");
          return;
        }
        error.value = e.message;
      }
    };

    const loadGroupOptions = async () => {
      try {
        const groupsRes = await apiFetch("/skills/groups/list");
        allGroups.value = groupsRes.items || [];
      } catch (e) {
        error.value = e.message;
      }
    };

    const loadUserGroupMap = async () => {
      try {
        const res = await apiFetch("/skills/groups/users_map");
        const map = {};
        for (const item of res.items || []) {
          map[item.user_id] = item.groups || [];
        }
        userGroupMap.value = map;
      } catch (e) {
        error.value = e.message;
      }
    };

    const userGroups = (userId) => userGroupMap.value[userId] || [];

    const toggleSelectUser = (userId) => {
      if (selectedUserIds.value.includes(userId)) {
        selectedUserIds.value = selectedUserIds.value.filter((id) => id !== userId);
      } else {
        selectedUserIds.value = [...selectedUserIds.value, userId];
      }
    };

    const toggleSelectAll = (val) => {
      if (val) {
        selectedUserIds.value = users.value.map((u) => u.id);
      } else {
        selectedUserIds.value = [];
      }
    };

    const clearBulkSelection = () => {
      selectedUserIds.value = [];
      bulkGroupIds.value = [];
    };

    const applyBulkGroups = async () => {
      if (selectedUserIds.value.length === 0 || bulkGroupIds.value.length === 0) {
        notice.value = t("admin.select_user_group");
        return;
      }
      bulkLoading.value = true;
      error.value = "";
      notice.value = "";
      try {
        const tasks = [];
        for (const groupId of bulkGroupIds.value) {
          for (const userId of selectedUserIds.value) {
            tasks.push(
              apiFetch(`/skills/groups/${groupId}/users`, {
                method: "POST",
                body: JSON.stringify({ user_id: userId }),
              })
            );
          }
        }
        await Promise.all(tasks);
        notice.value = t("admin.bulk_done");
        await loadUserGroupMap();
      } catch (e) {
        error.value = e.message;
      } finally {
        bulkLoading.value = false;
      }
    };

    const loadGroupsForUser = async (userId) => {
      groupLoading.value = true;
      try {
        const [groupsRes, userGroupsRes] = await Promise.all([
          apiFetch("/skills/groups/list"),
          apiFetch(`/skills/groups/for_user/${userId}`),
        ]);
        allGroups.value = groupsRes.items || [];
        const groupIds = (userGroupsRes.items || []).map((g) => g.id);
        editGroupIds.value = [...groupIds];
        editGroupOriginalIds.value = [...groupIds];
      } finally {
        groupLoading.value = false;
      }
    };

    const createUser = async () => {
      loadingCreate.value = true;
      error.value = "";
      notice.value = "";
      try {
        await apiFetch("/users", { method: "POST", body: JSON.stringify(createForm) });
        notice.value = t("admin.user_created");
        createForm.username = "";
        createForm.display_name = "";
        createForm.email = "";
        createForm.password = "";
        createForm.is_admin = false;
        createDialog.value = false;
        await loadUsers();
      } catch (e) {
        error.value = e.message;
      } finally {
        loadingCreate.value = false;
      }
    };

    const toggleAdmin = async (u) => {
      error.value = "";
      notice.value = "";
      try {
        await apiFetch(`/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ is_admin: !u.is_admin }) });
        notice.value = t("admin.role_updated");
        await loadUsers();
      } catch (e) {
        error.value = e.message;
      }
    };

    const toggleBlock = async (u) => {
      error.value = "";
      notice.value = "";
      try {
        await apiFetch(`/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ is_blocked: !u.is_blocked }) });
        notice.value = t("admin.status_updated");
        await loadUsers();
      } catch (e) {
        error.value = e.message;
      }
    };

    const removeUser = async (u) => {
      error.value = "";
      notice.value = "";
      try {
        await apiFetch(`/users/${u.id}`, { method: "DELETE" });
        notice.value = t("admin.user_deleted");
        await loadUsers();
      } catch (e) {
        error.value = e.message;
      }
    };

    const openEdit = (u) => {
      editForm.id = u.id;
      editForm.username = u.username;
      editForm.display_name = u.display_name || "";
      editForm.email = u.email || "";
      editAvatarFile.value = null;
      editCropReady.value = false;
      editCropImage.value = null;
      editCropSourceUrl.value = "";
      loadGroupsForUser(u.id).catch((e) => {
        error.value = e.message;
      });
      editDialog.value = true;
    };

    const saveEdit = async () => {
      savingEdit.value = true;
      error.value = "";
      notice.value = "";
      try {
        await apiFetch(`/users/${editForm.id}`, {
          method: "PATCH",
          body: JSON.stringify({ display_name: editForm.display_name, email: editForm.email }),
        });
        if (editCropReady.value) {
          const blob = await getEditCroppedAvatarBlob();
          if (!blob) throw new Error(t("admin.avatar_crop_failed"));
          const fd = new FormData();
          fd.append("avatar", blob, "avatar.png");
          const headers = {};
          if (session.token) headers.Authorization = `Bearer ${session.token}`;
          const res = await fetch(`${API_BASE}/users/${editForm.id}/avatar`, { method: "POST", headers, body: fd });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
          editAvatarFile.value = null;
          editCropReady.value = false;
          editCropImage.value = null;
          editCropSourceUrl.value = "";
        }
        const toAdd = editGroupIds.value.filter((id) => !editGroupOriginalIds.value.includes(id));
        const toRemove = editGroupOriginalIds.value.filter((id) => !editGroupIds.value.includes(id));
        if (toAdd.length || toRemove.length) {
          await Promise.all([
            ...toAdd.map((groupId) =>
              apiFetch(`/skills/groups/${groupId}/users`, {
                method: "POST",
                body: JSON.stringify({ user_id: editForm.id }),
              })
            ),
            ...toRemove.map((groupId) =>
              apiFetch(`/skills/groups/${groupId}/users/${editForm.id}`, { method: "DELETE" })
            ),
          ]);
          editGroupOriginalIds.value = [...editGroupIds.value];
        }
        notice.value = t("admin.profile_updated");
        editDialog.value = false;
        await loadUsers();
      } catch (e) {
        error.value = e.message;
      } finally {
        savingEdit.value = false;
      }
    };

    onBeforeUnmount(stopEditDrag);
    onMounted(() => {
      if (!session.user?.is_admin) {
        router.push("/new-chat");
        return;
      }
      loadGroupOptions();
      loadUsers();
    });
    return {
      users,
      error,
      notice,
      loadingCreate,
      createDialog,
      createForm,
      editDialog,
      editCropDialog,
      savingEdit,
      editForm,
      editGroupIds,
      groupOptions,
      groupLoading,
      editAvatarFile,
      editCropSourceUrl,
      editDisplay,
      editCropBoxStyle,
      bulkGroupIds,
      bulkLoading,
      selectedUserIds,
      isAllSelected,
      isPartSelected,
      userGroups,
      toAssetUrl,
      loadUsers,
      applyBulkGroups,
      toggleSelectUser,
      toggleSelectAll,
      clearBulkSelection,
      openCreateDialog: () => {
        createDialog.value = true;
      },
      createUser,
      toggleAdmin,
      toggleBlock,
      removeUser,
      openEdit,
      saveEdit,
      startEditMove,
      startEditResize,
      cancelEditCrop,
      confirmEditCrop,
      onEditAvatarFileChange,
    };
  },
};
