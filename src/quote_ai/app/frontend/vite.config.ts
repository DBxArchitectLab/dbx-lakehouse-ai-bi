// The React screens of QuoteAI v1. `npm run dev` proxies /api to a local back end
// (QUOTE_AI_DEMO=1 QUOTE_AI_LOCAL_USER=rep.alice uvicorn backend.main:app --port 8000, from src/quote_ai/app).
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist", emptyOutDir: true },
});
