<template>
  <div class="wrap" :class="{ 'library-wide': sidebarOpen }">
    <div class="toolbar">
      <button
        class="btn secondary"
        type="button"
        :title="sidebarOpen ? '收起分类栏' : '展开分类栏'"
        @click="toggleSidebar"
      >{{ sidebarOpen ? "收起分类" : "分类" }}</button>
      <input class="search" v-model="q" placeholder="搜索书名、作者、丛书、标签、出版社、ISBN…" @keyup.enter="load(1)" />
      <select class="filter" v-model="format" @change="load(1)">
        <option value="">全部格式</option>
        <option v-for="f in formats" :key="f" :value="f">{{ f }}</option>
      </select>
      <select class="filter" v-model="language" @change="load(1)">
        <option value="">全部语言</option>
        <option v-for="l in languages" :key="l" :value="l">{{ l }}</option>
      </select>
      <select class="filter" v-model="missing" @change="load(1)">
        <option value="">全部书籍</option>
        <option value="cover">无封面</option>
        <option value="tags">无标签</option>
        <option value="pending">待刮削</option>
      </select>
      <select class="filter" v-model="sort" @change="onSortChange">
        <option value="added">最新入库</option>
        <option value="title">按标题</option>
        <option value="author">按作者</option>
        <option value="series">按丛书</option>
        <option value="rating">按评分</option>
        <option value="published">按出版日期</option>
      </select>
      <button class="btn secondary" @click="load(1)">搜索</button>
      <div class="view-switch" role="group" aria-label="视图">
        <button type="button" :class="{ on: view === 'grid' }" @click="setView('grid')">封面</button>
        <button type="button" :class="{ on: view === 'list' }" @click="setView('list')">列表</button>
      </div>
      <button class="btn secondary" @click="toggleSelect">{{ selecting ? "退出多选" : "多选" }}</button>
      <button class="btn" type="button" @click="openPicker">上传图书</button>
    </div>
    <div class="library-layout" :class="{ 'no-sidebar': !sidebarOpen }">
      <LibrarySidebar v-show="sidebarOpen" ref="sidebar" :selection="facetSel" @pick="onPickFacet" />
      <div class="library-main">
        <input
          ref="fileInput"
          type="file"
          hidden
          multiple
          accept=".epub,.pdf,.mobi,.azw3,.azw,.cbz,application/epub+zip,application/pdf"
          @change="onPick"
        />
        <div
          class="upload-zone"
          :class="{ active: dragging, busy: uploading }"
          @dragenter.prevent="onDragEnter"
          @dragover.prevent="onDragEnter"
          @dragleave.prevent="onDragLeave"
          @drop.prevent="onDrop"
        >
          <p>
            将电子书拖到此处，或
            <button class="btn secondary" type="button" @click="openPicker">选择文件</button>
          </p>
          <p class="muted">
            支持 epub、pdf、mobi、azw3、azw、cbz，单个文件不超过 1 GB。上传后自动入库并刮削。
            书库目录需可写（Docker 下把 /library 去掉 :ro），不必开启整理功能。
          </p>
        </div>
        <div v-if="uploads.length" class="upload-list">
          <div class="upload-item" v-for="(item, i) in uploads" :key="i">
            <div>
              <strong>{{ item.name }}</strong>
              <span class="muted" v-if="item.rel_path"> · {{ item.rel_path }}</span>
            </div>
            <p class="muted">{{ statusText(item) }}</p>
            <p v-if="item.error" class="error">{{ item.error }}</p>
            <p v-if="item.duplicate" class="muted">可能是重复书：{{ item.duplicate }}</p>
            <router-link v-if="item.uuid" class="upload-link" :to="`/books/${item.uuid}`">查看这本书</router-link>
          </div>
        </div>
        <div v-if="selecting" class="select-bar">
          <span>已选 <b>{{ selected.length }}</b> 本</span>
          <button class="btn ghost" @click="selectPage">全选本页</button>
          <button class="btn ghost" @click="selectAllResults" :disabled="total > 2000">全选全部结果（{{ total }}）</button>
          <button class="btn ghost" @click="selected = []">清空</button>
          <div class="spacer"></div>
          <button class="btn" :disabled="!selected.length" @click="showDialog = true">批量编辑</button>
        </div>
        <div class="result-bar">
          <span class="muted">共 {{ total }} 本</span>
          <span v-for="f in activeFacets" :key="f.key" class="chip facet-chip">
            {{ f.label }}：{{ f.name }}
            <button type="button" title="取消" @click="onPickFacet(f.key, null)">×</button>
          </span>
          <button v-if="hasFilters" class="btn ghost" type="button" @click="clearFilters">清除筛选</button>
        </div>
        <div v-if="view === 'grid'" class="grid">
          <component
            :is="selecting ? 'div' : 'router-link'"
            class="card"
            :class="{ picked: isSelected(book.uuid) }"
            v-for="book in items"
            :key="book.uuid"
            :to="selecting ? undefined : `/books/${book.uuid}`"
            @click="selecting && toggle(book.uuid)"
          >
            <div class="cover">
              <AuthImg v-if="book.thumb_url" class="cover-photo" :src="book.thumb_url" :alt="book.title" />
              <div v-else class="placeholder">{{ book.title }}</div>
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
        <div v-else class="book-table-wrap">
          <table class="book-table">
            <thead>
              <tr>
                <th v-if="selecting" class="col-check"></th>
                <th>书名</th>
                <th>作者</th>
                <th>丛书</th>
                <th>标签</th>
                <th>格式</th>
                <th>入库时间</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="book in items"
                :key="book.uuid"
                :class="{ picked: isSelected(book.uuid) }"
                @click="onRowClick(book)"
              >
                <td v-if="selecting" class="col-check">
                  <span class="row-check">{{ isSelected(book.uuid) ? "✓" : "" }}</span>
                </td>
                <td class="col-title">
                  <div class="title-cell">
                    <span class="thumb">
                      <AuthImg v-if="book.thumb_url" class="cover-photo" :src="book.thumb_url" :alt="book.title" />
                    </span>
                    <router-link v-if="!selecting" :to="`/books/${book.uuid}`" @click.stop>{{ book.title }}</router-link>
                    <span v-else>{{ book.title }}</span>
                  </div>
                </td>
                <td>{{ book.authors.join(" / ") || "未知作者" }}</td>
                <td>
                  <template v-if="book.series">{{ book.series }}<span v-if="book.series_index" class="muted"> #{{ book.series_index }}</span></template>
                </td>
                <td><span class="chip" v-for="t in book.tags" :key="t">{{ t }}</span></td>
                <td><span class="chip" v-for="f in book.formats" :key="f">{{ f }}</span></td>
                <td class="muted nowrap">{{ fmtDate(book.added_at) }}</td>
              </tr>
              <tr v-if="!items.length">
                <td :colspan="selecting ? 7 : 6" class="muted">没有符合条件的书</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="view === 'grid' && !items.length" class="muted">没有符合条件的书</p>
        <div class="pager" v-if="pages > 1">
          <button class="btn secondary" :disabled="page<=1" @click="load(page-1)">上一页</button>
          <span class="muted">{{ page }} / {{ pages }}</span>
          <button class="btn secondary" :disabled="page>=pages" @click="load(page+1)">下一页</button>
        </div>
      </div>
    </div>
    <BatchEditDialog v-if="showDialog" :uuids="selected" @close="showDialog = false" @done="onBatchDone" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import BatchEditDialog from "../components/BatchEditDialog.vue";
