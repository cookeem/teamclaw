const AppDialog = {
  template: `
    <v-dialog v-model="dialogState.open" max-width="460" persistent>
      <v-card>
        <v-card-title>{{ dialogState.title }}</v-card-title>
        <v-card-text>
          <div class="dialog-message">{{ dialogState.message }}</div>
          <v-text-field
            v-if="dialogState.mode === 'prompt'"
            v-model="dialogState.input"
            :label="dialogState.inputLabel || $t('common.input')"
            :placeholder="dialogState.inputPlaceholder"
            variant="outlined"
            class="mt-3"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn v-if="dialogState.mode !== 'alert'" variant="text" @click="dialogCancel">
            {{ dialogState.cancelText }}
          </v-btn>
          <v-btn color="secondary" @click="dialogOk">{{ dialogState.okText }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  `,
  setup() {
    return {
      dialogState,
      dialogOk,
      dialogCancel,
    };
  },
};
