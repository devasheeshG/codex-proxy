import type { NextConfig } from "next";

const nextConfig: NextConfig = {
    output: "standalone",
    outputFileTracingRoot: process.cwd(),
    // Local-dev convenience only: when API_PROXY_TARGET is set (e.g. running
    // `next dev` outside the compose stack), proxy same-origin "/api" calls to a
    // backend so the dashboard works without the gateway. In production the var
    // is unset and the Caddy/Traefik gateway handles "/api" routing, so this adds
    // no rewrite and changes nothing.
    async rewrites() {
        const target = process.env.API_PROXY_TARGET;
        if (!target) return [];
        return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
    },
};

export default nextConfig;
