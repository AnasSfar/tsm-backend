// Preview of the album ranking pages on their own port (3020), /api proxied to
// the PROD API (read-only: leaderboard GETs + site settings). Ranking POSTs
// are skipped on localhost by client.js and also aborted by shoot.py.
import react from "file:///C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend/node_modules/@vitejs/plugin-react/dist/index.js";

export default {
  root: "C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend",
  envDir: "C:/Users/sfara/Documents/GitHub/tsm-backend/previews_and_sims/ranking-share-redesign/env",
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify("preview") },
  server: {
    port: 3020,
    strictPort: true,
    proxy: { "/api": { target: "https://thetsmuseum.app", changeOrigin: true, secure: true } },
  },
};
