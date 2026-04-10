import { computed, defineComponent, inject, ref, type Ref } from "vue";
import { useRouter } from "vue-router";

import { useI18n } from "../../i18n";
import { signup } from "../../services/api";
import { useAuthStore } from "../../stores/auth";

const EMAIL_MIN_LENGTH = 8;
const EMAIL_MAX_LENGTH = 128;
const USERNAME_MIN_LENGTH = 4;
const USERNAME_MAX_LENGTH = 64;
const DISPLAY_NAME_MAX_LENGTH = 128;
const PASSWORD_MIN_LENGTH = 8;
const PASSWORD_MAX_LENGTH = 256;
const PASSWORD_COMPLEXITY_REGEX = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9\s]).+$/;

export default defineComponent({
  name: "SignupPage",
  setup() {
    const router = useRouter();
    const auth = useAuthStore();
    const { locale, setLocale, localeItems, t } = useI18n();
    const injectedAppVersion = inject<Ref<string>>("teamclawAppVersion", ref(""));

    const email = ref("");
    const username = ref("");
    const displayName = ref("");
    const password = ref("");
    const confirmPassword = ref("");
    const loading = ref(false);
    const errorText = ref("");
    const appVersion = computed(() => (injectedAppVersion.value || "").trim());
    const passwordMismatch = computed(
      () => confirmPassword.value.length > 0 && password.value !== confirmPassword.value,
    );
    const hasAnyInput = computed(
      () =>
        email.value.length > 0 ||
        username.value.length > 0 ||
        displayName.value.length > 0 ||
        password.value.length > 0 ||
        confirmPassword.value.length > 0,
    );

    const usernameErrors = computed(() => {
      const value = username.value.trim();
      if (!hasAnyInput.value && value.length === 0) {
        return [];
      }
      if (value.length === 0) {
        return [t("auth.usernameRequired")];
      }
      if (value.length < USERNAME_MIN_LENGTH || value.length > USERNAME_MAX_LENGTH) {
        return [t("auth.usernameLength", { min: USERNAME_MIN_LENGTH, max: USERNAME_MAX_LENGTH })];
      }
      return [];
    });

    const displayNameErrors = computed(() => {
      const value = displayName.value.trim();
      if (value.length > DISPLAY_NAME_MAX_LENGTH) {
        return [t("auth.displayNameLength", { max: DISPLAY_NAME_MAX_LENGTH })];
      }
      return [];
    });

    const emailErrors = computed(() => {
      const value = email.value.trim();
      if (!hasAnyInput.value && value.length === 0) {
        return [];
      }
      if (value.length === 0) {
        return [t("auth.emailRequired")];
      }
      if (value.length < EMAIL_MIN_LENGTH || value.length > EMAIL_MAX_LENGTH) {
        return [t("auth.emailLength", { min: EMAIL_MIN_LENGTH, max: EMAIL_MAX_LENGTH })];
      }
      return [];
    });

    const passwordErrors = computed(() => {
      const value = password.value;
      if (!hasAnyInput.value && value.length === 0) {
        return [];
      }
      if (value.length === 0) {
        return [t("auth.passwordRequired")];
      }
      if (value.length < PASSWORD_MIN_LENGTH || value.length > PASSWORD_MAX_LENGTH) {
        return [t("auth.passwordLength", { min: PASSWORD_MIN_LENGTH, max: PASSWORD_MAX_LENGTH })];
      }
      if (!PASSWORD_COMPLEXITY_REGEX.test(value)) {
        return [t("auth.passwordComplexity")];
      }
      return [];
    });

    const confirmPasswordErrors = computed(() => {
      if (!hasAnyInput.value && confirmPassword.value.length === 0) {
        return [];
      }
      if (confirmPassword.value.length === 0) {
        return [t("auth.confirmPasswordRequired")];
      }
      if (passwordMismatch.value) {
        return [t("auth.passwordMismatch")];
      }
      return [];
    });

    const validationMessages = computed(() => {
      const merged = [
        ...usernameErrors.value,
        ...displayNameErrors.value,
        ...emailErrors.value,
        ...passwordErrors.value,
        ...confirmPasswordErrors.value,
      ];
      return Array.from(new Set(merged));
    });

    const canSubmit = computed(() => {
      return (
        username.value.trim().length > 0 &&
        email.value.trim().length > 0 &&
        password.value.length > 0 &&
        confirmPassword.value.length > 0 &&
        usernameErrors.value.length === 0 &&
        displayNameErrors.value.length === 0 &&
        emailErrors.value.length === 0 &&
        passwordErrors.value.length === 0 &&
        confirmPasswordErrors.value.length === 0
      );
    });

    async function submit() {
      if (!canSubmit.value || loading.value) {
        return;
      }

      loading.value = true;
      errorText.value = "";

      try {
        const response = await signup({
          email: email.value.trim(),
          username: username.value.trim(),
          password: password.value,
          display_name: displayName.value.trim() || undefined,
        });
        auth.setAuth({
          accessToken: response.access_token,
          refreshToken: response.refresh_token,
          user: response.user,
        });
        await router.push("/app/chats/new");
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("auth.signupFailed");
      } finally {
        loading.value = false;
      }
    }

    return {
      email,
      username,
      displayName,
      password,
      confirmPassword,
      locale,
      setLocale,
      localeItems,
      t,
      loading,
      errorText,
      usernameErrors,
      displayNameErrors,
      emailErrors,
      passwordErrors,
      confirmPasswordErrors,
      validationMessages,
      canSubmit,
      submit,
      appVersion,
    };
  },
});
