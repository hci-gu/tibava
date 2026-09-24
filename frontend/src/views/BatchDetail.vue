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
            :disabled="!hasReadyVideos || videoBatchStore.isExportingElan"
            :loading="videoBatchStore.isExportingElan"
            @click="exportElan(true)"
          >
            <v-icon left>mdi-file-export-outline</v-icon>
            Export ELAN
          </v-btn>
          <v-btn
            outlined
            class="mr-2"
            :disabled="!hasReadyVideos || videoBatchStore.isExportingElan"
            :loading="videoBatchStore.isExportingElan"
            @click="exportElan(false)"
          >
            <v-icon left>mdi-file-export-outline</v-icon>
            Export raw ELAN
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

      <v-alert v-if="videoBatchStore.isExportingElan" dense outlined type="info" class="mb-4">
        <div class="mb-2">{{ videoBatchStore.elanExportMessage }}</div>
        <v-progress-linear :value="videoBatchStore.elanExportProgress" height="18">
          <strong>{{ videoBatchStore.elanExportProgress }}%</strong>
        </v-progress-linear>
        <div class="text-caption mt-2">
          {{ videoBatchStore.elanExportExported }} exported,
          {{ videoBatchStore.elanExportFailed }} failed.
          This can take several minutes for large batches.
        </div>
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
            <span class="text-caption mr-4">{{ selectedItemIds.length }} selected · {{ itemsTotal }} matches</span>
            <v-btn small outlined class="mr-2" @click="selectVisible">
              <v-icon left small>mdi-checkbox-multiple-marked-outline</v-icon>
              Select page
            </v-btn>
            <v-btn small outlined class="mr-2" @click="invertVisibleSelection">
              <v-icon left small>mdi-checkbox-multiple-blank-outline</v-icon>
              Invert visible
            </v-btn>
            <v-btn small outlined class="mr-2" :disabled="folderFilter === null" @click="selectCurrentFolder">
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

      <v-alert v-if="batch.failed_count" dense outlined type="error" class="mb-4">
        {{ batch.failed_count }} item{{ batch.failed_count === 1 ? "" : "s" }} need attention.
      </v-alert>

      <v-alert v-if="itemsError" dense outlined type="error" class="mb-4">
        {{ itemsError }}
      </v-alert>

      <v-data-table
        v-model="selectedRows"
        :headers="tableHeaders"
        :items="tableItems"
        :loading="itemsLoading"
        :options.sync="tableOptions"
        :server-items-length="itemsTotal"
        :footer-props="{ 'items-per-page-options': [25, 50, 100] }"
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
              {{ group || "Root" }} ({{ folderItems(group).length }} on page)
            </span>
            <v-btn icon small title="Filter folder" @click.stop="folderFilter = group">
              <v-icon small>mdi-filter-outline</v-icon>
            </v-btn>
            <v-btn
              icon
              small
              title="Run plugins for folder"
              :disabled="!canRunBatchPlugins || !readyCountForFolder(group)"
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
        <template v-slot:item.display_path="{ item }">
          <span class="path-cell">{{ item.display_path }}</span>
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
                :label="`Current folder (${currentFolderReadyCount})`"
                value="folder"
                :disabled="folderFilter === null || !currentFolderReadyCount"
              ></v-radio>
              <v-radio
                :label="`Filtered ready videos (${readyFilteredCount})`"
                value="filtered"
                :disabled="!readyFilteredCount"
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
        :scope-selection="customPluginScope"
        :scope-count="customPluginScopeCount"
        :scope-label="customPluginScopeLabel"
        @ran="refreshBatch"
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
      selectedItemDetails: {},
      pageItems: [],
      pagePluginRuns: [],
      itemsTotal: 0,
      readyFilteredCount: 0,
      itemsLoading: false,
      itemsError: "",
      tableOptions: {
        page: 1,
        itemsPerPage: 50,
        sortBy: [],
        sortDesc: [],
      },
      pageRequestSerial: 0,
      lastPageQuery: null,
      searchTimer: null,
      refreshInProgress: false,
      presetScopeMode: "all",
      presetToRun: null,
      showCustomPluginSet: false,
      customPluginItemIds: [],
      customPluginScope: null,
      customPluginScopeCount: 0,
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
    this.fetchSummary();
    this.fetchPage();
    this.timer = setInterval(() => {
      if (this.shouldPoll) this.refreshBatch();
    }, 10000);
  },
  beforeDestroy() {
    if (this.timer) clearInterval(this.timer);
    if (this.searchTimer) clearTimeout(this.searchTimer);
    this.pageRequestSerial += 1;
  },
  watch: {
    batchId() {
      this.pageItems = [];
      this.pagePluginRuns = [];
      this.itemsTotal = 0;
      this.selectedItemIds = [];
      this.selectedItemDetails = {};
      this.lastPageQuery = null;
      this.tableOptions.page = 1;
      this.fetchSummary();
      this.fetchPage();
    },
    filter() {
      this.resetPage();
    },
    folderFilter() {
      this.resetPage();
    },
    search() {
      if (this.searchTimer) clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.resetPage(), 300);
    },
    tableOptions: {
      deep: true,
      handler() {
        this.fetchPage();
      },
    },
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
    pluginsByItem() {
      const plugins = {};
      this.pagePluginRuns.forEach((plugin) => {
        if (!plugins[plugin.item_id]) plugins[plugin.item_id] = [];
        plugins[plugin.item_id].push(plugin);
      });
      return plugins;
    },
    tableItems() {
      return this.pageItems.map((item) => {
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
      return (this.batch && this.batch.folders ? this.batch.folders : [])
        .slice()
        .sort((a, b) => a.path.localeCompare(b.path))
        .map(({ path, count }) => ({
          value: path,
          label: `${path || "Root"} (${count})`,
        }));
    },
    selectedRows: {
      get() {
        const selected = new Set(this.selectedItemIds);
        return this.tableItems.filter((item) => selected.has(item.id));
      },
      set(rows) {
        const pageIds = new Set(this.pageItems.map((item) => item.id));
        const selected = new Set(rows.map((row) => row.id));
        const ids = new Set(this.selectedItemIds.filter((id) => !pageIds.has(id)));
        selected.forEach((id) => ids.add(id));
        this.selectedItemIds = Array.from(ids);
        this.pageItems.forEach((item) => {
          if (selected.has(item.id)) this.$set(this.selectedItemDetails, item.id, item);
          else this.$delete(this.selectedItemDetails, item.id);
        });
      },
    },
    selectedItems() {
      return this.selectedItemIds
        .map((id) => this.selectedItemDetails[id])
        .filter(Boolean);
    },
    readySelectedItemIds() {
      return this.selectedItems
        .filter((item) => item.ingest_status === "READY" && item.video_id)
        .map((item) => item.id);
    },
    currentFolderReadyCount() {
      return this.folderFilter === null ? 0 : this.readyCountForFolder(this.folderFilter);
    },
    pluginColumns() {
      return this.batch && this.batch.plugin_columns ? this.batch.plugin_columns : [];
    },
    tableHeaders() {
      return [
        { text: "Open", value: "video_link", sortable: false, width: 56 },
        { text: "Path", value: "display_path" },
        { text: "File", value: "display_filename" },
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
        { text: "Error", value: "error_summary", sortable: false },
      ];
    },
    hasReadyVideos() {
      return Boolean(this.batch && this.batch.ready_count);
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
        count: (this.batch.item_status_counts || {})[status] || 0,
      }));
      pluginStatuses.forEach((status) => {
        summaries.push({
          key: `plugin-${status}`,
          label: status,
          count: (this.batch.plugin_status_counts || {})[status] || 0,
        });
      });
      return summaries;
    },
    shouldPoll() {
      if (!this.batch || this.batch.status === "CANCELLED") return false;
      const pluginCounts = this.batch.plugin_status_counts || {};
      return ["UPLOADING", "INGESTING", "RUNNING"].includes(this.batch.status) ||
        Boolean(pluginCounts.RUNNING || pluginCounts.PENDING);
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
    async fetchSummary() {
      try {
        await this.videoBatchStore.fetch(this.batchId, { summary: true });
      } catch (error) {
        this.itemsError = "The batch summary could not be loaded.";
      }
    },
    pageParams() {
      const options = this.tableOptions;
      const params = {
        page: options.page || 1,
        page_size: options.itemsPerPage || 50,
        sort_by: options.sortBy && options.sortBy[0] || "original_path",
        sort_desc: Boolean(options.sortDesc && options.sortDesc[0]),
        status: this.filter,
        search: this.search || "",
      };
      if (this.folderFilter !== null) params.folder = this.folderFilter;
      return params;
    },
    async fetchPage(force = false) {
      const batchId = this.batchId;
      const params = this.pageParams();
      const queryKey = `${batchId}:${JSON.stringify(params)}`;
      if (!force && queryKey === this.lastPageQuery) return;
      this.lastPageQuery = queryKey;
      const serial = ++this.pageRequestSerial;
      this.itemsLoading = true;
      this.itemsError = "";
      try {
        const data = await this.videoBatchStore.fetchItems(batchId, params);
        if (serial !== this.pageRequestSerial || batchId !== this.batchId) return;
        this.pageItems = data.items;
        this.pagePluginRuns = data.plugin_runs;
        this.itemsTotal = data.total;
        this.readyFilteredCount = data.ready_count;
        if (data.page !== this.tableOptions.page) this.tableOptions.page = data.page;
        this.pageItems.forEach((item) => {
          if (this.selectedItemIds.includes(item.id)) {
            this.$set(this.selectedItemDetails, item.id, item);
          }
        });
      } catch (error) {
        if (serial !== this.pageRequestSerial) return;
        this.lastPageQuery = null;
        this.itemsError = "The batch items could not be loaded.";
      } finally {
        if (serial === this.pageRequestSerial) this.itemsLoading = false;
      }
    },
    resetPage() {
      this.tableOptions.page = 1;
      this.fetchPage();
    },
    async refreshBatch() {
      if (this.refreshInProgress) return;
      this.refreshInProgress = true;
      try {
        await Promise.all([this.fetchSummary(), this.fetchPage(true)]);
      } finally {
        this.refreshInProgress = false;
      }
    },
    folderPathForItem(item) {
      const pathParts = (item.display_path || item.original_path || "").split("/");
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
    async toggleFolderSelection(group) {
      const state = this.folderSelectionState(group);
      const ids = new Set(this.selectedItemIds);
      let entries;
      try {
        entries = await this.videoBatchStore.fetchItemIds(this.batchId, {
          ...this.pageParams(),
          folder: group || "",
          exact_folder: true,
        });
      } catch (error) {
        this.itemsError = "The folder selection could not be loaded.";
        return;
      }
      entries.forEach((item) => {
        if (state.all) {
          ids.delete(item.id);
          this.$delete(this.selectedItemDetails, item.id);
        } else {
          ids.add(item.id);
          this.$set(this.selectedItemDetails, item.id, item);
        }
      });
      this.selectedItemIds = Array.from(ids);
    },
    selectVisible() {
      const ids = new Set(this.selectedItemIds);
      this.pageItems.forEach((item) => {
        ids.add(item.id);
        this.$set(this.selectedItemDetails, item.id, item);
      });
      this.selectedItemIds = Array.from(ids);
    },
    invertVisibleSelection() {
      const ids = new Set(this.selectedItemIds);
      this.tableItems.forEach((item) => {
        if (ids.has(item.id)) {
          ids.delete(item.id);
          this.$delete(this.selectedItemDetails, item.id);
        } else {
          ids.add(item.id);
          this.$set(this.selectedItemDetails, item.id, item);
        }
      });
      this.selectedItemIds = Array.from(ids);
    },
    async selectCurrentFolder() {
      const ids = new Set(this.selectedItemIds);
      let entries;
      try {
        entries = await this.videoBatchStore.fetchItemIds(this.batchId, {
          folder: this.folderFilter,
        });
      } catch (error) {
        this.itemsError = "The folder selection could not be loaded.";
        return;
      }
      entries.forEach((item) => {
        ids.add(item.id);
        this.$set(this.selectedItemDetails, item.id, item);
      });
      this.selectedItemIds = Array.from(ids);
    },
    clearSelection() {
      this.selectedItemIds = [];
      this.selectedItemDetails = {};
    },
    pluginsForItem(item) {
      return this.pluginsByItem[item.id] || [];
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
      this.refreshBatch();
    },
    async openRunPresetDialog() {
      await this.videoBatchStore.fetchPresets();
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
        return { type: "folder", folder_path: this.folderFilter, include_subfolders: true };
      }
      if (this.presetScopeMode === "filtered") {
        return {
          type: "filtered",
          status: this.filter,
          folder: this.folderFilter,
          search: this.search || "",
        };
      }
      return { type: "all" };
    },
    async retryFailed() {
      await this.videoBatchStore.retryFailed(this.batchId);
      this.refreshBatch();
    },
    async retryPluginSteps() {
      await this.videoBatchStore.retryFailedPluginSteps(this.batchId);
      this.refreshBatch();
    },
    async exportElan(applyFiltering = true) {
      this.exportError = "";
      try {
        await this.videoBatchStore.exportElan(
          this.batchId,
          this.batch.name,
          applyFiltering,
        );
      } catch (error) {
        this.exportError = "The ELAN batch export could not be created.";
      }
    },
    async cancelBatch() {
      this.confirmCancel = false;
      await this.videoBatchStore.cancel(this.batchId);
      this.refreshBatch();
    },
    async deleteBatch() {
      this.confirmDelete = false;
      await this.videoBatchStore.delete(this.batchId);
      this.$router.push({ path: "/batches" });
    },
    openSelectedVideos() {
      const firstVideo = this.selectedItems.find((item) => item.video_id);
      if (firstVideo) {
        this.$router.push({ path: `/videoanalysis/${firstVideo.video_id}` });
      }
    },
    openCustomPluginSetForAll() {
      this.customPluginItemIds = [];
      this.customPluginScope = { type: "all" };
      this.customPluginScopeCount = this.batch.ready_count;
      this.customPluginScopeLabel = "all ready videos";
      this.showCustomPluginSet = true;
    },
    openCustomPluginSetForSelection() {
      this.customPluginItemIds = this.readySelectedItemIds;
      this.customPluginScope = null;
      this.customPluginScopeCount = this.readySelectedItemIds.length;
      this.customPluginScopeLabel = "selected ready videos";
      this.showCustomPluginSet = true;
    },
    openCustomPluginSetForFolder(group) {
      const folder = group || "";
      this.customPluginItemIds = [];
      this.customPluginScope = {
        type: "folder",
        folder_path: folder,
        include_subfolders: true,
      };
      this.customPluginScopeCount = this.readyCountForFolder(folder);
      this.customPluginScopeLabel = folder ? `folder ${folder} and subfolders` : "root folder";
      this.showCustomPluginSet = true;
    },
    readyCountForFolder(folderPath) {
      if (!this.batch || !this.batch.folders) return 0;
      return this.batch.folders
        .filter((folder) =>
          folderPath
            ? folder.path === folderPath || folder.path.startsWith(`${folderPath}/`)
            : folder.path === ""
        )
        .reduce((count, folder) => count + folder.ready_count, 0);
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
