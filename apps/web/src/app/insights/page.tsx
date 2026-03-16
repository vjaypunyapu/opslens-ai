"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { RefreshCw, Zap, Lightbulb, CheckCircle, BellOff, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { InsightCard } from "@/components/insights/InsightCard";
import { INSIGHT_TYPE_LABELS } from "@/lib/utils";
import { Insight, InsightStatus, InsightSummary, InsightType } from "@/types";
import { insightsApi } from "@/lib/api";

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
    const token = await getToken();
    await insightsApi.updateStatus(id, newStatus, snoozeHours, token!);
    await load();
    toast.success(`Insight ${newStatus}`);
  };

  const handleGenerate = async () => {
    setGenerating(true);
    const token = await getToken();
    await insightsApi.generate(token!);
    toast.success("Insight generation triggered — results will appear shortly");
    setTimeout(load, 3000);
    setGenerating(false);
  };

  const kpis = [
    { label: "Active",   value: summary?.active ?? 0,                color: "#f59e0b", icon: Lightbulb },
    { label: "Critical", value: summary?.by_magnitude?.critical ?? 0, color: "#ef4444", icon: AlertTriangle },
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
