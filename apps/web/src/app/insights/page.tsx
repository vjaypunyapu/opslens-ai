"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { RefreshCw, Zap, Lightbulb, CheckCircle, BellOff, AlertTriangle, BookOpen, ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { InsightCard } from "@/components/insights/InsightCard";
import { INSIGHT_TYPE_LABELS } from "@/lib/utils";
import { Insight, InsightStatus, InsightSummary, InsightType } from "@/types";
import { insightsApi, analyticsApi, RetrospectiveResponse } from "@/lib/api";

const STATUS_TABS: { value: InsightStatus | "all"; label: string }[] = [
  { value: "all",      label: "All" },
  { value: "active",   label: "Active" },
  { value: "snoozed",  label: "Snoozed" },
  { value: "resolved", label: "Resolved" },
];

const TYPE_FILTERS: { value: InsightType | ""; label: string }[] = [
  { value: "",                    label: "All types" },
  { value: "complaint_spike",     label: INSIGHT_TYPE_LABELS.complaint_spike },
  { value: "feature_trend",       label: INSIGHT_TYPE_LABELS.feature_trend },
  { value: "release_correlation", label: INSIGHT_TYPE_LABELS.release_correlation },
  { value: "eng_bottleneck",      label: INSIGHT_TYPE_LABELS.eng_bottleneck },
  { value: "churn_risk",          label: INSIGHT_TYPE_LABELS.churn_risk },
];

