import { defineComponent, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useI18n } from "../../i18n";
import { listUsers, updateUser } from "../../services/api";
import { useAuthStore } from "../../stores/auth";

export default defineComponent({
  name: "EditUserPage",
  setup() {
    const route = useRoute();
    const router = useRouter();
    const auth = useAuthStore();
    const { t } = useI18n();

    const userId = String(route.params.id ?? "");
    const saving = ref(false);
    const errorText = ref("");
    const successText = ref("");

    const form = reactive({
      username: "",
      display_name: "",
      is_admin: false,
      is_blocked: false,
    });

    async function load() {
      if (!auth.state.accessToken || !userId) {
        return;
      }
      errorText.value = "";
      try {
        const res = await listUsers(auth.state.accessToken, { page_size: 200 });
        const target = res.items.find((item) => item.id === userId);
        if (!target) {
          errorText.value = t("admin.editUser.userNotFound");
          return;
        }
        form.username = target.username;
        form.display_name = target.display_name ?? "";
        form.is_admin = target.is_admin;
        form.is_blocked = target.is_blocked;
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("admin.editUser.loadFailed");
      }
    }

    async function save() {
      if (!auth.state.accessToken || !userId || saving.value) {
        return;
      }
      saving.value = true;
      errorText.value = "";
      successText.value = "";
      try {
        await updateUser(auth.state.accessToken, userId, {
          username: form.username.trim(),
          display_name: form.display_name.trim() || undefined,
          is_admin: form.is_admin,
          is_blocked: form.is_blocked,
        });
        successText.value = t("admin.editUser.saveSuccess");
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("admin.editUser.saveFailed");
      } finally {
        saving.value = false;
      }
    }

    function goBack() {
      void router.push("/admin/users");
    }

    onMounted(() => {
      void load();
    });

    return {
      userId,
      form,
      saving,
      errorText,
      successText,
      t,
      save,
      goBack,
    };
  },
});
