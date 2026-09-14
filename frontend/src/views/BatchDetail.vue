<template>
  <v-main>
    <v-container v-if="batch" fluid class="py-8 px-6">
      <v-row align="center" class="mb-4">
        <v-col>
          <h1 class="text-h5 mb-1">{{ batch.name }}</h1>
          <div class="text-caption">
            {{ batch.status }} - {{ batch.total_count }} videos
            <span v-if="batch.preset"> - {{ presetLabel }}</span>
            <span v-if="batch.auto_run_preset"> - auto-run</span>
          </div>
        </v-col>
        <v-col cols="auto">
          <v-btn
            outlined
            class="mr-2"
            :disabled="!canRunBatchPlugins"
            @click="openRunPresetDialog"
          >
            <v-icon left>mdi-play</v-icon>
            Run preset
          </v-btn>
          <v-btn
            outlined
            class="mr-2"
            :disabled="!canRunBatchPlugins"
            @click="openCustomPluginSetForAll"
          >
            <v-icon left>mdi-playlist-check</v-icon>
            Custom set
          </v-btn>
          <v-btn
            outlined
            class="mr-2"
            :disabled="!hasReadyVideos"
            :loading="videoBatchStore.isExportingElan"
            @click="exportElan"
          >
            <v-icon left>mdi-file-export-outline</v-icon>
            Export ELAN
          </v-btn>
          <v-btn outlined class="mr-2" @click="retryFailed">
            <v-icon left>mdi-refresh</v-icon>
            Retry ingest
          </v-btn>
          <v-btn outlined class="mr-2" @click="retryPluginSteps">
            <v-icon left>mdi-replay</v-icon>
            Retry plugins
          </v-btn>
          <v-btn
            outlined
            class="mr-2"
            :disabled="!canCancelBatch"
            @click="confirmCancel = true"
          >
            <v-icon left>mdi-stop-circle-outline</v-icon>
            Cancel
          </v-btn>
          <v-btn color="red" outlined @click="confirmDelete = true">
            <v-icon left>mdi-trash-can-outline</v-icon>
            Delete
          </v-btn>
        </v-col>
      </v-row>

      <v-row class="mb-4">
        <v-col cols="12">
          <v-chip
            v-for="summary in statusSummaries"
            :key="summary.key"
            small
            outlined
            class="mr-2 mb-2"
            :color="statusColor(summary.label)"
          >
            {{ summary.label }} {{ summary.count }}
          </v-chip>
        </v-col>
      </v-row>

      <v-alert v-if="exportError" dense outlined type="error" dismissible @input="exportError = ''">
        {{ exportError }}
      </v-alert>

      <v-row class="mb-4">
        <v-col cols="12" md="2">
          <v-select v-model="filter" :items="filters" label="Status" hide-details></v-select>
        </v-col>
        <v-col cols="12" md="3">
          <v-select
            v-model="folderFilter"
            :items="folderOptions"
            item-text="label"
            item-value="value"
            label="Folder"
            hide-details
            clearable
          ></v-select>
        </v-col>
        <v-col cols="12" md="3">
          <v-text-field v-model="search" label="Search name or path" hide-details clearable></v-text-field>
        </v-col>
        <v-col cols="12" md="4">
          <v-progress-linear :value="batchProgress" height="24">
            <strong>{{ batchProgress }}%</strong>
          </v-progress-linear>
        </v-col>
      </v-row>

      <v-row class="mb-4" align="center">
        <v-col cols="12">
          <v-toolbar dense flat class="selection-toolbar">
            <span class="text-caption mr-4">{{ selectedItemIds.length }} / {{ tableItems.length }} selected</span>
            <v-btn small outlined class="mr-2" @click="selectVisible">
              <v-icon left small>mdi-checkbox-multiple-marked-outline</v-icon>
              Select visible
            </v-btn>
            <v-btn small outlined class="mr-2" @click="invertVisibleSelection">
              <v-icon left small>mdi-checkbox-multiple-blank-outline</v-icon>
              Invert visible
            </v-btn>
            <v-btn small outlined class="mr-2" :disabled="!folderFilter" @click="selectCurrentFolder">
              <v-icon left small>mdi-folder-check-outline</v-icon>
              Select folder
            </v-btn>
            <v-btn small outlined class="mr-2" @click="openSelectedVideos">
              <v-icon left small>mdi-open-in-new</v-icon>
              Open first
            </v-btn>
            <v-btn
              small
              outlined
              class="mr-2"
              :disabled="!readySelectedItemIds.length || !canRunBatchPlugins"
              @click="openCustomPluginSetForSelection"
            >
              <v-icon left small>mdi-playlist-check</v-icon>
              Run plugins
            </v-btn>
            <v-btn small outlined class="mr-2" @click="filter = 'ERROR'">
              <v-icon left small>mdi-alert-circle-outline</v-icon>
              Show errors
            </v-btn>
            <v-spacer></v-spacer>
            <v-btn small icon @click="clearSelection" title="Clear selection">
              <v-icon small>mdi-close</v-icon>
            </v-btn>
          </v-toolbar>
        </v-col>
      </v-row>

      <v-alert v-if="failedItems.length" dense outlined type="error" class="mb-4">
        {{ failedItems.length }} item{{ failedItems.length === 1 ? "" : "s" }} need attention.
      </v-alert>

      <v-data-table
        v-model="selectedRows"
        :headers="tableHeaders"
        :items="tableItems"
        :loading="videoBatchStore.isLoading"
        :items-per-page="50"
        item-key="id"
        show-select
        group-by="folder_path"
        dense
      >
        <template v-slot:loading>
          <span>Loading batch...</span>
        </template>
        <template v-slot:no-data>
          <span>No videos in this batch.</span>
        </template>
        <template v-slot:no-results>
          <span>No videos match the current filter.</span>
        </template>
        <template v-slot:group.header="{ group, headers, toggle, isOpen }">
          <td :colspan="headers.length" class="folder-row">
            <v-checkbox
              class="folder-checkbox"
              dense
              hide-details
              :input-value="folderSelectionState(group).all"
              :indeterminate="folderSelectionState(group).some"
              @click.stop="toggleFolderSelection(group)"
            ></v-checkbox>
            <span class="folder-label" @click="toggle">
              <v-icon small class="mr-1">{{ isOpen ? "mdi-folder-open-outline" : "mdi-folder-outline" }}</v-icon>
              {{ group || "Root" }} ({{ folderItems(group).length }})
            </span>
            <v-btn icon small title="Filter folder" @click.stop="folderFilter = group">
              <v-icon small>mdi-filter-outline</v-icon>
            </v-btn>
            <v-btn
              icon
              small
              title="Run plugins for folder"
              :disabled="!canRunBatchPlugins"
              @click.stop="openCustomPluginSetForFolder(group)"
            >
              <v-icon small>mdi-playlist-play</v-icon>
            </v-btn>
          </td>
        </template>
        <template v-slot:item.video_link="{ item }">
          <v-btn
            v-if="item.video"
            icon
            small
            :to="{ path: `/videoanalysis/${item.video.id}` }"
            title="Open video"
          >
            <v-icon small>mdi-open-in-new</v-icon>
          </v-btn>
        </template>
        <template v-slot:item.original_path="{ item }">
          <span class="path-cell">{{ item.original_path }}</span>
        </template>
        <template v-slot:item.ingest_status="{ item }">
          <v-chip small :color="statusColor(item.ingest_status)" dark>
            {{ item.ingest_status }}
          </v-chip>
        </template>
        <template v-slot:item.video.duration="{ item }">
          <span v-if="item.video">{{ getDisplayTime(item.video.duration) }}</span>
        </template>
        <template v-slot:item.video.resolution="{ item }">
          <span v-if="item.video">{{ item.video.width }}x{{ item.video.height }}</span>
        </template>
        <template
          v-for="plugin in pluginColumns"
          v-slot:[`item.plugin_statuses.${plugin}`]="{ item }"
        >
          <v-chip
            v-if="item.plugin_statuses[plugin]"
            :key="plugin"
            x-small
            :color="statusColor(item.plugin_statuses[plugin].status)"
            dark
          >
            {{ item.plugin_statuses[plugin].status }}
          </v-chip>
        </template>
        <template v-slot:item.error_summary="{ item }">
          <span class="error-cell">{{ item.error_summary }}</span>
        </template>
      </v-data-table>

      <v-dialog v-model="confirmRunPreset" max-width="420">
        <v-card>
          <v-card-title>Run preset</v-card-title>
          <v-card-text>
            <v-select
              v-model="presetToRun"
              :items="runPresetItems"
              item-text="name"
              item-value="id"
              label="Preset"
              outlined
              dense
            ></v-select>
            <v-radio-group v-model="presetScopeMode" dense>
              <v-radio label="All ready videos" value="all"></v-radio>
              <v-radio
                :label="`Selected ready videos (${readySelectedItemIds.length})`"
                value="selected"
                :disabled="!readySelectedItemIds.length"
              ></v-radio>
              <v-radio
                :label="`Current folder (${currentFolderReadyItemIds.length})`"
                value="folder"
                :disabled="!folderFilter || !currentFolderReadyItemIds.length"
              ></v-radio>
              <v-radio
                :label="`Filtered ready videos (${filteredReadyItemIds.length})`"
                value="filtered"
                :disabled="!filteredReadyItemIds.length"
              ></v-radio>
            </v-radio-group>
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn text @click="confirmRunPreset = false">Cancel</v-btn>
            <v-btn
              color="primary"
              text
              :disabled="!canRunBatchPlugins || !presetToRun"
              @click="runPreset"
            >Run</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <ModalBatchPluginSet
        v-model="showCustomPluginSet"
        :batch-id="batchId"
        :item-ids="customPluginItemIds"
        :scope-label="customPluginScopeLabel"
        @ran="fetchBatch"
      />

      <v-dialog v-model="confirmCancel" max-width="420">
        <v-card>
          <v-card-title>Cancel batch</v-card-title>
          <v-card-text>Queued work will be marked as cancelled. Already-started analyser work may finish.</v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn text @click="confirmCancel = false">Keep running</v-btn>
            <v-btn
              color="warning"
              text
              :disabled="!canCancelBatch"
              @click="cancelBatch"
            >Cancel batch</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <v-dialog v-model="confirmDelete" max-width="420">
        <v-card>
          <v-card-title>Delete batch</v-card-title>
          <v-card-text>This removes the batch record and any remaining temporary batch sources.</v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn text @click="confirmDelete = false">Keep batch</v-btn>
            <v-btn color="red" text @click="deleteBatch">Delete</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </v-container>
  </v-main>
