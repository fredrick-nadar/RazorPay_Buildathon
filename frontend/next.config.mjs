/**
 * Backend origin for the /api proxy.
 *
 * Defaults to the local development backend, unchanged, and is overridable so
 * the proxy target is not hard-coded for a non-local deployment.
 *
 * Note: Next resolves rewrites at BUILD time into the routes manifest, so this
 * value is fixed by `next build` and cannot be changed by `next start`. A
 * different target therefore requires a rebuild.
 */
const backendOrigin = process.env.ARGUS_BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
