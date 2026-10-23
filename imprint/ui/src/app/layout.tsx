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
      <body className="h-full bg-background text-foreground flex">
        <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-background focus:p-3">Skip to content</a>
        <Heartbeat />
        <Sidebar />
        <main id="main-content" tabIndex={-1} className="flex-1 min-w-0 h-full overflow-auto">{children}</main>
      </body>
    </html>
  );
}
