import Vue from "vue";
import VueRouter from "vue-router";
import Home from "@/views/Home.vue";
import HomeMenu from "@/views/HomeMenu.vue";
import VideoAnalysis from "@/views/VideoAnalysis.vue";
import VideoAnalysisMenu from "@/views/VideoAnalysisMenu.vue";
import BatchList from "@/views/BatchList.vue";
import BatchDetail from "@/views/BatchDetail.vue";
import UserMenu from "@/components/UserMenu.vue";

Vue.use(VueRouter);
const router = new VueRouter({
  mode: "history",
  base: process.env.BASE_URL,
  routes: [
    { path: "/", name: "Home", components: { default: Home, menu: HomeMenu } },
    {
      path: "/videoanalysis/:id",
      name: "VideoAnalysis",
      components: { default: VideoAnalysis, menu: VideoAnalysisMenu },
    },
    { path: "/batches", name: "BatchList", components: { default: BatchList, menu: UserMenu } },
    { path: "/batches/:id", name: "BatchDetail", components: { default: BatchDetail, menu: UserMenu } },
    // { path: '*', name: 'NotFound', component: NotFound },
  ],
});

export default router;
