import type { Metadata } from "next";
import { Roboto } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { Heartbeat } from "@/components/heartbeat";

const roboto = Roboto({
  variable: "--font-roboto",
  subsets: ["latin"],
  weight: ["300", "400", "500", "700"],
});

export const metadata: Metadata = {
  title: "Imprint",
  description: "AI memory dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${roboto.variable} dark h-full`}>
      <body className="min-h-full bg-background text-foreground flex">
        <Heartbeat />
        <Sidebar />
        <main className="flex-1 overflow-auto">{children}</main>
      </body>
    </html>
  );
}
