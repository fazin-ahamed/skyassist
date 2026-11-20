import { resolve } from "node:path";
import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  turbopack: {
    root: resolve(__dirname, ".."),
    rules: {
      "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" },
    },
  },
};

export default function config(phase: string): NextConfig {
  if (phase !== PHASE_DEVELOPMENT_SERVER) return nextConfig;

  const api = process.env.SKYASSIST_DEV_API ?? "http://127.0.0.1:8000";
  const paths = [
    "/clock", "/flights", "/flights/understand", "/i18n/:path*", "/journeys/:path*", "/route/:path*", "/staff/cases/:path*",
    "/demo/:path*", "/map/:path*", "/system/:path*", "/voice/:path*", "/traffic/:path*", "/eval/:path*",
  ];
  return {
    ...nextConfig,
    output: undefined,
    trailingSlash: false,
    skipTrailingSlashRedirect: true,
    devIndicators: false,
    async rewrites() {
      return paths.map((path) => ({ source: path, destination: api + path }));
    },
  };
}
