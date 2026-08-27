<template>
  <v-dialog v-model="dialog" max-width="90%" style="height: 80vh;">
    <v-card>
      <v-card-title class="mb-0"> {{ $t("modal.plugin.title") }} </v-card-title>
      <v-card-text>
        <v-row>
          <v-col cols="3" style="max-height: 600px; overflow: hidden;">
            <v-sheet class="pa-1" style="background-color: rgb(174, 19, 19) !important;">
              <v-text-field v-model="search" label="Search Plugin" class="searchField" dark flat solo-inverted
                hide-details clearable clear-icon="mdi-close-circle-outline">
              </v-text-field>
            </v-sheet>
            <v-treeview :items="plugins_sorted" :search="search" :open.sync="open" activatable open-all
              style="cursor: pointer; overflow-y: scroll; height: 500px;" :active.sync="active">
              <template v-slot:prepend="{ item }">
                <v-icon>{{ item.icon }}</v-icon>
              </template>
            </v-treeview>
          </v-col>
          <v-col cols="9">
            <div v-if="!selected" class="text-h6 grey--text font-weight-light" style="text-align: center;">
              {{ $t("modal.plugin.search.select") }}
            </div>
            <v-card v-else :key="selected.id" class="mx-auto overflow-y-auto" style="max-height: calc(80vh - 50px);"
              flat>
              <v-card-title class="mb-0"> {{ selected.name }} </v-card-title>
              <v-card-text>
                <div class="" style="padding-bottom: 2em;" v-html="selected.description"></div>
                <Parameters :parameters="selected.parameters" :videoIds="videoIds"> </Parameters>
                <v-expansion-panels v-if="selected.optional_parameters &&
    selected.optional_parameters.length > 0
    ">
                  <v-expansion-panel>
                    <v-expansion-panel-header expand-icon="mdi-menu-down">
                      Advanced Options
                    </v-expansion-panel-header>

                    <v-expansion-panel-content>
                      <Parameters :parameters="selected.optional_parameters" :videoIds="videoIds">
                      </Parameters>
                    </v-expansion-panel-content>
                  </v-expansion-panel>
                </v-expansion-panels>
              </v-card-text>
              <v-card-actions class="pt-0">
              </v-card-actions>
            </v-card>
          </v-col>
        </v-row>
      </v-card-text>
      <v-card-actions class="pt-0">
        <v-btn @click="dialog = false">{{ $t("modal.plugin.close") }}</v-btn>
        <v-spacer></v-spacer>
        <v-btn v-if="selected" @click="
    runPlugin(
      selected.plugin,
      selected.parameters,
      selected.optional_parameters
    )
    ">{{ $t("modal.plugin.run") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script>
import { mapStores } from "pinia";
import { usePluginRunStore } from "@/store/plugin_run";
import { useVideoBatchStore } from "@/store/video_batch";
import { clonePluginCatalog, flattenPluginCatalog } from "@/plugins/plugin_catalog";
import Parameters from "./Parameters.vue";
// import { useTimelineStore } from "../store/timeline";

export default {
  props: ["value", "videoIds"],
  data() {
    return {
      dialog: false,
      open: [1, 2],
      search: null,
      active: [],
    };
  },
  async mounted() {
    await this.videoBatchStore.fetchPluginCatalog();
  },
  computed: {
    plugins() {
      return clonePluginCatalog(this.videoBatchStore.pluginCatalog);
    },
    plugins_sorted() {
      return this.plugins.slice(0).sort((a, b) => a.name.localeCompare(b.name));
    },
    selected() {
      if (!this.active.length) return undefined;

      const id = this.active[0];
      if (id < 100) return undefined;

      return flattenPluginCatalog(this.videoBatchStore.pluginCatalog).find((plugin) => plugin.id === id);
    },
    filter() {
      return (item, search, textKey) => item[textKey].indexOf(search) > -1;
    },
    ...mapStores(usePluginRunStore, useVideoBatchStore),
  },
  methods: {
    async runPlugin(plugin, parameters, optional_parameters) {
      parameters = parameters.concat(optional_parameters);
      parameters = parameters.map((e) => {
        if ("file" in e) {
          return { name: e.name, file: e.file };
        } else {
          return { name: e.name, value: e.value };
        }
      });
      for (const video of this.videoIds) {
        const video_params = []
        // if multiple videos were selected, choose the correct timeline in parameters
        for (const param of parameters) {
          if (param.name === 'shot_timeline_id' || param.name == 'scalar_timeline_id') {
            video_params.push({
              name: param.name,
              value: param.value.timeline_ids[param.value.video_ids.indexOf(video)]
            });
          } else if (param.name === 'timeline_ids') {
            video_params.push({
              name: param.name,
              value: param.value.map(t => t.timeline_ids[t.video_ids.indexOf(video)])
            })
          } else {
            video_params.push(param);
          }
        }
        this.pluginRunStore
          .submit({ plugin: plugin, parameters: video_params, videoId: video })
          .then(() => {
            this.dialog = false;
          });
      }
    },
  },
  watch: {
    dialog(value) {
      this.$emit("input", value);
    },
    value(value) {
      if (value) {
        this.dialog = true;
      }
    },
  },
  components: { Parameters },
};
</script>

<style>
div.tabs-left [role="tab"] {
  justify-content: flex-start;
}
</style>
