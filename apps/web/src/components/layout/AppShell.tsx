import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main
        className="flex-1 overflow-auto"
        style={{ marginLeft: "var(--sidebar-width)" }}
      >
        {children}
      </main>
    </div>
  );
}
