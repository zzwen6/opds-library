<template>
  <div class="wrap" v-if="book">
    <p class="muted"><router-link to="/">← 返回书库</router-link></p>
    <div class="detail">
      <div>
        <img v-if="book.cover_url" class="poster" :src="book.cover_url" :alt="book.title" />
        <div v-else class="poster" style="display:grid;place-items:center;color:#6d6458">暂无封面</div>
        <label class="btn secondary" style="display:block;text-align:center;margin-top:12px">
          更换封面
          <input type="file" accept="image/*" hidden @change="onCover" />
        </label>
      </div>
      <div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
          <button class="btn" @click="save" :disabled="saving">保存并锁定已改字段</button>
          <button class="btn secondary" @click="doScrape" :disabled="scraping">刮削候选</button>
        </div>
        <p v-if="message" class="muted">{{ message }}</p>
        <p v-if="error" class="error">{{ error }}</p>
        <div class="form-grid">
          <div class="field full">
            <label>标题 <span class="lock" @click="toggle('title')">{{ locked('title') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.title" />
          </div>
          <div class="field full">
            <label>副标题 <span class="lock" @click="toggle('subtitle')">{{ locked('subtitle') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.subtitle" />
          </div>
          <div class="field full">
            <label>作者（逗号分隔） <span class="lock" @click="toggle('authors')">{{ locked('authors') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.authors" />
          </div>
          <div class="field">
            <label>ISBN-13 <span class="lock" @click="toggle('isbn13')">{{ locked('isbn13') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.isbn13" />
          </div>
          <div class="field">
            <label>语言 <span class="lock" @click="toggle('language')">{{ locked('language') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.language" />
          </div>
          <div class="field">
            <label>出版社 <span class="lock" @click="toggle('publisher')">{{ locked('publisher') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.publisher" />
          </div>
          <div class="field">
            <label>出版日期 <span class="lock" @click="toggle('published_date')">{{ locked('published_date') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.published_date" />
          </div>
          <div class="field">
            <label>丛书 <span class="lock" @click="toggle('series')">{{ locked('series') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.series" />
          </div>
          <div class="field">
            <label>丛书序号</label>
            <input v-model="form.series_index" />
          </div>
          <div class="field full">
            <label>标签（逗号分隔） <span class="lock" @click="toggle('tags')">{{ locked('tags') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <input v-model="form.tags" />
          </div>
          <div class="field full">
            <label>简介 <span class="lock" @click="toggle('description')">{{ locked('description') ? "已锁定 · 点击解锁" : "未锁" }}</span></label>
            <textarea v-model="form.description" rows="8"></textarea>
          </div>
        </div>
        <h3 style="margin-top:24px">文件</h3>
        <ul>
          <li v-for="f in book.files" :key="f.id">
            {{ f.filename }} · {{ f.format }} · {{ (f.size_bytes/1024).toFixed(0) }} KB
            <span v-if="f.missing" class="error">缺失</span>
          </li>
        </ul>
        <div v-if="candidates.length" class="queue-item" style="margin-top:20px">
          <h3>刮削候选（确认后写入，已锁定字段不会被覆盖）</h3>
          <div class="candidates">
            <div class="candidate" v-for="c in candidates" :key="c.record_id">
              <h4>{{ c.title }}</h4>
              <p class="muted">{{ c.authors.join(" / ") }} · {{ c.source }} · {{ c.confidence }} 分</p>
              <p class="muted">{{ c.publisher }} {{ c.published_date }}</p>
              <button class="btn" style="margin-top:8px" @click="confirm(c.record_id)">采用</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/client";

const route = useRoute();
const book = ref<any>(null);
const form = reactive<any>({});
const saving = ref(false);
const scraping = ref(false);
const message = ref("");
const error = ref("");
const candidates = ref<any[]>([]);

function locked(name: string) {
  return (book.value?.locks || []).some((l: any) => l.field_name === name);
}

function applyBook(data: any) {
  book.value = data;
  Object.assign(form, {
    title: data.title || "",
    subtitle: data.subtitle || "",
    authors: (data.authors || []).join(", "),
    isbn13: data.isbn13 || "",
    language: data.language || "",
    publisher: data.publisher || "",
    published_date: data.published_date || "",
    series: data.series || "",
    series_index: data.series_index || "",
    tags: (data.tags || []).join(", "),
    description: data.description || "",
  });
}

async function load() {
  const data = await api(`/api/books/${route.params.uuid}`);
  applyBook(data);
}

async function save() {
  saving.value = true;
  error.value = "";
  message.value = "";
  try {
    const payload = {
      title: form.title,
      subtitle: form.subtitle || null,
      authors: form.authors.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean),
      isbn13: form.isbn13 || null,
      language: form.language || null,
      publisher: form.publisher || null,
      published_date: form.published_date || null,
      series: form.series || null,
      series_index: form.series_index || null,
      tags: form.tags.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean),
      description: form.description || null,
    };
    const data = await api(`/api/books/${route.params.uuid}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    applyBook(data);
    message.value = "已保存，修改过的字段已锁定。";
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    saving.value = false;
  }
}

async function toggle(field: string) {
  const data = await api(`/api/books/${route.params.uuid}/locks`, {
    method: "POST",
    body: JSON.stringify({ field_name: field, locked: !locked(field) }),
  });
  applyBook(data);
}

async function doScrape() {
  scraping.value = true;
  error.value = "";
  try {
    const data = await api(`/api/books/${route.params.uuid}/scrape`, { method: "POST" });
    candidates.value = data.candidates || [];
    if (!candidates.value.length) message.value = "没有找到候选，可先补全 ISBN 或标题后再试。";
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    scraping.value = false;
  }
}

async function confirm(record_id: number) {
  const data = await api(`/api/books/${route.params.uuid}/scrape/confirm`, {
    method: "POST",
    body: JSON.stringify({ record_id }),
  });
  applyBook(data);
  candidates.value = [];
  message.value = "已采用该候选，未锁定字段已更新。";
}

async function onCover(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  const data = await api(`/api/books/${route.params.uuid}/cover`, { method: "POST", body });
  applyBook(data);
  message.value = "封面已更新并锁定。";
}

onMounted(load);
</script>
