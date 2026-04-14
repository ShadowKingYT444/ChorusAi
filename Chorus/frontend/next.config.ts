import path from "node:path";
import type { NextConfig } from "next";

/**
 * LAN / multi-device dev: Next blocks HMR (`/_next/webpack-hmr`) unless the client
 * is allowlisted. Next accepts **bare hostnames** (e.g. `10.232.44.141`) and/or
 * full origins (`http://192.168.1.5:3000`).
 *
 * `frontend/.env.local` (comma-separated):
 *   NEXT_ALLOWED_DEV_ORIGINS=10.232.44.141,http://192.168.1.5:3000
 *
 * If you only list `http://…:3000`, we also add the hostname so it matches Next’s check.
 */
function expandAllowedDevOrigins(raw: string[]): string[] {
  const out = new Set<string>();
  for (const entry of raw) {
    if (!entry) continue;
    out.add(entry);
    if (entry.includes("://")) {
      try {
        out.add(new URL(entry).hostname);
      } catch {
        /* ignore invalid URL */
      }
    }
  }
  return [...out];
}

const fromEnv =
  process.env.NEXT_ALLOWED_DEV_ORIGINS?.split(",").map((s) => s.trim()).filter(Boolean) ?? [];

const nextConfig: NextConfig = {
  allowedDevOrigins: expandAllowedDevOrigins([
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    ...fromEnv,
  ]),
  turbopack: {
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
