<template>
  <v-dialog v-model="dialog" max-width="760">
    <template v-slot:activator="{ on, attrs }">
      <v-btn color="primary" outlined v-bind="attrs" v-on="on">
        <v-icon left>mdi-folder-upload</v-icon>
        Upload batch
      </v-btn>
    </template>
    <v-card>
      <v-toolbar color="primary" dark>Upload video batch</v-toolbar>
      <v-card-text class="pt-4">
        <v-form>
          <v-text-field v-model="name" label="Batch name"></v-text-field>
          <v-btn-toggle v-model="mode" mandatory class="mb-4">
            <v-btn value="files">
              <v-icon left>mdi-file-multiple</v-icon>
              Videos
            </v-btn>
            <v-btn value="zip">
              <v-icon left>mdi-folder-zip</v-icon>
              Zip
            </v-btn>
          </v-btn-toggle>

          <v-file-input
            v-if="mode === 'files'"
            v-model="files"
            multiple
            accept=".mp4,.mkv,.ogv"
            label="Select video files"
            filled
            prepend-icon="mdi-movie-open"
          ></v-file-input>
          <v-file-input
            v-if="mode === 'zip'"
            v-model="zip"
            accept=".zip"
            label="Select zip archive"
            filled
            prepend-icon="mdi-folder-zip"
          ></v-file-input>

          <v-select
            v-model="preset"
            :items="presetItems"
            item-text="name"
            item-value="id"
            label="Plugin preset"
            clearable
          ></v-select>
          <v-checkbox
            v-model="autoRunPreset"
            :disabled="!preset"
            label="Run preset after ingest"
          ></v-checkbox>

          <div class="text-caption mb-3">
            {{ selectedCount }} files selected, {{ selectedSize }}
          </div>
          <v-progress-linear
            v-if="isUploading"
            :value="uploadingProgress"
            class="mb-3"
          ></v-progress-linear>
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="disabled" @click="upload">
          <v-icon left>mdi-upload</v-icon>
          Upload
        </v-btn>
        <v-spacer></v-spacer>
        <v-btn text @click="dialog = false">Close</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script>
import { mapStores } from "pinia";
import { useVideoBatchStore } from "@/store/video_batch";

export default {
  data() {
    return {
      dialog: false,
      mode: "files",
      name: "",
      files: [],
      zip: null,
      preset: null,
      autoRunPreset: true,
    };
  },
  mounted() {
    this.videoBatchStore.fetchPresets();
  },
  computed: {
    disabled() {
      if (this.isUploading) return true;
      if (this.mode === "zip") return !this.zip;
      return this.files.length === 0;
    },
    selectedCount() {
      return this.mode === "zip" ? (this.zip ? 1 : 0) : this.files.length;
    },
    selectedSize() {
      const size =
        this.mode === "zip"
          ? this.zip
            ? this.zip.size
            : 0
          : this.files.reduce((total, file) => total + file.size, 0);
      if (size > 1024 * 1024 * 1024) return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
      if (size > 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`;
      if (size > 1024) return `${(size / 1024).toFixed(2)} kB`;
      return `${size} B`;
    },
    presetItems() {
      return this.videoBatchStore.presets;
    },
    isUploading() {
      return this.videoBatchStore.isUploading;
    },
    uploadingProgress() {
      return this.videoBatchStore.progress;
    },
    ...mapStores(useVideoBatchStore),
  },
  methods: {
    async upload() {
      const result = await this.videoBatchStore.upload({
        mode: this.mode,
        files: this.files,
        zip: this.zip,
        name: this.name,
        preset: this.preset,
        autoRunPreset: this.autoRunPreset,
      });
      if (result.status === "ok") {
        this.dialog = false;
        this.$router.push({ path: `/batches/${result.batch_id}` });
      }
    },
  },
};
</script>
