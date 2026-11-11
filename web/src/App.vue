<template>
  <div v-if="route.path !== '/login'">
    <header class="topbar">
      <router-link to="/" class="brand">我的<span>书库</span></router-link>
      <nav>
        <router-link to="/">书目</router-link>
        <router-link to="/scrape">刮削确认</router-link>
        <router-link to="/duplicates">重复书</router-link>
        <router-link to="/edits">作者归并 / 批量记录</router-link>
        <router-link to="/organize">整理文件</router-link>
      </nav>
      <div class="spacer"></div>
      <button class="btn secondary" :disabled="scanning" @click="rescan">
        {{ scanning ? "扫描中…" : "重新扫描" }}
      </button>
      <button class="btn ghost" @click="logout">退出</button>
    </header>
    <router-view />
  </div>
  <router-view v-else />
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, clearAuth } from "./api/client";

const route = useRoute();
const router = useRouter();
const scanning = ref(false);

async function rescan() {
  scanning.value = true;
  try {
    await api("/api/scan", { method: "POST" });
    // 轮询直到结束
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 500));
      const st = await api("/api/scan/status");
      if (!st.running) break;
    }
    if (route.path === "/") {
      location.reload();
    }
  } catch (err) {
    alert((err as Error).message);
  } finally {
    scanning.value = false;
  }
}

function logout() {
  clearAuth();
  router.push("/login");
}
</script>
