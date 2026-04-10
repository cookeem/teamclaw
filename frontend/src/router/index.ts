import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";

import LoginPage from "../views/auth/LoginPage.vue";
import SignupPage from "../views/auth/SignupPage.vue";
import ForgotPasswordPage from "../views/auth/ForgotPasswordPage.vue";
import CreateChatPage from "../views/chat/CreateChatPage.vue";
import ChatPage from "../views/chat/ChatPage.vue";
import UsersPage from "../views/admin/UsersPage.vue";
import AuditLogsPage from "../views/admin/AuditLogsPage.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/app/chats/new" },
    { path: "/login", component: LoginPage, meta: { public: true } },
    { path: "/signup", component: SignupPage, meta: { public: true } },
    { path: "/forgot-password", component: ForgotPasswordPage, meta: { public: true } },
    { path: "/app/chats/new", component: CreateChatPage, meta: { requiresAuth: true } },
    { path: "/app/chats/:id", component: ChatPage, meta: { requiresAuth: true } },
    { path: "/admin/users", component: UsersPage, meta: { requiresAuth: true, requiresAdmin: true } },
    { path: "/admin/audit-logs", component: AuditLogsPage, meta: { requiresAuth: true, requiresAdmin: true } },
  ],
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (to.meta.requiresAuth && !auth.isLoggedIn.value) {
    return "/login";
  }
  if (to.meta.requiresAdmin && !auth.isAdmin.value) {
    return "/app/chats/new";
  }
  if (to.meta.public && auth.isLoggedIn.value && (to.path === "/login" || to.path === "/signup")) {
    return "/app/chats/new";
  }
  return true;
});

export default router;
