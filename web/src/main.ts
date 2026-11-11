import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import Library from "./views/Library.vue";
import BookDetail from "./views/BookDetail.vue";
import ScrapeQueue from "./views/ScrapeQueue.vue";
import Login from "./views/Login.vue";
import "./style.css";
import { hasAuth } from "./api/client";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: Login },
    { path: "/", component: Library },
    { path: "/books/:uuid", component: BookDetail },
    { path: "/scrape", component: ScrapeQueue },
  ],
});

router.beforeEach((to) => {
  if (to.path !== "/login" && !hasAuth()) {
    return "/login";
  }
});

createApp(App).use(router).mount("#app");
