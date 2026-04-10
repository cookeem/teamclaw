import { computed, defineComponent, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { applyBackendDefaultLocale, useI18n } from "./i18n";
import { apiBase, getAppSettings, getMe, listConversations, logout, updateConversation, updateMe, uploadMyAvatar } from "./services/api";
import { useAuthStore } from "./stores/auth";
import type { Conversation } from "./types/models";

export default defineComponent({
  name: "App",
  setup() {
    const router = useRouter();
    const route = useRoute();
    const auth = useAuthStore();
    const { locale, setLocale, t, localeItems, hasStoredLocalePreference } = useI18n();
    const isAuthRoute = computed(() => Boolean(route.meta.public));
    const isChatRoute = computed(() => String(route.path).startsWith("/app/chats/"));
    const conversations = ref<Conversation[]>([]);
    const sidebarLoading = ref(false);
    const sidebarError = ref("");
    const renameDialog = ref(false);
    const renameSaving = ref(false);
    const renameTargetConversation = ref<Conversation | null>(null);
    const renameValue = ref("");
    const profileDialog = ref(false);
    const profileLoading = ref(false);
    const profileSaving = ref(false);
    const profileError = ref("");
    const profileSuccess = ref("");
    const profileAvatarFile = ref<File | File[] | null>(null);
    const pendingCroppedAvatar = ref<File | null>(null);
    const pendingCroppedAvatarPreviewUrl = ref("");
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
    const profileForm = reactive({
      username: "",
      display_name: "",
      email: "",
      current_password: "",
      new_password: "",
      confirm_password: "",
    });
    const appVersion = ref("");
    provide("teamclawAppVersion", appVersion);

    const activeConversationId = computed(() => String(route.params.id ?? ""));
    const displayName = computed(
      () => auth.state.user?.display_name || auth.state.user?.username || "User",
    );
    const canSubmitRename = computed(() => {
      const target = renameTargetConversation.value;
      if (!target || !auth.state.accessToken || renameSaving.value) {
        return false;
      }
      const normalized = renameValue.value.trim();
      return normalized.length > 0 && normalized !== (target.title || "");
    });

    onMounted(async () => {
      try {
        const settings = await getAppSettings();
        appVersion.value = typeof settings.version === "string" ? settings.version : "";
        if (!hasStoredLocalePreference()) {
          applyBackendDefaultLocale(settings.language);
        }
      } catch {
        // Keep frontend default locale when backend settings are unavailable.
      }
    });

    function sortedConversations(items: Conversation[]): Conversation[] {
      return [...items].sort((a, b) => {
        const pinnedDiff = Number(b.is_pinned) - Number(a.is_pinned);
        if (pinnedDiff !== 0) {
          return pinnedDiff;
        }
        return Date.parse(b.updated_at) - Date.parse(a.updated_at);
      });
    }

    async function loadConversations() {
      if (!auth.state.accessToken || isAuthRoute.value) {
        conversations.value = [];
        return;
      }
      sidebarLoading.value = true;
      sidebarError.value = "";
      try {
        const payload = await listConversations(auth.state.accessToken, { page: 1, page_size: 100 });
        conversations.value = sortedConversations(payload.items);
      } catch (error) {
        sidebarError.value = error instanceof Error ? error.message : t("sidebar.loadFailed");
      } finally {
        sidebarLoading.value = false;
      }
    }

    async function handleLogout() {
      if (!auth.state.accessToken) {
        auth.clearAuth();
        await router.push("/login");
        return;
      }

      try {
        await logout(auth.state.accessToken, {
          refresh_token: auth.state.refreshToken || undefined,
          revoke_all: false,
        });
      } catch {
        // Ignore logout network errors; local clear is enough for UX.
      }

      auth.clearAuth();
      await router.push("/login");
    }

    function userInitials(): string {
      const raw = displayName.value || "";
      const cleaned = raw.trim();
      if (!cleaned) {
        return "TC";
      }
      const parts = cleaned.split(/\s+/).filter(Boolean);
      if (parts.length >= 2) {
        return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
      }
      return cleaned.slice(0, 2).toUpperCase();
    }

    function toApiAssetUrl(url: string | null | undefined): string | null {
      if (!url || !url.trim()) {
        return null;
      }
      if (/^https?:\/\//i.test(url)) {
        return url;
      }
      const base = apiBase().replace(/\/$/, "");
      const path = url.startsWith("/") ? url : `/${url}`;
      return `${base}${path}`;
    }

    const userAvatarUrl = computed(() => toApiAssetUrl(auth.state.user?.avatar_url ?? null));
    const profileAvatarPreviewUrl = computed(
      () => pendingCroppedAvatarPreviewUrl.value || userAvatarUrl.value,
    );
    const avatarCropRectStyle = computed(() => ({
      left: `${avatarCropRect.x}px`,
      top: `${avatarCropRect.y}px`,
      width: `${avatarCropRect.size}px`,
      height: `${avatarCropRect.size}px`,
    }));

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

    function revokePendingAvatarPreviewUrl() {
      if (pendingCroppedAvatarPreviewUrl.value) {
        URL.revokeObjectURL(pendingCroppedAvatarPreviewUrl.value);
        pendingCroppedAvatarPreviewUrl.value = "";
      }
    }

    function clearPendingCroppedAvatar() {
      pendingCroppedAvatar.value = null;
      profileAvatarFile.value = null;
      revokePendingAvatarPreviewUrl();
    }

    function closeProfileDialog() {
      profileDialog.value = false;
      profileError.value = "";
      profileSuccess.value = "";
      profileForm.current_password = "";
      profileForm.new_password = "";
      profileForm.confirm_password = "";
      clearPendingCroppedAvatar();
      closeCropDialog();
    }

    async function openProfileDialog() {
      if (!auth.state.accessToken) {
        return;
      }
      profileDialog.value = true;
      profileLoading.value = true;
      profileError.value = "";
      profileSuccess.value = "";
      clearPendingCroppedAvatar();
      try {
        const me = await getMe(auth.state.accessToken);
        auth.updateUser(me);
        profileForm.username = me.username;
        profileForm.display_name = me.display_name ?? "";
        profileForm.email = me.email;
        profileForm.current_password = "";
        profileForm.new_password = "";
        profileForm.confirm_password = "";
      } catch (error) {
        profileError.value = error instanceof Error ? error.message : t("profile.loadFailed");
      } finally {
        profileLoading.value = false;
      }
    }

    function revokeCropImageUrl() {
      if (avatarCropImageUrl.value) {
        URL.revokeObjectURL(avatarCropImageUrl.value);
        avatarCropImageUrl.value = "";
      }
    }

    function closeCropDialog() {
      stopAvatarCropDrag();
      avatarCropDialog.value = false;
      profileAvatarFile.value = null;
      teardownAvatarResizeObserver();
      avatarCropImageRef.value = null;
      revokeCropImageUrl();
      resetAvatarCropState();
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

    function stopAvatarCropDrag(event?: PointerEvent) {
      avatarCropDragging.value = false;
      if (event) {
        (event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId);
      }
    }

    function onAvatarFileChange(files: File | File[] | null) {
      const selected = Array.isArray(files) ? files[0] : files;
      if (!selected) {
        clearPendingCroppedAvatar();
        return;
      }
      profileError.value = "";
      profileSuccess.value = "";
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

        revokePendingAvatarPreviewUrl();
        pendingCroppedAvatar.value = new File([blob], "avatar.png", { type: "image/png" });
        pendingCroppedAvatarPreviewUrl.value = URL.createObjectURL(blob);
        closeCropDialog();
      } catch (error) {
        profileError.value = error instanceof Error ? error.message : t("profile.saveFailed");
        closeCropDialog();
      }
    }

    const profilePasswordMismatch = computed(
      () =>
        profileForm.new_password.trim().length > 0 &&
        profileForm.confirm_password.trim().length > 0 &&
        profileForm.new_password !== profileForm.confirm_password,
    );

    const canSaveProfile = computed(() => {
      if (profileSaving.value || profileLoading.value || !auth.state.accessToken) {
        return false;
      }
      if (profilePasswordMismatch.value) {
        return false;
      }
      if (profileForm.new_password.trim().length > 0 && profileForm.current_password.trim().length < 8) {
        return false;
      }
      return true;
    });

    async function saveProfile() {
      if (!auth.state.accessToken || !canSaveProfile.value) {
        return;
      }
      profileSaving.value = true;
      profileError.value = "";
      profileSuccess.value = "";
      try {
        const avatarFile = pendingCroppedAvatar.value;
        if (avatarFile) {
          const uploaded = await uploadMyAvatar(auth.state.accessToken, avatarFile);
          auth.updateUser(uploaded);
          clearPendingCroppedAvatar();
        }

        const updated = await updateMe(auth.state.accessToken, {
          display_name: profileForm.display_name.trim() || undefined,
          email: profileForm.email.trim() || undefined,
          current_password: profileForm.current_password.trim() || undefined,
          new_password: profileForm.new_password.trim() || undefined,
        });
        auth.updateUser(updated);
        profileForm.username = updated.username;
        profileForm.display_name = updated.display_name ?? "";
        profileForm.email = updated.email;
        profileForm.current_password = "";
        profileForm.new_password = "";
        profileForm.confirm_password = "";
        profileSuccess.value = t("profile.saveSuccess");
      } catch (error) {
        profileError.value = error instanceof Error ? error.message : t("profile.saveFailed");
      } finally {
        profileSaving.value = false;
      }
    }

    function openConversation(id: string) {
      void router.push(`/app/chats/${id}`);
    }

    function goNewChat() {
      void router.push("/app/chats/new");
    }

    function openRenameConversationDialog(item: Conversation) {
      renameTargetConversation.value = item;
      renameValue.value = item.title || "";
      renameSaving.value = false;
      renameDialog.value = true;
    }

    function closeRenameConversationDialog() {
      renameDialog.value = false;
      renameSaving.value = false;
      renameTargetConversation.value = null;
      renameValue.value = "";
    }

    async function submitRenameConversation() {
      const target = renameTargetConversation.value;
      if (!target || !auth.state.accessToken || !canSubmitRename.value) {
        return;
      }
      const normalized = renameValue.value.trim();
      renameSaving.value = true;
      try {
        await updateConversation(auth.state.accessToken, target.id, { title: normalized });
        await loadConversations();
        closeRenameConversationDialog();
      } catch (error) {
        sidebarError.value = error instanceof Error ? error.message : t("sidebar.updateFailed");
      } finally {
        renameSaving.value = false;
      }
    }

    async function togglePinConversation(item: Conversation) {
      if (!auth.state.accessToken) {
        return;
      }
      try {
        await updateConversation(auth.state.accessToken, item.id, { is_pinned: !item.is_pinned });
        await loadConversations();
      } catch (error) {
        sidebarError.value = error instanceof Error ? error.message : t("sidebar.updateFailed");
      }
    }

    watch(
      () => [auth.state.accessToken, route.fullPath],
      () => {
        void loadConversations();
      },
      { immediate: true },
    );

    watch(avatarCropScale, (value) => {
      applyCropScale(value);
    });

    watch(profileDialog, (open) => {
      if (!open) {
        profileError.value = "";
        profileSuccess.value = "";
        profileForm.current_password = "";
        profileForm.new_password = "";
        profileForm.confirm_password = "";
        clearPendingCroppedAvatar();
        closeCropDialog();
      }
    });

    watch(avatarCropDialog, (open) => {
      if (open) {
        nextTick(() => updateAvatarCropMetrics(true));
      }
    });

    onBeforeUnmount(() => {
      teardownAvatarResizeObserver();
      revokeCropImageUrl();
      revokePendingAvatarPreviewUrl();
    });

    return {
      auth,
      locale,
      setLocale,
      t,
      localeItems,
      isAuthRoute,
      isChatRoute,
      activeConversationId,
      displayName,
      userAvatarUrl,
      conversations,
      sidebarLoading,
      sidebarError,
      renameDialog,
      renameSaving,
      renameValue,
      renameTargetConversation,
      canSubmitRename,
      profileDialog,
      appVersion,
      profileLoading,
      profileSaving,
      profileError,
      profileSuccess,
      profileAvatarFile,
      pendingCroppedAvatar,
      profileAvatarPreviewUrl,
      avatarCropDialog,
      avatarCropImageUrl,
      avatarCropImageRef,
      avatarCropScale,
      avatarCropRectStyle,
      profileForm,
      profilePasswordMismatch,
      canSaveProfile,
      handleLogout,
      openProfileDialog,
      closeProfileDialog,
      saveProfile,
      onAvatarFileChange,
      onAvatarImageLoaded,
      startAvatarCropDrag,
      onAvatarCropPointerMove,
      stopAvatarCropDrag,
      confirmAvatarCrop,
      closeCropDialog,
      avatarCropRect,
      goNewChat,
      openConversation,
      openRenameConversationDialog,
      closeRenameConversationDialog,
      submitRenameConversation,
      togglePinConversation,
      userInitials,
    };
  },
});
