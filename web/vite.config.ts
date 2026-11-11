import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/opds": "http://127.0.0.1:8080", // 同时覆盖 /opds2
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
