// Builds the web pages' scripts, stylesheet and fonts into files named after their content, with
// a manifest through which the web layer finds them; browsers keep each file until it changes.
import { defineConfig } from "vite";

export default defineConfig({
  publicDir: false,
  base: "/static/build/",
  build: {
    outDir: "src/openfesl/web/static/build",
    emptyOutDir: true,
    manifest: "manifest.json",
    modulePreload: false,
    rolldownOptions: {
      input: {
        head: "src/openfesl/web/assets/head.js",
        app: "src/openfesl/web/assets/app.js",
      },
    },
  },
});
