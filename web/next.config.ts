import path from "node:path";
import type { NextConfig } from "next";

// the contract + fixtures live in ../shared (Pam's), so let the bundler look one folder up
const repoRoot = path.join(__dirname, "..");

const nextConfig: NextConfig = {
  outputFileTracingRoot: repoRoot,
  turbopack: {
    root: repoRoot,
  },
};

export default nextConfig;