</template>

<script>
import { mapStores } from "pinia";
import { useVideoBatchStore } from "@/store/video_batch";
import ModalBatchPluginSet from "@/components/ModalBatchPluginSet.vue";

export default {
  data() {
    return {
      filter: "All",
      folderFilter: null,
      search: "",
      selectedItemIds: [],
      presetScopeMode: "all",
      presetToRun: null,
      showCustomPluginSet: false,
      customPluginItemIds: [],
      customPluginScopeLabel: "all ready videos",
      timer: null,
      confirmRunPreset: false,
      confirmCancel: false,
      confirmDelete: false,
      exportError: "",
      filters: [
        "All",
        "PENDING",
        "INGESTING",
        "READY",
        "ERROR",
        "RUNNING",
        "DONE",
        "SKIPPED",
        "CANCELLED",
      ],
    };
  },
  mounted() {
    this.videoBatchStore.fetchPresets();
    this.fetchBatch();
    this.timer = setInterval(this.fetchBatch, 3000);
  },
  beforeDestroy() {
    if (this.timer) clearInterval(this.timer);
  },
  computed: {
    batchId() {
      return this.$route.params.id;
    },
    batch() {
      return this.videoBatchStore.get(this.batchId);
    },
    presetLabel() {
      if (!this.batch || !this.batch.preset) return "";
      if (this.batch.custom_preset_definition) {
        return this.batch.custom_preset_definition.name || "Custom plugin set";
      }
      const preset = this.videoBatchStore.presets.find(
        (entry) => entry.id === this.batch.preset
      );
      return preset ? preset.name : this.batch.preset;
    },
    runPresetItems() {
      const presets = [...this.videoBatchStore.presets];
      if (
        this.batch &&
        this.batch.preset &&
        this.batch.custom_preset_definition &&
        !presets.some((preset) => preset.id === this.batch.preset)
      ) {
        presets.push({
          id: this.batch.preset,
          name: this.presetLabel,
          source: "batch",
        });
      }
      return presets;
    },
    filteredItems() {
      if (!this.batch || !this.batch.items) return [];
      const search = (this.search || "").toLowerCase();
      return this.batch.items.filter((item) => {
        const folderPath = this.folderPathForItem(item);
        const pluginStatuses = this.pluginsForItem(item).map((plugin) => plugin.status);
        const matchesFilter =
          this.filter === "All" ||
          item.ingest_status === this.filter ||
          pluginStatuses.includes(this.filter);
        const matchesFolder =
          !this.folderFilter ||
          folderPath === this.folderFilter ||
          folderPath.startsWith(`${this.folderFilter}/`);
        const matchesSearch =
          !search ||
          item.original_path.toLowerCase().includes(search) ||
          item.original_filename.toLowerCase().includes(search);
        return matchesFilter && matchesFolder && matchesSearch;
      });
    },
    tableItems() {
      return this.filteredItems.map((item) => {
        const plugin_statuses = {};
        this.pluginsForItem(item).forEach((plugin) => {
          plugin_statuses[plugin.plugin] = plugin;
        });
        const folder_path = this.folderPathForItem(item);
        const errors = [
          item.ingest_error,
          ...Object.values(plugin_statuses).map((plugin) => plugin.error),
        ].filter(Boolean);
        return {
          ...item,
          folder_path,
          plugin_statuses,
          error_summary: errors.join(", "),
        };
      });
    },
    folderOptions() {
      const folders = new Map();
      this.tableSourceItems.forEach((item) => {
        const folderPath = this.folderPathForItem(item);
        folders.set(folderPath, (folders.get(folderPath) || 0) + 1);
      });
      return Array.from(folders.entries())
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([value, count]) => ({
          value,
          label: `${value || "Root"} (${count})`,
        }));
    },
    tableSourceItems() {
      return this.batch && this.batch.items ? this.batch.items : [];
    },
    selectedRows: {
      get() {
        const selected = new Set(this.selectedItemIds);
        return this.tableItems.filter((item) => selected.has(item.id));
      },
      set(rows) {
        this.selectedItemIds = rows.map((row) => row.id);
      },
    },
    selectedItems() {
      const selected = new Set(this.selectedItemIds);
      return this.tableSourceItems.filter((item) => selected.has(item.id));
    },
    readySelectedItemIds() {
      return this.selectedItems
        .filter((item) => item.ingest_status === "READY" && item.video)
        .map((item) => item.id);
    },
    filteredReadyItemIds() {
      return this.tableItems
        .filter((item) => item.ingest_status === "READY" && item.video)
        .map((item) => item.id);
    },
    currentFolderReadyItemIds() {
      if (!this.folderFilter) return [];
      return this.tableSourceItems
        .filter((item) => {
          const folderPath = this.folderPathForItem(item);
          return (
            item.ingest_status === "READY" &&
            item.video &&
            (folderPath === this.folderFilter || folderPath.startsWith(`${this.folderFilter}/`))
          );
        })
        .map((item) => item.id);
    },
    pluginColumns() {
      if (!this.batch || !this.batch.plugin_runs) return [];
      const columns = [];
      this.batch.plugin_runs
        .slice()
        .sort((a, b) => a.step_index - b.step_index || a.plugin.localeCompare(b.plugin))
        .forEach((plugin) => {
          if (!columns.includes(plugin.plugin)) columns.push(plugin.plugin);
        });
      return columns;
    },
    tableHeaders() {
      return [
        { text: "Open", value: "video_link", sortable: false, width: 56 },
        { text: "Path", value: "original_path" },
        { text: "File", value: "original_filename" },
        { text: "Ingest", value: "ingest_status", width: 110 },
        { text: "Uploaded", value: "date" },
        { text: "Duration", value: "video.duration", sortable: false, width: 110 },
        { text: "Resolution", value: "video.resolution", sortable: false, width: 120 },
        ...this.pluginColumns.map((plugin) => ({
          text: plugin,
          value: `plugin_statuses.${plugin}`,
          sortable: false,
          width: 150,
        })),
        { text: "Error", value: "error_summary" },
      ];
    },
    failedItems() {
      if (!this.batch || !this.batch.items) return [];
      return this.batch.items.filter((item) => item.ingest_status === "ERROR");
    },
    hasReadyVideos() {
      return Boolean(
        this.batch &&
          this.batch.items &&
          this.batch.items.some(
            (item) => item.ingest_status === "READY" && item.video
          )
      );
    },
    canRunBatchPlugins() {
      return Boolean(
        this.hasReadyVideos &&
          this.batch &&
          !["UPLOADING", "INGESTING", "RUNNING", "CANCELLED"].includes(
            this.batch.status
          )
      );
    },
    canCancelBatch() {
      return Boolean(
        this.batch &&
          ["UPLOADING", "INGESTING", "RUNNING"].includes(this.batch.status)
      );
    },
    statusSummaries() {
      const itemStatuses = ["PENDING", "INGESTING", "READY", "ERROR"];
      const pluginStatuses = ["PENDING", "RUNNING", "DONE", "ERROR", "SKIPPED"];
      const summaries = itemStatuses.map((status) => ({
        key: `item-${status}`,
        label: status,
        count: this.batch.items.filter((item) => item.ingest_status === status).length,
      }));
      pluginStatuses.forEach((status) => {
        summaries.push({
          key: `plugin-${status}`,
          label: status,
          count: this.batch.plugin_runs.filter((plugin) => plugin.status === status).length,
        });
      });
      return summaries;
    },
    batchProgress() {
      if (!this.batch || !this.batch.total_count) return 0;
      return Math.round(
        ((this.batch.ready_count + this.batch.failed_count) * 100) / this.batch.total_count
      );
    },
    ...mapStores(useVideoBatchStore),
  },
  methods: {
    fetchBatch() {
      this.videoBatchStore.fetch(this.batchId);
    },
    folderPathForItem(item) {
      const pathParts = (item.original_path || "").split("/");
      return pathParts.length > 1 ? pathParts.slice(0, -1).join("/") : "";
    },
    folderItems(group) {
      return this.tableItems.filter((item) => item.folder_path === (group || ""));
    },
    folderSelectionState(group) {
      const items = this.folderItems(group);
      const ids = new Set(this.selectedItemIds);
      const selectedCount = items.filter((item) => ids.has(item.id)).length;
      return {
        all: items.length > 0 && selectedCount === items.length,
        some: selectedCount > 0 && selectedCount < items.length,
      };
    },
    toggleFolderSelection(group) {
      const items = this.folderItems(group);
      const state = this.folderSelectionState(group);
      const ids = new Set(this.selectedItemIds);
      items.forEach((item) => {
        if (state.all) {
          ids.delete(item.id);
        } else {
          ids.add(item.id);
        }
      });
      this.selectedItemIds = Array.from(ids);
    },
    selectVisible() {
      const ids = new Set(this.selectedItemIds);
      this.tableItems.forEach((item) => ids.add(item.id));
      this.selectedItemIds = Array.from(ids);
    },
    invertVisibleSelection() {
      const ids = new Set(this.selectedItemIds);
      this.tableItems.forEach((item) => {
        if (ids.has(item.id)) ids.delete(item.id);
        else ids.add(item.id);
      });
      this.selectedItemIds = Array.from(ids);
    },
    selectCurrentFolder() {
      const ids = new Set(this.selectedItemIds);
      this.tableSourceItems
        .filter((item) => {
          const folderPath = this.folderPathForItem(item);
          return folderPath === this.folderFilter || folderPath.startsWith(`${this.folderFilter}/`);
        })
        .forEach((item) => ids.add(item.id));
      this.selectedItemIds = Array.from(ids);
    },
    clearSelection() {
      this.selectedItemIds = [];
    },
    pluginsForItem(item) {
      if (!this.batch || !this.batch.plugin_runs) return [];
      return this.batch.plugin_runs.filter((plugin) => plugin.item_id === item.id);
    },
    getDisplayTime(value) {
      if (!value) return "";
      const minutes = Math.floor(value / 60);
      const seconds = Math.floor(value % 60).toString().padStart(2, "0");
      return `${minutes}:${seconds}`;
    },
    statusColor(status) {
      if (status === "ERROR" || status === "PARTIAL_ERROR") return "red";
      if (status === "CANCELLED" || status === "SKIPPED") return "grey";
      if (status === "READY" || status === "DONE") return "green";
      if (status === "RUNNING" || status === "INGESTING") return "blue";
      return "orange";
    },
    async runPreset() {
      this.confirmRunPreset = false;
      await this.videoBatchStore.runScopedPreset({
        batchId: this.batchId,
        preset: this.presetToRun,
        scope: this.scopeForPresetRun(),
      });
      this.fetchBatch();
    },
    openRunPresetDialog() {
      this.presetToRun =
        (this.batch && this.batch.preset) ||
        (this.runPresetItems.length ? this.runPresetItems[0].id : null);
      this.confirmRunPreset = true;
    },
    scopeForPresetRun() {
      if (this.presetScopeMode === "selected") {
        return { type: "item_ids", item_ids: this.readySelectedItemIds };
      }
      if (this.presetScopeMode === "folder") {
        return { type: "item_ids", item_ids: this.currentFolderReadyItemIds };
      }
      if (this.presetScopeMode === "filtered") {
        return { type: "item_ids", item_ids: this.filteredReadyItemIds };
      }
      return { type: "all" };
    },
    async retryFailed() {
      await this.videoBatchStore.retryFailed(this.batchId);
      this.fetchBatch();
    },
    async retryPluginSteps() {
      await this.videoBatchStore.retryFailedPluginSteps(this.batchId);
      this.fetchBatch();
    },
    async exportElan() {
      this.exportError = "";
      try {
        await this.videoBatchStore.exportElan(this.batchId, this.batch.name);
      } catch (error) {
        this.exportError = "The ELAN batch export could not be created.";
      }
    },
    async cancelBatch() {
      this.confirmCancel = false;
      await this.videoBatchStore.cancel(this.batchId);
      this.fetchBatch();
    },
    async deleteBatch() {
      this.confirmDelete = false;
      await this.videoBatchStore.delete(this.batchId);
      this.$router.push({ path: "/batches" });
    },
    openSelectedVideos() {
      const firstVideo = this.selectedItems.find((item) => item.video);
      if (firstVideo) {
        this.$router.push({ path: `/videoanalysis/${firstVideo.video.id}` });
      }
    },
    openCustomPluginSetForAll() {
      this.customPluginItemIds = this.tableSourceItems
        .filter((item) => item.ingest_status === "READY" && item.video)
        .map((item) => item.id);
      this.customPluginScopeLabel = "all ready videos";
      this.showCustomPluginSet = true;
    },
    openCustomPluginSetForSelection() {
      this.customPluginItemIds = this.readySelectedItemIds;
      this.customPluginScopeLabel = "selected ready videos";
      this.showCustomPluginSet = true;
    },
    openCustomPluginSetForFolder(group) {
      const folder = group || "";
      this.customPluginItemIds = this.tableSourceItems
        .filter((item) => {
          const folderPath = this.folderPathForItem(item);
          return (
            item.ingest_status === "READY" &&
            item.video &&
            (folderPath === folder || (folder && folderPath.startsWith(`${folder}/`)))
          );
        })
        .map((item) => item.id);
      this.customPluginScopeLabel = folder ? `folder ${folder} and subfolders` : "root folder";
      this.showCustomPluginSet = true;
    },
  },
  components: { ModalBatchPluginSet },
};
</script>

<style scoped>
.path-cell {
  font-family: monospace;
  font-size: 12px;
}

.error-cell {
  color: #b00020;
  font-size: 12px;
}

.folder-row {
  background: #f5f5f5;
  font-weight: 600;
}

.folder-checkbox {
  display: inline-flex;
  margin: 0 8px 0 0;
  vertical-align: middle;
}

.folder-label {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  margin-right: 8px;
}

.selection-toolbar {
  border: 1px solid #e0e0e0;
}
</style>
