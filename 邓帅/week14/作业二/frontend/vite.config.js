import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 后端 FastAPI 固定 8001（8000 常被占用）；/api 与 /files（解析产物图片静态服务）都走 proxy
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8001", changeOrigin: true },
      "/files": { target: "http://127.0.0.1:8001", changeOrigin: true },
    },
  },
});
