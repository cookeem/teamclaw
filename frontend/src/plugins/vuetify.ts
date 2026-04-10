import "vuetify/styles";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";
import { aliases, mdi } from "vuetify/iconsets/mdi";

export default createVuetify({
  components,
  directives,
  icons: {
    defaultSet: "mdi",
    aliases,
    sets: { mdi },
  },
  theme: {
    defaultTheme: "teamclawLight",
    themes: {
      teamclawLight: {
        dark: false,
        colors: {
          background: "#f2efe8",
          surface: "#fffdf8",
          primary: "#0f766e",
          secondary: "#f97316",
          accent: "#1f2937",
          success: "#15803d",
          warning: "#d97706",
          error: "#b91c1c",
          info: "#2563eb",
        },
      },
    },
  },
  defaults: {
    VCard: {
      rounded: "sm",
      elevation: 1,
    },
    VBtn: {
      rounded: "sm",
      elevation: 0,
    },
  },
});
