import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DriftZero — Autonomous Breaking-Change Migration Engine",
  description:
    "Zero-Token AST Discovery, Multi-Endpoint Tavily Grounding, and Complexity-Aware Nebius Token Factory Router.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[#070b12] text-slate-100 antialiased selection:bg-blue-500/30 selection:text-blue-200">
        {children}
      </body>
    </html>
  );
}
