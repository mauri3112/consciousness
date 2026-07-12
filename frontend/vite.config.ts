import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  const token = env.CONSCIOUSNESS_API_TOKEN;
  return ({
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
    port: 5173,
    proxy: {
      "/api": {
        target: env.CONSCIOUSNESS_API_UPSTREAM ?? "http://localhost:8770",
        changeOrigin: true,
        headers: token ? { Authorization: `Bearer ${token}` } : undefined
      }
    }
  }
  });
});
