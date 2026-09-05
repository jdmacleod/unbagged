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
    // the app has to work with the network cable pulled out. That guarantee
    // comes from importing nothing external, not from this setting.
    //
    // 0, not Vite's default 4096. An inlined asset becomes a `data:` URI, and
    // the Content-Security-Policy in api.py serves `img-src 'self'` with no
    // `data:` — the app loads none today, and allowing them would widen the
    // directive that most limits what injected markup can pull in. At 0 every
    // asset stays a same-origin file under /assets, which the policy already
    // permits. tests/test_frontend_build.py asserts no data: URI survives the
    // build, so raising this again fails there rather than in a browser.
    assetsInlineLimit: 0,
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
