<template src="./ChatPage.template.html"></template>
<script lang="ts" src="./ChatPage.ts"></script>

<style scoped>
.layout-grid {
  max-width: none;
  width: 100%;
  margin: 0;
  height: 100%;
  min-height: 0;
  padding: 14px 0 0;
}

.chat-page-grid {
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
}

.chat-main-col {
  min-height: 0;
}

.chat-settings-col {
  min-height: 0;
}

.sticky-settings {
  position: relative;
  top: 0;
  align-self: stretch;
}

.panel-shell {
  width: 100%;
  height: 100%;
  min-height: 0;
  background: #fff8ee;
  border-left: 1px solid #e6dece;
  border-top: 1px solid #e6dece;
  border-bottom: 1px solid #e6dece;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panel-subtitle {
  color: rgba(17, 24, 39, 0.6);
  margin-bottom: 12px;
}

.panel-content {
  background: transparent;
  border: 0;
  padding: 14px 16px 16px;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
}

.status-chip {
  width: fit-content;
}

.meta-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-row span {
  font-size: 0.8rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(17, 24, 39, 0.64);
}

.meta-row code {
  font-family: var(--mono);
  background: rgba(255, 255, 255, 0.72);
  border-radius: 4px;
  padding: 8px 10px;
}

.chat-main-shell {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  background: #ffffff;
  border: 1px solid #e6dece;
}

.chat-viewport {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 14px 12px;
  background: #ffffff;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.message-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
}

.message-row-targeted .message-bubble {
  border-color: rgba(239, 108, 0, 0.72);
  box-shadow: 0 0 0 2px rgba(239, 108, 0, 0.14);
  transition: box-shadow 0.18s ease, border-color 0.18s ease;
}

.role-user {
  justify-content: flex-end;
}

.role-assistant,
.role-system {
  justify-content: flex-start;
}

.message-avatar {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  border: 1px solid #d7ccbd;
  background: #fffdf8;
  color: #615a4d;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  flex-shrink: 0;
  overflow: hidden;
}

.avatar-assistant {
  background: #fff8ee;
}

.avatar-user {
  background: #e5f5f1;
  color: #1f4d45;
}

.avatar-system {
  background: #eef2ff;
  color: #3f4d78;
}

.message-avatar img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  background: #f7f3eb;
}

.message-bubble {
  max-width: min(86ch, 100%);
  border: 1px solid rgba(17, 24, 39, 0.08);
  padding: 12px 14px;
}

.message-bubble.loading {
  animation: pulse 1.4s ease-in-out infinite;
}

.bubble-text {
  line-height: 1.55;
  word-break: break-word;
}

