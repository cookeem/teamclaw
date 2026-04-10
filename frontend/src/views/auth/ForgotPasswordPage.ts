import { computed, defineComponent, inject, onMounted, ref, type Ref } from "vue";
import { useRoute } from "vue-router";

import { useI18n } from "../../i18n";
import { forgotPassword, resetPassword } from "../../services/api";

export default defineComponent({
  name: "ForgotPasswordPage",
  setup() {
    const { locale, setLocale, localeItems, t } = useI18n();
    const route = useRoute();
    const injectedAppVersion = inject<Ref<string>>("teamclawAppVersion", ref(""));
    const email = ref("");
    const code = ref("");
    const newPassword = ref("");
    const confirmNewPassword = ref("");

    const sendLoading = ref(false);
    const resetLoading = ref(false);
    const resetCode = ref("");
    const messageText = ref("");
    const messageType = ref<"success" | "error" | "info">("info");
    const appVersion = computed(() => (injectedAppVersion.value || "").trim());

    const passwordMismatch = computed(
      () => confirmNewPassword.value.length > 0 && confirmNewPassword.value !== newPassword.value,
    );
    const canReset = computed(
      () =>
        email.value.trim().length > 0 &&
        /^\d{6}$/.test(code.value.trim()) &&
        newPassword.value.length >= 8 &&
        !passwordMismatch.value,
    );

    onMounted(() => {
      const queryCode = route.query.code ?? route.query.token;
      if (typeof queryCode === "string" && queryCode.trim()) {
        code.value = queryCode.trim();
      }
      const queryEmail = route.query.email;
      if (typeof queryEmail === "string" && queryEmail.trim()) {
        email.value = queryEmail.trim();
      }
    });

    async function sendToken() {
      if (!email.value.trim() || sendLoading.value) {
        return;
      }
      sendLoading.value = true;
      messageText.value = "";
      try {
        const res = await forgotPassword({ email: email.value.trim() });
        resetCode.value = res.reset_code ?? res.reset_token ?? "";
        if (resetCode.value) {
          code.value = resetCode.value;
        }
        if (res.delivery === "failed") {
          messageType.value = "error";
          messageText.value = res.message || t("auth.emailSendFailed");
          if (res.error) {
            messageText.value = `${messageText.value} (${res.error})`;
          }
        } else if (res.delivery === "email") {
          messageType.value = "success";
          messageText.value = res.message || t("auth.emailSent");
        } else if (resetCode.value || res.delivery === "debug_token") {
          messageType.value = "info";
          messageText.value = res.message || t("auth.debugCodeReturned");
        } else {
          messageType.value = "info";
          messageText.value = res.message || t("auth.requestSubmitted");
        }
      } catch (error) {
        messageType.value = "error";
        messageText.value = error instanceof Error ? error.message : t("auth.sendFailed");
      } finally {
        sendLoading.value = false;
      }
    }

    async function reset() {
      if (!canReset.value || resetLoading.value) {
        return;
      }
      resetLoading.value = true;
      messageText.value = "";
      try {
        await resetPassword({
          email: email.value.trim(),
          code: code.value.trim(),
          new_password: newPassword.value,
        });
        messageType.value = "success";
        messageText.value = t("auth.passwordResetSuccess");
      } catch (error) {
        messageType.value = "error";
        messageText.value = error instanceof Error ? error.message : t("auth.resetFailed");
      } finally {
        resetLoading.value = false;
      }
    }

    return {
      email,
      code,
      newPassword,
      confirmNewPassword,
      locale,
      setLocale,
      localeItems,
      t,
      sendLoading,
      resetLoading,
      resetCode,
      messageText,
      messageType,
      passwordMismatch,
      canReset,
      sendToken,
      reset,
      appVersion,
    };
  },
});
