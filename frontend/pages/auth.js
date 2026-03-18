const LoginPage = {
  template: `
    <div class="auth-wrap">
      <div class="auth-panel">
        <div class="auth-header">
          <div class="auth-brand">
            <img class="auth-logo" src="/images/teamclaw_logo.png" alt="TeamClaw logo" />
            <h1 class="auth-title">TeamClaw</h1>
          </div>
          <v-select
            v-model="locale"
            :items="localeOptions"
            density="compact"
            variant="outlined"
            hide-details
            class="lang-select auth-lang"
          ></v-select>
        </div>

        <v-tabs v-model="tab" class="mb-4" color="secondary">
          <v-tab value="login">{{ $t('auth.login') }}</v-tab>
          <v-tab value="signup">{{ $t('auth.signup') }}</v-tab>
          <v-tab value="forgot">{{ $t('auth.forgot') }}</v-tab>
        </v-tabs>

        <v-window v-model="tab">
          <v-window-item value="login">
            <v-text-field :label="$t('auth.username_or_email')" v-model="loginForm.username" variant="outlined" hide-details />
            <v-text-field :label="$t('auth.password')" v-model="loginForm.password" type="password" variant="outlined" class="mt-3" hide-details />
            <v-btn :loading="loading" block color="secondary" class="mt-4" @click="doLogin">{{ $t('auth.login_submit') }}</v-btn>
          </v-window-item>

          <v-window-item value="signup">
            <v-text-field :label="$t('auth.username')" v-model="registerForm.username" variant="outlined" hide-details />
            <v-text-field :label="$t('auth.display_name')" v-model="registerForm.display_name" variant="outlined" class="mt-3" hide-details />
            <v-text-field :label="$t('auth.email')" v-model="registerForm.email" variant="outlined" class="mt-3" hide-details />
            <v-text-field :label="$t('auth.password')" v-model="registerForm.password" type="password" variant="outlined" class="mt-3" hide-details />
            <v-btn :loading="loading" block color="secondary" class="mt-4" @click="doRegister">{{ $t('auth.signup_submit') }}</v-btn>
          </v-window-item>

          <v-window-item value="forgot">
            <v-text-field :label="$t('auth.registered_email')" v-model="forgotForm.email" variant="outlined" hide-details />
            <div class="d-flex ga-2 mt-3">
              <v-btn :loading="loading" color="secondary" @click="requestResetCode">{{ $t('auth.send_reset') }}</v-btn>
              <v-chip size="small" variant="tonal">Step {{ forgotStep }}/3</v-chip>
            </div>

            <template v-if="forgotStep >= 2">
              <v-text-field :label="$t('auth.code_label')" v-model="forgotForm.code" variant="outlined" class="mt-3" hide-details />
              <v-btn :loading="loading" block color="secondary" class="mt-3" @click="verifyResetCode">{{ $t('auth.verify_code') }}</v-btn>
            </template>

            <template v-if="forgotStep >= 3">
              <v-text-field
                :label="$t('auth.new_password')"
                v-model="forgotForm.new_password"
                type="password"
                variant="outlined"
                class="mt-3"
                hide-details
              />
              <v-btn :loading="loading" block color="secondary" class="mt-3" @click="resetPassword">{{ $t('auth.reset_password') }}</v-btn>
            </template>

            <v-alert v-if="forgotHint" type="info" variant="tonal" class="mt-3">{{ forgotHint }}</v-alert>
          </v-window-item>
        </v-window>

        <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
      </div>
    </div>
  `,
  setup() {
    const i18n = window.TeamClawI18n || { t: (key) => key, setLocale: () => {}, getLocale: () => "en" };
    const { t } = i18n;
    const route = useRoute();
    const router = useRouter();
    const tab = ref("login");
    const loading = ref(false);
    const error = ref("");
    const forgotHint = ref("");
    const forgotStep = ref(1);
    const locale = ref(i18n.getLocale ? i18n.getLocale() : "en");
    const localeOptions = computed(() => [
      { title: t("common.lang_en"), value: "en" },
      { title: t("common.lang_zh"), value: "zh" },
    ]);

    const loginForm = reactive({ username: "", password: "" });
    const registerForm = reactive({ username: "", display_name: "", email: "", password: "" });
    const forgotForm = reactive({
      email: "",
      code: "",
      reset_token: "",
      new_password: "",
    });

    const doLogin = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await apiFetch("/auth/login", { method: "POST", body: JSON.stringify(loginForm) });
        setSession(data.access_token, data.user);
        router.push("/new-chat");
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    };

    const doRegister = async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = await apiFetch("/auth/register", { method: "POST", body: JSON.stringify(registerForm) });
        setSession(data.access_token, data.user);
        router.push("/new-chat");
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    };

    const requestResetCode = async () => {
      loading.value = true;
      error.value = "";
      forgotHint.value = "";
      try {
        const data = await apiFetch("/auth/password/forgot/request", {
          method: "POST",
          body: JSON.stringify({ email: forgotForm.email }),
        });
        forgotStep.value = 2;
        forgotHint.value = data.message || t("auth.hint_code_sent");
        if (data.debug) {
          forgotForm.code = data.debug.code || "";
          forgotHint.value = t("auth.hint_debug_code", { code: forgotForm.code });
        }
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    };

    const verifyResetCode = async () => {
      loading.value = true;
      error.value = "";
      forgotHint.value = "";
      try {
        const data = await apiFetch("/auth/password/forgot/verify", {
          method: "POST",
          body: JSON.stringify({
            email: forgotForm.email,
            code: forgotForm.code || "",
          }),
        });
        forgotForm.reset_token = data.reset_token;
        forgotStep.value = 3;
        forgotHint.value = t("auth.hint_verified");
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    };

    const resetPassword = async () => {
      loading.value = true;
      error.value = "";
      forgotHint.value = "";
      try {
        await apiFetch("/auth/password/forgot/reset", {
          method: "POST",
          body: JSON.stringify({
            reset_token: forgotForm.reset_token,
            new_password: forgotForm.new_password,
          }),
        });
        forgotHint.value = t("auth.hint_reset_success");
        tab.value = "login";
        forgotStep.value = 1;
        forgotForm.code = "";
        forgotForm.reset_token = "";
        forgotForm.new_password = "";
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    };

    onMounted(() => {
      if (route.query?.tab === "forgot") tab.value = "forgot";
      if (typeof route.query?.email === "string") forgotForm.email = route.query.email;
      if (tab.value === "forgot" && forgotForm.email) forgotStep.value = 2;
    });

    watch(locale, (next) => {
      const normalized = i18n.setLocale ? i18n.setLocale(next) : next;
      if (normalized && normalized !== next) {
        locale.value = normalized;
      }
    });

    return {
      tab,
      loginForm,
      registerForm,
      forgotForm,
      loading,
      error,
      forgotHint,
      forgotStep,
      locale,
      localeOptions,
      doLogin,
      doRegister,
      requestResetCode,
      verifyResetCode,
      resetPassword,
    };
  },
};
