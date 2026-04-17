import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
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
    }}>
      {/* Teal glow background — matches landing page hero */}
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

      {/* Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "1.75rem", position: "relative", zIndex: 1 }}>
        <div style={{
          width: "36px", height: "36px", background: "#14B8A6",
          borderRadius: "9px", display: "flex", alignItems: "center",
          justifyContent: "center", fontWeight: 800, fontSize: "18px", color: "#0F172A",
        }}>O</div>
        <span style={{ fontSize: "1.2rem", fontWeight: 700, color: "#F1F5F9" }}>OpsLens AI</span>
      </div>

      {/* Clerk SignIn with full dark theme */}
      <div style={{ position: "relative", zIndex: 1 }}>
        <SignIn
          forceRedirectUrl="/chat"
          fallbackRedirectUrl="/chat"
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
              colorSuccess: "#14B8A6",
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
                padding: "2rem",
              },
              headerTitle: {
                color: "#F1F5F9",
                fontSize: "1.4rem",
                fontWeight: "800",
              },
              headerSubtitle: {
                color: "#94A3B8",
              },
              socialButtonsBlockButton: {
                background: "#0F172A",
                border: "1px solid #334155",
                color: "#F1F5F9",
                borderRadius: "8px",
              },
              socialButtonsBlockButton__hover: {
                borderColor: "#14B8A6",
              },
              dividerLine: {
                background: "#334155",
              },
              dividerText: {
                color: "#94A3B8",
              },
              formFieldLabel: {
                color: "#94A3B8",
                fontSize: "0.8rem",
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
              formFieldInput__focus: {
                borderColor: "#14B8A6",
                boxShadow: "0 0 0 2px rgba(20,184,166,0.2)",
              },
              formButtonPrimary: {
                background: "#14B8A6",
                color: "#0F172A",
                fontWeight: "700",
                fontSize: "1rem",
                borderRadius: "8px",
                boxShadow: "0 0 20px rgba(20,184,166,0.25)",
              },
              formButtonPrimary__hover: {
                background: "#0D9488",
                boxShadow: "0 0 30px rgba(20,184,166,0.4)",
              },
              footerActionLink: {
                color: "#14B8A6",
                fontWeight: "600",
              },
              identityPreviewText: {
                color: "#F1F5F9",
              },
              identityPreviewEditButton: {
                color: "#14B8A6",
              },
              alertText: {
                color: "#F1F5F9",
              },
              formResendCodeLink: {
                color: "#14B8A6",
              },
            },
          }}
        />
      </div>

      {/* Footer */}
      <p style={{ marginTop: "2rem", fontSize: "0.8rem", color: "#64748B", position: "relative", zIndex: 1 }}>
        © 2025 OpsLens AI · <a href="/" style={{ color: "#14B8A6", textDecoration: "none" }}>Back to home</a>
      </p>
    </div>
  );
}
