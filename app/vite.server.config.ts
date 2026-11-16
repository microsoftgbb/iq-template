import { defineConfig } from 'vite'
import { fileURLToPath } from 'node:url'

// The production server as one ES module (dist-server/main.mjs), with its dependencies and the shared protocol
// modules bundled in, so the container image needs no node_modules.
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  publicDir: false,
  build: {
    ssr: 'server/main.ts',
    outDir: 'dist-server',
    emptyOutDir: true,
    target: 'node24',
    sourcemap: true,
    minify: false,
    rolldownOptions: {
      output: {
        entryFileNames: 'main.mjs',
        codeSplitting: false,
      },
    },
  },
  ssr: {
    noExternal: true,
    target: 'node',
  },
})
