<template>
  <v-virtual-scroll ref="parentContainer" :class="['d-flex', 'flex-column', 'pa-2']" :items="shots" item-height="140"
    :bench="shotsLength">
    <template v-slot:default="{ item }">
      <ShotCard :shot="item" :ref="`childContainer-${item.id}`" @childHighlighted="scrollToHighlightedChild" />
    </template>
  </v-virtual-scroll>
</template>

<script>
import { mapStores } from "pinia";
import ShotCard from "@/components/ShotCard.vue";
import { useShotStore } from "@/store/shot";
export default {
  methods: {
    scrollToHighlightedChild(childID) {
      const parentContainer = this.$refs.parentContainer;
      const childRef = this.$refs[`childContainer-${childID}`];
      const childContainer = Array.isArray(childRef) ? childRef[0] : childRef;

      if (parentContainer && childContainer && childContainer.$el && childContainer.$el.parentElement) {
        const offset = (parentContainer.$el.offsetHeight - childContainer.$el.offsetHeight) / 2;
        parentContainer.$el.scroll(0, childContainer.$el.parentElement.offsetTop - offset);
      }
    },
  },
  computed: {
    shotsLength() {
      return this.shots.length;
    },
    shots() {
      return this.shotStore.shots;
    },
    ...mapStores(useShotStore),
  },
  components: {
    ShotCard,
  },
};
</script>
