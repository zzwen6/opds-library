<template>
  <div class="wrap">
    <h2>待确认刮削</h2>
    <p class="muted">高分候选排在前面。确认写入时会跳过已锁定字段。MVP 默认不会自动采纳，即使 ISBN 精确匹配。</p>
    <div v-if="!items.length" class="muted">当前没有待确认的书目。打开一本书后点击「刮削候选」。</div>
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

onMounted(load);
</script>
