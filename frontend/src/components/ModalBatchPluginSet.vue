<template>
  <v-dialog v-model="dialog" max-width="1100">
    <v-card>
      <v-toolbar color="primary" dark>
        <v-icon left>mdi-playlist-check</v-icon>
        Custom plugin set
      </v-toolbar>
      <v-card-text class="pt-4">
        <v-row>
          <v-col cols="12" md="4">
            <v-select
              v-model="selectedPresetId"
              :items="presetItems"
              item-text="name"
              item-value="id"
              label="Start from a preset"
              dense
              outlined
              clearable
              @change="loadPreset"
            >
              <template v-slot:item="{ item }">
                <v-list-item-content>
                  <v-list-item-title>{{ item.name }}</v-list-item-title>
                  <v-list-item-subtitle>
                    {{ item.editable ? "Saved preset" : "Built-in preset" }}
                  </v-list-item-subtitle>
                </v-list-item-content>
              </template>
            </v-select>
            <v-text-field v-model="name" label="Set name" dense outlined></v-text-field>
            <v-text-field
              v-model="description"
              label="Description (optional)"
              dense
              outlined
            ></v-text-field>
            <v-text-field
              v-model="search"
              label="Search plugins"
              dense
              outlined
              clearable
              prepend-inner-icon="mdi-magnify"
            ></v-text-field>
            <div class="catalog-list">
              <div v-for="group in filteredCatalog" :key="group.id" class="mb-3">
                <div class="text-caption font-weight-bold mb-1">{{ group.name }}</div>
                <v-list dense outlined>
                  <v-list-item
                    v-for="plugin in group.children"
                    :key="plugin.plugin"
                    :disabled="!plugin.batch.supported || alreadySelected(plugin.plugin)"
                    @click="addPlugin(plugin)"
                  >
                    <v-list-item-icon>
                      <v-icon>{{ plugin.icon }}</v-icon>
                    </v-list-item-icon>
                    <v-list-item-content>
                      <v-list-item-title>{{ plugin.name }}</v-list-item-title>
                      <v-list-item-subtitle v-if="!plugin.batch.supported">
                        {{ plugin.batch.unsupported_reason }}
                      </v-list-item-subtitle>
                    </v-list-item-content>
                    <v-list-item-action v-if="plugin.batch.supported">
                      <v-icon small>mdi-plus</v-icon>
                    </v-list-item-action>
                  </v-list-item>
                </v-list>
              </div>
            </div>
          </v-col>

          <v-col cols="12" md="8">
            <div class="text-caption mb-2">{{ itemIds.length }} videos - {{ scopeLabel }}</div>
            <v-list dense outlined class="plugin-list">
              <v-list-item
                v-for="(step, index) in steps"
                :key="`${step.plugin}-${index}`"
                :class="{ 'active-step': index === activeStepIndex }"
                @click="activeStepIndex = index"
              >
                <v-list-item-icon>
                  <v-icon>{{ definitionFor(step.plugin).icon }}</v-icon>
                </v-list-item-icon>
                <v-list-item-content>
                  <v-list-item-title>{{ index + 1 }}. {{ definitionFor(step.plugin).name }}</v-list-item-title>
                  <v-list-item-subtitle v-if="stepWarning(step)">
                    {{ stepWarning(step) }}
                  </v-list-item-subtitle>
                </v-list-item-content>
                <v-list-item-action>
                  <v-btn icon small title="Move up" :disabled="index === 0" @click.stop="moveStep(index, -1)">
                    <v-icon small>mdi-arrow-up</v-icon>
                  </v-btn>
                </v-list-item-action>
                <v-list-item-action>
                  <v-btn icon small title="Move down" :disabled="index === steps.length - 1" @click.stop="moveStep(index, 1)">
                    <v-icon small>mdi-arrow-down</v-icon>
                  </v-btn>
                </v-list-item-action>
                <v-list-item-action>
                  <v-btn icon small title="Remove" @click.stop="removeStep(index)">
                    <v-icon small>mdi-delete-outline</v-icon>
                  </v-btn>
                </v-list-item-action>
              </v-list-item>
              <v-list-item v-if="!steps.length">
                <v-list-item-content>
                  <v-list-item-title class="text-caption grey--text">No plugins selected</v-list-item-title>
                </v-list-item-content>
              </v-list-item>
            </v-list>

            <v-divider class="my-4"></v-divider>
            <div v-if="activeStep" class="step-editor">
              <div class="text-subtitle-2 mb-3">{{ definitionFor(activeStep.plugin).name }}</div>
              <v-row>
                <v-col
                  v-for="parameter in editableParameters(activeStep)"
                  :key="parameter.name"
                  cols="12"
                  md="6"
                >
                  <v-checkbox
                    v-if="parameter.field === 'checkbox'"
                    v-model="parameter.value"
                    :label="parameter.text"
                    dense
                  ></v-checkbox>
                  <v-select
                    v-else-if="parameter.field === 'select_options'"
                    v-model="parameter.value"
                    :items="parameter.items"
                    :label="parameter.text"
                    dense
                    outlined
                  ></v-select>
                  <v-select
                    v-else-if="parameter.field === 'buttongroup'"
                    v-model="parameter.value"
                    :items="buttonItems(parameter)"
                    item-text="label"
                    item-value="value"
                    :label="parameter.text"
                    dense
                    outlined
                  ></v-select>
                  <v-text-field
                    v-else-if="parameter.field === 'slider'"
                    v-model.number="parameter.value"
                    :label="parameter.text"
                    :min="parameter.min"
                    :max="parameter.max"
                    :step="parameter.step"
                    type="number"
                    dense
                    outlined
                  ></v-text-field>
                  <v-text-field
                    v-else
                    v-model="parameter.value"
                    :label="parameter.text"
                    dense
                    outlined
                  ></v-text-field>
                </v-col>
              </v-row>

              <v-row v-if="resolutionParameters(activeStep).length">
                <v-col
                  v-for="parameter in resolutionParameters(activeStep)"
                  :key="parameter.name"
                  cols="12"
                  md="6"
                >
                  <v-select
                    v-model="activeStep.parameter_resolution[parameter.name].strategy"
                    :items="resolutionStrategyItems(parameter)"
                    item-text="label"
                    item-value="value"
                    :label="parameter.text"
                    dense
                    outlined
                  ></v-select>
                  <v-text-field
                    v-if="usesNameResolution(activeStep, parameter)"
                    v-model="activeStep.parameter_resolution[parameter.name].name"
                    label="Timeline name"
                    dense
                    outlined
                  ></v-text-field>
                  <v-combobox
                    v-if="usesMultiNameResolution(activeStep, parameter)"
                    v-model="activeStep.parameter_resolution[parameter.name].names"
                    label="Scalar timeline names"
                    dense
                    outlined
                    multiple
                    small-chips
                    deletable-chips
                  ></v-combobox>
                  <v-file-input
                    v-if="usesSharedFileResolution(activeStep, parameter)"
                    v-model="activeStep.parameter_resolution[parameter.name].file"
                    :label="parameter.text"
                    :accept="acceptFor(parameter)"
                    dense
                    outlined
                    @change="onSharedFileChange(activeStep, parameter)"
                  ></v-file-input>
                  <div
                    v-if="usesSharedFileResolution(activeStep, parameter) && activeStep.parameter_resolution[parameter.name].origin"
                    class="text-caption grey--text"
                  >
                    Uploaded: {{ activeStep.parameter_resolution[parameter.name].origin }}
                  </div>
                </v-col>
              </v-row>
            </div>

            <v-alert v-if="validationError" dense outlined type="error" class="mt-4">
              {{ validationError }}
            </v-alert>
            <v-alert v-else-if="validationResult" dense outlined type="success" class="mt-4">
              {{ validationResult.runnable_count }} videos, {{ validationResult.step_count }} steps,
              {{ validationResult.total_jobs }} jobs.
              <span v-if="validationResult.skipped_count">
                {{ validationResult.skipped_count }} videos will be skipped.
              </span>
            </v-alert>
            <v-alert
              v-if="presetError"
              dense
              outlined
              dismissible
              type="error"
              class="mt-4"
              @input="presetError = ''"
            >
              {{ presetError }}
            </v-alert>
            <v-alert
              v-if="presetMessage"
              dense
              outlined
              dismissible
              type="success"
              class="mt-4"
              @input="presetMessage = ''"
            >
              {{ presetMessage }}
            </v-alert>
          </v-col>
        </v-row>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="!canRun" :loading="isSubmitting" @click="run">
          <v-icon left>mdi-play</v-icon>
          Run
        </v-btn>
        <v-btn
          outlined
          :disabled="!canSavePreset"
          :loading="isSavingPreset"
          @click="savePreset"
        >
          <v-icon left>mdi-content-save-outline</v-icon>
          {{ selectedPreset && selectedPreset.editable ? "Update preset" : "Save preset" }}
        </v-btn>
        <v-btn
          v-if="selectedPreset && selectedPreset.editable"
          text
          color="red"
          @click="confirmDeletePreset = true"
        >
          <v-icon left>mdi-delete-outline</v-icon>
          Delete preset
        </v-btn>
        <v-spacer></v-spacer>
        <v-btn text @click="dialog = false">Close</v-btn>
      </v-card-actions>
      <v-dialog v-model="confirmDeletePreset" max-width="420">
        <v-card>
          <v-card-title>Delete saved preset</v-card-title>
          <v-card-text>
            Delete "{{ selectedPreset ? selectedPreset.name : "" }}"? Existing batches
            will retain their saved copy.
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn text @click="confirmDeletePreset = false">Keep preset</v-btn>
            <v-btn color="red" text :loading="isDeletingPreset" @click="deletePreset">
              Delete
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </v-card>
  </v-dialog>
