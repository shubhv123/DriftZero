/** @type {import('next').NextConfig} */
process.env.BACKEND_URL = process.env.BACKEND_URL || 'http://backend:8000';

const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: process.env.BACKEND_URL + "/api/:path*",
      },
    ];
  },
};

module.exports = nextConfig;

