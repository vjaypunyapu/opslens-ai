import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/contexts/ThemeContext";
import "./globals.css";

// All routes require authentication and ClerkProvider needs a live router
// context — static pre-rendering is not possible or useful for this app.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "OpsLens AI — Operational Intelligence Copilot",
  description: "Ask questions about your business operations in plain English.",
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider afterSignInUrl="/dashboard" afterSignUpUrl="/dashboard">
      <html lang="en" suppressHydrationWarning>
        <body className="min-h-screen">
          <ThemeProvider>
            {children}
            <Toaster position="bottom-right" richColors />
          </ThemeProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
