import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base './'：构建产物由 FastAPI 静态托管，使用相对路径引用资源
export default defineConfig({
  base: './',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发模式下 /api 转发到本地后端（python server.py）
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 600,
  },
})
