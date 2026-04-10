import { computed, defineComponent, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { useI18n } from "../../i18n";
import { createConversation, getModels } from "../../services/api";
import { useAuthStore } from "../../stores/auth";
import type { ModelsResponse, ProviderItem } from "../../types/models";

export default defineComponent({
  name: "CreateChatPage",
  setup() {
    const router = useRouter();
    const auth = useAuthStore();
    const { t } = useI18n();

    const title = ref("");
    const creating = ref(false);
    const loading = ref(false);
    const errorText = ref("");
    const providers = ref<ProviderItem[]>([]);
    const selectedModelKey = ref("");
    const defaultProvider = ref("");
    const defaultModel = ref("");

    const modelItems = computed(() => {
      const items: Array<{ title: string; value: string }> = [];
      for (const provider of providers.value) {
        for (const model of provider.models) {
          items.push({
            title: `${provider.name} / ${model}`,
            value: `${provider.name}::${model}`,
          });
        }
      }
      return items;
    });

    async function load() {
      loading.value = true;
      errorText.value = "";
      try {
        const modelsPayload = (await getModels()) as ModelsResponse;
        providers.value = modelsPayload.providers;
        defaultProvider.value = modelsPayload.default_provider;
        defaultModel.value = modelsPayload.default_model;
        const preferred = `${modelsPayload.default_provider}::${modelsPayload.default_model}`;
        selectedModelKey.value = modelItems.value.some((item) => item.value === preferred)
          ? preferred
          : (modelItems.value[0]?.value ?? "");
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("chat.loadConversationFailed");
      } finally {
        loading.value = false;
      }
    }

    async function createAndEnter() {
      if (!auth.state.accessToken || creating.value) {
        return;
      }
      if (!selectedModelKey.value) {
        errorText.value = t("createChat.modelRequired");
        return;
      }
      creating.value = true;
      errorText.value = "";
      try {
        const [provider, model] = selectedModelKey.value.split("::", 2);
        const conversation = await createConversation(auth.state.accessToken, {
          title: title.value.trim() || undefined,
          default_provider: provider || defaultProvider.value || undefined,
          default_model: model || defaultModel.value || undefined,
        });
        await router.push(`/app/chats/${conversation.id}`);
      } catch (error) {
        errorText.value = error instanceof Error ? error.message : t("createChat.createFailed");
      } finally {
        creating.value = false;
      }
    }

    onMounted(() => {
      void load();
    });

    return {
      title,
      creating,
      loading,
      errorText,
      modelItems,
      selectedModelKey,
      t,
      createAndEnter,
    };
  },
});
