<template>
  <div class="login">
    <form class="login-card" @submit.prevent="submit">
      <h1>我的书库</h1>
      <p>使用配置中的 HTTP Basic 账号登录，即可管理书目并开放 OPDS。</p>
      <div class="field">
        <label>用户名</label>
        <input v-model="username" autocomplete="username" />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="password" type="password" autocomplete="current-password" />
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <button class="btn" type="submit">进入书库</button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api, setAuth } from "../api/client";

const username = ref("admin");
const password = ref("");
const error = ref("");
const router = useRouter();

async function submit() {
  error.value = "";
  setAuth(username.value, password.value);
  try {
    await api("/api/books?page=1&page_size=1");
    router.push("/");
  } catch (err) {
    error.value = "登录失败，请检查用户名和密码";
  }
}
</script>
