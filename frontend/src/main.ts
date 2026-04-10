import { createApp } from "vue";
import App from "./App.vue";
import vuetify from "./plugins/vuetify";
import router from "./router";
import { registerUnauthorizedHandler } from "./services/api";
import { useAuthStore } from "./stores/auth";
import "./style.css";
import "@mdi/font/css/materialdesignicons.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/ibm-plex-mono/400.css";

const app = createApp(App);
let redirectingToLogin = false;

registerUnauthorizedHandler(() => {
  const auth = useAuthStore();
  auth.clearAuth();
  if (redirectingToLogin || router.currentRoute.value.path === "/login") {
    return;
  }
  redirectingToLogin = true;
  void router.push("/login").finally(() => {
    redirectingToLogin = false;
  });
});

app.use(vuetify).use(router).mount("#app");
