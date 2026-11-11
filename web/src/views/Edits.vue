<template>
  <div class="wrap">
    <h2>作者归并</h2>
    <p class="muted">把同一作者的不同写法（全角/半角、空格、[美] 等国籍括注、“姓, 名”）统一成一个。作者字段已锁定的书会跳过并列出。</p>

    <h3>疑似同一作者</h3>
    <div v-if="!variants.length" class="muted">没有发现写法不同的作者。</div>
    <div class="queue-item" v-for="v in variants" :key="v.key">
      <p>
        统一为：
        <label v-for="a in v.authors" :key="a.id" style="margin-right:12px">
          <input type="radio" :name="'t-' + v.key" :value="a.name" v-model="targets[v.key]" />
          {{ a.name }} <span class="muted">（{{ a.book_count }} 本）</span>
        </label>
        <input v-model="targets[v.key]" placeholder="或输入新写法" style="width:180px" />
      </p>
      <button class="btn secondary" @click="mergePreview(v.authors.map((a: any) => a.name), targets[v.key])">预览</button>
    </div>

    <h3 style="margin-top:24px">手动改名 / 归并</h3>
    <div class="batch-row">
      <label>把这些作者</label>
      <select multiple v-model="manualSources" style="min-width:240px;min-height:90px">
        <option v-for="a in authors" :key="a.id" :value="a.name">{{ a.name }}（{{ a.book_count }}）</option>
      </select>
      <label>改成</label>
      <input v-model="manualTarget" placeholder="统一后的作者名" />
      <button class="btn secondary" :disabled="!manualSources.length || !manualTarget" @click="mergePreview(manualSources, manualTarget)">预览</button>
    </div>
    <div class="batch-row">
      <label><input type="checkbox" v-model="lockAfter" /> 改过的作者字段加锁</label>
    </div>

    <div v-if="mergePlan" class="preview-box">
      <p>
        {{ mergeReq.sources.filter((s: string) => s !== mergePlan.target).join("、") }} → <b>{{ mergePlan.target }}</b>：
        将修改 {{ mergePlan.affected_books }} 本<span v-if="mergePlan.skipped_books">，作者已锁定跳过 {{ mergePlan.skipped_books }} 本</span>。
      </p>
      <ul class="detail-list">
        <li v-for="i in mergePlan.items" :key="i.uuid">
          {{ i.title }}：
          <span v-for="c in i.changes" :key="c.field">{{ c.old.join("、") }} → {{ c.new.join("、") }}</span>
          <span v-for="s in i.skipped" :key="s.field" class="chip locked">🔒 已锁定，跳过</span>
        </li>
      </ul>
      <button class="btn" :disabled="busy || !mergePlan.change_count" @click="mergeApply">确认归并</button>
      <button class="btn ghost" @click="mergePlan = null">取消</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>

    <h2 style="margin-top:36px">批量记录</h2>
    <p class="muted">批量编辑、作者归并、重复书合并的记录。撤销时若某字段之后又被改过，会保留新值并提示。</p>
    <div v-if="!batches.length" class="muted">还没有记录。</div>
    <div class="queue-item" v-for="b in batches" :key="b.id">
      <h3>#{{ b.id }} · {{ kindLabel(b.kind) }} · {{ statusLabel(b.status) }}</h3>
      <p>{{ b.summary }}</p>
      <p class="muted">{{ new Date(b.created_at).toLocaleString() }} · {{ b.change_count }} 处字段变更</p>
      <p v-if="b.result?.skipped?.length" class="muted">跳过（已锁定）：{{ b.result.skipped.map((s: any) => s.title).join("、") }}</p>
      <button v-if="b.status === 'applied'" class="btn secondary" :disabled="busy" @click="undo(b.id)">撤销</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api } from "../api/client";

const variants = ref<any[]>([]);
const targets = reactive<Record<string, string>>({});
const authors = ref<any[]>([]);
const manualSources = ref<string[]>([]);
const manualTarget = ref("");
const lockAfter = ref(false);
const mergePlan = ref<any>(null);
const mergeReq = ref<any>({ sources: [] });
const batches = ref<any[]>([]);
const busy = ref(false);
const error = ref("");

const kindLabel = (k: string) => ({ batch_edit: "批量编辑", author_merge: "作者归并", book_merge: "合并重复书" } as any)[k] || k;
const statusLabel = (s: string) => ({ applied: "已执行", undone: "已撤销", partially_undone: "部分撤销", noop: "无改动" } as any)[s] || s;

async function load() {
  const [v, a, b] = await Promise.all([api("/api/authors/variants"), api("/api/authors"), api("/api/edit-batches")]);
  variants.value = v;
  for (const g of v) if (!targets[g.key]) targets[g.key] = g.suggested;
  authors.value = a;
  batches.value = b;
}

async function mergePreview(sources: string[], target: string) {
  error.value = "";
  mergeReq.value = { sources, target, lock_after: lockAfter.value };
  try { mergePlan.value = await api("/api/authors/merge/preview", { method: "POST", body: JSON.stringify(mergeReq.value) }); }
  catch (e) { error.value = (e as Error).message; mergePlan.value = null; }
}

async function mergeApply() {
  busy.value = true;
  try {
    const r = await api("/api/authors/merge", { method: "POST", body: JSON.stringify(mergeReq.value) });
    alert(`已归并 ${r.affected_books} 本（批次 #${r.batch_id}）`);
    mergePlan.value = null;
    manualSources.value = [];
    manualTarget.value = "";
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
  await load();
}

async function undo(id: number) {
  if (!confirm(`撤销批次 #${id}？`)) return;
  busy.value = true;
  try {
    const r = await api(`/api/edit-batches/${id}/undo`, { method: "POST" });
    const n = r.conflicts?.length || 0;
    alert(n ? `已撤销，但有 ${n} 处之后又被改过或无法恢复，已保留现状` : "已撤销");
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
  await load();
}

onMounted(load);
</script>
