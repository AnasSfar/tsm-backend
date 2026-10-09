// Preview of the era-symbols feature (tsm-frontend worktree, branch
// feature/era-symbols) on port 3030, /api proxied to the PROD API (read-only).
import react from "file:///C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend/node_modules/@vitejs/plugin-react/dist/index.js";

export default {
  root: "C:/Users/sfara/Documents/GitHub/tsm-frontend-era-symbols/frontend",
  envDir: "C:/Users/sfara/Documents/GitHub/tsm-backend/previews_and_sims/era-symbols/env",
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify("preview") },
  server: {
    port: 3030,
    strictPort: true,
    proxy: { "/api": { target: "https://thetsmuseum.app", changeOrigin: true, secure: true } },
  },
};
