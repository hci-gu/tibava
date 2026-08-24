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
          <v-btn outlined class="mr-2" @click="confirmRunPreset = true">
            <v-icon left>mdi-play</v-icon>
            Run preset
          </v-btn>
          <v-btn outlined class="mr-2" @click="retryFailed">
            <v-icon left>mdi-refresh</v-icon>
            Retry ingest
          </v-btn>
          <v-btn outlined class="mr-2" @click="retryPluginSteps">
            <v-icon left>mdi-replay</v-icon>
            Retry plugins
          </v-btn>
          <v-btn outlined class="mr-2" @click="confirmCancel = true">
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

      <v-row class="mb-4">
        <v-col cols="12" md="3">
          <v-select v-model="filter" :items="filters" label="Status" hide-details></v-select>
        </v-col>
        <v-col cols="12" md="5">
          <v-text-field v-model="search" label="Search name or path" hide-details clearable></v-text-field>
        </v-col>
        <v-col cols="12" md="4">
          <v-progress-linear :value="batchProgress" height="24">
            <strong>{{ batchProgress }}%</strong>
          </v-progress-linear>
        </v-col>
      </v-row>

      <v-row v-if="selected.length" class="mb-4" align="center">
        <v-col cols="12">
          <v-toolbar dense flat class="selection-toolbar">
            <span class="text-caption mr-4">{{ selected.length }} selected</span>
            <v-btn small outlined class="mr-2" @click="openSelectedVideos">
              <v-icon left small>mdi-open-in-new</v-icon>
              Open first
            </v-btn>
            <v-btn small outlined class="mr-2" @click="filter = 'ERROR'">
              <v-icon left small>mdi-alert-circle-outline</v-icon>
              Show errors
            </v-btn>
            <v-spacer></v-spacer>
            <v-btn small icon @click="selected = []" title="Clear selection">
              <v-icon small>mdi-close</v-icon>
            </v-btn>
          </v-toolbar>
        </v-col>
      </v-row>

      <v-alert v-if="failedItems.length" dense outlined type="error" class="mb-4">
        {{ failedItems.length }} item{{ failedItems.length === 1 ? "" : "s" }} need attention.
      </v-alert>

      <v-data-table
        v-model="selected"
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
          <td :colspan="headers.length" class="folder-row" @click="toggle">
            <v-icon small class="mr-1">{{ isOpen ? "mdi-folder-open-outline" : "mdi-folder-outline" }}</v-icon>
            {{ group || "Root" }}
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
          <v-card-text>This starts the configured preset for all ready videos in this batch.</v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn text @click="confirmRunPreset = false">Cancel</v-btn>
            <v-btn color="primary" text @click="runPreset">Run</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <v-dialog v-model="confirmCancel" max-width="420">
        <v-card>
          <v-card-title>Cancel batch</v-card-title>
          <v-card-text>Queued work will be marked as cancelled. Already-started analyser work may finish.</v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn text @click="confirmCancel = false">Keep running</v-btn>
            <v-btn color="warning" text @click="cancelBatch">Cancel batch</v-btn>
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

export default {
  data() {
    return {
      filter: "All",
      search: "",
      selected: [],
      timer: null,
      confirmRunPreset: false,
      confirmCancel: false,
      confirmDelete: false,
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
      const preset = this.videoBatchStore.presets.find(
        (entry) => entry.id === this.batch.preset
      );
      return preset ? preset.name : this.batch.preset;
    },
    filteredItems() {
      if (!this.batch || !this.batch.items) return [];
      const search = (this.search || "").toLowerCase();
      return this.batch.items.filter((item) => {
        const pluginStatuses = this.pluginsForItem(item).map((plugin) => plugin.status);
        const matchesFilter =
          this.filter === "All" ||
          item.ingest_status === this.filter ||
          pluginStatuses.includes(this.filter);
        const matchesSearch =
          !search ||
          item.original_path.toLowerCase().includes(search) ||
          item.original_filename.toLowerCase().includes(search);
        return matchesFilter && matchesSearch;
      });
    },
    tableItems() {
      return this.filteredItems.map((item) => {
        const plugin_statuses = {};
        this.pluginsForItem(item).forEach((plugin) => {
          plugin_statuses[plugin.plugin] = plugin;
        });
        const pathParts = item.original_path.split("/");
        const folder_path = pathParts.length > 1 ? pathParts.slice(0, -1).join("/") : "";
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
      await this.videoBatchStore.runPreset({ batchId: this.batchId });
      this.fetchBatch();
    },
    async retryFailed() {
      await this.videoBatchStore.retryFailed(this.batchId);
      this.fetchBatch();
    },
    async retryPluginSteps() {
      await this.videoBatchStore.retryFailedPluginSteps(this.batchId);
      this.fetchBatch();
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
      const firstVideo = this.selected.find((item) => item.video);
      if (firstVideo) {
        this.$router.push({ path: `/videoanalysis/${firstVideo.video.id}` });
      }
    },
  },
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
  cursor: pointer;
  font-weight: 600;
}

.selection-toolbar {
  border: 1px solid #e0e0e0;
}
</style>
