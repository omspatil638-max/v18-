import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "ContractLens",
  description: "Privacy-conscious AI contract intelligence",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="flex flex-col md:flex-row h-screen h-dvh overflow-hidden bg-slate-50">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
