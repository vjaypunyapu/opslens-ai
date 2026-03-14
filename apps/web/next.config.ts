import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Proxy /api/* to the FastAPI backend so the frontend never exposes the backend URL
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
  // Allow images from your CDN / avatar sources
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "*.clerk.com" },
      { protocol: "https", hostname: "avatars.githubusercontent.com" },
    ],
  },
};

export default nextConfig;
