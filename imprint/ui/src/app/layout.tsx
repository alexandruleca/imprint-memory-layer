import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { DashboardShell } from "@/components/dashboard-shell";
import { Heartbeat } from "@/components/heartbeat";
import { JobToasts } from "@/components/job-toasts";
import { RecorderBridge } from "@/components/recorder-bridge";
import { RootApprovalDialog } from "@/components/root-approval-dialog";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const inter = localFont({
  src: "./fonts/Inter-Variable.ttf",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
});

// Applies the stored theme (lib/theme.ts) while the HTML is parsed, before
// the first paint, so a dark dashboard never flashes light. It applies again
// when another window changes the choice or the system's setting changes, so
// pages without the theme menu (the orb, its ring, Echo) follow as well.
const THEME_SCRIPT = `(function(){function a(){try{var c=localStorage.getItem("imprint.theme");if(c!=="light"&&c!=="dark")c="system";var d=c==="dark"||(c==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);var r=document.documentElement;r.classList.toggle("dark",d);r.style.colorScheme=d?"dark":"light";r.dataset.themeChoice=c}catch(e){}}a();try{addEventListener("storage",function(e){if(e.key==="imprint.theme")a()});matchMedia("(prefers-color-scheme: dark)").addEventListener("change",a)}catch(e){}})()`;

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
    <html lang="en" className={`${inter.variable} h-full`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="h-full bg-background text-foreground flex">
        <TooltipProvider>
          <DashboardShell overlays={<><Heartbeat /><RootApprovalDialog /><JobToasts /><RecorderBridge /><Toaster /></>}>{children}</DashboardShell>
        </TooltipProvider>
      </body>
    </html>
  );
}
