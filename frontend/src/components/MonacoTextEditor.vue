<template>
  <div ref="containerRef" class="monaco-text-editor"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    language?: string;
    readOnly?: boolean;
    theme?: string;
  }>(),
  {
    language: "plaintext",
    readOnly: false,
    theme: "vs-dark",
  },
);

const emit = defineEmits<{
  (event: "update:modelValue", value: string): void;
}>();

const containerRef = ref<HTMLElement | null>(null);
let editorInstance: monaco.editor.IStandaloneCodeEditor | null = null;
let isApplyingExternalChange = false;

const monacoGlobal = globalThis as typeof globalThis & {
  MonacoEnvironment?: {
    getWorker: (_: string, label: string) => Worker;
  };
  __TEAMCLAW_MONACO_ENV_READY__?: boolean;
};

if (!monacoGlobal.__TEAMCLAW_MONACO_ENV_READY__) {
  monacoGlobal.MonacoEnvironment = {
    getWorker(_: string, label: string): Worker {
      if (label === "json") {
        return new jsonWorker();
      }
      if (label === "css" || label === "scss" || label === "less") {
        return new cssWorker();
      }
      if (label === "html" || label === "handlebars" || label === "razor") {
        return new htmlWorker();
      }
      if (label === "typescript" || label === "javascript") {
        return new tsWorker();
      }
      return new editorWorker();
    },
  };
  monacoGlobal.__TEAMCLAW_MONACO_ENV_READY__ = true;
}

onMounted(() => {
  if (!containerRef.value) {
    return;
  }
  editorInstance = monaco.editor.create(containerRef.value, {
    value: props.modelValue,
    language: props.language,
    readOnly: props.readOnly,
    theme: props.theme,
    automaticLayout: true,
    minimap: { enabled: false },
    fontFamily: "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    fontSize: 13,
    lineHeight: 22,
    tabSize: 2,
    wordWrap: "on",
    scrollBeyondLastLine: false,
    renderWhitespace: "selection",
  });

  editorInstance.onDidChangeModelContent(() => {
    if (!editorInstance || isApplyingExternalChange) {
      return;
    }
    emit("update:modelValue", editorInstance.getValue());
  });
});

onBeforeUnmount(() => {
  if (editorInstance) {
    editorInstance.dispose();
    editorInstance = null;
  }
});

watch(
  () => props.modelValue,
  (next) => {
    if (!editorInstance) {
      return;
    }
    const current = editorInstance.getValue();
    if (next === current) {
      return;
    }
    isApplyingExternalChange = true;
    editorInstance.setValue(next);
    isApplyingExternalChange = false;
  },
);

watch(
  () => props.language,
  (next) => {
    if (!editorInstance) {
      return;
    }
    const model = editorInstance.getModel();
    if (!model) {
      return;
    }
    monaco.editor.setModelLanguage(model, next || "plaintext");
  },
);

watch(
  () => props.readOnly,
  (next) => {
    if (!editorInstance) {
      return;
    }
    editorInstance.updateOptions({ readOnly: next });
  },
);

watch(
  () => props.theme,
  (next) => {
    monaco.editor.setTheme(next || "vs-dark");
  },
);
</script>

<style scoped>
.monaco-text-editor {
  width: 100%;
  min-height: min(64vh, 720px);
  height: min(64vh, 720px);
  border: 1px solid rgba(17, 24, 39, 0.14);
  border-radius: 8px;
  overflow: hidden;
}
</style>
