<template>
  <div class="wrap">
    <h2>待确认刮削</h2>
    <p class="muted">
      分数达到阈值（默认 90）或 ISBN 精确命中时，会自动写入未锁定字段；其余仍在本页等待确认。
      自动写入可在书目详情页撤销一次。
    </p>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
      <button class="btn" :disabled="batching" @click="runBatch">
        {{ batching ? "批量刮削中…" : "批量刮削未处理书目" }}
      </button>
    </div>
    <p v-if="batchMsg" class="muted">{{ batchMsg }}</p>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="!items.length" class="muted">当前没有待确认的书目。打开一本书后点击「刮削候选」，或使用上面的批量刮削。</div>
    <div class="queue-item" v-for="item in items" :key="item.uuid">
      <h3>
        <router-link :to="`/books/${item.uuid}`">{{ item.title }}</router-link>
      </h3>
      <p class="muted">{{ item.authors.join(" / ") }}</p>
      <div class="candidates">
        <div class="candidate" v-for="c in item.candidates" :key="c.record_id">
          <h4>{{ c.title }}</h4>
          <p class="muted">{{ c.authors.join(" / ") }}</p>
          <p class="muted">{{ c.source }} · {{ c.confidence }} 分 · {{ c.isbn13 }}</p>
          <button class="btn" @click="confirm(item.uuid, c.record_id)">采用</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";

const items = ref<any[]>([]);
const batching = ref(false);
const batchMsg = ref("");
const error = ref("");

async function load() {
  const data = await api("/api/scrape/queue");
  items.value = data.items || [];
}

async function confirm(uuid: string, record_id: number) {
  await api(`/api/books/${uuid}/scrape/confirm`, {
    method: "POST",
    body: JSON.stringify({ record_id }),
  });
  await load();
}

async function runBatch() {
  batching.value = true;
  error.value = "";
  batchMsg.value = "已开始对 scrape_status=pending 的书目批量刮削…";
  try {
    await api("/api/scrape/batch", { method: "POST" });
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 500));
      const st = await api("/api/scrape/batch/status");
      if (!st.running) {
        if (st.last_error) {
          error.value = st.last_error;
        } else if (st.last_result) {
          const r = st.last_result;
          batchMsg.value = `批量完成：共 ${r.total} 本，自动写入 ${r.auto_applied}，待确认 ${r.needs_review}，失败 ${r.failed}。`;
        }
        break;
      }
    }
    await load();
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    batching.value = false;
  }
}

onMounted(load);
</script>
