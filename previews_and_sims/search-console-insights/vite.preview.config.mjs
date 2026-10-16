// Local preview server for the Search Console admin + per-page panel on its own ports (3010 ->
// API 8013), so it never collides with the owner's dev.bat (3000/8003).
import react from "file:///C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend/node_modules/@vitejs/plugin-react/dist/index.js";

export default {
  root: "C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend",
  // No .env files: frontend/.env.local points VITE_API_BASE at the owner's
  // API (and holds a dev token) — here every /api call goes through the proxy.
  envDir: "C:/Users/sfara/Documents/GitHub/tsm-backend/previews_and_sims/search-console-insights/env",
  plugins: [react()],
  // @supabase/supabase-js is not installed in the local node_modules: stub it
  // (accounts are off by default) instead of touching the owner's install.
  resolve: { alias: { "@supabase/supabase-js": "C:/Users/sfara/Documents/GitHub/tsm-backend/previews_and_sims/search-console-insights/supabase-stub.js" } },
  define: { __APP_VERSION__: JSON.stringify("preview") },
  server: {
    port: 3010,
    strictPort: true,
    proxy: { "/api": { target: "http://localhost:8013", changeOrigin: true } },
  },
};
