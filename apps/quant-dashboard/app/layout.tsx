import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Qlib · Quant Research Console",
  description: "Nasdaq-100 medium/low-frequency strategy research dashboard",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