import AuthImg from "../components/AuthImg.vue";
import LibrarySidebar from "../components/LibrarySidebar.vue";
import type { FacetItem, FacetKey, FacetSelection } from "../components/LibrarySidebar.vue";

type UploadItem = {
  name: string;
  rel_path?: string;
  uuid?: string;
  title?: string;
  scrape_status?: string;
  error?: string;
  duplicate?: string;
  phase: "uploading" | "done" | "error";
};

const q = ref("");
const format = ref("");
const language = ref("");
const sort = ref("added");
const missing = ref("");
const router = useRouter();
const sidebar = ref<InstanceType<typeof LibrarySidebar> | null>(null);
const facetSel = reactive<FacetSelection>({ author: null, series: null, tag: null, publisher: null });
const facetLabels: Record<FacetKey, string> = { author: "作者", series: "丛书", tag: "标签", publisher: "出版社" };
const facetParams: Record<FacetKey, string> = {
  author: "author_id",
  series: "series_id",
  tag: "tag_id",
  publisher: "publisher_id",
};
// 选丛书时若仍是默认排序，自动切到按卷号；取消丛书时再切回
let autoSeriesSort = false;
const VIEW_KEY = "library.view";
const SIDEBAR_KEY = "library.sidebar";
const view = ref<"grid" | "list">(localStorage.getItem(VIEW_KEY) === "list" ? "list" : "grid");
const sidebarOpen = ref(
  localStorage.getItem(SIDEBAR_KEY) ? localStorage.getItem(SIDEBAR_KEY) === "open" : window.innerWidth >= 900,
);
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
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const dragDepth = ref(0);
const uploading = ref(false);
const uploads = ref<UploadItem[]>([]);
let pollTimer: number | undefined;

