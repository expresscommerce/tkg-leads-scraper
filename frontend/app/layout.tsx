import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lead Scraper",
  description: "Scrape Google Maps for business leads",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
