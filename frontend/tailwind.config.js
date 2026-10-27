/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#090d16",
        foreground: "#f8fafc",
        card: {
          DEFAULT: "rgba(15, 23, 42, 0.75)",
          foreground: "#f8fafc",
          border: "rgba(51, 65, 85, 0.4)",
        },
        nebius: {
          50: "#ecfdf5",
          400: "#34d399",
          500: "#10b981",
          600: "#059669",
        },
        super: {
          DEFAULT: "#10b981",
          glow: "rgba(16, 185, 129, 0.25)",
        },
        ultra: {
          DEFAULT: "#a855f7",
          glow: "rgba(168, 85, 247, 0.25)",
        },
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
        "3xl": "1.5rem",
      },
      animation: {
        "pulse-subtle": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "glow-slow": "glow 4s ease-in-out infinite alternate",
      },
      keyframes: {
        glow: {
          "0%": { opacity: 0.4, transform: "scale(1)" },
          "100%": { opacity: 0.8, transform: "scale(1.05)" },
        },
      },
    },
  },
  plugins: [],
};
