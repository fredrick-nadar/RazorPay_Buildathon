import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "ARGUS CONTROL — Financial Flight Recorder",
  description:
    "Deterministic merchant reconciliation with proof-carrying corrections, human authority, and honest unresolved cases. Synthetic data only.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#08090b] font-sans text-zinc-200 antialiased">
        {children}
      </body>
    </html>
  );
}
