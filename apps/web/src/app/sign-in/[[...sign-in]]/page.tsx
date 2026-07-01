"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { SignIn } from "@clerk/nextjs";

interface TenantBranding {
  name: string;
  logo_url: string | null;
}

function SignInInner() {
  const searchParams = useSearchParams();
  const org = searchParams.get("org");
  const [branding, setBranding] = useState<TenantBranding | null>(null);

  useEffect(() => {
    if (!org) return;
    fetch(`/api/v1/public/tenants/${encodeURIComponent(org)}/branding`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setBranding(data))
      .catch(() => setBranding(null));
  }, [org]);

  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      background: "#0F172A",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      position: "relative",
      overflow: "hidden",
      padding: "2rem",
    }}>
      {/* Teal glow — matches landing page */}
      <div style={{
        position: "absolute",
        top: "-20%",
        left: "50%",
        transform: "translateX(-50%)",
        width: "700px",
        height: "700px",
        background: "radial-gradient(circle, rgba(20,184,166,0.12) 0%, transparent 65%)",
        pointerEvents: "none",
      }} />

      {/* Client logo — shown above the OpsLens AI logo when signing in via a
          tenant-specific link (?org=<slug>) resolved through the public
          branding endpoint. Rendered as a plain <img> (not next/image or
          dangerouslySetInnerHTML) so an admin-supplied external SVG can
          never execute script in this context. */}
      {branding?.logo_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={branding.logo_url}
          alt={`${branding.name} logo`}
          style={{
            maxHeight: "48px", maxWidth: "220px", marginBottom: "1.25rem",
            position: "relative", zIndex: 1,
          }}
        />
      )}

      {/* Logo */}
      <a href="/" style={{
        display: "flex", alignItems: "center", gap: "0.6rem",
        marginBottom: "1.75rem", position: "relative", zIndex: 1,
        textDecoration: "none",
      }}>
        <div style={{
          width: "36px", height: "36px", background: "#14B8A6",
          borderRadius: "9px", display: "flex", alignItems: "center",
          justifyContent: "center", fontWeight: 800, fontSize: "18px", color: "#0F172A",
        }}>O</div>
        <span style={{ fontSize: "1.2rem", fontWeight: 700, color: "#F1F5F9" }}>OpsLens AI</span>
      </a>

      {/* Clerk SignIn */}
      <div style={{ position: "relative", zIndex: 1 }}>
        <SignIn
          forceRedirectUrl="/dashboard"
          fallbackRedirectUrl="/dashboard"
          appearance={{
            variables: {
              colorPrimary: "#14B8A6",
              colorBackground: "#1E293B",
              colorInputBackground: "#0F172A",
              colorInputText: "#F1F5F9",
              colorText: "#F1F5F9",
              colorTextSecondary: "#94A3B8",
              colorNeutral: "#334155",
              colorDanger: "#EF4444",
              borderRadius: "10px",
              fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
              fontSize: "15px",
            },
            elements: {
              card: {
                background: "#1E293B",
                border: "1px solid rgba(20,184,166,0.15)",
                boxShadow: "0 40px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(20,184,166,0.08)",
                borderRadius: "16px",
              },
              headerTitle: { color: "#F1F5F9", fontSize: "1.4rem", fontWeight: "800" },
              headerSubtitle: { color: "#94A3B8" },
              socialButtonsBlockButton: {
                background: "#0F172A",
                border: "1px solid #334155",
                color: "#F1F5F9",
                borderRadius: "8px",
              },
              dividerLine: { background: "#334155" },
              dividerText: { color: "#94A3B8" },
              formFieldLabel: {
                color: "#94A3B8",
                fontSize: "0.78rem",
                fontWeight: "600",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              },
              formFieldInput: {
                background: "#0F172A",
                border: "1px solid #334155",
                borderRadius: "8px",
                color: "#F1F5F9",
                fontSize: "0.925rem",
              },
              formButtonPrimary: {
                background: "#14B8A6",
                color: "#0F172A",
                fontWeight: "700",
                fontSize: "1rem",
                borderRadius: "8px",
                boxShadow: "0 0 20px rgba(20,184,166,0.25)",
              },
              // Forgot password link — visible in teal
              formFieldAction: {
                color: "#14B8A6",
                fontWeight: "600",
                fontSize: "0.8rem",
              },
              footerActionLink: { color: "#14B8A6", fontWeight: "600" },
              // Hide only the "Don't have an account? Sign up" row
              footerAction: { display: "none" },
              identityPreviewText: { color: "#F1F5F9" },
              identityPreviewEditButton: { color: "#14B8A6" },
              formResendCodeLink: { color: "#14B8A6" },
              alertText: { color: "#F1F5F9" },
            },
          }}
        />
      </div>

      <p style={{
        marginTop: "1rem", fontSize: "0.8rem", color: "#64748B",
        position: "relative", zIndex: 1,
      }}>
        © 2025 OpsLens AI &nbsp;·&nbsp;
        <a href="/" style={{ color: "#14B8A6", textDecoration: "none" }}>Back to home</a>
      </p>
    </div>
  );
}

export default function SignInPage() {
  return (
    <Suspense>
      <SignInInner />
    </Suspense>
  );
}
