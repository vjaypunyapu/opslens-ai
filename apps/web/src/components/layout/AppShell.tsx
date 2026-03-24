import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", background: "#0f172a" }}>
      <Sidebar />
      <main style={{ flex: 1, overflow: "auto", marginLeft: "260px", background: "#0f172a", color: "#f1f5f9" }}>
        {children}
      </main>
    </div>
  );
}
