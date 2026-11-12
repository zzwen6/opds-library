<template>
  <div class="wrap">
    <h2>整理书库文件</h2>
    <p class="muted">
      按命名模板移动 / 重命名书库中的文件，不改文件内容、不写回 OPF。执行前必须先预览并确认。
      本功能默认关闭；启用需设置 <code>ORGANIZE_ENABLED=true</code>，Docker 下还需把
      <code>/library</code> 改为可写挂载（见 README）。
    </p>
    <p v-if="!settings.organize_enabled" class="error">整理功能当前未启用，只能查看预览，不能执行。</p>
    <p v-else-if="settings.library_writable === false" class="error">书库目录当前不可写，无法执行或回滚。</p>
    <div class="form-grid" style="margin:16px 0">
      <div class="field full">
        <label>命名模板</label>
        <input v-model="template" placeholder="{author}/{series}/{title}.{ext}" />
        <p class="muted">
          可用变量：author、authors、series、series_index、title、subtitle、publisher、year、isbn、language、ext、format、filename、uuid。
          非法字符会被替换，空的路径段会省略；重名时追加 (2)、(3)…
          「title」若仍是「Kindle电子书:《三体》」这类脏书名，整理时会自动用清洗后的「三体」。建议先到「刮削确认」页点「清理脏标题并重新刮削」，再用官方书名整理。
        </p>
      </div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
      <button class="btn secondary" @click="preview" :disabled="busy">生成预览</button>
      <button
        class="btn"
        @click="apply"
        :disabled="busy || !previewData || !previewData.move_count || !settings.organize_enabled || settings.library_writable === false"
      >
        确认执行（{{ previewData?.move_count || 0 }} 个文件）
      </button>
    </div>
    <p v-if="message" class="muted">{{ message }}</p>
    <p v-if="error" class="error">{{ error }}</p>
    <table v-if="previewData" class="org-table">
      <thead>
        <tr>
          <th>动作</th>
          <th>书名</th>
          <th>当前路径</th>
          <th>目标路径</th>
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in previewData.items" :key="item.file_id" :class="item.action">
          <td>{{ labelAction(item.action) }}</td>
          <td>{{ item.title }}</td>
          <td class="muted">{{ item.from_path }}</td>
          <td>{{ item.to_path }}</td>
          <td class="muted">{{ item.note }}</td>
        </tr>
      </tbody>
    </table>
    <h3 style="margin-top:32px">最近批次</h3>
    <div v-if="!batches.length" class="muted">还没有执行过整理。</div>
    <div class="queue-item" v-for="b in batches" :key="b.id">
      <h3>批次 #{{ b.id }} · {{ b.status }} · {{ b.template }}</h3>
      <p class="muted">{{ b.created_at }} · {{ (b.moves || []).length }} 条移动记录</p>
      <ul>
        <li v-for="m in b.moves" :key="m.id">{{ m.from_path }} → {{ m.to_path }}（{{ m.status }}）</li>
      </ul>
      <button
        v-if="b.status === 'applied'"
        class="btn secondary"
        style="margin-top:8px"
        :disabled="busy || !settings.organize_enabled || settings.library_writable === false"
        @click="rollback(b.id)"
      >
        回滚此批次
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";

const settings = ref<any>({});
const template = ref("{author}/{series}/{title}.{ext}");
const previewData = ref<any>(null);
const batches = ref<any[]>([]);
const busy = ref(false);
const message = ref("");
const error = ref("");

function labelAction(action: string) {
  return ({ move: "将移动", skip: "已就位", missing: "文件缺失", error: "错误" } as any)[action] || action;
}

async function loadSettings() {
  settings.value = await api("/api/settings");
  if (settings.value.organize_template) template.value = settings.value.organize_template;
}

async function loadBatches() {
  const data = await api("/api/organize/batches");
  batches.value = data.items || [];
}

async function preview() {
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    const q = encodeURIComponent(template.value);
    previewData.value = await api(`/api/organize/preview?template=${q}`);
    message.value = `预览完成：将移动 ${previewData.value.move_count} 个文件。尚未改动磁盘。`;
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    busy.value = false;
  }
}

async function apply() {
  if (!previewData.value?.move_count) return;
  if (!confirm(`确认按预览移动 ${previewData.value.move_count} 个文件？此操作可在本页回滚。`)) return;
  busy.value = true;
  error.value = "";
  try {
    const batch = await api("/api/organize/apply", {
      method: "POST",
      body: JSON.stringify({ template: template.value }),
    });
    message.value = `已执行批次 #${batch.id}，移动 ${(batch.moves || []).length} 个文件。数据库路径已同步。`;
    await preview();
    await loadBatches();
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    busy.value = false;
  }
}

async function rollback(id: number) {
  if (!confirm(`确认回滚批次 #${id}？文件将尽量移回原路径。`)) return;
  busy.value = true;
  error.value = "";
  try {
    await api(`/api/organize/rollback/${id}`, { method: "POST" });
    message.value = `批次 #${id} 已回滚。`;
    await preview();
    await loadBatches();
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  await loadSettings();
  await loadBatches();
});
</script>
