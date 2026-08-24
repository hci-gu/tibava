import Vue from 'vue';
import axios from '../plugins/axios';
import config from '../../app.config';
import { defineStore } from 'pinia';
import { usePlayerStore } from "@/store/player";

export const usePluginRunResultStore = defineStore('pluginRunResult', {
    state: () => {
        return {
            pluginRunResults: {},
            pluginRunResultList: [],
            isLoading: false,
        }
    },
    getters: {
        get: (state) => (id) => {
            return state.pluginRunResults[id];
        },
        all: (state) => {
            return state.pluginRunResultList;
        },
        forPlugin: (state) => (id) => {
            return state.pluginRunResults[id];
        },
        forPluginRun: (state) => (plugin_run_id) => {
            return state.pluginRunResultList
                .map((id) => state.pluginRunResults[id])
                .filter((e) => e.plugin_run_id === plugin_run_id);
        },
    },
    actions: {
        async fetchForVideo({ addResults = false, videoId = null, pluginRunId = null, force = false }) {
            if (this.isLoading && !force) {
                return
            }
            this.isLoading = true

            const params = {
                add_results: addResults,
            }

            //use video id or take it from the current video
            if (videoId) {
                params.video_id = videoId;
            } else {

                const playerStore = usePlayerStore();
                const videoId = playerStore.videoId;
                if (videoId) {
                    params.video_id = videoId;
                }
            }

            if (pluginRunId) {
                params.plugin_run_id = pluginRunId;
            }

            return axios.get(`${config.API_LOCATION}/plugin/run/result/list`, { params })
                .then((res) => {
                    if (res.data.status === 'ok') {
                        this.updateAll(res.data.entries);
                    }
                })
                .finally(() => {
                    this.isLoading = false;
                });
            // .catch((error) => {
            //   const info = { date: Date(), error, origin: 'collection' };
            //   commit('error/update', info, { root: true });
            // });
        },
        clearStore() {
            this.pluginRunResults = {}
            this.pluginRunResultList = []
        },
        deleteForPluginRuns(id_list) {
            id_list.forEach((id) => {
                let results = this.forPluginRun(id);
                results.forEach((result) => {
                    const result_id = result.id;
                    let index = this.pluginRunResultList.findIndex((item) => item === result_id);
                    if (index >= 0) {
                        this.pluginRunResultList.splice(index, 1);
                    }
                    Vue.delete(this.pluginRunResults, result_id);
                })
              }
            )
          },
        updateAll(pluginRunResults) {
            pluginRunResults.forEach((e) => {
                if (e.id in this.pluginRunResults) {
                    Vue.set(this.pluginRunResults, e.id, {
                        ...this.pluginRunResults[e.id],
                        ...e,
                    });
                } else {
                    Vue.set(this.pluginRunResults, e.id, e);
                    this.pluginRunResultList.push(e.id);
                }
            });
        },
    },
})
