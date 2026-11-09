import type { NextConfig } from "next";

// Client-rendered dashboard over the LeadGraph API (api/main.py); no server data fetching.
const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