function params(p: number, size: number) {
  const ps = new URLSearchParams({ page: String(p), page_size: String(size), sort: sort.value });
  if (q.value) ps.set("q", q.value);
  if (format.value) ps.set("format", format.value);
  if (language.value) ps.set("language", language.value);
  if (missing.value === "cover") ps.set("missing_cover", "1");
  if (missing.value === "tags") ps.set("missing_tags", "1");
  if (missing.value === "pending") ps.set("scrape_status", "pending");
  for (const key of Object.keys(facetParams) as FacetKey[]) {
    const item = facetSel[key];
    if (item) ps.set(facetParams[key], String(item.id));
  }
  return ps;
}

const activeFacets = computed(() =>
  (Object.keys(facetLabels) as FacetKey[])
    .filter((k) => facetSel[k])
    .map((k) => ({ key: k, label: facetLabels[k], name: facetSel[k]!.name })),
);
const hasFilters = computed(
  () => activeFacets.value.length > 0 || !!q.value || !!format.value || !!language.value || !!missing.value,
);

function onPickFacet(key: FacetKey, item: FacetItem | null) {
  facetSel[key] = item;
  if (key === "series") {
    if (item && sort.value === "added") {
      sort.value = "series";
      autoSeriesSort = true;
    } else if (!item && autoSeriesSort && sort.value === "series") {
      sort.value = "added";
      autoSeriesSort = false;
    }
  }
  void load(1);
}

function onSortChange() {
  autoSeriesSort = false;
  void load(1);
}

function clearFilters() {
  q.value = "";
  format.value = "";
  language.value = "";
  missing.value = "";
  for (const key of Object.keys(facetSel) as FacetKey[]) facetSel[key] = null;
  if (autoSeriesSort && sort.value === "series") sort.value = "added";
  autoSeriesSort = false;
  void load(1);
}

function setView(v: "grid" | "list") {
  view.value = v;
  localStorage.setItem(VIEW_KEY, v);
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value;
  localStorage.setItem(SIDEBAR_KEY, sidebarOpen.value ? "open" : "closed");
}

function onRowClick(book: any) {
  if (selecting.value) toggle(book.uuid);
  else void router.push(`/books/${book.uuid}`);
}

function fmtDate(value?: string) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

async function onBatchDone() {
  await load(page.value);
  void sidebar.value?.reload();
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

function openPicker() {
  fileInput.value?.click();
}
function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const list = input.files ? Array.from(input.files) : [];
  input.value = "";
  if (list.length) void sendFiles(list);
}
function onDragEnter() {
  dragDepth.value += 1;
  dragging.value = true;
}
function onDragLeave() {
  dragDepth.value = Math.max(0, dragDepth.value - 1);
  if (dragDepth.value === 0) dragging.value = false;
}
function onDrop(ev: DragEvent) {
  dragDepth.value = 0;
  dragging.value = false;
  const list = ev.dataTransfer?.files ? Array.from(ev.dataTransfer.files) : [];
  if (list.length) void sendFiles(list);
}

