import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Physical MCP",
  description: "An AI that can see a control panel and press the buttons, and asks a human before anything risky.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
