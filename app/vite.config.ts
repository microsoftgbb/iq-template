import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { managedApps } from "@microsoft/managed-apps-vite-plugin";
import { fileURLToPath } from "node:url";

// https://vite.dev/config/
export default defineConfig({
  root: fileURLToPath(new URL("../", import.meta.url)),
  publicDir: "app/public",
  plugins: [react(), managedApps()],
  build: {
    outDir: "app/dist",
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    strictPort: true,
  },
});
