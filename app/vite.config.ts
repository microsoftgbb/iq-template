import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { readdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { brotliCompressSync, constants, gzipSync } from 'node:zlib'

const COMPRESSIBLE = new Set(['.html', '.js', '.css', '.json', '.svg', '.txt', '.map'])

// Writes brotli and gzip copies of the built text files next to them; the server sends them to browsers that
// accept them, so a session's first load moves a fraction of the bytes without compressing on every request.
function precompress(): Plugin {
  let outDir = ''
  return {
    name: 'precompress',
    apply: 'build',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir)
    },
    async closeBundle() {
      const entries = await readdir(outDir, { recursive: true, withFileTypes: true })
      await Promise.all(entries.filter((entry) => entry.isFile() && COMPRESSIBLE.has(path.extname(entry.name))).map(async (entry) => {
        const file = path.join(entry.parentPath, entry.name)
        const content = await readFile(file)
        if (content.length < 1024) return
        await writeFile(`${file}.br`, brotliCompressSync(content, { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } }))
        await writeFile(`${file}.gz`, gzipSync(content, { level: 9 }))
      }))
    },
  }
}

const root = fileURLToPath(new URL('.', import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  root,
  plugins: [react(), precompress()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // The app and MSAL's redirect bridge, each its own page.
    rolldownOptions: {
      input: {
        index: path.join(root, 'index.html'),
        redirect: path.join(root, 'redirect.html'),
      },
    },
  },
  server: {
    port: 3000,
    strictPort: true,
  },
})
