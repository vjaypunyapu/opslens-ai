/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverComponentsExternalPackages: ["@clerk/nextjs", "@clerk/backend", "@clerk/shared"],
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // API_URL is a server-side-only var (not NEXT_PUBLIC_) so it's always
        // available in next.config.mjs regardless of build-time inlining quirks.
        destination: (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://api:8000") + "/api/:path*",
      },
    ];
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "*.clerk.com" },
      { protocol: "https", hostname: "avatars.githubusercontent.com" },
    ],
  },
};
export default nextConfig;