.message-attachments {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message-attachment-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.message-attachment-image {
  display: block;
  width: min(100%, 460px);
  border-radius: 10px;
  border: 1px solid rgba(17, 24, 39, 0.12);
  background: #f8fafc;
}

.message-attachment-link {
  color: #0f766e;
  text-decoration: none;
  font-size: 0.88rem;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.message-attachment-link:hover {
  text-decoration: underline;
}

.markdown-content :deep(p) {
  margin: 0 0 0.7em;
}

.markdown-content :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  margin: 0.2em 0 0.5em;
  line-height: 1.3;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin: 0.35em 0 0.8em;
  padding-left: 1.2em;
}

.markdown-content :deep(li) {
  margin: 0.2em 0;
}

.markdown-content :deep(blockquote) {
  margin: 0.6em 0;
  padding: 0.2em 0.8em;
  border-left: 3px solid rgba(15, 118, 110, 0.5);
  background: rgba(15, 118, 110, 0.06);
}

.markdown-content :deep(code) {
  font-family: var(--mono);
  font-size: 0.9em;
  background: rgba(17, 24, 39, 0.08);
  border-radius: 6px;
  padding: 0.12em 0.35em;
}

.markdown-content :deep(pre) {
  margin: 0.7em 0;
  overflow-x: auto;
  background: #111827;
  color: #f8fafc;
  border-radius: 10px;
  padding: 0.75em 0.85em;
}

.markdown-content :deep(pre code) {
  display: block;
  background: transparent;
  color: inherit;
  border-radius: 0;
  padding: 0;
  font-size: 0.88em;
  line-height: 1.5;
}

.tool-trace {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tool-item {
  border: 1px solid rgba(17, 24, 39, 0.1);
  padding: 8px 10px;
}

.tool-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #f59e0b;
  flex-shrink: 0;
}

.tool-call {
  font-family: var(--mono);
  font-size: 0.86rem;
  overflow-wrap: anywhere;
}

.tool-item-body {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-time {
  margin-top: 3px;
  margin-left: 16px;
  font-size: 0.72rem;
  line-height: 1.2;
  color: rgba(17, 24, 39, 0.56);
  font-family: var(--mono);
}

.tool-output {
  margin: 0;
  padding: 8px 9px;
  border-radius: 8px;
  background: #111827;
  color: #f8fafc;
  font-family: var(--mono);
  font-size: 0.82rem;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
}

.message-meta {
  margin-top: 8px;
  font-size: 0.75rem;
  color: rgba(17, 24, 39, 0.55);
}

.composer {
  position: relative;
  flex-shrink: 0;
  margin-top: auto;
  border-top: 1px solid #e6dece;
  padding: 10px 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #ffffff;
}

.composer-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
}

.composer-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.attachment-chip {
  max-width: 100%;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.composer-row :deep(.v-field) {
  border: 1px solid #d7ccbd;
  box-shadow: none;
  background: #ffffff;
}

.composer-row :deep(.v-field--variant-solo-filled) {
  box-shadow: none;
}

.composer-row :deep(.v-btn) {
  min-height: 40px;
  letter-spacing: 0.08em;
  font-weight: 700;
}

.settings-tabs {
  border-bottom: 1px solid #e6dece;
  padding: 0 6px;
  background: #fff8ee;
  position: sticky;
  top: 0;
  z-index: 2;
}

.settings-tabs :deep(.v-tab) {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
  font-size: 0.84rem;
}

.settings-window {
  flex: 1;
  min-height: 0;
  background: #fff8ee;
  overflow: hidden;
}

.settings-window :deep(.v-window__container),
.settings-window :deep(.v-window-item),
.settings-window :deep(.v-window-item--active) {
  height: 100%;
  min-height: 0;
}

.settings-window :deep(.v-window-item),
.settings-window :deep(.v-window-item--active) {
  overflow: hidden;
}

.chat-settings-panel :deep(.v-input) {
  flex: 0 0 auto;
}

.files-panel {
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.files-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.files-target-tip {
  font-size: 0.8rem;
  color: rgba(17, 24, 39, 0.65);
}

.files-target-tip code {
  font-family: var(--mono);
  background: rgba(255, 255, 255, 0.72);
  border-radius: 4px;
  padding: 3px 6px;
}

.files-sort-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.files-sort-row :deep(.v-input) {
  flex: 1;
  min-width: 140px;
}

.files-scroll-zone {
  flex: 1;
  min-height: 0;
  overflow-y: scroll;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-right: 4px;
  scrollbar-gutter: stable;
}

.files-tree-shell {
  min-height: 260px;
  max-height: min(50vh, 560px);
  overflow: auto;
  border: 1px solid rgba(17, 24, 39, 0.12);
  border-radius: 8px;
  background: #fffdfa;
}

.files-empty {
  padding: 14px 12px;
  color: rgba(17, 24, 39, 0.58);
  font-size: 0.86rem;
}

.file-row {
  border-bottom: 1px solid rgba(17, 24, 39, 0.06);
  cursor: pointer;
}

.file-row:last-child {
  border-bottom: 0;
}

.file-row.selected {
  background: rgba(15, 118, 110, 0.08);
}

.file-row-main {
  min-height: 34px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding-top: 6px;
  padding-bottom: 6px;
  padding-right: 8px;
}

.file-row-main :deep(.v-btn) {
  min-width: 24px;
  width: 24px;
  height: 24px;
  padding: 0;
}

.file-row-toggle-placeholder {
  width: 24px;
  flex-shrink: 0;
}

.file-row-icon {
  color: rgba(17, 24, 39, 0.78);
  flex-shrink: 0;
}

.file-row-name {
  flex: 1;
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  line-height: 1.28;
  font-size: 0.8rem;
}

.file-row-meta {
  flex-shrink: 0;
  align-self: center;
  font-size: 0.72rem;
  color: rgba(17, 24, 39, 0.56);
  font-family: var(--mono);
  padding-top: 0;
}

.files-selection {
  border: 1px solid rgba(17, 24, 39, 0.12);
  border-radius: 8px;
  background: #fffdf8;
  padding: 10px;
}

.scheduled-run-item {
  border: 1px solid rgba(17, 24, 39, 0.12);
  border-radius: 8px;
  padding: 8px 10px;
  background: #ffffff;
  margin-bottom: 8px;
  cursor: pointer;
}

.scheduled-run-item:hover {
  border-color: rgba(239, 108, 0, 0.42);
  background: #fffdf9;
}

.scheduled-run-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.82rem;
}

.scheduled-run-meta {
  margin-top: 4px;
  font-family: var(--mono);
  font-size: 0.76rem;
  color: rgba(17, 24, 39, 0.62);
}

.scheduled-run-actions {
  margin-top: 4px;
  display: flex;
  justify-content: flex-end;
}

.files-selection .meta-row code {
  font-size: 0.78rem;
  line-height: 1.34;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.files-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.files-view-text {
  margin: 0;
  max-height: min(62vh, 680px);
  overflow: auto;
  border-radius: 10px;
  border: 1px solid rgba(17, 24, 39, 0.12);
  background: #111827;
  color: #f8fafc;
  padding: 12px;
  font-family: var(--mono);
  font-size: 0.84rem;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.files-view-image-wrap {
  display: flex;
  justify-content: center;
  max-height: min(62vh, 680px);
  overflow: auto;
  border-radius: 10px;
  border: 1px solid rgba(17, 24, 39, 0.12);
  background: #f8fafc;
  padding: 8px;
}

.files-view-image {
  max-width: 100%;
  max-height: calc(min(62vh, 680px) - 16px);
  object-fit: contain;
}

.tips-snackbar :deep(.v-snackbar__wrapper) {
  opacity: 1 !important;
  backdrop-filter: none;
}

.tips-snackbar :deep(.v-snackbar__content) {
  font-weight: 600;
}

.msg-fade-enter-active,
.msg-fade-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}

.msg-fade-enter-from,
.msg-fade-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

@keyframes pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(15, 118, 110, 0.05);
  }
  50% {
    box-shadow: 0 0 0 8px rgba(15, 118, 110, 0.02);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(15, 118, 110, 0.05);
  }
}

@media (max-width: 1279px) {
  .chat-main-shell {
    min-height: 72vh;
    height: auto;
  }

  .chat-page-grid {
    height: auto;
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .composer {
    padding: 10px 10px 12px;
  }

  .composer-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .panel-shell {
    height: auto;
    min-height: 360px;
  }
}
</style>
