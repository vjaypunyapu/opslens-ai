"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserButton } from "@clerk/nextjs";
import { MessageSquare, Lightbulb, Bell, Plug, BarChart2, Settings, AlertTriangle, Shield, FileText, Route, Sun, Moon } from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";

const NAV = [
  { href: "/dashboard",    label: "Dashboard",    icon: BarChart2 },
  { href: "/chat",         label: "Chat",         icon: MessageSquare },
  { href: "/incidents",    label: "Incidents",    icon: AlertTriangle },
  { href: "/rrt-briefs",     label: "RRT Briefs",     icon: FileText },
  { href: "/routing-rules",  label: "Routing Rules",  icon: Route },
  { href: "/insights",       label: "Insights",       icon: Lightbulb },
  { href: "/alerts",       label: "Alerts",       icon: Bell },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/settings",     label: "Settings",     icon: Settings },
  { href: "/admin",        label: "Admin",        icon: Shield },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const { isDark, toggle } = useTheme();
  return (
    <aside style={{
      position: "fixed", top: 0, left: 0, bottom: 0, width: "260px",
      background: "#0f172a", color: "#fff",
      display: "flex", flexDirection: "column", zIndex: 40,
      borderRight: "1px solid rgba(255,255,255,0.06)",
    }}>
      <div style={{ height: "64px", display: "flex", alignItems: "center", gap: "12px",
        padding: "0 20px", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
        <div style={{ width: "32px", height: "32px", borderRadius: "8px", background: "#14b8a6",
          display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <BarChart2 size={16} color="#fff" />
        </div>
        <div>
          <div style={{ fontSize: "13px", fontWeight: 700, color: "#fff", lineHeight: 1.2 }}>OpsLens AI</div>
          <div style={{ fontSize: "10px", color: "#64748b", marginTop: "2px" }}>Intelligence Copilot</div>
        </div>
      </div>

      <nav style={{ flex: 1, overflowY: "auto", padding: "16px 12px" }}>
        <div style={{ fontSize: "10px", fontWeight: 600, color: "#475569",
          textTransform: "uppercase", letterSpacing: "0.1em", padding: "0 12px 8px" }}>
          Navigation
        </div>
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link key={href} href={href} style={{
              display: "flex", alignItems: "center", gap: "12px",
              padding: "10px 12px", borderRadius: "8px", marginBottom: "2px",
              textDecoration: "none", fontSize: "14px", fontWeight: 500,
              background: active ? "rgba(20,184,166,0.15)" : "transparent",
              color: active ? "#2dd4bf" : "#94a3b8",
              border: active ? "1px solid rgba(20,184,166,0.25)" : "1px solid transparent",
            }}>
              <Icon size={16} />
              {label}
              {active && <span style={{ marginLeft: "auto", width: "6px", height: "6px",
                borderRadius: "50%", background: "#2dd4bf" }} />}
            </Link>
          );
        })}
      </nav>

      <div style={{ borderTop: "1px solid rgba(255,255,255,0.08)", padding: "12px 16px",
        display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <UserButton appearance={{ elements: { avatarBox: { width: 28, height: 28 } } }} />
          <span style={{ fontSize: "12px", color: "#64748b" }}>My Account</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          {/* Dark / Light toggle */}
          <button
            onClick={toggle}
            title={isDark ? "Switch to light mode" : "Switch to dark mode"}
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              width: "28px", height: "28px", borderRadius: "6px", border: "none",
              cursor: "pointer", background: "rgba(255,255,255,0.06)",
              color: isDark ? "#fbbf24" : "#94a3b8",
              transition: "background 0.15s, color 0.15s",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.12)")}
            onMouseLeave={e => (e.currentTarget.style.background = "rgba(255,255,255,0.06)")}
          >
            {isDark ? <Sun size={14} /> : <Moon size={14} />}
          </button>
          <Link href="/settings" style={{ color: "#475569", display: "flex",
            width: "28px", height: "28px", alignItems: "center", justifyContent: "center",
            borderRadius: "6px",
          }}>
            <Settings size={15} />
          </Link>
        </div>
      </div>
    </aside>
  );
}
