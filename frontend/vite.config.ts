import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The build lands inside the Python package so one uvicorn process serves both
// the API and the UI. One container, one service — a separate frontend
// container would be friction with no payoff for a single local user.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/unbagged/static",
    emptyOutDir: true,
    // No CDN requests, ever: a request to a third party leaks usage timing, and
    // the app has to work with the network cable pulled out.
    assetsInlineLimit: 4096,
  },
  server: {
    // 0.0.0.0 only inside a container; the compose file publishes it on
    // 127.0.0.1 so it is still not reachable from the network.
    host: process.env.VITE_API_PROXY ? "0.0.0.0" : "127.0.0.1",
    port: 5173,
    proxy: {
      // Locally the backend is on the host; under `make dev` it is a compose
      // service reached by name.
      "/api": process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000",
    },
  },
});
