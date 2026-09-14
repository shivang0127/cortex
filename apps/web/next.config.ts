import path from "node:path";
import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

// One `.env` for the whole repository (see .env.example at the root). Next.js
// only reads env files from its own directory, so load the root one explicitly.
// NEXT_PUBLIC_* values are inlined into the browser bundle at build time.
loadEnvConfig(path.resolve(__dirname, "../.."));

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000",
  },
};

export default nextConfig;
