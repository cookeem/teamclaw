import { computed, defineComponent, inject, ref, type Ref } from "vue";
import { useRouter } from "vue-router";

import { useI18n } from "../../i18n";
import { login } from "../../services/api";
import { useAuthStore } from "../../stores/auth";

export default defineComponent({
  name: "LoginPage",
  setup() {
    const router = useRouter();
    const auth = useAuthStore();
    const { locale, setLocale, localeItems, t } = useI18n();
    const injectedAppVersion = inject<Ref<string>>("teamclawAppVersion", ref(""));

    const account = ref("");
    const password = ref("");
    const loading = ref(false);
    const errorText = ref("");
    const appVersion = computed(() => (injectedAppVersion.value || "").trim());

    const canSubmit = computed(() => account.value.trim().length > 0 && password.value.trim().length >= 8);

    async function submit() {
      if (!canSubmit.value || loading.value) {
        return;
      }
      loading.value = true;
      errorText.value = "";
      try {
        const response = await login({
          account: account.value.trim(),
          password: password.value,
        });
        auth.setAuth({
          accessToken: response.access_token,
          refreshToken: response.refresh_token,
          user: response.user,
        });
        await router.push("/app/chats/new");
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("auth.loginFailed");
      } finally {
        loading.value = false;
      }
    }

    return {
      account,
      password,
      locale,
      setLocale,
      localeItems,
      t,
      loading,
      errorText,
      canSubmit,
      submit,
      appVersion,
    };
  },
});