function scrapeLabel(status?: string) {
  switch (status) {
    case "pending":
      return "正在刮削…";
    case "auto_applied":
      return "已自动写入元数据";
    case "needs_review":
      return "需人工确认刮削结果";
    case "failed":
      return "刮削失败";
    case "confirmed":
      return "已确认";
    default:
      return status || "";
  }
}

function statusText(item: UploadItem) {
  if (item.phase === "uploading") return "正在上传…";
  if (item.error) return "上传失败";
  const title = item.title ? `已入库「${item.title}」` : "已入库";
  const scrape = scrapeLabel(item.scrape_status);
  return scrape ? `${title} · ${scrape}` : title;
}

async function sendFiles(fileList: File[]) {
  uploading.value = true;
  const pending: UploadItem[] = fileList.map((f) => ({ name: f.name, phase: "uploading" }));
  uploads.value = [...pending, ...uploads.value];
  const form = new FormData();
  for (const f of fileList) form.append("files", f);
  try {
    const data = await api("/api/books/upload", { method: "POST", body: form });
    const results: any[] = data.items || [];
    for (let i = 0; i < pending.length; i++) {
      const row = results[i] || {};
      pending[i].phase = row.ok ? "done" : "error";
      pending[i].name = row.filename || pending[i].name;
      pending[i].rel_path = row.rel_path;
      pending[i].uuid = row.book_uuid;
      pending[i].title = row.title;
      pending[i].scrape_status = row.scrape_status;
      pending[i].error = row.error || (row.ok ? undefined : data.message);
      if (row.possible_duplicate) {
        const of = row.duplicate_of?.title || row.duplicate_of?.uuid || "";
        pending[i].duplicate = row.duplicate_reason + (of ? `（${of}）` : "");
      }
    }
    if (results.length > pending.length) {
      for (const row of results.slice(pending.length)) {
        uploads.value.push({
          name: row.filename || "未命名",
          phase: row.ok ? "done" : "error",
          rel_path: row.rel_path,
          uuid: row.book_uuid,
          title: row.title,
          scrape_status: row.scrape_status,
          error: row.error,
        });
      }
    }
    startPolling();
    await load(1);
    void sidebar.value?.reload();
  } catch (err: any) {
    const message = err?.message || "上传失败";
    for (const item of pending) {
      item.phase = "error";
      item.error = message;
    }
  } finally {
    uploading.value = false;
  }
}

function pendingScrapeUuids() {
  return uploads.value
    .filter((u) => u.uuid && (!u.scrape_status || u.scrape_status === "pending"))
    .map((u) => u.uuid!) ;
}

async function pollScrape() {
  const uuids = pendingScrapeUuids();
  if (!uuids.length) {
    stopPolling();
    return;
  }
  for (const uuid of uuids) {
    try {
      const book = await api(`/api/books/${uuid}`);
      const item = uploads.value.find((u) => u.uuid === uuid);
      if (item) {
        item.title = book.title || item.title;
        item.scrape_status = book.scrape_status;
      }
    } catch {
      /* 轮询失败时下一轮再试 */
    }
  }
  if (!pendingScrapeUuids().length) {
    stopPolling();
    await load(page.value);
    void sidebar.value?.reload();
  }
}

function startPolling() {
  stopPolling();
  if (!pendingScrapeUuids().length) return;
  void pollScrape();
  pollTimer = window.setInterval(() => {
    void pollScrape();
  }, 1500);
}

function stopPolling() {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
}

onMounted(async () => {
  const meta = await api("/api/meta/filters");
  languages.value = meta.languages || [];
  await load(1);
});
onUnmounted(() => stopPolling());
</script>
