"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { RefreshCw, Zap } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { InsightCard } from "@/components/insights/InsightCard";
import { cn } from "@/lib/utils";
import { INSIGHT_TYPE_LABELS, MAGNITUDE_COLORS } from "@/lib/utils";
import { Insight, InsightMagnitude, InsightStatus, InsightSummary, InsightType } from "@/types";
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
  const [insights, setInsights]   = useState<Insight[]>([]);
  const [summary, setSummary]     = useState<InsightSummary | null>(null);
  const [status, setStatus]       = useState<InsightStatus | "all">("active");
  const [typeFilter, setTypeFilter] = useState<InsightType | "">("");
  const [loading, setLoading]     = useState(true);
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const token = await getToken();
    const [list, sum] = await Promise.all([
      insightsApi.list(token!, {
        status: status === "all" ? undefined : status,
        type: typeFilter || undefined,
      }),
      insightsApi.summary(token!),
    ]);
    setInsights(list);
    setSummary(sum);
    setLoading(false);
  }, [getToken, status, typeFilter]);

  useEffect(() => { load(); }, [load]);

  const handleStatusChange = async (
    id: string,
    newStatus: "active" | "resolved" | "snoozed",
    snoozeHours?: number,
  ) => {
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

  return (
    <AppShell>
      <div className="p-6 max-w-4xl mx-auto w-full space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Insights</h1>
            <p className="text-sm text-gray-400 mt-0.5">
              AI-detected patterns across your operational data
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={load}
              disabled={loading}
              className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:border-gray-300 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
              Refresh
            </button>
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg bg-brand-navy text-white hover:bg-brand-blue transition-colors disabled:opacity-50"
            >
              <Zap className="h-3.5 w-3.5" />
              {generating ? "Running…" : "Run now"}
            </button>
          </div>
        </div>

        {/* Summary cards */}
        {summary && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {(
              [
                { label: "Active",   value: summary.active,   color: "text-orange-600" },
                { label: "Critical", value: summary.by_magnitude?.critical ?? 0, color: "text-red-600" },
                { label: "Resolved", value: summary.resolved, color: "text-green-600" },
                { label: "Snoozed",  value: summary.snoozed,  color: "text-gray-400" },
              ] as const
            ).map(({ label, value, color }) => (
              <div
                key={label}
                className="bg-white border border-gray-200 rounded-xl p-4 text-center"
              >
                <p className={cn("text-2xl font-bold", color)}>{value}</p>
                <p className="text-xs text-gray-500 mt-1">{label}</p>
              </div>
            ))}
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-wrap gap-3 items-center">
          {/* Status tabs */}
          <div className="flex rounded-lg border border-gray-200 overflow-hidden bg-white">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.value}
                onClick={() => setStatus(tab.value)}
                className={cn(
                  "px-3 py-1.5 text-sm transition-colors",
                  status === tab.value
                    ? "bg-brand-navy text-white font-medium"
                    : "text-gray-600 hover:bg-gray-50",
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Type select */}
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as InsightType | "")}
            className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 text-gray-600 bg-white focus:outline-none focus:border-brand-teal"
          >
            {TYPE_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>

        {/* Cards */}
        {loading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-24 bg-gray-100 rounded-xl animate-pulse" />
            ))}
          </div>
        ) : insights.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <p className="text-sm">No insights found for the selected filters.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {insights.map((i) => (
              <InsightCard
                key={i.id}
                insight={i}
                onStatusChange={handleStatusChange}
              />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
