// Local preview server for the admin Image Studio on its own ports (3010 ->
// API 8013), so it never collides with the owner's dev.bat (3000/8003).
import react from "file:///C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend/node_modules/@vitejs/plugin-react/dist/index.js";

export default {
  root: "C:/Users/sfara/Documents/GitHub/tsm-frontend/frontend",
  // No .env files: frontend/.env.local points VITE_API_BASE at the owner's
  // API (and holds a dev token) — here every /api call goes through the proxy.
  envDir: "C:/Users/sfara/Documents/GitHub/tsm-backend/previews_and_sims/image-studio-picker/env",
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify("preview") },
  server: {
    port: 3010,
    strictPort: true,
    proxy: { "/api": { target: "http://localhost:8013", changeOrigin: true } },
  },
};
