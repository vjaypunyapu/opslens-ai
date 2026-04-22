/**
 * Founder-only layout — no sidebar, no AppShell.
 * Overrides the parent /admin/layout.tsx so the clients page
 * looks nothing like the regular app. Anyone without a platform-admin
 * email just sees the access-denied screen with zero nav to click around.
 */
import { AuthGuard } from "@/components/auth/AuthGuard";

export default function ClientsLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <div style={{
        minHeight: "100vh",
        background: "#0f172a",
        color: "#f1f5f9",
      }}>
        {/* Minimal header — no links back into the app */}
        <header style={{
          height: "56px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          display: "flex",
          alignItems: "center",
          padding: "0 1.5rem",
          gap: "0.75rem",
        }}>
          <div style={{
            width: 28, height: 28, borderRadius: 7,
            background: "#14b8a6",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: "0.75rem", fontWeight: 700, color: "#fff",
          }}>
            O
          </div>
          <span style={{ fontSize: "0.875rem", fontWeight: 600, color: "#f1f5f9" }}>
            OpsLens AI
          </span>
          <span style={{
            fontSize: "0.7rem", padding: "0.125rem 0.5rem",
            borderRadius: "9999px", fontWeight: 600,
            background: "rgba(20,184,166,0.15)", color: "#14b8a6",
            marginLeft: "0.25rem",
          }}>
            FOUNDER PANEL
          </span>
        </header>

        {/* Page content — full width, no sidebar offset */}
        <main style={{ padding: "2rem 1.5rem" }}>
          {children}
        </main>
      </div>
    </AuthGuard>
  );
}
