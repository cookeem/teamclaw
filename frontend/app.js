const routes = [
  { path: "/", redirect: "/login" },
  { path: "/login", component: LoginPage, meta: { authless: true } },
  { path: "/new-chat", component: NewChatPage },
  { path: "/chat/:id", component: ChatPage },
  { path: "/skills", component: SkillsPage },
  { path: "/skills/:id", component: SkillEditorPage },
  { path: "/admin/skills", component: AdminSkillsPage, meta: { requiresAdmin: true } },
  { path: "/admin/skill-groups", component: SkillGroupsPage, meta: { requiresAdmin: true } },
  { path: "/profile", component: ProfilePage },
  { path: "/admin", component: AdminPage, meta: { requiresAdmin: true } },
];

const router = createRouter({ history: createWebHashHistory(), routes });

router.beforeEach(async (to) => {
  if (to.meta.authless) return true;
  if (!session.token) return "/login";
  if (!session.user || to.meta.requiresAdmin) {
    await syncCurrentUser();
  }
  if (to.meta.requiresAdmin && !session.user?.is_admin) return "/new-chat";
  return true;
});

const App = {
  components: {
    AppDialog,
  },
  template: `
    <v-app>
      <template v-if="isAuthless"><router-view /></template>
      <template v-else>
          <v-layout class="main-layout">
            <v-navigation-drawer width="280" permanent class="left-drawer">
            <div class="brand">
              <img class="brand-logo" src="/images/teamclaw_logo.png" alt="TeamClaw logo" />
              <span>TeamClaw</span>
            </div>
            <v-list nav>
              <v-list-item :title="$t('nav.new_chat')" prepend-icon="mdi-plus-circle-outline" to="/new-chat" rounded="lg"></v-list-item>
              <v-list-item title="Skills" prepend-icon="mdi-hammer-wrench" to="/skills" rounded="lg"></v-list-item>
              <v-list-group
                v-if="session.user?.is_admin"
                value="admin"
                prepend-icon="mdi-shield-account"
                class="admin-group"
              >
                <template #activator="{ props }">
                  <v-list-item v-bind="props" :title="$t('nav.admin_console')" rounded="lg"></v-list-item>
                </template>
                <v-list-item :title="$t('nav.user_management')" prepend-icon="mdi-account-cog" to="/admin" rounded="lg"></v-list-item>
                <v-list-item :title="$t('nav.skill_management')" prepend-icon="mdi-folder-wrench" to="/admin/skills" rounded="lg"></v-list-item>
                <v-list-item :title="$t('nav.skill_groups')" prepend-icon="mdi-folder-multiple-outline" to="/admin/skill-groups" rounded="lg"></v-list-item>
              </v-list-group>
              <v-list-item
                v-for="conv in conversations"
                :key="conv.id"
                :title="conv.title"
                :prepend-icon="conv.is_pinned ? 'mdi-pin' : 'mdi-message-outline'"
                :active="route.params.id === conv.id"
                link
                @click="openConversation(conv)"
                rounded="lg"
              >
                <template #append>
                  <v-menu location="bottom end">
                    <template #activator="{ props }">
                      <v-btn
                        icon="mdi-dots-vertical"
                        variant="text"
                        size="small"
                        v-bind="props"
                        @mousedown.stop
                        @click.stop
                      ></v-btn>
                    </template>
                    <v-list density="compact">
                      <v-list-item :title="$t('app.rename')" prepend-icon="mdi-pencil" @click="renameConversation(conv)"></v-list-item>
                      <v-list-item
                        :title="conv.is_pinned ? $t('app.unpin') : $t('app.pin')"
                        :prepend-icon="conv.is_pinned ? 'mdi-pin-off' : 'mdi-pin'"
                        @click="togglePinConversation(conv)"
                      ></v-list-item>
                      <v-list-item :title="$t('app.delete_conversation')" prepend-icon="mdi-delete-outline" @click="deleteConversation(conv)"></v-list-item>
                    </v-list>
                  </v-menu>
                </template>
              </v-list-item>
            </v-list>
          </v-navigation-drawer>

          <v-main class="main-scroll">
            <v-app-bar flat class="top-bar">
              <v-app-bar-title>
                <div class="top-brand">
                  <img class="top-brand-logo" src="/images/teamclaw_logo.png" alt="TeamClaw logo" />
                  <span>TeamClaw</span>
                </div>
              </v-app-bar-title>
              <v-spacer></v-spacer>
              <v-select
                v-model="locale"
                :items="localeOptions"
                density="compact"
                variant="outlined"
                hide-details
                class="lang-select"
              ></v-select>
              <v-btn variant="text" to="/profile" class="profile-btn">
                <v-avatar size="30" class="mr-2">
                  <v-img v-if="session.user?.avatar_url" :src="toAssetUrl(session.user.avatar_url)" cover></v-img>
                  <span v-else>{{ userInitials(session.user?.display_name, session.user?.username) }}</span>
                </v-avatar>
                <span>{{ session.user?.display_name || 'Profile' }}</span>
              </v-btn>
              <v-btn variant="text" @click="logout">{{ $t('nav.logout') }}</v-btn>
            </v-app-bar>
            <router-view :key="locale" />
            <app-dialog />
          </v-main>
        </v-layout>
      </template>
    </v-app>
  `,
  setup() {
    const i18n = window.TeamClawI18n || { t: (key) => key, setLocale: () => {}, getLocale: () => "en" };
    const { t } = i18n;
    const route = useRoute();
    const router = useRouter();
    const conversations = ref([]);
    const isAuthless = computed(() => route.meta.authless);
    const locale = ref(i18n.getLocale ? i18n.getLocale() : "en");
    const localeOptions = computed(() => [
      { title: t("common.lang_en"), value: "en" },
      { title: t("common.lang_zh"), value: "zh" },
    ]);

    const loadConversations = async () => {
      if (!session.token) {
        conversations.value = [];
        return;
      }
      try {
        const data = await apiFetch("/conversations");
        conversations.value = data.items || [];
      } catch {
        conversations.value = [];
      }
    };

    const renameConversation = async (conv) => {
      const nextTitle = await promptDialog(t("dialogs.rename_prompt"), {
        title: t("dialogs.rename_title"),
        defaultValue: conv.title || t("app.new_conversation"),
      });
      if (nextTitle === null) return;
      const normalized = String(nextTitle).trim();
      if (!normalized) return;
      try {
        await apiFetch(`/conversations/${conv.id}`, {
          method: "PATCH",
          body: JSON.stringify({ title: normalized }),
        });
        await loadConversations();
      } catch (e) {
        await alertDialog(e.message || t("app.rename_failed"));
      }
    };

    const togglePinConversation = async (conv) => {
      try {
        await apiFetch(`/conversations/${conv.id}`, {
          method: "PATCH",
          body: JSON.stringify({ is_pinned: !conv.is_pinned }),
        });
        await loadConversations();
      } catch (e) {
        await alertDialog(e.message || t("app.pin_failed"));
      }
    };

    const deleteConversation = async (conv) => {
      const ok = await confirmDialog(
        t("dialogs.delete_conversation_confirm", { title: conv.title || t("app.new_conversation") })
      );
      if (!ok) return;
      try {
        await apiFetch(`/conversations/${conv.id}`, { method: "DELETE" });
        if (route.path === `/chat/${conv.id}`) {
          router.push("/new-chat");
        }
        await loadConversations();
      } catch (e) {
        await alertDialog(e.message || t("app.delete_failed"));
      }
    };

    const bootstrap = async () => {
      if (session.token) {
        await syncCurrentUser();
      }
      await loadConversations();
    };

    const logout = () => {
      clearSession();
      router.push("/login");
    };

    const openConversation = (conv) => {
      router.push(`/chat/${conv.id}`);
      apiFetch(`/conversations/${conv.id}/refresh_skills`, { method: "POST" }).catch(() => {});
    };

    watch(locale, (next) => {
      const normalized = i18n.setLocale ? i18n.setLocale(next) : next;
      if (normalized && normalized !== next) {
        locale.value = normalized;
      }
    });

    onMounted(bootstrap);
    watch(() => route.fullPath, loadConversations);

    return {
      session,
      conversations,
      route,
      isAuthless,
      locale,
      localeOptions,
      logout,
      renameConversation,
      togglePinConversation,
      deleteConversation,
      openConversation,
      toAssetUrl,
      userInitials,
    };
  },
};

const vuetify = createVuetify({
  theme: {
    defaultTheme: "teamclaw",
    themes: {
      teamclaw: {
        dark: false,
        colors: {
          background: "#f5f2eb",
          surface: "#fffdf8",
          primary: "#1f4d45",
          secondary: "#d1503f",
          success: "#2a7f62",
          error: "#b23a2f",
        },
      },
    },
  },
});

const app = createApp(App);
const i18n = window.TeamClawI18n || { t: (key) => key };
app.config.globalProperties.$t = i18n.t;
app.use(router).use(vuetify).mount("#app");
