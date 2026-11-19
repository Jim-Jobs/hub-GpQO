import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// langgraph 套：前端 5174，代理到本套网关 8101（与 openai-agents 套端口段隔离）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8101",
        changeOrigin: true,
      },
    },
  },
});
