import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [react()],
  base: "./",
  build: {
    outDir: fileURLToPath(new URL("../dist", import.meta.url)),
    emptyOutDir: true,
    // Electron 本地加载单页：React + lightweight-charts 合计约 500KB，
    // 拆包几乎无收益，仅抬高阈值避免误报。
    chunkSizeWarningLimit: 600,
  },
});
