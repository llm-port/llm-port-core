import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

const apiProxyTarget =
  (process.env.VITE_API_PROXY_TARGET as string | undefined) ??
  "http://127.0.0.1:8000";

// Dev servers need to be reachable from other machines (e.g. a remote
// dev box accessed over the LAN), so the dev server binds all
// interfaces by default. Set VITE_HOST=127.0.0.1 (or localhost) to
// restrict to the local machine.
const devHost = (process.env.VITE_HOST as string | undefined) ?? "0.0.0.0";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter(), tsconfigPaths()],
  server: {
    host: devHost,
    port: 5173,
    proxy: {
      // Forward all /api requests to the FastAPI backend in dev
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
});
