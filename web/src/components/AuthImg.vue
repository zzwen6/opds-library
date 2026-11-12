<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { fetchAuthBlob } from "../api/client";

const props = defineProps<{
  src?: string | null;
  alt?: string;
}>();

const objectUrl = ref("");
let generation = 0;

async function load(src: string | null | undefined) {
  const my = ++generation;
  const prev = objectUrl.value;
  objectUrl.value = "";
  if (prev) URL.revokeObjectURL(prev);
  if (!src) return;
  const url = await fetchAuthBlob(src);
  if (my !== generation) {
    if (url) URL.revokeObjectURL(url);
    return;
  }
  objectUrl.value = url || "";
}

watch(() => props.src, load, { immediate: true });
onUnmounted(() => {
  generation += 1;
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value);
});
</script>

<template>
  <img v-if="objectUrl" :src="objectUrl" :alt="alt || ''" />
</template>
