import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", background: "var(--bg-app)" }}>
      <Sidebar />
      <main style={{ flex: 1, overflow: "auto", marginLeft: "260px", background: "var(--bg-app)", color: "var(--text-primary)" }}>
        {children}
      </main>
    </div>
  );
}
