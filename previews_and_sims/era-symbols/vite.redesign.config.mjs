// Preview of the era-symbols feature (tsm-frontend-redesign worktree, branch
// redesign/eras-theme) on port 3031, /api proxied to the PROD API (read-only).
import react from "file:///C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend/node_modules/@vitejs/plugin-react/dist/index.js";

export default {
  root: "C:/Users/sfara/Documents/GitHub/tsm-frontend-redesign/frontend",
  envDir: "C:/Users/sfara/Documents/GitHub/tsm-backend/previews_and_sims/era-symbols/env",
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify("preview") },
  server: {
    port: 3031,
    strictPort: true,
    proxy: { "/api": { target: "https://thetsmuseum.app", changeOrigin: true, secure: true } },
  },
};
