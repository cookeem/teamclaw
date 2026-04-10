import { computed, reactive } from "vue";
import type { UserPublic } from "../types/models";

interface AuthState {
  accessToken: string;
  refreshToken: string;
  user: UserPublic | null;
}

const STORAGE_KEY = "teamclaw_auth";

const state = reactive<AuthState>({
  accessToken: "",
  refreshToken: "",
  user: null,
});

function loadFromStorage() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<AuthState>;
    state.accessToken = typeof parsed.accessToken === "string" ? parsed.accessToken : "";
    state.refreshToken = typeof parsed.refreshToken === "string" ? parsed.refreshToken : "";
    state.user = parsed.user ?? null;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
}

function persist() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      accessToken: state.accessToken,
      refreshToken: state.refreshToken,
      user: state.user,
    }),
  );
}

let loaded = false;

export function useAuthStore() {
  if (!loaded) {
    loadFromStorage();
    loaded = true;
  }

  const isLoggedIn = computed(() => Boolean(state.accessToken) && state.user !== null);
  const isAdmin = computed(() => state.user?.is_admin === true);

  function setAuth(payload: { accessToken: string; refreshToken: string; user: UserPublic }) {
    state.accessToken = payload.accessToken;
    state.refreshToken = payload.refreshToken;
    state.user = payload.user;
    persist();
  }

  function updateUser(user: UserPublic) {
    state.user = user;
    persist();
  }

  function clearAuth() {
    state.accessToken = "";
    state.refreshToken = "";
    state.user = null;
    localStorage.removeItem(STORAGE_KEY);
  }

  return {
    state,
    isLoggedIn,
    isAdmin,
    setAuth,
    updateUser,
    clearAuth,
  };
}
