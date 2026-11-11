<template>
  <div class="wrap">
    <div class="toolbar">
      <input class="search" v-model="q" placeholder="搜索书名、作者、ISBN…" @keyup.enter="load(1)" />
      <select class="filter" v-model="format" @change="load(1)">
        <option value="">全部格式</option>
        <option v-for="f in formats" :key="f" :value="f">{{ f }}</option>
      </select>
      <select class="filter" v-model="language" @change="load(1)">
        <option value="">全部语言</option>
        <option v-for="l in languages" :key="l" :value="l">{{ l }}</option>
      </select>
      <select class="filter" v-model="sort" @change="load(1)">
        <option value="added">最新入库</option>
        <option value="title">按标题</option>
      </select>
      <button class="btn secondary" @click="load(1)">搜索</button>
    </div>
    <p class="muted">共 {{ total }} 本</p>
    <div class="grid">
      <router-link class="card" v-for="book in items" :key="book.uuid" :to="`/books/${book.uuid}`">
        <div class="cover" :style="book.thumb_url ? { backgroundImage: `url(${book.thumb_url})` } : {}">
          <div v-if="!book.thumb_url" class="placeholder">{{ book.title }}</div>
        </div>
        <div class="meta">
          <h3>{{ book.title }}</h3>
          <p>{{ book.authors.join(" / ") || "未知作者" }}</p>
          <div style="margin-top:6px">
            <span class="chip" v-for="f in book.formats" :key="f">{{ f }}</span>
          </div>
        </div>
      </router-link>
    </div>
    <div class="pager" v-if="pages > 1">
      <button class="btn secondary" :disabled="page<=1" @click="load(page-1)">上一页</button>
      <span class="muted">{{ page }} / {{ pages }}</span>
      <button class="btn secondary" :disabled="page>=pages" @click="load(page+1)">下一页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";

const q = ref("");
const format = ref("");
const language = ref("");
const sort = ref("added");
const items = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 24;
const languages = ref<string[]>([]);
const formats = ["epub", "pdf", "mobi", "azw3", "cbz"];
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));

async function load(p = 1) {
  page.value = p;
  const params = new URLSearchParams({
    page: String(p),
    page_size: String(pageSize),
    sort: sort.value,
  });
  if (q.value) params.set("q", q.value);
  if (format.value) params.set("format", format.value);
  if (language.value) params.set("language", language.value);
  const data = await api(`/api/books?${params}`);
  items.value = data.items;
  total.value = data.total;
}

onMounted(async () => {
  const meta = await api("/api/meta/filters");
  languages.value = meta.languages || [];
  await load(1);
});
</script>
