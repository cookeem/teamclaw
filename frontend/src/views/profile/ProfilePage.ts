import { defineComponent, onMounted, reactive, ref } from "vue";

import { useI18n } from "../../i18n";
import { getMe, updateMe } from "../../services/api";
import { useAuthStore } from "../../stores/auth";

export default defineComponent({
  name: "ProfilePage",
  setup() {
    const auth = useAuthStore();
    const { t } = useI18n();

    const saving = ref(false);
    const errorText = ref("");
    const successText = ref("");

    const form = reactive({
      display_name: "",
      username: "",
      email: "",
      current_password: "",
      new_password: "",
      confirm_password: "",
    });

    async function load() {
      if (!auth.state.accessToken) {
        return;
      }
      errorText.value = "";
      try {
        const me = await getMe(auth.state.accessToken);
        auth.updateUser(me);
        form.display_name = me.display_name ?? "";
        form.username = me.username;
        form.email = me.email;
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("profile.loadFailed");
      }
    }

    async function save() {
      if (!auth.state.accessToken || saving.value) {
        return;
      }
      saving.value = true;
      errorText.value = "";
      successText.value = "";
      try {
        const updated = await updateMe(auth.state.accessToken, {
          display_name: form.display_name.trim() || undefined,
          email: form.email.trim() || undefined,
          current_password: form.current_password.trim() || undefined,
          new_password: form.new_password.trim() || undefined,
        });
        auth.updateUser(updated);
        form.current_password = "";
        form.new_password = "";
        form.confirm_password = "";
        successText.value = t("profile.saveSuccess");
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("profile.saveFailed");
      } finally {
        saving.value = false;
      }
    }

    onMounted(() => {
      void load();
    });

    return {
      form,
      saving,
      errorText,
      successText,
      t,
      save,
    };
  },
});
