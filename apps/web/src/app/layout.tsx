import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/contexts/ThemeContext";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpsLens AI — Operational Intelligence Copilot",
  description: "Ask questions about your business operations in plain English.",
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider afterSignInUrl="/chat" afterSignUpUrl="/chat">
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
