<template>
  <div class="wrap">
    <h2>重复书检测</h2>
    <p class="muted">
      三类线索：文件哈希相同（完全相同的文件，置信度 100）、ISBN 相同（95）、书名+作者规范化后相同或相近（55–80，仅作建议）。
      合并只改数据库：把多本书目的文件挂到同一个条目下（OPDS 里一本书提供多种格式下载），<b>不会删除或移动磁盘文件</b>。
      合并可在“批量记录”里撤销。
    </p>
    <div class="toolbar">
      <button class="btn secondary" @click="load" :disabled="busy">重新检测</button>
      <label class="muted"><input type="checkbox" v-model="showIgnored" @change="load" /> 显示已标记“不是重复”的分组（{{ ignoredCount }}）</label>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="!groups.length && !busy" class="muted">没有发现重复。</div>

    <div class="queue-item" v-for="g in groups" :key="g.group_key" :class="{ dim: g.ignored }">
      <h3>
        <span class="chip" :class="'kind-' + g.kind">{{ kindLabel(g.kind) }}</span>
        置信度 {{ g.confidence }}
        <span v-if="g.ignored" class="chip">已标记不是重复</span>
      </h3>
      <p class="muted">{{ g.reason }}</p>

      <table v-if="g.kind === 'hash'" class="org-table">
        <thead><tr><th>文件</th><th>格式</th><th>大小</th><th>所属书目</th></tr></thead>
        <tbody>
          <tr v-for="f in g.files" :key="f.id">
            <td>{{ f.rel_path }}</td><td>{{ f.format }}</td><td>{{ size(f.size_bytes) }}</td>
            <td><router-link :to="`/books/${f.book_uuid}`">{{ f.book_title }}</router-link></td>
          </tr>
        </tbody>
      </table>

      <table v-if="g.books && (g.kind !== 'hash' || g.can_merge)" class="org-table" style="margin-top:8px">
        <thead><tr><th>主书目</th><th>并入</th><th>书名</th><th>作者</th><th>ISBN</th><th>格式</th><th>已锁字段</th></tr></thead>
        <tbody>
          <tr v-for="b in g.books" :key="b.uuid">
            <td><input type="radio" :name="'p-' + g.group_key" :value="b.uuid" v-model="state(g).primary" @change="state(g).preview = null" /></td>
            <td><input type="checkbox" :disabled="state(g).primary === b.uuid" :checked="state(g).primary !== b.uuid && state(g).include[b.uuid] !== false" @change="toggleInclude(g, b.uuid, ($event.target as HTMLInputElement).checked)" /></td>
            <td><router-link :to="`/books/${b.uuid}`">{{ b.title }}</router-link></td>
            <td>{{ b.authors.join(" / ") }}</td>
            <td>{{ b.isbn13 || "" }}</td>
            <td><span class="chip" v-for="f in b.formats" :key="f">{{ f }}</span></td>
            <td class="muted">{{ b.locked.join("、") }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="state(g).preview" class="preview-box">
        <p>合并后格式：{{ state(g).preview.formats_after.join(" / ") }}，共 {{ state(g).preview.files.length }} 个文件（磁盘文件不动）。</p>
        <p v-if="state(g).preview.kept_locked_fields.length" class="muted">主书目锁定字段保持不变：{{ state(g).preview.kept_locked_fields.join("、") }}</p>
        <ul class="detail-list" v-if="state(g).preview.field_changes.length">
          <li v-for="c in state(g).preview.field_changes" :key="c.field">
            {{ c.field }}：{{ show(c.old) }} → {{ show(c.new) }}
            <span class="muted">（{{ c.from === "union" ? "标签合并" : c.lock ? "来自被并入书目的锁定值，合并后保持锁定" : "主书目为空，取被并入书目的值" }}）</span>
          </li>
        </ul>
        <p v-else class="muted">主书目元数据不需要补充。</p>
      </div>

      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
        <template v-if="g.books && (g.kind !== 'hash' || g.can_merge)">
          <button class="btn secondary" :disabled="busy || !others(g).length" @click="preview(g)">预览合并</button>
          <button class="btn" :disabled="busy || !state(g).preview" @click="merge(g)">确认合并（{{ others(g).length }} 本并入）</button>
        </template>
        <button v-if="!g.ignored" class="btn ghost" :disabled="busy" @click="ignore(g)">不是重复</button>
        <button v-else class="btn ghost" :disabled="busy" @click="unignore(g)">取消标记</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api } from "../api/client";

const groups = ref<any[]>([]);
const ignoredCount = ref(0);
const showIgnored = ref(false);
const busy = ref(false);
const error = ref("");
const states = reactive<Record<string, { primary: string; include: Record<string, boolean>; preview: any }>>({});

const kindLabel = (k: string) => ({ hash: "相同文件", isbn: "相同 ISBN", fuzzy: "书名作者相近" } as any)[k] || k;
const show = (v: any) => (Array.isArray(v) ? (v.length ? v.join("、") : "（空）") : v ?? "（空）");
const size = (n: number) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`);

function state(g: any) {
  if (!states[g.group_key]) {
    states[g.group_key] = { primary: g.suggested_primary || g.books?.[0]?.uuid, include: {}, preview: null };
  }
  return states[g.group_key];
}
function others(g: any): string[] {
  const s = state(g);
  return (g.books || []).map((b: any) => b.uuid).filter((u: string) => u !== s.primary && s.include[u] !== false);
}
function toggleInclude(g: any, uuid: string, checked: boolean) {
  state(g).include[uuid] = checked;
  state(g).preview = null;
}

async function load() {
  busy.value = true;
  error.value = "";
  try {
    const data = await api(`/api/duplicates?include_ignored=${showIgnored.value}`);
    groups.value = data.groups;
    ignoredCount.value = data.ignored_count;
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}

async function preview(g: any) {
  busy.value = true;
  try {
    state(g).preview = await api("/api/duplicates/merge/preview", {
      method: "POST",
      body: JSON.stringify({ primary_uuid: state(g).primary, other_uuids: others(g) }),
    });
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}

async function merge(g: any) {
  busy.value = true;
  try {
    const r = await api("/api/duplicates/merge", {
      method: "POST",
      body: JSON.stringify({ primary_uuid: state(g).primary, other_uuids: others(g) }),
    });
    alert(`已合并（批次 #${r.batch_id}），现有格式：${r.formats.join(" / ")}。可在“批量记录”撤销。`);
    delete states[g.group_key];
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
  await load();
}

async function ignore(g: any) {
  await api("/api/duplicates/ignore", { method: "POST", body: JSON.stringify({ group_key: g.group_key, kind: g.kind }) });
  await load();
}
async function unignore(g: any) {
  await api("/api/duplicates/unignore", { method: "POST", body: JSON.stringify({ group_key: g.group_key, kind: g.kind }) });
  await load();
}

onMounted(load);
</script>
