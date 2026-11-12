import { defineConfig } from "astro/config";
import react from "@astrojs/react";

// Static output on purpose: the phone reads the remote database directly, so
// there is no server to pay for and nothing to keep warm.
export default defineConfig({
  integrations: [react()],
  output: "static",
  build: {
    assets: "_assets",
  },
});
