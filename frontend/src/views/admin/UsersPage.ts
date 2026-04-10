import { computed, defineComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { useI18n } from "../../i18n";
import {
  apiBase,
  createUser,
  deleteUser,
  getAppSettings,
  listUsers,
  updateUser,
  uploadAdminUserAvatar,
} from "../../services/api";
import { useAuthStore } from "../../stores/auth";
import type { UserPublic } from "../../types/models";

export default defineComponent({
  name: "UsersPage",
  setup() {
    const auth = useAuthStore();
    const { t, dateLocale } = useI18n();

    const users = ref<UserPublic[]>([]);
    const loading = ref(false);
    const saving = ref(false);
    const creating = ref(false);
    const deletingUserId = ref("");
    const errorText = ref("");
    const search = ref("");
    const createDialog = ref(false);
    const createErrorText = ref("");
    const editDialog = ref(false);
    const editErrorText = ref("");
    const editSuccessText = ref("");
    const deleteDialog = ref(false);
    const deleteTargetUser = ref<UserPublic | null>(null);
    const avatarFile = ref<File | File[] | null>(null);
    const croppedAvatarFile = ref<File | null>(null);
    const avatarPreviewUrl = ref("");
    const avatarCropDialog = ref(false);
    const avatarCropImageUrl = ref("");
    const avatarCropImageRef = ref<HTMLImageElement | null>(null);
    const avatarResizeObserver = ref<ResizeObserver | null>(null);
    const avatarCropScale = ref(75);
    const avatarCropDragging = ref(false);
    const avatarCropImageSize = reactive({ width: 0, height: 0 });
    const avatarCropNaturalSize = reactive({ width: 0, height: 0 });
    const avatarCropRect = reactive({ x: 0, y: 0, size: 0 });
    const avatarDragStart = reactive({ pointerX: 0, pointerY: 0, rectX: 0, rectY: 0 });

    const form = reactive({
      id: "",
      username: "",
      display_name: "",
      email: "",
      new_password: "",
      confirm_password: "",
      is_admin: false,
      is_blocked: false,
      conversation_limit_input: "",
      avatar_url: "",
    });
    const createForm = reactive({
      username: "",
      display_name: "",
      email: "",
      password: "",
      confirm_password: "",
      is_admin: false,
      is_blocked: false,
      conversation_limit_input: "",
    });

    const headers = computed(() => [
      { title: t("admin.users.table.avatar"), key: "avatar", sortable: false },
      { title: t("admin.users.table.email"), key: "email" },
      { title: t("admin.users.table.username"), key: "username" },
      { title: t("admin.users.table.displayName"), key: "display_name" },
      { title: t("admin.users.table.role"), key: "is_admin" },
      { title: t("admin.users.table.status"), key: "is_blocked" },
      { title: t("admin.users.table.conversationLimit"), key: "conversation_limit", sortable: false },
      { title: t("admin.users.table.timeInfo"), key: "time_info", sortable: false },
      { title: t("admin.users.table.actions"), key: "actions", sortable: false },
    ]);

    function parseConversationLimitInput(raw: string): { valid: boolean; value: number | null } {
      const cleaned = raw.trim();
      if (!cleaned) {
        return { valid: true, value: null };
      }
      if (!/^-?\d+$/.test(cleaned)) {
        return { valid: false, value: null };
      }
      const parsed = Number.parseInt(cleaned, 10);
      if (!Number.isFinite(parsed) || parsed < -1) {
        return { valid: false, value: null };
      }
      return { valid: true, value: parsed };
    }

    const createConversationLimitState = computed(() => parseConversationLimitInput(createForm.conversation_limit_input));
    const editConversationLimitState = computed(() => parseConversationLimitInput(form.conversation_limit_input));

    const passwordMismatch = computed(
      () =>
        form.new_password.trim().length > 0 &&
        form.confirm_password.trim().length > 0 &&
        form.new_password !== form.confirm_password,
    );

    const canSave = computed(
      () => !saving.value && !passwordMismatch.value && form.id.length > 0 && editConversationLimitState.value.valid,
    );
    const createPasswordMismatch = computed(
      () =>
        createForm.password.trim().length > 0 &&
        createForm.confirm_password.trim().length > 0 &&
        createForm.password !== createForm.confirm_password,
    );
    const canCreate = computed(
      () =>
        !creating.value &&
        createForm.username.trim().length >= 2 &&
        createForm.email.trim().length >= 3 &&
        createForm.password.trim().length >= 8 &&
        createConversationLimitState.value.valid &&
        !createPasswordMismatch.value,
    );
    const currentUserId = computed(() => auth.state.user?.id ?? "");
    const isEditingSelf = computed(() => auth.state.user?.id === form.id);
    const defaultUserConversationLimit = ref(4);

    const avatarPreviewSrc = computed(() => {
      if (avatarPreviewUrl.value) {
        return avatarPreviewUrl.value;
      }
      if (form.avatar_url) {
        if (/^https?:\/\//i.test(form.avatar_url)) {
          return form.avatar_url;
        }
        const base = apiBase().replace(/\/$/, "");
        return `${base}${form.avatar_url.startsWith("/") ? form.avatar_url : `/${form.avatar_url}`}`;
      }
      return null;
    });
    const avatarCropRectStyle = computed(() => ({
      left: `${avatarCropRect.x}px`,
      top: `${avatarCropRect.y}px`,
      width: `${avatarCropRect.size}px`,
      height: `${avatarCropRect.size}px`,
    }));

    function fmt(value: string): string {
      return new Date(value).toLocaleString(dateLocale.value, { hour12: false });
    }

    function fmtOptional(value: string | null | undefined): string {
      if (!value) {
        return "-";
      }
      return fmt(value);
    }

    function formatConversationLimit(value: number | null | undefined): string {
      if (typeof value !== "number") {
        return t("admin.users.conversationLimit.default", { default: defaultUserConversationLimit.value });
      }
      if (value < 0) {
        return t("admin.users.conversationLimit.unlimited");
      }
      return t("admin.users.conversationLimit.value", { limit: value });
    }

    function userInitials(user: Pick<UserPublic, "display_name" | "username" | "email">): string {
      const raw = user.display_name || user.username || user.email || "U";
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

    function userAvatarSrc(user: Pick<UserPublic, "avatar_url">): string | null {
      const raw = user.avatar_url;
      if (!raw || !raw.trim()) {
        return null;
      }
      if (/^https?:\/\//i.test(raw)) {
        return raw;
      }
      const base = apiBase().replace(/\/$/, "");
      return `${base}${raw.startsWith("/") ? raw : `/${raw}`}`;
    }

    function revokeAvatarPreviewUrl() {
      if (avatarPreviewUrl.value) {
        URL.revokeObjectURL(avatarPreviewUrl.value);
        avatarPreviewUrl.value = "";
      }
    }

    function resetAvatarCropState() {
      avatarCropDragging.value = false;
      avatarCropImageSize.width = 0;
      avatarCropImageSize.height = 0;
      avatarCropNaturalSize.width = 0;
      avatarCropNaturalSize.height = 0;
      avatarCropRect.x = 0;
      avatarCropRect.y = 0;
      avatarCropRect.size = 0;
      avatarCropScale.value = 75;
    }

    function teardownAvatarResizeObserver() {
      if (avatarResizeObserver.value) {
        avatarResizeObserver.value.disconnect();
        avatarResizeObserver.value = null;
      }
    }

    function revokeCropImageUrl() {
      if (avatarCropImageUrl.value) {
        URL.revokeObjectURL(avatarCropImageUrl.value);
        avatarCropImageUrl.value = "";
      }
    }

    function clearSelectedAvatar() {
      avatarFile.value = null;
      croppedAvatarFile.value = null;
      revokeAvatarPreviewUrl();
    }

    function stopAvatarCropDrag(event?: PointerEvent) {
      avatarCropDragging.value = false;
      if (event) {
        (event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId);
      }
    }

    function closeCropDialog() {
      stopAvatarCropDrag();
      avatarCropDialog.value = false;
      teardownAvatarResizeObserver();
      avatarCropImageRef.value = null;
      revokeCropImageUrl();
      resetAvatarCropState();
      avatarFile.value = croppedAvatarFile.value;
    }

    function clampCropRect() {
      const maxX = Math.max(0, avatarCropImageSize.width - avatarCropRect.size);
      const maxY = Math.max(0, avatarCropImageSize.height - avatarCropRect.size);
      avatarCropRect.x = Math.min(maxX, Math.max(0, avatarCropRect.x));
      avatarCropRect.y = Math.min(maxY, Math.max(0, avatarCropRect.y));
    }

    function applyCropScale(scale: number) {
      const shortest = Math.min(avatarCropImageSize.width, avatarCropImageSize.height);
      if (shortest <= 0) return;
      avatarCropRect.size = Math.min(shortest, Math.max(40, Math.floor((shortest * scale) / 100)));
      clampCropRect();
    }

    function updateAvatarCropMetrics(preserveCenter: boolean) {
      const target = avatarCropImageRef.value;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      const width = Math.floor(rect.width);
      const height = Math.floor(rect.height);
      if (width <= 0 || height <= 0) {
        return;
      }

      const prevWidth = avatarCropImageSize.width;
      const prevHeight = avatarCropImageSize.height;
      const prevCenterX = avatarCropRect.x + avatarCropRect.size / 2;
      const prevCenterY = avatarCropRect.y + avatarCropRect.size / 2;

      avatarCropImageSize.width = width;
      avatarCropImageSize.height = height;
      avatarCropNaturalSize.width = target.naturalWidth;
      avatarCropNaturalSize.height = target.naturalHeight;

      const shortest = Math.min(width, height);
      avatarCropRect.size = Math.min(shortest, Math.max(40, Math.floor((shortest * avatarCropScale.value) / 100)));

      if (preserveCenter && prevWidth > 0 && prevHeight > 0) {
        const ratioX = prevCenterX / prevWidth;
        const ratioY = prevCenterY / prevHeight;
        avatarCropRect.x = Math.floor(ratioX * width - avatarCropRect.size / 2);
        avatarCropRect.y = Math.floor(ratioY * height - avatarCropRect.size / 2);
      } else {
        avatarCropRect.x = Math.floor((width - avatarCropRect.size) / 2);
        avatarCropRect.y = Math.floor((height - avatarCropRect.size) / 2);
      }

      clampCropRect();
    }

    function onAvatarImageLoaded() {
      teardownAvatarResizeObserver();
      updateAvatarCropMetrics(false);
      nextTick(() => updateAvatarCropMetrics(true));
      window.setTimeout(() => updateAvatarCropMetrics(true), 120);

      if (avatarCropImageRef.value && typeof ResizeObserver !== "undefined") {
        avatarResizeObserver.value = new ResizeObserver(() => {
          updateAvatarCropMetrics(true);
        });
        avatarResizeObserver.value.observe(avatarCropImageRef.value);
      }
    }

    function startAvatarCropDrag(event: PointerEvent) {
      if (avatarCropRect.size <= 0) {
        return;
      }
      avatarCropDragging.value = true;
      avatarDragStart.pointerX = event.clientX;
      avatarDragStart.pointerY = event.clientY;
      avatarDragStart.rectX = avatarCropRect.x;
      avatarDragStart.rectY = avatarCropRect.y;
      (event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId);
    }

    function onAvatarCropPointerMove(event: PointerEvent) {
      if (!avatarCropDragging.value) return;
      const dx = event.clientX - avatarDragStart.pointerX;
      const dy = event.clientY - avatarDragStart.pointerY;
      avatarCropRect.x = Math.floor(avatarDragStart.rectX + dx);
      avatarCropRect.y = Math.floor(avatarDragStart.rectY + dy);
      clampCropRect();
    }

    function replaceUserInList(updated: UserPublic) {
      users.value = users.value.map((item) => (item.id === updated.id ? updated : item));
    }

    function prependUserInList(created: UserPublic) {
      users.value = [created, ...users.value.filter((item) => item.id !== created.id)];
    }

    function removeUserFromList(userId: string) {
      users.value = users.value.filter((item) => item.id !== userId);
    }

    function resetCreateForm() {
      createForm.username = "";
      createForm.display_name = "";
      createForm.email = "";
      createForm.password = "";
      createForm.confirm_password = "";
      createForm.is_admin = false;
      createForm.is_blocked = false;
      createForm.conversation_limit_input = "";
      createErrorText.value = "";
    }

    function resetEditForm() {
      closeCropDialog();
      form.id = "";
      form.username = "";
      form.display_name = "";
      form.email = "";
      form.new_password = "";
      form.confirm_password = "";
      form.is_admin = false;
      form.is_blocked = false;
      form.conversation_limit_input = "";
      form.avatar_url = "";
      clearSelectedAvatar();
      editErrorText.value = "";
      editSuccessText.value = "";
    }

    function openCreate() {
      resetCreateForm();
      createDialog.value = true;
    }

    function closeCreate() {
      createDialog.value = false;
      resetCreateForm();
    }

    async function load() {
      if (!auth.state.accessToken) {
        return;
      }
      loading.value = true;
      errorText.value = "";
      try {
        const res = await listUsers(auth.state.accessToken, {
          search: search.value.trim() || undefined,
        });
        users.value = res.items;
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("admin.users.loadFailed");
      } finally {
        loading.value = false;
      }
    }

    async function submitCreate() {
      if (!auth.state.accessToken || !canCreate.value) {
        return;
      }
      creating.value = true;
      createErrorText.value = "";
      try {
        const created = await createUser(auth.state.accessToken, {
          username: createForm.username.trim(),
          display_name: createForm.display_name.trim() || undefined,
          email: createForm.email.trim(),
          password: createForm.password,
          is_admin: createForm.is_admin,
          is_blocked: createForm.is_blocked,
          conversation_limit: createConversationLimitState.value.value,
        });
        prependUserInList(created);
        closeCreate();
      } catch (error) {
        createErrorText.value = error instanceof Error ? error.message : t("admin.users.createFailed");
      } finally {
        creating.value = false;
      }
    }

    function openEdit(user: UserPublic) {
      resetEditForm();
      form.id = user.id;
      form.username = user.username;
      form.display_name = user.display_name ?? "";
      form.email = user.email;
      form.is_admin = user.is_admin;
      form.is_blocked = user.is_blocked;
      form.conversation_limit_input =
        typeof user.conversation_limit === "number" ? String(user.conversation_limit) : "";
      form.avatar_url = user.avatar_url ?? "";
      editDialog.value = true;
    }

    function openDeleteDialog(user: UserPublic) {
      if (deletingUserId.value) {
        return;
      }
      if (auth.state.user?.id === user.id) {
        errorText.value = t("admin.editUser.selfDeleteForbidden");
        return;
      }
      deleteTargetUser.value = user;
      deleteDialog.value = true;
    }

    function closeDeleteDialog() {
      if (deletingUserId.value) {
        return;
      }
      deleteDialog.value = false;
      deleteTargetUser.value = null;
    }

    async function removeUser(user: UserPublic) {
      if (!auth.state.accessToken || deletingUserId.value) {
        return;
      }
      if (auth.state.user?.id === user.id) {
        errorText.value = t("admin.editUser.selfDeleteForbidden");
        return;
      }
      deletingUserId.value = user.id;
      errorText.value = "";
      try {
        await deleteUser(auth.state.accessToken, user.id);
        removeUserFromList(user.id);
        if (form.id === user.id) {
          closeEdit();
        }
        closeDeleteDialog();
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("admin.users.deleteFailed");
      } finally {
        deletingUserId.value = "";
      }
    }

    async function confirmDeleteUser() {
      if (!deleteTargetUser.value) {
        closeDeleteDialog();
        return;
      }
      await removeUser(deleteTargetUser.value);
    }

    function closeEdit() {
      editDialog.value = false;
      resetEditForm();
    }

    function onAvatarFileChange(files: File | File[] | null) {
      const selected = Array.isArray(files) ? files[0] : files;
      if (!selected) {
        clearSelectedAvatar();
        closeCropDialog();
        return;
      }
      editErrorText.value = "";
      editSuccessText.value = "";
      revokeCropImageUrl();
      resetAvatarCropState();
      avatarCropImageUrl.value = URL.createObjectURL(selected);
      avatarCropDialog.value = true;
    }

    async function confirmAvatarCrop() {
      if (!avatarCropImageUrl.value || avatarCropImageSize.width <= 0 || avatarCropNaturalSize.width <= 0) {
        closeCropDialog();
        return;
      }
      try {
        const img = new Image();
        const objectUrl = avatarCropImageUrl.value;
        await new Promise<void>((resolve, reject) => {
          img.onload = () => resolve();
          img.onerror = () => reject(new Error("avatar load failed"));
          img.src = objectUrl;
        });

        const scaleX = avatarCropNaturalSize.width / avatarCropImageSize.width;
        const scaleY = avatarCropNaturalSize.height / avatarCropImageSize.height;
        const sx = Math.max(0, Math.floor(avatarCropRect.x * scaleX));
        const sy = Math.max(0, Math.floor(avatarCropRect.y * scaleY));
        const sSize = Math.max(1, Math.floor(avatarCropRect.size * Math.min(scaleX, scaleY)));

        const canvas = document.createElement("canvas");
        const outputSize = 512;
        canvas.width = outputSize;
        canvas.height = outputSize;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          throw new Error("canvas context unavailable");
        }
        ctx.drawImage(img, sx, sy, sSize, sSize, 0, 0, outputSize, outputSize);

        const blob = await new Promise<Blob | null>((resolve) => {
          canvas.toBlob((value) => resolve(value), "image/png", 0.92);
        });
        if (!blob) {
          throw new Error("avatar encode failed");
        }

        croppedAvatarFile.value = new File([blob], "avatar.png", { type: "image/png" });
        avatarFile.value = croppedAvatarFile.value;
        revokeAvatarPreviewUrl();
        avatarPreviewUrl.value = URL.createObjectURL(blob);
        closeCropDialog();
      } catch (error) {
        editErrorText.value = error instanceof Error ? error.message : t("admin.editUser.saveFailed");
        closeCropDialog();
      }
    }

    async function saveEdit() {
      if (!auth.state.accessToken || !canSave.value) {
        return;
      }

      saving.value = true;
      editErrorText.value = "";
      editSuccessText.value = "";
      try {
        const selectedAvatar = croppedAvatarFile.value;
        if (selectedAvatar) {
          const avatarUpdatedUser = await uploadAdminUserAvatar(auth.state.accessToken, form.id, selectedAvatar);
          form.avatar_url = avatarUpdatedUser.avatar_url ?? "";
          replaceUserInList(avatarUpdatedUser);
          if (auth.state.user?.id === avatarUpdatedUser.id) {
            auth.updateUser(avatarUpdatedUser);
          }
          clearSelectedAvatar();
        }

        const payload: {
          display_name?: string;
          email?: string;
          new_password?: string;
          is_admin?: boolean;
          is_blocked?: boolean;
          conversation_limit?: number | null;
        } = {
          display_name: form.display_name.trim() || undefined,
          email: form.email.trim() || undefined,
          new_password: form.new_password.trim() || undefined,
          conversation_limit: editConversationLimitState.value.value,
        };
        if (!isEditingSelf.value) {
          payload.is_admin = form.is_admin;
          payload.is_blocked = form.is_blocked;
        }
        const updated = await updateUser(auth.state.accessToken, form.id, payload);

        replaceUserInList(updated);
        if (auth.state.user?.id === updated.id) {
          auth.updateUser(updated);
        }
        form.new_password = "";
        form.confirm_password = "";
        editSuccessText.value = t("admin.editUser.saveSuccess");
      } catch (error) {
        editErrorText.value = error instanceof Error ? error.message : t("admin.editUser.saveFailed");
      } finally {
        saving.value = false;
      }
    }

    onMounted(() => {
      void (async () => {
        try {
          const settings = await getAppSettings();
          if (typeof settings.default_user_conversation_limit === "number") {
            defaultUserConversationLimit.value = settings.default_user_conversation_limit;
          }
        } catch {
          // Ignore settings load failures and keep local fallback.
        }
      })();
      void load();
    });

    watch(avatarCropScale, (value) => {
      applyCropScale(value);
    });

    watch(avatarCropDialog, (open) => {
      if (open) {
        nextTick(() => updateAvatarCropMetrics(true));
      }
    });

    watch(createDialog, (open) => {
      if (!open) {
        resetCreateForm();
      }
    });

    onBeforeUnmount(() => {
      clearSelectedAvatar();
      closeCropDialog();
    });

    return {
      users,
      loading,
      saving,
      creating,
      deletingUserId,
      errorText,
      createErrorText,
      editErrorText,
      editSuccessText,
      deleteDialog,
      deleteTargetUser,
      createDialog,
      editDialog,
      search,
      form,
      createForm,
      avatarFile,
      avatarPreviewSrc,
      avatarCropDialog,
      avatarCropImageUrl,
      avatarCropImageRef,
      avatarCropScale,
      avatarCropRectStyle,
      avatarCropRect,
      passwordMismatch,
      createPasswordMismatch,
      canSave,
      canCreate,
      currentUserId,
      isEditingSelf,
      headers,
      t,
      fmt,
      fmtOptional,
      formatConversationLimit,
      defaultUserConversationLimit,
      createConversationLimitState,
      editConversationLimitState,
      load,
      openCreate,
      closeCreate,
      submitCreate,
      openEdit,
      closeEdit,
      openDeleteDialog,
      closeDeleteDialog,
      confirmDeleteUser,
      removeUser,
      saveEdit,
      onAvatarFileChange,
      onAvatarImageLoaded,
      startAvatarCropDrag,
      onAvatarCropPointerMove,
      stopAvatarCropDrag,
      closeCropDialog,
      confirmAvatarCrop,
      userInitials,
      userAvatarSrc,
    };
  },
});
