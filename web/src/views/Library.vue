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
      <button class="btn secondary" @click="toggleSelect">{{ selecting ? "退出多选" : "多选" }}</button>
    </div>
    <div v-if="selecting" class="select-bar">
      <span>已选 <b>{{ selected.length }}</b> 本</span>
      <button class="btn ghost" @click="selectPage">全选本页</button>
      <button class="btn ghost" @click="selectAllResults" :disabled="total > 2000">全选全部结果（{{ total }}）</button>
      <button class="btn ghost" @click="selected = []">清空</button>
      <div class="spacer"></div>
      <button class="btn" :disabled="!selected.length" @click="showDialog = true">批量编辑</button>
    </div>
    <p class="muted">共 {{ total }} 本</p>
    <div class="grid">
      <component
        :is="selecting ? 'div' : 'router-link'"
        class="card"
        :class="{ picked: isSelected(book.uuid) }"
        v-for="book in items"
        :key="book.uuid"
        :to="selecting ? undefined : `/books/${book.uuid}`"
        @click="selecting && toggle(book.uuid)"
      >
        <div class="cover" :style="book.thumb_url ? { backgroundImage: `url(${book.thumb_url})` } : {}">
          <div v-if="!book.thumb_url" class="placeholder">{{ book.title }}</div>
          <span v-if="selecting" class="check">{{ isSelected(book.uuid) ? "✓" : "" }}</span>
        </div>
        <div class="meta">
          <h3>{{ book.title }}</h3>
          <p>{{ book.authors.join(" / ") || "未知作者" }}</p>
          <div style="margin-top:6px">
            <span class="chip" v-for="f in book.formats" :key="f">{{ f }}</span>
          </div>
        </div>
      </component>
    </div>
    <div class="pager" v-if="pages > 1">
      <button class="btn secondary" :disabled="page<=1" @click="load(page-1)">上一页</button>
      <span class="muted">{{ page }} / {{ pages }}</span>
      <button class="btn secondary" :disabled="page>=pages" @click="load(page+1)">下一页</button>
    </div>
    <BatchEditDialog v-if="showDialog" :uuids="selected" @close="showDialog = false" @done="load(page)" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import BatchEditDialog from "../components/BatchEditDialog.vue";

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
const selecting = ref(false);
const selected = ref<string[]>([]);
const showDialog = ref(false);

function params(p: number, size: number) {
  const ps = new URLSearchParams({ page: String(p), page_size: String(size), sort: sort.value });
  if (q.value) ps.set("q", q.value);
  if (format.value) ps.set("format", format.value);
  if (language.value) ps.set("language", language.value);
  return ps;
}

async function load(p = 1) {
  page.value = p;
  const data = await api(`/api/books?${params(p, pageSize)}`);
  items.value = data.items;
  total.value = data.total;
}

const isSelected = (u: string) => selected.value.includes(u);
function toggle(u: string) {
  selected.value = isSelected(u) ? selected.value.filter((x) => x !== u) : [...selected.value, u];
}
function toggleSelect() {
  selecting.value = !selecting.value;
  if (!selecting.value) selected.value = [];
}
function selectPage() {
  const add = items.value.map((b) => b.uuid).filter((u) => !isSelected(u));
  selected.value = [...selected.value, ...add];
}
async function selectAllResults() {
  const all: string[] = [];
  for (let p = 1; p <= Math.ceil(total.value / 200); p++) {
    const data = await api(`/api/books?${params(p, 200)}`);
    all.push(...data.items.map((b: any) => b.uuid));
  }
  selected.value = Array.from(new Set([...selected.value, ...all]));
}

onMounted(async () => {
  const meta = await api("/api/meta/filters");
  languages.value = meta.languages || [];
  await load(1);
});
</script>
