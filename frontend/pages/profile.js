const ProfilePage = {
  template: `
    <v-container fluid class="page-shell">
      <h2 class="page-title">{{ $t('profile.title') }}</h2>
      <v-card class="panel-card" rounded="xl">
        <v-card-text>
          <v-text-field :label="$t('profile.username')" :model-value="session.user?.username || ''" variant="outlined" readonly />
          <v-text-field :label="$t('profile.display_name')" v-model="form.display_name" variant="outlined" />
          <v-text-field :label="$t('profile.email')" v-model="form.email" variant="outlined" />
          <v-file-input
            :label="$t('profile.avatar_upload')"
            prepend-icon="mdi-camera"
            accept="image/*"
            v-model="avatarFileInput"
            @update:modelValue="onAvatarFileChange"
            variant="outlined"
            show-size
          />
          <div class="mb-3" v-if="session.user?.avatar_url">
            <v-avatar size="64">
              <v-img :src="toAssetUrl(session.user.avatar_url)" cover></v-img>
            </v-avatar>
          </div>
          <v-btn color="secondary" :loading="savingProfile" @click="saveProfile">{{ $t('profile.save_profile') }}</v-btn>
        </v-card-text>
      </v-card>

      <v-card class="panel-card mt-4" rounded="xl">
        <v-card-title>{{ $t('profile.change_password') }}</v-card-title>
        <v-card-text>
          <v-text-field :label="$t('profile.old_password')" v-model="passwordForm.old_password" type="password" variant="outlined" />
          <v-text-field :label="$t('profile.new_password')" v-model="passwordForm.new_password" type="password" variant="outlined" />
          <v-btn color="secondary" :loading="savingPassword" @click="changePassword">{{ $t('profile.update_password') }}</v-btn>
        </v-card-text>
      </v-card>

      <v-dialog v-model="cropDialog" max-width="980" persistent>
        <v-card>
          <v-card-title>{{ $t('profile.crop_image') }}</v-card-title>
          <v-card-text>
            <div
              class="crop-stage"
              ref="cropStageRef"
              :style="{ width: display.width + 'px', height: display.height + 'px' }"
            >
              <img class="crop-image" :src="cropSourceUrl" alt="crop-source" />
              <div
                class="crop-square"
                :style="cropBoxStyle"
                @mousedown="startMove"
              >
                <div class="crop-handle tl" @mousedown.stop="startResize('tl', $event)"></div>
                <div class="crop-handle tr" @mousedown.stop="startResize('tr', $event)"></div>
                <div class="crop-handle bl" @mousedown.stop="startResize('bl', $event)"></div>
                <div class="crop-handle br" @mousedown.stop="startResize('br', $event)"></div>
              </div>
            </div>
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" @click="cancelCrop">{{ $t('profile.cancel') }}</v-btn>
            <v-btn color="secondary" @click="confirmCrop">{{ $t('profile.confirm') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
      <v-alert v-if="notice" type="success" variant="tonal" class="mt-3">{{ notice }}</v-alert>
    </v-container>
  `,
  setup() {
    const { t } = window.TeamClawI18n;
    const error = ref("");
    const notice = ref("");
    const savingProfile = ref(false);
    const savingPassword = ref(false);
    const avatarFileInput = ref(null);
    const cropDialog = ref(false);
    const cropStageRef = ref(null);
    const cropSourceUrl = ref("");
    const cropImage = ref(null);
    const cropImageReady = ref(false);
    const display = reactive({ width: 0, height: 0 });
    const crop = reactive({ x: 0, y: 0, size: 160, minSize: 64 });
    const drag = reactive({ mode: "", startX: 0, startY: 0, baseX: 0, baseY: 0, baseSize: 0, corner: "br" });

    const form = reactive({ display_name: "", email: "" });
    const passwordForm = reactive({ old_password: "", new_password: "" });

    const cropBoxStyle = computed(() => ({
      left: `${crop.x}px`,
      top: `${crop.y}px`,
      width: `${crop.size}px`,
      height: `${crop.size}px`,
    }));

    const clampCrop = () => {
      const maxSize = Math.min(display.width, display.height);
      crop.size = Math.max(crop.minSize, Math.min(crop.size, maxSize));
      const maxX = Math.max(0, display.width - crop.size);
      const maxY = Math.max(0, display.height - crop.size);
      crop.x = Math.max(0, Math.min(crop.x, maxX));
      crop.y = Math.max(0, Math.min(crop.y, maxY));
    };

    const stopDrag = () => {
      drag.mode = "";
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", stopDrag);
    };

    const onMouseMove = (e) => {
      if (!drag.mode) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (drag.mode === "move") {
        crop.x = drag.baseX + dx;
        crop.y = drag.baseY + dy;
      } else if (drag.mode === "resize") {
        if (drag.corner === "br") {
          const delta = Math.max(dx, dy);
          const maxSize = Math.min(display.width - drag.baseX, display.height - drag.baseY);
          crop.size = Math.max(crop.minSize, Math.min(drag.baseSize + delta, maxSize));
        } else if (drag.corner === "tl") {
          const delta = Math.min(dx, dy);
          const targetSize = drag.baseSize - delta;
          const maxSize = Math.min(drag.baseX + drag.baseSize, drag.baseY + drag.baseSize);
          crop.size = Math.max(crop.minSize, Math.min(targetSize, maxSize));
          crop.x = drag.baseX + (drag.baseSize - crop.size);
          crop.y = drag.baseY + (drag.baseSize - crop.size);
        } else if (drag.corner === "tr") {
          const delta = Math.max(dx, -dy);
          const maxSize = Math.min(display.width - drag.baseX, drag.baseY + drag.baseSize);
          crop.size = Math.max(crop.minSize, Math.min(drag.baseSize + delta, maxSize));
          crop.y = drag.baseY + (drag.baseSize - crop.size);
        } else if (drag.corner === "bl") {
          const delta = Math.max(-dx, dy);
          const maxSize = Math.min(drag.baseX + drag.baseSize, display.height - drag.baseY);
          crop.size = Math.max(crop.minSize, Math.min(drag.baseSize + delta, maxSize));
          crop.x = drag.baseX + (drag.baseSize - crop.size);
        }
      }
      clampCrop();
    };

    const startMove = (e) => {
      drag.mode = "move";
      drag.startX = e.clientX;
      drag.startY = e.clientY;
      drag.baseX = crop.x;
      drag.baseY = crop.y;
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", stopDrag);
    };

    const startResize = (corner, e) => {
      drag.mode = "resize";
      drag.corner = corner;
      drag.startX = e.clientX;
      drag.startY = e.clientY;
      drag.baseX = crop.x;
      drag.baseY = crop.y;
      drag.baseSize = crop.size;
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", stopDrag);
    };

    const cancelCrop = () => {
      cropDialog.value = false;
      cropImageReady.value = false;
      cropImage.value = null;
      cropSourceUrl.value = "";
      avatarFileInput.value = null;
      stopDrag();
    };

    const confirmCrop = () => {
      cropDialog.value = false;
      clampCrop();
      cropImageReady.value = true;
    };

    const onAvatarFileChange = async (fileValue) => {
      const file = Array.isArray(fileValue) ? fileValue[0] : fileValue;
      if (!file) {
        cancelCrop();
        return;
      }
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error(t("profile.read_avatar_failed")));
        reader.readAsDataURL(file);
      });
      const image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error(t("profile.invalid_image")));
        img.src = dataUrl;
      });

      cropImage.value = image;
      cropSourceUrl.value = dataUrl;
      const maxW = 860;
      const maxH = 520;
      const scale = Math.min(maxW / image.width, maxH / image.height, 1);
      display.width = Math.round(image.width * scale);
      display.height = Math.round(image.height * scale);

      crop.size = Math.round(Math.min(display.width, display.height) * 0.7);
      crop.x = Math.round((display.width - crop.size) / 2);
      crop.y = Math.round((display.height - crop.size) / 2);
      clampCrop();
      cropImageReady.value = true;
      cropDialog.value = true;
    };

    const getCroppedAvatarBlob = async () => {
      const image = cropImage.value;
      if (!image || display.width <= 0 || display.height <= 0) return null;
      const scaleX = image.width / display.width;
      const scaleY = image.height / display.height;
      const sx = Math.round(crop.x * scaleX);
      const sy = Math.round(crop.y * scaleY);
      const sSize = Math.round(Math.min(crop.size * scaleX, crop.size * scaleY));
      const output = document.createElement("canvas");
      output.width = 256;
      output.height = 256;
      const ctx = output.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(image, sx, sy, sSize, sSize, 0, 0, 256, 256);
      return await new Promise((resolve) => output.toBlob(resolve, "image/png", 0.95));
    };

    const loadMe = async () => {
      error.value = "";
      try {
        const me = await apiFetch("/users/me");
        session.user = me;
        localStorage.setItem("teamclaw_user", JSON.stringify(me));
        form.display_name = me.display_name || "";
        form.email = me.email || "";
      } catch (e) {
        error.value = e.message;
      }
    };

    const saveProfile = async () => {
      savingProfile.value = true;
      error.value = "";
      notice.value = "";
      try {
        const me = await apiFetch("/users/me", {
          method: "PATCH",
          body: JSON.stringify({
            display_name: form.display_name,
            email: form.email,
          }),
        });
        if (cropImageReady.value) {
          const blob = await getCroppedAvatarBlob();
          if (!blob) throw new Error(t("profile.crop_failed"));
          const fd = new FormData();
          fd.append("avatar", blob, "avatar.png");
          const headers = {};
          if (session.token) headers.Authorization = `Bearer ${session.token}`;
          const res = await fetch(`${API_BASE}/users/me/avatar`, {
            method: "POST",
            headers,
            body: fd,
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
          Object.assign(me, data);
          avatarFileInput.value = null;
          cropImage.value = null;
          cropSourceUrl.value = "";
          cropImageReady.value = false;
        }
        session.user = me;
        localStorage.setItem("teamclaw_user", JSON.stringify(me));
        notice.value = t("profile.profile_updated");
      } catch (e) {
        error.value = e.message;
      } finally {
        savingProfile.value = false;
      }
    };

    const changePassword = async () => {
      savingPassword.value = true;
      error.value = "";
      notice.value = "";
      try {
        await apiFetch("/users/me/password", {
          method: "POST",
          body: JSON.stringify(passwordForm),
        });
        notice.value = t("profile.password_updated");
        passwordForm.old_password = "";
        passwordForm.new_password = "";
      } catch (e) {
        error.value = e.message;
      } finally {
        savingPassword.value = false;
      }
    };

    onBeforeUnmount(stopDrag);
    onMounted(loadMe);
    return {
      session,
      form,
      avatarFileInput,
      cropDialog,
      cropStageRef,
      cropSourceUrl,
      display,
      crop,
      cropBoxStyle,
      startMove,
      startResize,
      cancelCrop,
      confirmCrop,
      cropImageReady,
      passwordForm,
      savingProfile,
      savingPassword,
      error,
      notice,
      toAssetUrl,
      onAvatarFileChange,
      saveProfile,
      changePassword,
    };
  },
};
