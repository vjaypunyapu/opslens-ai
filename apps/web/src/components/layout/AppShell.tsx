import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", background: "#f9fafb" }}>
      <Sidebar />
      <main style={{ flex: 1, overflow: "auto", marginLeft: "260px" }}>
        {children}
      </main>
    </div>
  );
}
