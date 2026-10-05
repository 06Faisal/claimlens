const dev = process.env.NODE_ENV !== "production"; // React dev mode needs eval for stack traces; production stays strict
const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";

module.exports = {
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Content-Security-Policy", value: `default-src 'self'; script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'` },
      ],
    }];
  },
};
