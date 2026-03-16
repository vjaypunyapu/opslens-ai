"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  BarChart2, Lightbulb, FileText, Plug, MessageSquare,
  Bell, TrendingUp, RefreshCw, AlertTriangle, CheckCircle,
  Clock, Zap, ArrowRight,
} from "lucide-react";
import { dashboardApi, DashboardData } from "@/lib/api";

const SOURCE_LABELS: Record<string, string> = {
  slack: "Slack", jira: "Jira", github: "GitHub",
  gdrive: "Google Drive", zendesk: "Zendesk", hubspot: "HubSpot",
};

const SOURCE_COLORS: Record<string, string> = {
  slack: "#4A154B", jira: "#0052CC", github: "#24292F",
  gdrive: "#4285F4", zendesk: "#03363D", hubspot: "#FF7A59",
};

const MAGNITUDE_COLOR: Record<string, string> = {
  critical: "#ef4444", high: "#f97316", medium: "#eab308", low: "#22c55e",
};

function KpiCard({
  icon: Icon, label, value, sub, color = "#14b8a6", href,
}: {
  icon: React.ElementType; label: string; value: number | string;
  sub?: string; color?: string; href?: string;
}) {
  const inner = (
    <div style={{
      background: "#1e293b", borderRadius: 12, padding: "20px 24px",
      border: "1px solid rgba(255,255,255,0.07)",
      display: "flex", alignItems: "center", gap: 16,
      transition: "border-color 0.15s",
    }}>
      <div style={{
        width: 44, height: 44, borderRadius: 10,
        background: `${color}22`, display: "flex",
        alignItems: "center", justifyContent: "center", flexShrink: 0,
      }}>
        <Icon size={20} color={color} />
      </div>
      <div>
        <div style={{ fontSize: 26, fontWeight: 700, color: "#f1f5f9", lineHeight: 1 }}>
          {value}
        </div>
        <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>{label}</div>
        {sub && <div style={{ fontSize: 11, color: color, marginTop: 2 }}>{sub}</div>}
      </div>
    </div>
  );
  return href ? (
    <Link href={href} style={{ textDecoration: "none" }}>{inner}</Link>
  ) : inner;
}

