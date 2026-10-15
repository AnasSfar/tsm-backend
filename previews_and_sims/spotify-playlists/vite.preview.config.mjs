// Preview of the /playlists page (tsm-frontend worktree, branch
// claude/spotify-playlists) on port 3040, /api proxied to the PROD API (read-only).
import react from "file:///C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend/node_modules/@vitejs/plugin-react/dist/index.js";

export default {
  root: "C:/Users/sfara/Documents/GitHub/tsm-frontend-playlists/frontend",
  envDir: "C:/Users/sfara/Documents/GitHub/tsm-backend/previews_and_sims/spotify-playlists/env",
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify("preview") },
  server: {
    port: 3040,
    strictPort: true,
    proxy: { "/api": { target: "https://thetsmuseum.app", changeOrigin: true, secure: true } },
  },
};
