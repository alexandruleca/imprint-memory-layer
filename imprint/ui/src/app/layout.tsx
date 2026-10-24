import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { DashboardShell } from "@/components/dashboard-shell";
import { Heartbeat } from "@/components/heartbeat";
import { JobToasts } from "@/components/job-toasts";
import { RootApprovalDialog } from "@/components/root-approval-dialog";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const inter = localFont({
  src: "./fonts/Inter-Variable.ttf",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
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
    <html lang="en" className={`${inter.variable} h-full`}>
      <body className="h-full bg-background text-foreground flex">
        <TooltipProvider>
          <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-background focus:p-3">Skip to content</a>
          <Heartbeat />
          <DashboardShell>{children}</DashboardShell>
          <RootApprovalDialog />
          <JobToasts />
          <Toaster />
        </TooltipProvider>
      </body>
    </html>
  );
}
