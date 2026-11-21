import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 后端 FastAPI 固定 8001（8000 常被占用），dev server 用 proxy 转发避免跨域
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
});
