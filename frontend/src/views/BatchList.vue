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
        <template v-slot:loading>
          <span>Loading batches...</span>
        </template>
        <template v-slot:no-data>
          <span>No batches yet.</span>
        </template>
        <template v-slot:item.status="{ item }">
          <v-chip small :color="statusColor(item.status)" dark>{{ item.status }}</v-chip>
        </template>
        <template v-slot:item.progress="{ item }">
          <v-progress-linear :value="batchProgress(item)" height="8"></v-progress-linear>
        </template>
        <template v-slot:item.actions="{ item }">
          <v-btn icon small title="Delete batch" @click.stop="askDeleteBatch(item)">
            <v-icon small color="red">mdi-trash-can-outline</v-icon>
          </v-btn>
        </template>
      </v-data-table>

      <v-dialog v-model="confirmDelete" max-width="420">
        <v-card>
          <v-card-title>Delete batch</v-card-title>
          <v-card-text>
            This removes "{{ batchToDeleteName }}" from the batch list and deletes any remaining temporary batch sources.
          </v-card-text>
          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn text @click="confirmDelete = false">Keep batch</v-btn>
            <v-btn color="red" text :loading="isDeleting" @click="deleteBatch">Delete</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
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
        { text: "Plugins done", value: "plugin_done_count" },
        { text: "Plugins failed", value: "plugin_failed_count" },
        { text: "Progress", value: "progress", sortable: false },
        { text: "", value: "actions", sortable: false, align: "end", width: 56 },
      ],
      confirmDelete: false,
      batchToDelete: null,
      isDeleting: false,
    };
  },
  mounted() {
    this.videoBatchStore.fetchAll();
  },
  computed: {
    batches() {
      return this.videoBatchStore.all;
    },
    batchToDeleteName() {
      return this.batchToDelete ? this.batchToDelete.name : "";
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
      if (status === "CANCELLED") return "grey";
      if (status === "READY") return "green";
      if (status === "RUNNING") return "blue";
      return "orange";
    },
    askDeleteBatch(batch) {
      this.batchToDelete = batch;
      this.confirmDelete = true;
    },
    async deleteBatch() {
      if (!this.batchToDelete) return;
      this.isDeleting = true;
      try {
        await this.videoBatchStore.delete(this.batchToDelete.id);
        this.confirmDelete = false;
        this.batchToDelete = null;
      } finally {
        this.isDeleting = false;
      }
    },
  },
  components: {
    ModalVideoBatchUpload,
  },
};
</script>
