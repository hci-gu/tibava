<template>
  <v-main>
    <v-container v-if="batch" fluid class="py-8 px-6">
      <v-row align="center" class="mb-4">
        <v-col>
          <h1 class="text-h5 mb-1">{{ batch.name }}</h1>
          <div class="text-caption">{{ batch.status }} · {{ batch.total_count }} videos</div>
        </v-col>
        <v-col cols="auto">
          <v-btn outlined class="mr-2" @click="runPreset">
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
          <v-btn outlined class="mr-2" @click="cancelBatch">
            <v-icon left>mdi-stop-circle-outline</v-icon>
            Cancel
          </v-btn>
          <v-btn color="red" outlined @click="deleteBatch">
            <v-icon left>mdi-trash-can-outline</v-icon>
            Delete
          </v-btn>
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

      <v-data-table
        :headers="headers"
        :items="filteredItems"
        :loading="videoBatchStore.isLoading"
        item-key="id"
        dense
      >
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
        <template v-slot:item.plugins="{ item }">
          <v-chip
            v-for="plugin in pluginsForItem(item)"
            :key="plugin.id"
            x-small
            class="mr-1"
            :color="statusColor(plugin.status)"
            dark
          >
            {{ plugin.plugin }}: {{ plugin.status }}
          </v-chip>
        </template>
      </v-data-table>
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
      timer: null,
      filters: ["All", "PENDING", "INGESTING", "READY", "ERROR", "RUNNING", "DONE"],
      headers: [
        { text: "Path", value: "original_path" },
        { text: "File", value: "original_filename" },
        { text: "Ingest", value: "ingest_status" },
        { text: "Uploaded", value: "date" },
        { text: "Duration", value: "video.duration", sortable: false },
        { text: "Resolution", value: "video.resolution", sortable: false },
        { text: "Plugins", value: "plugins", sortable: false },
        { text: "Error", value: "ingest_error" },
      ],
    };
  },
  mounted() {
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
      if (status === "CANCELLED") return "grey";
      if (status === "READY" || status === "DONE") return "green";
      if (status === "RUNNING" || status === "INGESTING") return "blue";
      return "orange";
    },
    async runPreset() {
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
      await this.videoBatchStore.cancel(this.batchId);
      this.fetchBatch();
    },
    async deleteBatch() {
      await this.videoBatchStore.delete(this.batchId);
      this.$router.push({ path: "/batches" });
    },
  },
};
</script>

<style scoped>
.path-cell {
  font-family: monospace;
  font-size: 12px;
}
</style>
