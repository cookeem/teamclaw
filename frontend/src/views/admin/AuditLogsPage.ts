import { computed, defineComponent, onMounted, ref } from "vue";

import { useI18n } from "../../i18n";
import { listAuditLogs } from "../../services/api";
import { useAuthStore } from "../../stores/auth";
import type { AuditLog } from "../../types/models";

export default defineComponent({
  name: "AuditLogsPage",
  setup() {
    const auth = useAuthStore();
    const { t, dateLocale } = useI18n();

    const logs = ref<AuditLog[]>([]);
    const loading = ref(false);
    const errorText = ref("");
    const action = ref("");
    const result = ref<string | null>(null);

    const resultItems = computed(() => [
      { title: t("admin.audit.all"), value: null },
      { title: "success", value: "success" },
      { title: "failed", value: "failed" },
    ]);

    const headers = computed(() => [
      { title: t("admin.audit.table.time"), key: "created_at" },
      { title: t("admin.audit.table.actor"), key: "actor_user_id" },
      { title: t("admin.audit.table.action"), key: "action" },
      { title: t("admin.audit.table.targetType"), key: "target_type" },
      { title: t("admin.audit.table.targetId"), key: "target_id" },
      { title: t("admin.audit.table.result"), key: "result" },
      { title: t("admin.audit.table.detail"), key: "detail_json" },
    ]);

    function fmt(value: string): string {
      return new Date(value).toLocaleString(dateLocale.value, { hour12: false });
    }

    function preview(payload: Record<string, unknown> | null): string {
      if (!payload) {
        return "-";
      }
      const text = JSON.stringify(payload, undefined, 0);
      return text.length > 90 ? `${text.slice(0, 90)}...` : text;
    }

    async function load() {
      if (!auth.state.accessToken) {
        return;
      }
      loading.value = true;
      errorText.value = "";
      try {
        const res = await listAuditLogs(auth.state.accessToken, {
          action: action.value.trim() || undefined,
          result: result.value || undefined,
          page_size: 100,
        });
        logs.value = res.items;
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("admin.audit.loadFailed");
      } finally {
        loading.value = false;
      }
    }

    onMounted(() => {
      void load();
    });

    return {
      logs,
      loading,
      errorText,
      action,
      result,
      resultItems,
      headers,
      t,
      fmt,
      preview,
      load,
    };
  },
});
