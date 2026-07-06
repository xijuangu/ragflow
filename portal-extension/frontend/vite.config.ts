import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
// 开发时 Vite dev server (默认 5173) 代理后端 API 到 FastAPI (默认 8000),
// 保证前端请求 /login、/share-pages 等路径时同源(cookie 自动携带)。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 后端 API 路径全部代理到 FastAPI,开发时保持同源(cookie 自动携带)
      '/login': 'http://localhost:8000',
      '/logout': 'http://localhost:8000',
      '/me': 'http://localhost:8000',
      '/share-pages': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
