<template>
  <v-dialog v-model="dialog" max-width="90%" min-width="600px" :fullscreen="$vuetify.breakpoint.xsOnly">
    <template v-slot:activator="{ on, attrs }">
      <slot name="activator" :on="on" :attrs="attrs">
        <v-btn tile text v-bind="attrs" v-on="on">
          <v-icon>{{ "mdi-plus" }}</v-icon>
          {{ $t("modal.plugin.link") }}
        </v-btn>
      </slot>
    </template>

    <v-card height="80vh" class="d-flex flex-column">
      <v-card-title>
        {{ $t("modal.plugin.title") }}

        <v-btn icon @click.native="dialog = false" absolute top right>
          <v-icon>mdi-close</v-icon>
        </v-btn>

        <v-tabs v-model="tab" fixed-tabs>
          <v-tabs-slider></v-tabs-slider>

          <v-tab> {{ $t("modal.plugin.tab_legal") }} </v-tab>
          <v-tab :disabled="!checkbox">
            {{ $t("modal.plugin.tab_plugin") }}
          </v-tab>
        </v-tabs>
      </v-card-title>
      <v-card-text style="overflow-y: scroll">
        <v-tabs-items v-model="tab">
          <v-tab-item class="scroll">
            <h1 class="mt-2">{{ $t("terms.title") }}</h1>
            <p v-html="$t('terms.content')"></p>
            <v-form class="terms-input">
              <v-checkbox v-model="checkbox" label="Do you agree with the terms of services?" required>
              </v-checkbox>
            </v-form>
          </v-tab-item>
          <v-tab-item style="height: 100%">
            <v-row style="height: 100%">
              <v-col cols="3" style="height: 100%; display: flex; flex-direction: column">
                <v-sheet class="pa-1" style="background-color: rgb(174, 19, 19) !important">
                  <v-text-field v-model="search" label="Search Plugin" class="searchField" dark flat solo-inverted
                    hide-details clearable clear-icon="mdi-close-circle-outline">
                  </v-text-field>
                </v-sheet>
                <v-treeview :items="plugins_sorted" :search="search" :open.sync="open" activatable open-all
                  style="cursor: pointer; overflow-y: scroll" :active.sync="active">
                  <template v-slot:prepend="{ item }">
                    <v-icon>{{ item.icon }}</v-icon>
                  </template>
                </v-treeview>
              </v-col>
              <v-col cols="9" style="height: 100%; display: flex; flex-direction: column">
                <div v-if="!selected" class="text-h6 grey--text font-weight-light" style="text-align: center">
                  {{ $t("modal.plugin.search.select") }}
                </div>
                <v-card v-else :key="selected.id" class="mx-auto overflow-y-auto" style="height: 100%" flat>
                  <v-card-title class="mb-0">
                    {{ selected.name }}
                  </v-card-title>
                  <v-card-text>
                    <div style="padding-bottom: 2em" v-html="selected.description"></div>
                    <v-alert v-if="missingShotTimeline" type="info" dense>
                      Select a shot timeline for every selected video. Run shot detection first if needed.
                    </v-alert>
                    <Parameters :parameters="selected.parameters" :videoIds="videoIds">
                    </Parameters>
                    <v-expansion-panels v-if="
                      selected.optional_parameters &&
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
                  <v-card-actions class="pt-0"> </v-card-actions>
                </v-card>
              </v-col>
            </v-row>
          </v-tab-item>
        </v-tabs-items>
      </v-card-text>

      <v-spacer></v-spacer>
      <v-card-actions class="pt-0">
        <v-btn v-if="tab == 0" class="mr-4" :disabled="!checkbox" @click="tab++">
          {{ $t("modal.plugin.continue") }}
        </v-btn>
        <v-btn v-if="tab == 1" :disabled="!selected || missingShotTimeline" @click="
          runPlugin(
            selected.plugin,
            selected.parameters,
            selected.optional_parameters
          )
          ">{{ $t("modal.plugin.run") }}</v-btn>
        <v-btn @click="dialog = false">{{ $t("modal.plugin.close") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script>
import { mapStores } from "pinia";
import { usePluginRunStore } from "@/store/plugin_run";
import { useVideoBatchStore } from "@/store/video_batch";
import { clonePluginCatalog, flattenPluginCatalog } from "@/plugins/plugin_catalog";
import { usePluginStore } from "@/store/plugin";
import { useTimelineStore } from "@/store/timeline";
import Parameters from "./Parameters.vue";
// import { useTimelineStore } from "../store/timeline";

export default {
  props: ["value", "videoIds"],
  data() {
    return {
      dialog: false,
      checkbox: false,
      tab: null,
      open: [1, 2],
      search: null,
      active: [],
    };
  },
  async mounted() {
    await Promise.all([
      this.videoBatchStore.fetchPluginCatalog(),
      this.pluginStore.fetchAll(),
    ]);
  },
  computed: {
    missingShotTimeline() {
      if (!this.selected) return false;
      return this.selected.parameters.some(parameter =>
        parameter.field === "select_timeline" &&
        (!parameter.value || !parameter.value.video_ids ||
          !this.videoIds.every(id => parameter.value.video_ids.includes(id)))
      );
    },
    plugins() {
      return clonePluginCatalog(this.videoBatchStore.pluginCatalog);
    },
    plugins_sorted() {
      const result = this.plugins.slice(0).sort((a, b) => a.name.localeCompare(b.name));
      const allowedPlugins = this.pluginStore.all;
      const filteredPlugins = result.map(category => ({
        ...category,
        children: category.children.filter(child =>
          allowedPlugins.some(allowed => allowed.name === child.plugin)
        )
      })).filter(category => category.children.length > 0);
      return filteredPlugins;
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
    ...mapStores(usePluginRunStore, useVideoBatchStore, usePluginStore, useTimelineStore),
  },
  methods: {
    async runPlugin(plugin, parameters, optional_parameters) {
      if (this.missingShotTimeline) return;
      parameters = parameters.concat(optional_parameters);
      parameters = parameters.map((e) => {
        if ("file" in e) {
          return { name: e.name, file: e.file };
        } else {
          return { name: e.name, value: e.value };
        }
      });
      for (const video of this.videoIds) {
        const video_params = [];
        // if multiple videos were selected, choose the correct timeline in parameters
        for (const param of parameters) {
          if (
            param.name === "shot_timeline_id" ||
            param.name == "scalar_timeline_id"
          ) {
            video_params.push({
              name: param.name,
              value:
                param.value.timeline_ids[param.value.video_ids.indexOf(video)],
            });
          } else if (param.name === "timeline_ids") {
            video_params.push({
              name: param.name,
              value: param.value.map(
                (t) => t.timeline_ids[t.video_ids.indexOf(video)]
              ),
            });
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
      if (value) this.timelineStore.fetchAll({ addResultsType: true });
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

.scroll {
  overflow-y: scroll;
}

.terms-input {
  margin-bottom: 10px;
  margin-left: 10px;
}
</style>
