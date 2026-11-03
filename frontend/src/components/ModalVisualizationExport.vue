<template>
  <v-dialog v-model="dialog" max-width="1050" scrollable>
    <v-card>
      <v-card-title>
        Export visualization video
        <v-spacer />
        <v-btn icon aria-label="Close" @click="dialog = false"><v-icon>mdi-close</v-icon></v-btn>
      </v-card-title>
      <v-card-text>
        <p>Combine existing analysis results with the original video. Preview the first five seconds of your selected range before exporting.</p>
        <v-alert v-if="error" type="error" dense dismissible @input="error = ''">{{ error }}</v-alert>
        <v-progress-linear v-if="loading" indeterminate class="mb-4" />
        <v-alert v-if="!loading && !sources.length" type="info" outlined>
          No supported results are available yet. Run an analysis such as Face clustering, OCR, Body pose, or a scalar plugin, or add a transcript first.
        </v-alert>
        <v-select
          v-model="selectedSources" :items="sourceItems" item-text="text" item-value="id"
          label="Results and timelines" multiple chips deletable-chips outlined
          hint="Choose up to six layers and twelve clusters. Separate plugin runs are listed individually."
          persistent-hint :disabled="loading || !!submitting"
        />
        <v-row v-for="layer in layers" :key="layer.source_id" align="center" class="mt-1">
          <v-col cols="12" sm="6">
            <v-text-field v-model="layer.label" label="Display name" maxlength="80" hide-details dense outlined />
          </v-col>
          <v-col cols="8" sm="4">
            <v-select v-model="layer.mode" :items="modes(layer)" label="Show as" hide-details dense outlined />
          </v-col>
          <v-col cols="4" sm="2">
            <label class="visualization-color">Text color <input v-model="layer.color" type="color" aria-label="Layer text color" /></label>
          </v-col>
          <v-col v-if="source(layer).modes" cols="12" class="pt-0">
            <v-alert v-if="source(layer).notice" type="info" dense outlined>{{ source(layer).notice }}</v-alert>
            <template v-if="source(layer).clusters">
              <v-select v-model="layer.cluster_ids" :items="source(layer).clusters" item-text="name" item-value="id"
                label="Clusters to include" multiple chips deletable-chips outlined dense />
              <div class="d-flex flex-wrap" style="gap: 12px">
                <label v-for="cluster in selectedClusters(layer)" :key="cluster.id" class="visualization-color">
                  {{ cluster.name }} <input v-model="layer.cluster_colors[cluster.id]" type="color" :aria-label="`${cluster.name} color`" />
                </label>
              </div>
            </template>
            <div v-if="layer.mode !== 'cluster_cards'" class="d-flex flex-wrap" style="gap: 18px">
              <v-checkbox v-model="layer.show_boxes" label="Boxes" hide-details />
              <v-checkbox v-model="layer.show_labels" label="Labels" hide-details />
              <v-checkbox v-if="source(layer).clusters" v-model="layer.show_legend" label="Cluster cards" hide-details />
              <v-checkbox v-if="source(layer).landmarks_available" v-model="layer.show_landmarks" label="Face landmarks" hide-details />
              <v-checkbox v-if="layer.mode === 'body_pose'" v-model="layer.show_skeleton" label="Skeleton" hide-details />
              <v-checkbox v-if="layer.mode !== 'speaker_tracks'" v-model="layer.smooth" label="Interpolate matched detections" hide-details />
            </div>
            <v-slider v-if="['face_boxes', 'face_expressions', 'body_pose'].includes(layer.mode)" v-model="layer.min_confidence"
              label="Detection confidence" :min="0" :max="1" :step="0.05" thumb-label class="mt-4" hide-details />
            <p v-if="layer.smooth" class="text-caption mt-3 mb-0">Requires a shot timeline. Smooths nearby matched detections; stops at cuts, gaps, and ambiguous matches. It does not recover missing detections.</p>
            <p v-if="layer.mode === 'face_expressions'" class="text-caption mt-3 mb-0">Labels show model predictions of visible facial expressions.</p>
          </v-col>
        </v-row>
        <v-row class="mt-4">
          <v-col cols="6" sm="3"><v-text-field v-model.number="start" label="Start (seconds)" type="number" min="0" step="0.1" outlined dense /></v-col>
          <v-col cols="6" sm="3"><v-text-field v-model.number="end" label="End (seconds)" type="number" :max="duration" min="0" step="0.1" outlined dense /></v-col>
          <v-col cols="12" sm="6">
            <v-select v-model="maxDimension" :items="resolutions" label="Maximum long edge" hint="Keeps the original aspect ratio; never upscales." persistent-hint outlined dense />
          </v-col>
        </v-row>
        <v-row align="center">
          <v-col cols="12" sm="4"><v-slider v-model="fontScale" label="Text size" :min="0.8" :max="1.3" :step="0.1" thumb-label hide-details /></v-col>
          <v-col cols="12" sm="4"><v-slider v-model="opacity" label="Panel opacity" :min="0.2" :max="0.9" :step="0.05" thumb-label hide-details /></v-col>
          <v-col cols="12" sm="4"><v-switch v-model="audio" label="Include original audio" hide-details class="mt-0" /></v-col>
        </v-row>
        <p class="mt-4 text-caption">Values follow stored timestamps. Spatial overlays need results from updated plugin runs. Face and place cards use your current cluster edits. Person track numbers are local to the export and do not identify people across cuts.</p>
        <v-alert v-if="validationMessage" type="info" dense outlined>{{ validationMessage }}</v-alert>
        <div class="d-flex flex-wrap mb-4" style="gap: 12px">
          <v-btn :disabled="!canSubmit" :loading="submitting === 'preview'" @click="createExport(true)"><v-icon left>mdi-play-circle-outline</v-icon>Preview 5 seconds</v-btn>
          <v-btn color="primary" :disabled="!canSubmit" :loading="submitting === 'export'" @click="createExport(false)"><v-icon left>mdi-movie-open-outline</v-icon>Export MP4</v-btn>
        </div>
        <div v-if="viewedJob" class="mb-5">
          <p class="mb-1">{{ viewedJob.preview ? 'Preview' : 'Export' }} · {{ rangeLabel(viewedJob) }} · saved settings</p>
          <video :key="viewedJob.id" :src="fileUrl(viewedJob)" controls preload="metadata" class="visualization-preview" />
          <p class="text-caption">This video uses the settings saved when the job was created. Generate another preview after changing settings.</p>
        </div>
        <v-divider class="my-4" />
        <p class="text-subtitle-1">Recent previews and exports</p>
        <p v-if="!jobs.length" class="text--secondary">Your exports will appear here. Rendering continues when this dialog is closed.</p>
        <v-card v-for="job in jobs" :key="job.id" outlined class="mb-3 pa-3">
          <div class="d-flex align-center flex-wrap" style="gap: 8px">
            <strong>{{ job.preview ? 'Preview' : 'Video export' }}</strong>
            <span>{{ rangeLabel(job) }}</span>
            <v-chip small>{{ job.cancel_requested && job.status === 'running' ? 'Cancelling' : job.status }}</v-chip>
            <v-spacer />
            <span class="text-caption">{{ new Date(job.created_at).toLocaleString() }}</span>
          </div>
          <v-progress-linear v-if="isActive(job)" :value="job.progress * 100" :indeterminate="job.status === 'queued'" class="my-3" />
          <p v-if="isActive(job)" class="text-caption mb-1">{{ Math.round(job.progress * 100) }}%</p>
          <v-alert v-if="job.error" type="error" dense class="mt-2">{{ job.error }}</v-alert>
          <div class="d-flex flex-wrap mt-2" style="gap: 8px">
            <v-btn v-if="isActive(job)" small :disabled="job.cancel_requested" @click="cancelJob(job)">Cancel</v-btn>
            <v-btn v-if="job.status === 'complete'" small @click="viewedJob = job">Play</v-btn>
            <v-btn v-if="job.status === 'complete'" small :href="fileUrl(job, 'download=1')">Download MP4</v-btn>
            <v-btn v-if="job.status === 'complete'" small text :href="fileUrl(job, 'manifest=1')">Data and settings</v-btn>
            <v-btn small text :disabled="!!submitting" @click="reuse(job.configuration)">Reuse settings</v-btn>
          </div>
        </v-card>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<script>
