import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/admin/',
  server: { proxy: { '/admin/api': { target: 'https://127.0.0.1:8443', secure: false } } },
  build: { target: 'es2022' },
})
