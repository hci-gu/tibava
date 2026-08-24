import axios from "../plugins/axios";
import config from "../../app.config";
import { defineStore } from "pinia";
import Vue from "vue";

export const useVideoBatchStore = defineStore("videoBatch", {
  state: () => {
    return {
      batches: {},
      batchList: [],
      presets: [],
      isLoading: false,
      isUploading: false,
      progress: 0,
    };
  },
  getters: {
    all(state) {
      return state.batchList.map((id) => state.batches[id]);
    },
    get(state) {
      return (id) => state.batches[id];
    },
  },
  actions: {
    async fetchPresets() {
      return axios.get(`${config.API_LOCATION}/video/batch/presets`).then((res) => {
        if (res.data.status === "ok") {
          this.presets = res.data.entries;
        }
      });
    },
    async fetchAll() {
      if (this.isLoading) return;
      this.isLoading = true;
      return axios
        .get(`${config.API_LOCATION}/video/batch/list`)
        .then((res) => {
          if (res.data.status === "ok") {
            this.batches = {};
            this.batchList = [];
            res.data.entries.forEach((batch) => {
              Vue.set(this.batches, batch.id, batch);
              this.batchList.push(batch.id);
            });
          }
        })
        .finally(() => {
          this.isLoading = false;
        });
    },
    async fetch(batchId) {
      if (this.isLoading) return;
      this.isLoading = true;
      return axios
        .get(`${config.API_LOCATION}/video/batch/get`, { params: { id: batchId } })
        .then((res) => {
          if (res.data.status === "ok") {
            Vue.set(this.batches, res.data.entry.id, res.data.entry);
            if (!this.batchList.includes(res.data.entry.id)) {
              this.batchList.push(res.data.entry.id);
            }
          }
        })
        .finally(() => {
          this.isLoading = false;
        });
    },
    async upload({ mode, files = [], zip = null, name, preset, autoRunPreset }) {
      const formData = new FormData();
      formData.append("name", name || "");
      if (preset) {
        formData.append("preset", preset);
      }
      formData.append("auto_run_preset", autoRunPreset ? "true" : "false");

      if (mode === "zip") {
        formData.append("zip", zip);
      } else {
        files.forEach((file) => {
          formData.append("files", file);
        });
        formData.append(
          "paths",
          JSON.stringify(files.map((file) => file.webkitRelativePath || file.name))
        );
      }

      this.isUploading = true;
      return axios
        .post(`${config.API_LOCATION}/video/batch/upload`, formData, {
          headers: { "Content-Type": "multipart/form-data" },
          onUploadProgress: (event) => {
            if (event.total) {
              this.progress = Math.round((event.loaded * 100) / event.total);
            }
          },
        })
        .then((res) => res.data)
        .finally(() => {
          this.isUploading = false;
          this.progress = 0;
        });
    },
    async runPreset({ batchId, preset = null }) {
      return axios.post(`${config.API_LOCATION}/video/batch/run-preset`, {
        id: batchId,
        preset,
      });
    },
    async retryFailed(batchId) {
      return axios.post(`${config.API_LOCATION}/video/batch/retry-failed`, {
        id: batchId,
      });
    },
    async retryFailedPluginSteps(batchId) {
      return axios.post(
        `${config.API_LOCATION}/video/batch/retry-failed-plugin-steps`,
        { id: batchId }
      );
    },
    async delete(batchId) {
      return axios
        .post(`${config.API_LOCATION}/video/batch/delete`, { id: batchId })
        .then((res) => {
          if (res.data.status === "ok") {
            this.batchList = this.batchList.filter((id) => id !== batchId);
            Vue.delete(this.batches, batchId);
          }
        });
    },
    async cancel(batchId) {
      return axios.post(`${config.API_LOCATION}/video/batch/cancel`, { id: batchId });
    },
  },
});
