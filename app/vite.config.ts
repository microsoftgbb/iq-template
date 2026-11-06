import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Container App serves dist/ and proxies /api to the hosted agent (server/server.mjs).
// For local development, run `npm start` on port 8080 with the same settings and point Vite at it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:8080',
      '/config.json': 'http://localhost:8080',
    },
  },
})
