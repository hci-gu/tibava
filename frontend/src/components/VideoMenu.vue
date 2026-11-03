<template>
  <div>
    <v-menu min-width="175" offset-y bottom left>
      <!-- open-on-hover close-delay -->
      <template v-slot:activator="{ attrs, on }">
        <v-btn tile text v-bind="attrs" v-on="on" class="ml-n2">
          <v-icon color="primary">mdi-swap-vertical-bold</v-icon>
          Export
        </v-btn>
      </template>
      <v-list>
        <v-list-item @click="showModalExport = true"><v-list-item-title>Export analysis data</v-list-item-title></v-list-item>
        <v-list-item @click="showVisualizationExport = true"><v-list-item-title>Export visualization video</v-list-item-title></v-list-item>
      </v-list>
    </v-menu>

    <ModalExport v-model="showModalExport"> </ModalExport>
    <ModalVisualizationExport v-model="showVisualizationExport" :video-id="videoId" :duration="playerStore.videoDuration" />
  </div>
</template>

<script>
import ModalExport from "@/components/ModalExport.vue";
import ModalVisualizationExport from "@/components/ModalVisualizationExport.vue";

import { mapStores } from "pinia";
import { useUserStore } from "@/store/user";
import { usePlayerStore } from "@/store/player";

export default {
  data() {
    return {
      showModalExport: false,
      showVisualizationExport: false,
    };
  },
  computed: {
    videoId() {
      const videoId = this.playerStore.videoId;
      return videoId;
    },
    loggedIn() {
      return this.userStore.loggedIn;
    },

    ...mapStores(useUserStore, usePlayerStore),
  },
  components: {
    ModalExport,
    ModalVisualizationExport,
  },
};
</script>

<style>
.v-menu__content .v-btn:not(.accent) {
  text-transform: capitalize;
  justify-content: left;
}

.v-btn:not(.v-btn--round).v-size--large {
  height: 48px;
}
</style>