</template>

<script>
import { mapStores } from "pinia";
import { useVideoBatchStore } from "@/store/video_batch";
import { clonePluginCatalog } from "@/plugins/plugin_catalog";

export default {
  props: {
    value: Boolean,
    batchId: String,
    itemIds: {
      type: Array,
      default: () => [],
    },
    scopeLabel: {
      type: String,
      default: "current scope",
    },
  },
  data() {
    return {
      dialog: false,
      name: "Custom batch analysis",
      description: "",
      search: "",
      selectedPresetId: null,
      steps: [],
      activeStepIndex: null,
      validationResult: null,
      validationError: "",
      isSubmitting: false,
      validationTimer: null,
      isUploadingSharedInput: false,
      isSavingPreset: false,
      isDeletingPreset: false,
      confirmDeletePreset: false,
      presetMessage: "",
      presetError: "",
    };
  },
  async mounted() {
    await Promise.all([
      this.videoBatchStore.fetchPluginCatalog(),
      this.videoBatchStore.fetchPresets(),
    ]);
  },
  computed: {
    catalog() {
      return clonePluginCatalog(this.videoBatchStore.pluginCatalog);
    },
    filteredCatalog() {
      const search = (this.search || "").toLowerCase();
      return this.catalog
        .map((group) => ({
          ...group,
          children: group.children.filter(
            (plugin) =>
              !search ||
              plugin.name.toLowerCase().includes(search) ||
              plugin.plugin.toLowerCase().includes(search)
          ),
        }))
        .filter((group) => group.children.length);
    },
    activeStep() {
      if (this.activeStepIndex === null) return null;
      return this.steps[this.activeStepIndex] || null;
    },
    canRun() {
      return Boolean(this.validationResult) && !this.validationError && !this.isSubmitting && !this.isUploadingSharedInput;
    },
    presetItems() {
      return this.videoBatchStore.presets;
    },
    selectedPreset() {
      return this.presetItems.find((preset) => preset.id === this.selectedPresetId) || null;
    },
    canSavePreset() {
      return Boolean(
        this.name.trim() &&
          this.steps.length &&
          !this.isSavingPreset &&
          !this.isUploadingSharedInput
      );
    },
    ...mapStores(useVideoBatchStore),
  },
  methods: {
    flatCatalog() {
      return this.catalog.flatMap((group) => group.children);
    },
    definitionFor(plugin) {
      return this.flatCatalog().find((definition) => definition.plugin === plugin) || {};
    },
    alreadySelected(plugin) {
      return this.steps.some((step) => step.plugin === plugin);
    },
    addPlugin(definition) {
      if (!definition.batch.supported || this.alreadySelected(definition.plugin)) return;
      this.steps.push(this.cloneStep(definition));
      this.activeStepIndex = this.steps.length - 1;
      this.scheduleValidation();
    },
    cloneStep(definition) {
      const parameters = [...(definition.parameters || []), ...(definition.optional_parameters || [])]
        .filter((parameter) => !this.isResolutionParameter(parameter))
        .map((parameter) => ({ ...parameter }));
      const parameterResolution = {};
      [...(definition.parameters || []), ...(definition.optional_parameters || [])]
        .filter((parameter) => this.isResolutionParameter(parameter))
        .forEach((parameter) => {
          parameterResolution[parameter.name] = this.defaultResolution(parameter);
        });

      return {
        plugin: definition.plugin,
        parameters,
        dependencies: {},
        parameter_resolution: parameterResolution,
      };
    },
    loadPreset(presetId) {
      if (!presetId) return;
      const preset = this.presetItems.find((entry) => entry.id === presetId);
      if (!preset) return;

      this.name = preset.name;
      this.description = preset.description || "";
      this.steps = [];
      const missingPlugins = [];
      (preset.steps || []).forEach((savedStep) => {
        const definition = this.definitionFor(savedStep.plugin);
        if (!definition.plugin) {
          missingPlugins.push(savedStep.plugin);
          return;
        }
        const step = this.cloneStep(definition);
        const savedValues = new Map(
          (savedStep.parameters || []).map((parameter) => [
            parameter.name,
            parameter.value,
          ])
        );
        step.parameters.forEach((parameter) => {
          if (savedValues.has(parameter.name)) {
            parameter.value = savedValues.get(parameter.name);
          }
        });
        step.dependencies = { ...(savedStep.dependencies || {}) };
        step.parameter_resolution = {
          ...step.parameter_resolution,
          ...JSON.parse(JSON.stringify(savedStep.parameter_resolution || {})),
        };
        Object.keys(step.dependencies).forEach((parameterName) => {
          this.$delete(step.parameter_resolution, parameterName);
        });
        this.steps.push(step);
      });
      this.activeStepIndex = this.steps.length ? 0 : null;
      this.presetMessage = "";
      this.presetError = missingPlugins.length
        ? `Unavailable plugins: ${missingPlugins.join(", ")}`
        : "";
      this.scheduleValidation();
    },
    isResolutionParameter(parameter) {
      return [
        "select_timeline",
        "select_scalar_timeline",
        "select_scalar_timelines",
        "image_input",
        "csv_input",
      ].includes(parameter.field);
    },
    defaultResolution(parameter) {
      if (parameter.field === "select_timeline") {
        if (this.steps.some((step) => step.plugin === "shotdetection")) {
          return {
            strategy: "previous_step_output",
            expression: "shotdetection.timelines.shots",
            required: true,
          };
        }
        return { strategy: "timeline_by_name", name: "Shots", required: true };
      }
      if (parameter.field === "select_scalar_timeline") {
        return { strategy: "scalar_timeline_by_name", name: "", required: true };
      }
      if (parameter.field === "select_scalar_timelines") {
        return { strategy: "scalar_timelines_by_name", names: [], required: true };
      }
      return { strategy: "shared_file", required: true };
    },
    editableParameters(step) {
      return step.parameters;
    },
    resolutionParameters(step) {
      const definition = this.definitionFor(step.plugin);
      return [...(definition.parameters || []), ...(definition.optional_parameters || [])].filter((parameter) =>
        this.isResolutionParameter(parameter)
      );
    },
    resolutionStrategyItems(parameter) {
      if (parameter.field === "select_timeline") {
        return [
          { label: "Previous step output", value: "previous_step_output" },
          { label: "Timeline by name", value: "timeline_by_name" },
        ];
      }
      if (parameter.field === "select_scalar_timeline") {
        return [{ label: "Scalar timeline by name", value: "scalar_timeline_by_name" }];
      }
      if (parameter.field === "select_scalar_timelines") {
        return [{ label: "Scalar timelines by name", value: "scalar_timelines_by_name" }];
      }
      return [{ label: "Shared file", value: "shared_file" }];
    },
    usesNameResolution(step, parameter) {
      const resolution = step.parameter_resolution[parameter.name] || {};
      return ["timeline_by_name", "scalar_timeline_by_name"].includes(resolution.strategy);
    },
    usesMultiNameResolution(step, parameter) {
      const resolution = step.parameter_resolution[parameter.name] || {};
      return resolution.strategy === "scalar_timelines_by_name";
    },
    usesSharedFileResolution(step, parameter) {
      const resolution = step.parameter_resolution[parameter.name] || {};
      return resolution.strategy === "shared_file";
    },
    acceptFor(parameter) {
      if (parameter.field === "csv_input") return "text/csv,.csv";
      if (parameter.field === "image_input") return "image/jpeg,image/png";
      return "";
    },
    onSharedFileChange(step, parameter) {
      const resolution = step.parameter_resolution[parameter.name];
      if (!resolution) return;
      this.$delete(resolution, "path");
      this.$delete(resolution, "origin");
      this.$delete(resolution, "file_size");
      this.$delete(resolution, "checksum");
      this.scheduleValidation();
    },
    buttonItems(parameter) {
      return (parameter.buttons || []).map((label, value) => ({ label, value }));
    },
    stepWarning(step) {
      const definition = this.definitionFor(step.plugin);
      const parameters = this.resolutionParameters(step);
      if (!parameters.length) return "";
      const missing = parameters.find((parameter) => {
        const resolution = step.parameter_resolution[parameter.name] || {};
        return (
          ["timeline_by_name", "scalar_timeline_by_name"].includes(resolution.strategy) &&
          !resolution.name
        ) || (resolution.strategy === "scalar_timelines_by_name" && !(resolution.names || []).length) ||
          (resolution.strategy === "shared_file" && !resolution.path && !resolution.file);
      });
      if (missing) {
        const resolution = step.parameter_resolution[missing.name] || {};
        if (resolution.strategy === "shared_file") return `${missing.text} needs a file.`;
        return `${missing.text} needs a timeline name.`;
      }
      if (definition.batch && !definition.batch.supported) return definition.batch.unsupported_reason;
      return "";
    },
    removeStep(index) {
      this.steps.splice(index, 1);
      if (!this.steps.length) {
        this.activeStepIndex = null;
      } else if (this.activeStepIndex >= this.steps.length) {
        this.activeStepIndex = this.steps.length - 1;
      }
      this.scheduleValidation();
    },
    moveStep(index, direction) {
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= this.steps.length) return;
      const [step] = this.steps.splice(index, 1);
      this.steps.splice(nextIndex, 0, step);
      this.activeStepIndex = nextIndex;
      this.scheduleValidation();
    },
    payloadSteps() {
      return this.steps.map((step) => ({
        plugin: step.plugin,
        parameters: step.parameters.map((parameter) => ({
          name: parameter.name,
          value: parameter.value,
        })),
        dependencies: step.dependencies || {},
        parameter_resolution: this.payloadParameterResolution(step.parameter_resolution || {}),
      }));
    },
    payloadParameterResolution(parameterResolution) {
      const payload = {};
      Object.entries(parameterResolution).forEach(([name, resolution]) => {
        const { file, ...serializable } = resolution;
        payload[name] = serializable;
      });
      return payload;
    },
    scope() {
      return {
        type: "item_ids",
        item_ids: this.itemIds,
      };
    },
    async validate() {
      this.validationResult = null;
      this.validationError = "";
      if (!this.batchId || !this.itemIds.length || !this.steps.length) return;

      try {
        await this.ensureSharedInputs();
        const response = await this.videoBatchStore.validatePluginSet({
          batchId: this.batchId,
          name: this.name,
          scope: this.scope(),
          steps: this.payloadSteps(),
        });
        if (response.data.status === "ok") {
          this.validationResult = response.data;
        }
      } catch (error) {
        const data = error.response && error.response.data ? error.response.data : {};
        this.validationError = data.reason || data.type || "validation_failed";
      }
    },
    scheduleValidation() {
      clearTimeout(this.validationTimer);
      this.validationTimer = setTimeout(this.validate, 250);
    },
    async ensureSharedInputs() {
      const uploads = [];
      this.steps.forEach((step) => {
        Object.values(step.parameter_resolution || {}).forEach((resolution) => {
          if (resolution.strategy === "shared_file" && resolution.file && !resolution.path) {
            uploads.push(resolution);
          }
        });
      });
      if (!uploads.length) return;

      this.isUploadingSharedInput = true;
      try {
        for (const resolution of uploads) {
          const response = await this.videoBatchStore.uploadSharedInput({
            batchId: this.batchId,
            file: resolution.file,
          });
          if (response.status !== "ok") {
            throw new Error(response.type || "shared_input_upload_failed");
          }
          this.$set(resolution, "path", response.entry.path);
          this.$set(resolution, "origin", response.entry.origin);
          this.$set(resolution, "file_size", response.entry.file_size);
          this.$set(resolution, "checksum", response.entry.checksum);
        }
      } finally {
        this.isUploadingSharedInput = false;
      }
    },
    async run() {
      this.isSubmitting = true;
      try {
        await this.ensureSharedInputs();
        await this.videoBatchStore.runPluginSet({
          batchId: this.batchId,
          name: this.name,
          scope: this.scope(),
          steps: this.payloadSteps(),
        });
        this.dialog = false;
        this.$emit("ran");
      } finally {
        this.isSubmitting = false;
      }
    },
    async savePreset() {
      this.isSavingPreset = true;
      this.presetMessage = "";
      this.presetError = "";
      try {
        const response = await this.videoBatchStore.savePreset({
          id:
            this.selectedPreset && this.selectedPreset.editable
              ? this.selectedPreset.id
              : null,
          name: this.name,
          description: this.description,
          steps: this.payloadSteps(),
        });
        if (response.status === "ok") {
          this.selectedPresetId = response.entry.id;
          this.presetMessage =
            "Preset saved. It is now available for batch uploads and preset runs.";
        }
      } catch (error) {
        const data = error.response && error.response.data ? error.response.data : {};
        if (data.type === "preset_name_exists") {
          this.presetError = "A saved preset with this name already exists.";
        } else if (data.type === "shared_file_preset_not_supported") {
          this.presetError =
            "Presets with shared image or CSV files cannot be saved yet.";
        } else {
          this.presetError = data.reason || data.type || "The preset could not be saved.";
        }
      } finally {
        this.isSavingPreset = false;
      }
    },
    async deletePreset() {
      if (!this.selectedPreset || !this.selectedPreset.editable) return;
      this.isDeletingPreset = true;
      this.presetMessage = "";
      this.presetError = "";
      try {
        const response = await this.videoBatchStore.deletePreset(
          this.selectedPreset.id
        );
        if (response.status === "ok") {
          this.selectedPresetId = null;
          this.confirmDeletePreset = false;
          this.presetMessage =
            "Preset deleted. The current plugin settings remain in the editor.";
        }
      } catch (error) {
        const data = error.response && error.response.data ? error.response.data : {};
        this.presetError = data.reason || data.type || "The preset could not be deleted.";
      } finally {
        this.isDeletingPreset = false;
      }
    },
    reset() {
      this.name = "Custom batch analysis";
      this.description = "";
      this.search = "";
      this.selectedPresetId = null;
      this.steps = [];
      this.activeStepIndex = null;
      this.validationResult = null;
      this.validationError = "";
      this.presetMessage = "";
      this.presetError = "";
      this.confirmDeletePreset = false;
      clearTimeout(this.validationTimer);
    },
  },
  watch: {
    dialog(value) {
      this.$emit("input", value);
      if (!value) this.reset();
      if (value) {
        this.videoBatchStore.fetchPluginCatalog();
        this.videoBatchStore.fetchPresets();
      }
    },
    value(value) {
      this.dialog = value;
    },
    steps: {
      deep: true,
      handler() {
        this.scheduleValidation();
      },
    },
    itemIds() {
      this.scheduleValidation();
    },
  },
};
</script>

<style scoped>
.catalog-list {
  max-height: 560px;
  overflow-y: auto;
}

.plugin-list {
  max-height: 260px;
  overflow-y: auto;
}

.active-step {
  background: #f5f5f5;
}
</style>
