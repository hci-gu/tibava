import Vue from 'vue'
import App from './App.vue'
import vuetify from '@/plugins/vuetify';
// import store from '@/store';
import i18n from '@/plugins/i18n';

import './styles/custom.css';

import { createPinia, PiniaVuePlugin } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'
import { useUserStore } from "@/store/user"
import axios from '@/plugins/axios'


Vue.use(PiniaVuePlugin)
const pinia = createPinia()
pinia.use(piniaPluginPersistedstate)



import router from '@/router';

async function startApp() {
  const userStore = useUserStore(pinia)
  axios.interceptors.response.use(undefined, (error) => {
    if (error.response && error.response.status === 403 &&
        error.response.data && error.response.data.type === 'not_authenticated') {
      userStore.$patch({
        loggedIn: false,
        username: null,
        email: null,
        date: null,
        allowance: 0,
        max_video_size: 0,
      })
    }
    return Promise.reject(error)
  })
  await userStore.getCSRFToken()
  await userStore.getUserData()

  new Vue({
    pinia,
    vuetify,
    router,
    i18n,
    render: h => h(App),
  }).$mount('#app')
}

startApp()

import Router from "vue-router";
Vue.use(Router)
