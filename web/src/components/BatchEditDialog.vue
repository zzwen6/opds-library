<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <h2>批量编辑 {{ uuids.length }} 本</h2>
      <p class="muted">只改勾选的字段。已锁定的字段会逐本跳过并列出。每次执行都会记录旧值，可在“批量记录”里撤销。</p>

      <div class="batch-row">
        <label><input type="checkbox" v-model="use.authors" /> 作者</label>
        <select v-model="authorsMode" :disabled="!use.authors">
          <option value="replace">替换为</option>
          <option value="add">追加</option>
          <option value="remove">移除</option>
        </select>
        <input v-model="authorsText" :disabled="!use.authors" placeholder="多个用逗号或顿号分隔" />
      </div>
      <div class="batch-row">
        <label><input type="checkbox" v-model="use.tags" /> 标签</label>
        <select v-model="tagsMode" :disabled="!use.tags">
          <option value="add">追加</option>
          <option value="remove">移除</option>
          <option value="replace">替换为</option>
        </select>
        <input v-model="tagsText" :disabled="!use.tags" placeholder="多个用逗号或顿号分隔" />
      </div>
      <div class="batch-row">
        <label><input type="checkbox" v-model="use.series" /> 系列</label>
        <input v-model="seriesName" :disabled="!use.series" placeholder="留空 = 清除系列" />
      </div>
      <div class="batch-row" v-if="use.series">
        <label class="sub">序号</label>
        <select v-model="indexMode">
          <option value="keep">不改</option>
          <option value="auto">按选择顺序自动编号</option>
          <option value="same">统一设为</option>
        </select>
        <input v-if="indexMode === 'auto'" type="number" v-model.number="indexStart" placeholder="起始号" />
        <input v-if="indexMode === 'same'" v-model="indexValue" placeholder="留空 = 清除序号" />
      </div>
      <div class="batch-row">
        <label><input type="checkbox" v-model="use.language" /> 语言</label>
        <input v-model="language" :disabled="!use.language" placeholder="如 zh、en；留空 = 清除" />
      </div>
      <div class="batch-row">
        <label><input type="checkbox" v-model="use.publisher" /> 出版社</label>
        <input v-model="publisher" :disabled="!use.publisher" placeholder="留空 = 清除" />
      </div>
      <div class="batch-row">
        <label><input type="checkbox" v-model="lockAfter" /> 改过的字段加锁（防止之后刮削覆盖，和单本手动编辑一致）</label>
      </div>

      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="preview" class="preview-box">
        <p>
          将修改 <b>{{ preview.affected_books }}</b> 本、共 {{ preview.change_count }} 处；
          <span v-if="preview.skipped_count">因字段锁定跳过 {{ preview.skipped_books }} 本 / {{ preview.skipped_count }} 处；</span>
          其余无变化。
        </p>
        <details>
          <summary class="muted">查看明细</summary>
          <ul class="detail-list">
            <li v-for="item in preview.items" :key="item.uuid">
              <b>{{ item.title }}</b>
              <span v-for="c in item.changes" :key="c.field" class="chip">{{ label(c.field) }}：{{ show(c.old) }} → {{ show(c.new) }}</span>
              <span v-for="s in item.skipped" :key="'s'+s.field" class="chip locked">🔒 {{ label(s.field) }} 已锁定，跳过</span>
              <span v-if="!item.changes.length && !item.skipped.length" class="muted">无变化</span>
            </li>
          </ul>
        </details>
      </div>
      <div v-if="result" class="preview-box">
        <p>已完成（批次 #{{ result.batch_id }}）：修改 {{ result.affected_books }} 本，跳过 {{ result.skipped_books }} 本。</p>
        <ul v-if="result.skipped.length" class="detail-list">
          <li v-for="s in result.skipped" :key="s.uuid">{{ s.title }}：{{ s.fields.map(label).join("、") }} 已锁定</li>
        </ul>
        <button class="btn secondary" :disabled="busy" @click="undo">撤销这次批量编辑</button>
      </div>

      <div class="modal-actions">
        <button class="btn ghost" @click="$emit('close')">{{ result ? "关闭" : "取消" }}</button>
        <button class="btn secondary" :disabled="busy || !anyField || !!result" @click="doPreview">预览影响范围</button>
        <button class="btn" :disabled="busy || !preview || !preview.change_count || !!result" @click="apply">
          确认执行（{{ preview?.affected_books || 0 }} 本）
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { api } from "../api/client";

const props = defineProps<{ uuids: string[] }>();
const emit = defineEmits(["close", "done"]);

const use = reactive({ authors: false, tags: false, series: false, language: false, publisher: false });
const authorsMode = ref("replace");
const authorsText = ref("");
const tagsMode = ref("add");
const tagsText = ref("");
const seriesName = ref("");
const indexMode = ref("keep");
const indexStart = ref<number>(1);
const indexValue = ref("");
const language = ref("");
const publisher = ref("");
const lockAfter = ref(true);
const preview = ref<any>(null);
const result = ref<any>(null);
const error = ref("");
const busy = ref(false);

const LABELS: Record<string, string> = {
  authors: "作者", tags: "标签", series: "系列", series_index: "序号", language: "语言", publisher: "出版社",
};
const label = (f: string) => LABELS[f] || f;
const show = (v: any) => (Array.isArray(v) ? (v.length ? v.join("、") : "（空）") : v ?? "（空）");
const split = (t: string) => t.split(/[,，、;；]/).map((s) => s.trim()).filter(Boolean);
const anyField = computed(() => Object.values(use).some(Boolean));

function body() {
  const ops: any = {};
  if (use.authors) ops.authors = { mode: authorsMode.value, values: split(authorsText.value) };
  if (use.tags) ops.tags = { mode: tagsMode.value, values: split(tagsText.value) };
  if (use.series) {
    ops.series = { name: seriesName.value };
    if (indexMode.value === "auto") ops.series.index_start = indexStart.value || 1;
    if (indexMode.value === "same") { ops.series.set_index = true; ops.series.index = indexValue.value; }
  }
  if (use.language) ops.language = language.value;
  if (use.publisher) ops.publisher = publisher.value;
  return JSON.stringify({ book_uuids: props.uuids, ops, lock_after: lockAfter.value });
}

// 任何输入变化都要求重新预览
watch(
  [() => ({ ...use }), authorsMode, authorsText, tagsMode, tagsText, seriesName, indexMode, indexStart, indexValue, language, publisher, lockAfter],
  () => { preview.value = null; },
  { deep: true },
);

async function doPreview() {
  error.value = "";
  busy.value = true;
  try { preview.value = await api("/api/batch/preview", { method: "POST", body: body() }); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}

async function apply() {
  error.value = "";
  busy.value = true;
  try {
    result.value = await api("/api/batch/apply", { method: "POST", body: body() });
    emit("done");
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}

async function undo() {
  busy.value = true;
  try {
    const r = await api(`/api/edit-batches/${result.value.batch_id}/undo`, { method: "POST" });
    alert(r.conflicts.length ? `已撤销 ${r.restored} 处，${r.conflicts.length} 处之后又被改过未还原` : `已撤销 ${r.restored} 处`);
    emit("done");
    emit("close");
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
</script>