export default function InsightsPage() {
  const { getToken } = useAuth();
  const [insights, setInsights]     = useState<Insight[]>([]);
  const [summary, setSummary]       = useState<InsightSummary | null>(null);
  const [status, setStatus]         = useState<InsightStatus | "all">("active");
  const [typeFilter, setTypeFilter] = useState<InsightType | "">("");
  const [loading, setLoading]       = useState(true);
  const [generating, setGenerating] = useState(false);

  // ── Retrospective ────────────────────────────────────────────────────────
  const today = new Date().toISOString().slice(0, 10);
  const twoWeeksAgo = new Date(Date.now() - 14 * 86400000).toISOString().slice(0, 10);
  const [retroStart, setRetroStart]     = useState(twoWeeksAgo);
  const [retroEnd, setRetroEnd]         = useState(today);
  const [retroData, setRetroData]       = useState<RetrospectiveResponse | null>(null);
  const [retroLoading, setRetroLoading] = useState(false);
  const [retroOpen, setRetroOpen]       = useState(true);

  const handleGenerateRetro = async () => {
    setRetroLoading(true);
    try {
      const token = await getToken();
      if (!token) return;
      const res = await analyticsApi.retrospective(token, retroStart, retroEnd);
      setRetroData(res);
      toast.success("Retrospective generated");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to generate retrospective");
    } finally {
      setRetroLoading(false);
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = await getToken();
      const [list, sum] = await Promise.all([
        insightsApi.list(token!, { status: status === "all" ? undefined : status, type: typeFilter || undefined }),
        insightsApi.summary(token!),
      ]);
      setInsights(list);
      setSummary(sum);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to load insights");
    } finally {
      setLoading(false);
    }
  }, [getToken, status, typeFilter]);

  useEffect(() => { load(); }, [load]);

  const handleStatusChange = async (id: string, newStatus: "active" | "resolved" | "snoozed", snoozeHours?: number) => {
    try {
      const token = await getToken();
      await insightsApi.updateStatus(id, newStatus, snoozeHours, token!);
      await load();
      toast.success(`Insight marked as ${newStatus}`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to update insight");
    }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const token = await getToken();
      await insightsApi.generate(token!);
      toast.success("Insight generation triggered — results will appear shortly");
      setTimeout(load, 3000);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to trigger insight generation");
    } finally {
      setGenerating(false);
    }
  };

  const kpis = [
    { label: "Active",   value: summary?.active ?? 0,                color: "#f59e0b", icon: Lightbulb },
    { label: "High",     value: summary?.by_magnitude?.high ?? 0,     color: "#ef4444", icon: AlertTriangle },
    { label: "Resolved", value: summary?.resolved ?? 0,              color: "#22c55e", icon: CheckCircle },
    { label: "Snoozed",  value: summary?.snoozed ?? 0,               color: "#64748b", icon: BellOff },
  ];

  return (
    <AppShell>
      <div style={{ padding: "28px 32px", maxWidth: 1100, margin: "0 auto" }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 28 }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9", margin: 0 }}>Insights</h1>
            <p style={{ fontSize: 13, color: "#64748b", margin: "4px 0 0" }}>
              AI-detected patterns across your operational data
            </p>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button onClick={load} disabled={loading} style={{
              display: "flex", alignItems: "center", gap: 7,
              background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8, padding: "8px 16px", color: "#cbd5e1",
              fontSize: 13, fontWeight: 500, cursor: "pointer",
            }}>
              <RefreshCw size={14} style={loading ? { animation: "spin 1s linear infinite" } : {}} />
              Refresh
            </button>
            <button onClick={handleGenerate} disabled={generating} style={{
              display: "flex", alignItems: "center", gap: 7,
              background: "rgba(20,184,166,0.15)", border: "1px solid rgba(20,184,166,0.3)",
              borderRadius: 8, padding: "8px 16px", color: "#2dd4bf",
              fontSize: 13, fontWeight: 500, cursor: "pointer",
            }}>
              <Zap size={14} />
              {generating ? "Running…" : "Run now"}
            </button>
          </div>
        </div>

        {/* ── Retrospective Generator ── */}
        <div style={{
          background: "#1e293b", borderRadius: 12, border: "1px solid rgba(255,255,255,0.07)",
          marginBottom: 28, overflow: "hidden",
        }}>
          {/* Header row */}
          <div
            onClick={() => setRetroOpen(o => !o)}
            style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "16px 20px", cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <BookOpen size={16} color="#a78bfa" />
              <span style={{ fontWeight: 600, fontSize: 14, color: "#e2e8f0" }}>Sprint Retrospective</span>
              <span style={{ fontSize: 12, color: "#64748b" }}>AI-generated from your incidents, PRs & tickets</span>
            </div>
            {retroOpen ? <ChevronUp size={16} color="#64748b" /> : <ChevronDown size={16} color="#64748b" />}
          </div>

          {retroOpen && (
            <div style={{ padding: "0 20px 20px" }}>
              {/* Date range + generate button */}
              <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 20, flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>From</span>
                  <input type="date" value={retroStart} onChange={e => setRetroStart(e.target.value)}
                    style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12, background: "#0f172a",
                      border: "1px solid rgba(255,255,255,0.1)", color: "#cbd5e1", outline: "none" }} />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>To</span>
                  <input type="date" value={retroEnd} onChange={e => setRetroEnd(e.target.value)}
                    style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12, background: "#0f172a",
                      border: "1px solid rgba(255,255,255,0.1)", color: "#cbd5e1", outline: "none" }} />
                </div>
                {/* Quick presets */}
                {[
                  { label: "Last sprint", days: 14 },
                  { label: "Last month", days: 30 },
                  { label: "Last quarter", days: 90 },
                ].map(p => (
                  <button key={p.label} onClick={() => {
                    const s = new Date(Date.now() - p.days * 86400000).toISOString().slice(0, 10);
                    setRetroStart(s); setRetroEnd(today);
                  }} style={{
                    padding: "6px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                    background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)",
                    color: "#94a3b8",
                  }}>{p.label}</button>
                ))}
                <button onClick={handleGenerateRetro} disabled={retroLoading} style={{
                  display: "flex", alignItems: "center", gap: 6, marginLeft: "auto",
                  padding: "8px 18px", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer",
                  background: "rgba(167,139,250,0.15)", border: "1px solid rgba(167,139,250,0.3)",
                  color: "#c4b5fd", opacity: retroLoading ? 0.6 : 1,
                }}>
                  <BookOpen size={14} />
                  {retroLoading ? "Generating…" : retroData ? "Regenerate" : "Generate Retrospective"}
                </button>
              </div>

              {/* Results */}
              {retroData && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>

                  {/* Incidents */}
                  <div style={{ background: "rgba(239,68,68,0.06)", borderRadius: 10, padding: 16, border: "1px solid rgba(239,68,68,0.15)" }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: "#f87171", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 10 }}>
                      🚨 Incidents & Errors
                    </div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: "#f1f5f9", marginBottom: 4 }}>{retroData.incidents.total}</div>
                    <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 10 }}>total incidents</div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                      {Object.entries(retroData.incidents.by_severity).map(([sev, cnt]) => (
                        <span key={sev} style={{ fontSize: 11, padding: "2px 8px", borderRadius: 20,
                          background: sev === "p0" ? "rgba(239,68,68,0.2)" : sev === "p1" ? "rgba(249,115,22,0.2)" : "rgba(100,116,139,0.2)",
                          color: sev === "p0" ? "#f87171" : sev === "p1" ? "#fb923c" : "#94a3b8" }}>
                          {sev.toUpperCase()}: {cnt}
                        </span>
                      ))}
                    </div>
                    {retroData.incidents.avg_resolution_minutes && (
                      <div style={{ fontSize: 12, color: "#64748b" }}>
                        Avg resolution: <span style={{ color: "#e2e8f0" }}>{retroData.incidents.avg_resolution_minutes} min</span>
                      </div>
                    )}
                    {retroData.incidents.top_incidents.slice(0, 3).map((inc, i) => (
                      <div key={i} style={{ marginTop: 8, padding: "8px 10px", background: "rgba(255,255,255,0.03)", borderRadius: 6 }}>
                        <div style={{ fontSize: 12, color: "#e2e8f0", fontWeight: 500 }}>{inc.title}</div>
                        <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{inc.service} · {inc.severity.toUpperCase()}</div>
                      </div>
                    ))}
                  </div>

                  {/* Deploys */}
                  <div style={{ background: "rgba(99,102,241,0.06)", borderRadius: 10, padding: 16, border: "1px solid rgba(99,102,241,0.15)" }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: "#818cf8", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 10 }}>
                      🚀 Deploys & Changes
                    </div>
                    <div style={{ display: "flex", gap: 20, marginBottom: 12 }}>
                      <div>
                        <div style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9" }}>{retroData.deploys.prs_merged}</div>
                        <div style={{ fontSize: 11, color: "#64748b" }}>PRs merged</div>
                      </div>
                      <div>
                        <div style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9" }}>{retroData.deploys.jira_tickets}</div>
                        <div style={{ fontSize: 11, color: "#64748b" }}>Jira tickets</div>
                      </div>
                    </div>
                    {retroData.deploys.top_prs.slice(0, 3).map((pr, i) => (
                      <div key={i} style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ fontSize: 12, color: "#cbd5e1", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{pr.title || "Untitled PR"}</span>
                        {pr.url && <a href={pr.url} target="_blank" rel="noreferrer" style={{ color: "#64748b", flexShrink: 0 }}><ExternalLink size={11} /></a>}
                      </div>
                    ))}
                  </div>

                  {/* Team patterns */}
                  <div style={{ background: "rgba(20,184,166,0.06)", borderRadius: 10, padding: 16, border: "1px solid rgba(20,184,166,0.15)" }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: "#2dd4bf", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 10 }}>
                      👥 Team Patterns
                    </div>
                    {retroData.team_patterns.most_affected_services.slice(0, 4).map((s, i) => (
                      <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                        <span style={{ fontSize: 12, color: "#cbd5e1" }}>{s.service}</span>
                        <span style={{ fontSize: 12, fontWeight: 600, color: "#f87171" }}>{s.incident_count} incidents</span>
                      </div>
                    ))}
                    {retroData.team_patterns.recurring_services.length > 0 && (
                      <div style={{ marginTop: 10, padding: "6px 10px", background: "rgba(239,68,68,0.08)", borderRadius: 6 }}>
                        <span style={{ fontSize: 11, color: "#f87171" }}>Recurring: </span>
                        <span style={{ fontSize: 11, color: "#94a3b8" }}>{retroData.team_patterns.recurring_services.join(", ")}</span>
                      </div>
                    )}
                    {retroData.team_patterns.top_contributors.slice(0, 3).map((c, i) => (
                      <div key={i} style={{ fontSize: 12, color: "#64748b", marginTop: 6 }}>
                        {c.author}: <span style={{ color: "#94a3b8" }}>{c.contributions} changes</span>
                      </div>
                    ))}
                  </div>

                  {/* Recommendations */}
                  <div style={{ background: "rgba(245,158,11,0.06)", borderRadius: 10, padding: 16, border: "1px solid rgba(245,158,11,0.15)" }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: "#fbbf24", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 10 }}>
                      ✅ Recommendations
                    </div>
                    {retroData.recommendations.map((rec, i) => (
                      <div key={i} style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: "#fbbf24", flexShrink: 0 }}>{i + 1}.</span>
                        <span style={{ fontSize: 12, color: "#cbd5e1", lineHeight: 1.5 }}>{rec}</span>
                      </div>
                    ))}
                  </div>

                </div>
              )}
            </div>
          )}
        </div>

        {/* KPI strip */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 28 }}>
          {kpis.map(({ label, value, color, icon: Icon }) => (
            <div key={label} style={{
              background: "#1e293b", borderRadius: 12, padding: "18px 20px",
              border: "1px solid rgba(255,255,255,0.07)",
              display: "flex", alignItems: "center", gap: 14,
            }}>
              <div style={{
                width: 40, height: 40, borderRadius: 10, flexShrink: 0,
                background: `${color}22`, display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                <Icon size={18} color={color} />
              </div>
              <div>
                <div style={{ fontSize: 26, fontWeight: 700, color: "#f1f5f9", lineHeight: 1 }}>{value}</div>
                <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>{label}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Filters */}
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 24 }}>
          <div style={{
            display: "flex", background: "#1e293b", borderRadius: 8,
            border: "1px solid rgba(255,255,255,0.07)", overflow: "hidden",
          }}>
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.value}
                onClick={() => setStatus(tab.value)}
                style={{
                  padding: "8px 18px", fontSize: 13, fontWeight: 500, border: "none", cursor: "pointer",
                  background: status === tab.value ? "rgba(20,184,166,0.15)" : "transparent",
                  color: status === tab.value ? "#2dd4bf" : "#64748b",
                  borderRight: "1px solid rgba(255,255,255,0.06)",
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as InsightType | "")}
            style={{
              padding: "8px 14px", fontSize: 13, borderRadius: 8, outline: "none",
              background: "#1e293b", border: "1px solid rgba(255,255,255,0.07)",
              color: "#94a3b8", cursor: "pointer",
            }}
          >
            {TYPE_FILTERS.map((f) => (
              <option key={f.value} value={f.value} style={{ background: "#1e293b" }}>{f.label}</option>
            ))}
          </select>
        </div>

        {/* Content */}
        {loading ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} style={{ height: 88, borderRadius: 12, background: "#1e293b", opacity: 0.5 }} />
            ))}
          </div>
        ) : insights.length === 0 ? (
          <div style={{ textAlign: "center", padding: "64px 0" }}>
            <Lightbulb size={40} color="#334155" style={{ display: "block", margin: "0 auto 16px" }} />
            <p style={{ fontSize: 14, color: "#475569", margin: 0 }}>No insights for the selected filters.</p>
            <p style={{ fontSize: 12, color: "#334155", marginTop: 6 }}>
              Connect a data source and click "Run now" to generate insights.
            </p>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {insights.map((i) => (
              <InsightCard key={i.id} insight={i} onStatusChange={handleStatusChange} />
            ))}
          </div>
        )}
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </AppShell>
  );
}
