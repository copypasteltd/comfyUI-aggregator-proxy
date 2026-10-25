import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'

const escapeHtml = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export default defineConfig({
  plugins: [vue(), {
    name: 'bundled-font-licenses',
    generateBundle() {
      const notices = ['Anton', 'Manrope'].map(family => {
        const text = readFileSync(new URL(`./src/assets/fonts/${family}-OFL.txt`, import.meta.url), 'utf8')
        return `<h2>${family}</h2><pre>${escapeHtml(text)}</pre>`
      }).join('\n')
      this.emitFile({ type: 'asset', fileName: 'licenses/fonts.html', source: `<!doctype html><html lang="en"><meta charset="utf-8"><title>Bundled font licenses</title><h1>Font notices · SIL OFL 1.1</h1>${notices}</html>` })
    },
  }],
  base: '/admin/',
  server: { proxy: { '/admin/api': { target: 'https://127.0.0.1:8443', secure: false } } },
  build: { target: 'es2022' },
})
