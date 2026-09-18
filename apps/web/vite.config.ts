import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // hub-api (uvicorn :8000) — session cookie is same-origin through here.
      "/api": "http://localhost:8000",
      "/auth": "http://localhost:8000",
    },
  },
});
