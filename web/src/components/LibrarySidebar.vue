<template>
  <aside class="facets">
    <section class="facet" v-for="f in facets" :key="f.key">
      <button class="facet-head" type="button" @click="toggleOpen(f.key)">
        <span>{{ open[f.key] ? "▾" : "▸" }} {{ f.label }}</span>
        <span class="muted">{{ lists[f.key].length }}</span>
      </button>
      <div v-if="open[f.key]" class="facet-body">
        <input
          v-if="lists[f.key].length > 10"
          class="facet-filter"
          v-model="filters[f.key]"
          :placeholder="`筛选${f.label}…`"
        />
        <ul class="facet-list">
          <li v-for="item in visible(f.key)" :key="item.id">
            <button
              type="button"
              class="facet-item"
              :class="{ active: selection[f.key]?.id === item.id }"
              :title="item.name"
              @click="pick(f.key, item)"
            >
              <span class="facet-name">{{ item.name }}</span>
              <span class="facet-count">{{ item.book_count }}</span>
            </button>
          </li>
          <li v-if="!visible(f.key).length" class="muted facet-empty">
            {{ lists[f.key].length ? "没有匹配项" : "暂无" }}
          </li>
        </ul>
      </div>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { onMounted, reactive } from "vue";
import { api } from "../api/client";

export type FacetKey = "author" | "series" | "tag" | "publisher";
export type FacetItem = { id: number; name: string; book_count: number };
export type FacetSelection = Record<FacetKey, FacetItem | null>;

const props = defineProps<{ selection: FacetSelection }>();
const emit = defineEmits<{ (e: "pick", key: FacetKey, item: FacetItem | null): void }>();

const facets: { key: FacetKey; label: string; url: string }[] = [
  { key: "author", label: "作者", url: "/api/authors" },
  { key: "series", label: "丛书", url: "/api/series" },
  { key: "tag", label: "标签", url: "/api/tags" },
  { key: "publisher", label: "出版社", url: "/api/publishers" },
];

const OPEN_KEY = "library.facets.open";
const savedOpen = (() => {
  try {
    return JSON.parse(localStorage.getItem(OPEN_KEY) || "null");
  } catch {
    return null;
  }
})();

const lists = reactive<Record<FacetKey, FacetItem[]>>({ author: [], series: [], tag: [], publisher: [] });
const filters = reactive<Record<FacetKey, string>>({ author: "", series: "", tag: "", publisher: "" });
const open = reactive<Record<FacetKey, boolean>>(
  savedOpen || { author: true, series: true, tag: true, publisher: false },
);

function toggleOpen(key: FacetKey) {
  open[key] = !open[key];
  localStorage.setItem(OPEN_KEY, JSON.stringify(open));
}

function visible(key: FacetKey) {
  const kw = filters[key].trim().toLowerCase();
  const rows = lists[key];
  return kw ? rows.filter((r) => r.name.toLowerCase().includes(kw)) : rows;
}

function pick(key: FacetKey, item: FacetItem) {
  // 再点一次已选中的项即取消
  emit("pick", key, props.selection[key]?.id === item.id ? null : item);
}

async function reload() {
  await Promise.all(
    facets.map(async (f) => {
      try {
        const rows: FacetItem[] = await api(f.url);
        lists[f.key] = rows.filter((r) => r.book_count > 0);
      } catch {
        lists[f.key] = [];
      }
    }),
  );
}

defineExpose({ reload });
onMounted(reload);
</script>
