import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          graph: ["@xyflow/react"],
          data: ["@tanstack/react-query", "zod"]
        }
      }
    }
  },
  server: {
    port: 5173
  }
});
