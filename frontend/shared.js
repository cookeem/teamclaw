const VueLib = window.Vue;
const VueRouterLib = window.VueRouter;
const VuetifyLib = window.Vuetify;

const renderBootstrapError = (message) => {
  const root = document.getElementById("app");
  if (!root) return;
  root.innerHTML = `
    <div style="max-width:720px;margin:10vh auto;padding:24px;border:1px solid #e0d8c8;background:#fffaf1;color:#3c2f2b;font-family:Arial,sans-serif;">
      <h2 style="margin:0 0 12px;font-size:20px;">TeamClaw failed to start</h2>
      <p style="margin:0 0 12px;">${message}</p>
      <p style="margin:0;">Open the browser console to see the exact error message.</p>
    </div>
  `;
};

window.addEventListener("error", (event) => {
  if (event?.error?.message) {
    renderBootstrapError(event.error.message);
  }
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event?.reason;
  const message = reason?.message || String(reason || "Unhandled promise rejection");
  renderBootstrapError(message);
});

if (!VueLib || !VueRouterLib || !VuetifyLib) {
  const missing = [
    !VueLib ? "Vue" : null,
    !VueRouterLib ? "VueRouter" : null,
    !VuetifyLib ? "Vuetify" : null,
  ]
    .filter(Boolean)
    .join(", ");
  renderBootstrapError(`Missing required library: ${missing}. Check CDN or network access.`);
  throw new Error(`Missing required library: ${missing}`);
}

const { createApp, ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } = VueLib;
window.TeamClawI18n =
  window.TeamClawI18n ||
  ({
    t: (key) => key,
    setLocale: () => {},
    getLocale: () => "en",
    messages: {},
  });
const { t } = window.TeamClawI18n;
const { createRouter, createWebHashHistory, useRoute, useRouter } = VueRouterLib;
const { createVuetify } = VuetifyLib;

const API_BASE = "http://localhost:8000/api/v1";
const API_ORIGIN = API_BASE.replace(/\/api\/v1$/, "");

const session = reactive({
  token: localStorage.getItem("teamclaw_token") || "",
  user: JSON.parse(localStorage.getItem("teamclaw_user") || "null"),
});

const dialogState = reactive({
  open: false,
  mode: "alert",
  title: "",
  message: "",
  input: "",
  inputLabel: "",
  inputPlaceholder: "",
  okText: t("common.ok"),
  cancelText: t("common.cancel"),
  resolve: null,
});

function openDialog(options = {}) {
  return new Promise((resolve) => {
    if (dialogState.open && dialogState.resolve) {
      dialogState.resolve(dialogState.mode === "prompt" ? null : false);
    }
    dialogState.mode = options.mode || "alert";
    dialogState.title = options.title || "";
    dialogState.message = options.message || "";
    dialogState.inputLabel = options.inputLabel || "";
    dialogState.inputPlaceholder = options.inputPlaceholder || "";
    dialogState.input = options.defaultValue ?? "";
    dialogState.okText = options.okText || t("common.ok");
    dialogState.cancelText = options.cancelText || t("common.cancel");
    dialogState.resolve = resolve;
    dialogState.open = true;
  });
}

function confirmDialog(message, options = {}) {
  return openDialog({
    mode: "confirm",
    title: options.title || t("common.confirm"),
    message,
    okText: options.okText || t("common.ok"),
    cancelText: options.cancelText || t("common.cancel"),
  });
}

function promptDialog(message, options = {}) {
  return openDialog({
    mode: "prompt",
    title: options.title || t("dialogs.prompt_title"),
    message,
    inputLabel: options.inputLabel || "",
    inputPlaceholder: options.inputPlaceholder || "",
    defaultValue: options.defaultValue ?? "",
    okText: options.okText || t("common.ok"),
    cancelText: options.cancelText || t("common.cancel"),
  });
}

function alertDialog(message, options = {}) {
  return openDialog({
    mode: "alert",
    title: options.title || t("dialogs.alert_title"),
    message,
    okText: options.okText || t("common.ok"),
  });
}

function resolveDialog(value) {
  const resolver = dialogState.resolve;
  dialogState.resolve = null;
  dialogState.open = false;
  if (resolver) resolver(value);
}

function dialogOk() {
  if (dialogState.mode === "prompt") {
    resolveDialog(dialogState.input);
  } else {
    resolveDialog(true);
  }
}

function dialogCancel() {
  if (dialogState.mode === "prompt") {
    resolveDialog(null);
  } else {
    resolveDialog(false);
  }
}

function setSession(token, user) {
  session.token = token;
  session.user = user;
  localStorage.setItem("teamclaw_token", token);
  localStorage.setItem("teamclaw_user", JSON.stringify(user));
}

function clearSession() {
  session.token = "";
  session.user = null;
  localStorage.removeItem("teamclaw_token");
  localStorage.removeItem("teamclaw_user");
}

async function apiFetch(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  }
  return data;
}

async function apiUpload(path, formData) {
  const headers = {};
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", body: formData, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  }
  return data;
}

function toAssetUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  if (path.startsWith("/")) return `${API_ORIGIN}${path}`;
  return `${API_ORIGIN}/${path}`;
}

function userInitials(displayName, username) {
  const base = (displayName || username || "U").trim();
  if (!base) return "U";
  return base.slice(0, 2).toUpperCase();
}

async function syncCurrentUser() {
  if (!session.token) return;
  try {
    const me = await apiFetch("/auth/me");
    session.user = me;
    localStorage.setItem("teamclaw_user", JSON.stringify(me));
  } catch {
    clearSession();
  }
}
