<template>
  <v-main>
    <v-container v-if="userStore.loggedIn" fluid class="py-8 px-6">
      <v-row align="center" class="mb-4">
        <v-col>
          <h1 class="text-h5 mb-0">Video batches</h1>
        </v-col>
        <v-col cols="auto">
          <ModalVideoBatchUpload />
        </v-col>
      </v-row>
      <v-data-table
        :headers="headers"
        :items="batches"
        :loading="videoBatchStore.isLoading"
        item-key="id"
        @click:row="showBatch"
      >
        <template v-slot:item.status="{ item }">
          <v-chip small :color="statusColor(item.status)" dark>{{ item.status }}</v-chip>
        </template>
        <template v-slot:item.progress="{ item }">
          <v-progress-linear :value="batchProgress(item)" height="8"></v-progress-linear>
        </template>
      </v-data-table>
    </v-container>
  </v-main>
</template>

<script>
import { mapStores } from "pinia";
import { useUserStore } from "@/store/user";
import { useVideoBatchStore } from "@/store/video_batch";
import ModalVideoBatchUpload from "@/components/ModalVideoBatchUpload.vue";

export default {
  data() {
    return {
      headers: [
        { text: "Name", value: "name" },
        { text: "Status", value: "status" },
        { text: "Videos", value: "total_count" },
        { text: "Ready", value: "ready_count" },
        { text: "Failed", value: "failed_count" },
        { text: "Progress", value: "progress", sortable: false },
      ],
    };
  },
  mounted() {
    this.videoBatchStore.fetchAll();
  },
  computed: {
    batches() {
      return this.videoBatchStore.all;
    },
    ...mapStores(useUserStore, useVideoBatchStore),
  },
  methods: {
    showBatch(batch) {
      this.$router.push({ path: `/batches/${batch.id}` });
    },
    batchProgress(batch) {
      if (!batch.total_count) return 0;
      return Math.round(((batch.ready_count + batch.failed_count) * 100) / batch.total_count);
    },
    statusColor(status) {
      if (status === "ERROR" || status === "PARTIAL_ERROR") return "red";
      if (status === "READY") return "green";
      if (status === "RUNNING") return "blue";
      return "orange";
    },
  },
  components: {
    ModalVideoBatchUpload,
  },
};
</script>