import axios from "@/plugins/axios";
import config from "../../app.config";

const endpoint = `${config.API_LOCATION}/video/visualization`;

export default {
  props: { value: Boolean, videoId: String, duration: Number },
  data() {
    return {
      loading: false, submitting: "", error: "", sources: [], jobs: [],
      selectedSources: [], layers: [], start: 0, end: 0, maxDimension: 1280,
      fontScale: 1, opacity: 0.65, audio: true, viewedJob: null,
      timer: null, generation: 0,
      resolutions: [{ text: "720 px", value: 720 }, { text: "1280 px", value: 1280 }, { text: "1920 px", value: 1920 }],
    };
  },
  computed: {
    dialog: {
      get() { return this.value; },
      set(value) { this.$emit("input", value); },
    },
    sourceItems() {
      return this.sources.map(source => ({ ...source, text: source.date
        ? `${source.name} · ${source.plugin} · ${new Date(source.date).toLocaleString()} · ${source.id.split(':')[1].slice(0, 8)}`
        : source.name }));
    },
    validationMessage() {
      if (!this.layers.length) return "Select at least one result or timeline.";
      if (this.layers.filter(layer => layer.mode === "caption").length > 1) return "Choose one caption timeline at a time.";
      if (this.layers.length > 6) return "Choose at most six layers.";
      if (this.layers.filter(layer => ["scalar", "label"].includes(layer.mode)).length > 5) return "Choose at most five panel rows.";
      if (this.layers.reduce((sum, layer) => sum + (layer.cluster_ids || []).length, 0) > 12) return "Choose at most twelve clusters.";
      if (this.layers.some(layer => this.source(layer).clusters && !layer.cluster_ids?.length)) return "Choose at least one cluster for each clustering layer.";
      if (!Number.isFinite(this.start) || !Number.isFinite(this.end) || this.start < 0 || this.end > this.duration || this.end - this.start < 0.1) return "Choose a valid clip range within the video (at least 0.1 seconds).";
      if (this.layers.some(layer => !layer.label.trim())) return "Give each layer a display name.";
      if (this.jobs.filter(this.isActive).length >= 2) return "Wait for an export to finish, or cancel it first.";
      return "";
    },
    canSubmit() { return !this.loading && !this.submitting && !this.validationMessage; },
  },
  watch: {
    value(open) { if (open) this.load(); else this.stopPolling(); },
    videoId() {
      this.generation++;
      this.stopPolling();
      this.selectedSources = []; this.layers = []; this.jobs = []; this.viewedJob = null;
      this.start = 0; this.end = this.duration || 0;
      if (this.value) this.load();
    },
    selectedSources(ids) {
      this.layers = ids.map(id => this.layers.find(layer => layer.source_id === id) || this.newLayer(id));
    },
  },
  beforeDestroy() { this.generation++; this.stopPolling(); },
  methods: {
    isActive(job) { return ["queued", "running"].includes(job.status); },
    source(layer) { return this.sources.find(source => source.id === layer.source_id) || {}; },
    selectedClusters(layer) { return (this.source(layer).clusters || []).filter(cluster => layer.cluster_ids.includes(cluster.id)); },
    newLayer(id) {
      const source = this.sources.find(source => source.id === id) || {};
      return { source_id: id, label: (source.name || "Result").slice(0, 80), mode: source.modes?.[0] || source.kind || "label", color: "#FFFFFF",
        cluster_ids: (source.clusters || []).slice(0, 6).map(cluster => cluster.id),
        cluster_colors: Object.fromEntries((source.clusters || []).map(cluster => [cluster.id, cluster.color])),
        show_boxes: true, show_labels: true, show_landmarks: false, show_legend: true, show_skeleton: true, smooth: false, min_confidence: 0.6 };
    },
    modes(layer) {
      const names = { cluster_cards: "Cluster cards", face_boxes: "Face boxes", speaker_tracks: "Active speaker", ocr_boxes: "Text boxes", face_expressions: "Per-face expressions", body_pose: "Body pose and people" };
      if (this.source(layer).modes) return this.source(layer).modes.map(value => ({ text: names[value] || value, value }));
      return layer.mode === "scalar" ? [{ text: "Metric panel", value: "scalar" }]
        : [{ text: "Label panel", value: "label" }, { text: "Captions", value: "caption" }];
    },
    rangeLabel(job) {
      const c = job.configuration;
      return `${c.start.toFixed(1)}–${(job.preview ? Math.min(c.end, c.start + 5) : c.end).toFixed(1)}s`;
    },
    fileUrl(job, extra = "") { return `${endpoint}/download?id=${job.id}${extra ? `&${extra}` : ""}`; },
    message(error) { return error.response?.data?.error || "Could not connect. Please try again."; },
    stopPolling() { clearTimeout(this.timer); this.timer = null; },
    async load() {
      const generation = ++this.generation;
      this.stopPolling(); this.loading = true; this.error = "";
      if (!this.end) this.end = this.duration || 0;
      try {
        const { data } = await axios.get(`${endpoint}/sources`, { params: { video_id: this.videoId } });
        if (generation !== this.generation) return;
        this.sources = data.sources; this.jobs = data.jobs;
        this.schedulePoll();
      } catch (error) { if (generation === this.generation) this.error = this.message(error); }
      finally { if (generation === this.generation) this.loading = false; }
    },
    schedulePoll() {
      this.stopPolling();
      if (this.value && this.jobs.some(this.isActive)) this.timer = setTimeout(this.poll, 1500);
    },
    async poll() {
      const generation = this.generation;
      try {
        const results = await Promise.all(this.jobs.filter(this.isActive).map(job => axios.get(`${endpoint}/status`, { params: { id: job.id } })));
        if (generation !== this.generation) return;
        results.forEach(({ data }) => {
          const index = this.jobs.findIndex(job => job.id === data.entry.id);
          if (index >= 0) this.$set(this.jobs, index, data.entry);
          if (data.entry.status === "complete" && data.entry.preview) this.viewedJob = data.entry;
        });
      } catch (error) { if (generation === this.generation) this.error = this.message(error); }
      finally { if (generation === this.generation) this.schedulePoll(); }
    },
    async createExport(preview) {
      const generation = this.generation;
      this.error = ""; this.submitting = preview ? "preview" : "export";
      const configuration = { start: this.start, end: this.end, layers: this.layers,
        max_dimension: this.maxDimension, font_scale: this.fontScale, opacity: this.opacity, audio: this.audio };
      try {
        const { data } = await axios.post(`${endpoint}/create`, { video_id: this.videoId, preview, configuration });
        if (generation !== this.generation) return;
        this.jobs.unshift(data.entry); this.schedulePoll();
      } catch (error) { if (generation === this.generation) this.error = this.message(error); }
      finally { this.submitting = ""; }
    },
    async cancelJob(job) {
      const generation = this.generation;
      try {
        const { data } = await axios.post(`${endpoint}/cancel`, { id: job.id });
        if (generation !== this.generation) return;
        const index = this.jobs.findIndex(entry => entry.id === job.id);
        if (index >= 0) this.$set(this.jobs, index, data.entry);
        this.schedulePoll();
      } catch (error) { if (generation === this.generation) this.error = this.message(error); }
    },
    reuse(configuration) {
      const c = JSON.parse(JSON.stringify(configuration));
      const layers = c.layers.filter(layer => this.sources.some(source => source.id === layer.source_id)).map(layer => ({ ...this.newLayer(layer.source_id), ...layer }));
      this.error = layers.length === c.layers.length ? "" : "Some saved results are no longer available. Select replacements before exporting.";
      this.layers = layers; this.selectedSources = layers.map(layer => layer.source_id);
      this.start = c.start; this.end = c.end; this.maxDimension = c.max_dimension;
      this.fontScale = c.font_scale; this.opacity = c.opacity; this.audio = c.audio;
    },
  },
};
</script>

<style scoped>
.visualization-preview { display: block; width: 100%; max-height: 480px; background: #111; }
.visualization-color { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.visualization-color input { width: 36px; height: 32px; cursor: pointer; }
</style>
