import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    // App logos served by Composio for the Connector Hub.
    remotePatterns: [{ protocol: "https", hostname: "logos.composio.dev" }],
  },
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
