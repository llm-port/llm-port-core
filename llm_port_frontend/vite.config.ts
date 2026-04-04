import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

const apiProxyTarget =
  (process.env.VITE_API_PROXY_TARGET as string | undefined) ??
  "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter(), tsconfigPaths()],
  server: {
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
