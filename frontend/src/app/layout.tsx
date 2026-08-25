import type { Metadata } from "next";
import { VoiceController } from "../components/voice-controller";

import "./globals.css";

export const metadata: Metadata = {
  title: "ARGUS CONTROL — Financial Flight Recorder",
  description:
    "Deterministic merchant reconciliation with proof-carrying corrections, human authority, and honest unresolved cases. Synthetic data only.",
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/favicon.svg", type: "image/svg+xml" },
    ],
    shortcut: "/favicon.svg",
    apple: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className="min-h-screen bg-[#f8fafc] font-sans text-slate-900 antialiased"
        suppressHydrationWarning
      >
        {children}
        <VoiceController />
      </body>
    </html>
  );
}