export default function DashboardPage() {
  const { getToken } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    else setRefreshing(true);
    try {
      const token = await getToken();
      if (!token) return;
      const d = await dashboardApi.get(token);
      setData(d);
      setError(null);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
      height: "100%", color: "#64748b", gap: 12 }}>
      <RefreshCw size={18} style={{ animation: "spin 1s linear infinite" }} />
      Loading dashboard…
    </div>
  );

  if (error) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
      height: "100%", color: "#ef4444", gap: 12 }}>
      <AlertTriangle size={18} /> {error}
    </div>
  );

  if (!data) return null;

  const { insights, documents, integrations, chat, alerts } = data;

  return (
    <div style={{ padding: "28px 32px", maxWidth: 1200, margin: "0 auto" }}>
      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9", margin: 0 }}>
            Workspace Dashboard
          </h1>
          <p style={{ fontSize: 13, color: "#64748b", margin: "4px 0 0" }}>
            Last updated {new Date(data.generated_at).toLocaleTimeString()}
          </p>
        </div>
        <button
          onClick={() => load(false)}
          disabled={refreshing}
          style={{
            display: "flex", alignItems: "center", gap: 8,
            background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.3)",
            borderRadius: 8, padding: "8px 16px", color: "#2dd4bf",
            fontSize: 13, fontWeight: 500, cursor: "pointer",
          }}
        >
          <RefreshCw size={14} style={refreshing ? { animation: "spin 1s linear infinite" } : {}} />
          Refresh
        </button>
      </div>

      {/* ── KPI Grid ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 16, marginBottom: 32 }}>
        <KpiCard icon={Lightbulb}    label="Active Insights"      value={insights.active}           sub={`+${insights.new_7d} this week`}     color="#f59e0b" href="/insights" />
        <KpiCard icon={FileText}     label="Documents Indexed"    value={documents.total.toLocaleString()} sub={`+${documents.indexed_7d} this week`} color="#6366f1" />
        <KpiCard icon={Plug}         label="Active Integrations"  value={`${integrations.active}/${integrations.total}`} sub="sources connected" color="#14b8a6" href="/integrations" />
        <KpiCard icon={MessageSquare} label="Chat Sessions"       value={chat.total_sessions}        sub={`${chat.sessions_7d} this week`}     color="#3b82f6" href="/chat" />
        <KpiCard icon={Bell}         label="Active Alert Rules"   value={alerts.active_rules}        sub="monitoring enabled"                  color="#ec4899" href="/alerts" />
        <KpiCard icon={CheckCircle}  label="Resolved Insights"    value={insights.resolved}          sub="all time"                            color="#22c55e" />
      </div>

      {/* ── Two-column ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 20 }}>

        {/* Recent Insights */}
        <Section title="Recent Insights" icon={Lightbulb} action={{ label: "View all", href: "/insights" }}>
          {insights.recent.length === 0 ? (
            <EmptyState icon={Lightbulb} message="No insights yet. Connect a data source to get started." />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {insights.recent.map(ins => (
                <div key={ins.id} style={{
                  display: "flex", alignItems: "flex-start", gap: 10,
                  padding: "10px 12px", background: "rgba(255,255,255,0.03)",
                  borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)",
                }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: "50%", marginTop: 5, flexShrink: 0,
                    background: MAGNITUDE_COLOR[ins.magnitude] || "#94a3b8",
                  }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 500,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {ins.title}
                    </div>
                    <div style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>
                      {ins.insight_type.replace(/_/g, " ")} ·{" "}
                      {new Date(ins.generated_at).toLocaleDateString()}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>

        {/* Connected Sources */}
        <Section title="Data Sources" icon={Plug} action={{ label: "Manage", href: "/integrations" }}>
          {integrations.total === 0 ? (
            <EmptyState icon={Plug} message="No sources connected yet." cta={{ label: "Connect a source", href: "/integrations" }} />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {integrations.sources.map(src => (
                <div key={src.id} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "10px 12px", background: "rgba(255,255,255,0.03)",
                  borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: 6, flexShrink: 0,
                      background: `${SOURCE_COLORS[src.source_type] || "#334155"}33`,
                      display: "flex", alignItems: "center", justifyContent: "center",
                    }}>
                      <Plug size={13} color={SOURCE_COLORS[src.source_type] || "#64748b"} />
                    </div>
                    <div>
                      <div style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 500 }}>
                        {SOURCE_LABELS[src.source_type] || src.source_type}
                      </div>
                      <div style={{ fontSize: 11, color: "#64748b" }}>
                        {src.last_synced_at
                          ? `Synced ${new Date(src.last_synced_at).toLocaleDateString()}`
                          : "Never synced"}
                      </div>
                    </div>
                  </div>
                  <StatusBadge status={src.status} />
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>

      {/* ── Two-column bottom ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>

        {/* Document distribution */}
        <Section title="Indexed Documents by Source" icon={FileText}>
          {Object.keys(documents.by_source).length === 0 ? (
            <EmptyState icon={FileText} message="No documents indexed yet." />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {Object.entries(documents.by_source)
                .sort(([, a], [, b]) => b - a)
                .map(([src, count]) => {
                  const pct = documents.total > 0 ? Math.round((count / documents.total) * 100) : 0;
                  return (
                    <div key={src}>
                      <div style={{ display: "flex", justifyContent: "space-between",
                        fontSize: 12, color: "#94a3b8", marginBottom: 4 }}>
                        <span>{SOURCE_LABELS[src] || src}</span>
                        <span>{count.toLocaleString()} ({pct}%)</span>
                      </div>
                      <div style={{ height: 6, background: "rgba(255,255,255,0.06)", borderRadius: 3 }}>
                        <div style={{
                          height: "100%", borderRadius: 3,
                          width: `${pct}%`,
                          background: SOURCE_COLORS[src] || "#14b8a6",
                        }} />
                      </div>
                    </div>
                  );
                })}
            </div>
          )}
        </Section>

        {/* Recent sessions */}
        <Section title="Recent Chat Sessions" icon={MessageSquare} action={{ label: "Open chat", href: "/chat" }}>
          {chat.recent_sessions.length === 0 ? (
            <EmptyState icon={MessageSquare} message="No chat sessions yet." cta={{ label: "Start a conversation", href: "/chat" }} />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {chat.recent_sessions.map(s => (
                <button
                  key={s.id}
                  onClick={() => router.push(`/chat/${s.id}`)}
                  style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "10px 12px", background: "rgba(255,255,255,0.03)",
                    borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)",
                    cursor: "pointer", textAlign: "left", width: "100%",
                  }}
                >
                  <MessageSquare size={14} color="#64748b" style={{ flexShrink: 0 }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 13, color: "#e2e8f0",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.title}
                    </div>
                    <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
                      {new Date(s.updated_at).toLocaleString()}
                    </div>
                  </div>
                  <ArrowRight size={12} color="#475569" style={{ flexShrink: 0 }} />
                </button>
              ))}
            </div>
          )}
        </Section>
      </div>

      {/* ── Quick Actions ── */}
      <div style={{
        marginTop: 24, padding: "16px 20px",
        background: "rgba(20,184,166,0.06)", borderRadius: 12,
        border: "1px solid rgba(20,184,166,0.15)",
        display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#2dd4bf" }}>
          <Zap size={16} />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Quick Actions</span>
        </div>
        {[
          { label: "Ask a question", href: "/chat", icon: MessageSquare },
          { label: "View insights", href: "/insights", icon: TrendingUp },
          { label: "Connect a source", href: "/integrations", icon: Plug },
          { label: "Set up alerts", href: "/alerts", icon: Bell },
        ].map(({ label, href, icon: Icon }) => (
          <Link key={href} href={href} style={{
            display: "flex", alignItems: "center", gap: 6,
            background: "rgba(255,255,255,0.06)", borderRadius: 8,
            padding: "6px 14px", fontSize: 12, fontWeight: 500, color: "#cbd5e1",
            textDecoration: "none", border: "1px solid rgba(255,255,255,0.08)",
          }}>
            <Icon size={13} />
            {label}
          </Link>
        ))}
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function Section({ title, icon: Icon, action, children }: {
  title: string; icon: React.ElementType;
  action?: { label: string; href: string };
  children: React.ReactNode;
}) {
  return (
    <div style={{
      background: "#1e293b", borderRadius: 12, padding: "20px",
      border: "1px solid rgba(255,255,255,0.07)",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#94a3b8" }}>
          <Icon size={15} />
          <span style={{ fontSize: 13, fontWeight: 600, color: "#cbd5e1" }}>{title}</span>
        </div>
        {action && (
          <Link href={action.href} style={{
            fontSize: 11, color: "#14b8a6", textDecoration: "none",
            display: "flex", alignItems: "center", gap: 4,
          }}>
            {action.label} <ArrowRight size={11} />
          </Link>
        )}
      </div>
      {children}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const cfg: Record<string, { bg: string; color: string }> = {
    active:  { bg: "rgba(34,197,94,0.12)",  color: "#22c55e" },
    pending: { bg: "rgba(234,179,8,0.12)",  color: "#eab308" },
    error:   { bg: "rgba(239,68,68,0.12)",  color: "#ef4444" },
  };
  const { bg, color } = cfg[status] || { bg: "rgba(148,163,184,0.1)", color: "#94a3b8" };
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: "3px 8px",
      borderRadius: 20, background: bg, color,
    }}>
      {status}
    </span>
  );
}

function EmptyState({ icon: Icon, message, cta }: {
  icon: React.ElementType; message: string;
  cta?: { label: string; href: string };
}) {
  return (
    <div style={{ textAlign: "center", padding: "24px 0", color: "#475569" }}>
      <Icon size={28} style={{ opacity: 0.4, marginBottom: 8 }} />
      <p style={{ fontSize: 13, margin: "0 0 12px" }}>{message}</p>
      {cta && (
        <Link href={cta.href} style={{
          fontSize: 12, color: "#14b8a6", textDecoration: "none",
          border: "1px solid rgba(20,184,166,0.3)", borderRadius: 6,
          padding: "5px 12px", display: "inline-block",
        }}>
          {cta.label}
        </Link>
      )}
    </div>
  );
}
