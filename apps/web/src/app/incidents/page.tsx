"use client";
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  AlertTriangle, Plus, RefreshCw, Search, ChevronRight,
  Clock, CheckCircle, Loader2, Zap, GitBranch, MessageSquare,
  FileText, Activity, X, ExternalLink, Play, Database
} from "lucide-react";
import { incidentsApi, logOpsApi, demoApi, Incident, TimelineEvent } from "@/lib/api";

// ─── Helpers ────────────────────────────────────────────────────────────────

const SEVERITY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  p0: { bg: "rgba(239,68,68,0.15)",  text: "#f87171", border: "rgba(239,68,68,0.4)" },
  p1: { bg: "rgba(249,115,22,0.15)", text: "#fb923c", border: "rgba(249,115,22,0.4)" },
  p2: { bg: "rgba(234,179,8,0.15)",  text: "#facc15", border: "rgba(234,179,8,0.4)" },
  p3: { bg: "rgba(34,197,94,0.15)",  text: "#4ade80", border: "rgba(34,197,94,0.4)" },
  p4: { bg: "rgba(100,116,139,0.15)", text: "#94a3b8", border: "rgba(100,116,139,0.4)" },
};

const STATUS_COLORS: Record<string, { text: string; bg: string }> = {
  open:          { text: "#f87171", bg: "rgba(239,68,68,0.12)" },
  investigating: { text: "#fb923c", bg: "rgba(249,115,22,0.12)" },
  analysing:     { text: "#a78bfa", bg: "rgba(167,139,250,0.12)" },
  resolved:      { text: "#4ade80", bg: "rgba(34,197,94,0.12)" },
  closed:        { text: "#64748b", bg: "rgba(100,116,139,0.12)" },
};

const SOURCE_ICONS: Record<string, typeof Activity> = {
  github:        GitBranch,
  slack:         MessageSquare,
  jira:          FileText,
  log:           Activity,
  elasticsearch: Activity,
  datadog:       Activity,
  cloudwatch:    Activity,
  splunk:        Activity,
  azure_monitor: Activity,
  gcp_logging:   Activity,
};

function SeverityBadge({ severity }: { severity: string }) {
  const c = SEVERITY_COLORS[severity] ?? SEVERITY_COLORS.p4;
  return (
    <span style={{ fontSize: "11px", fontWeight: 700, padding: "2px 8px",
      borderRadius: "4px", background: c.bg, color: c.text, border: `1px solid ${c.border}`,
      textTransform: "uppercase", letterSpacing: "0.06em" }}>
      {severity.toUpperCase()}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] ?? { text: "#94a3b8", bg: "rgba(100,116,139,0.12)" };
  return (
    <span style={{ fontSize: "11px", fontWeight: 600, padding: "2px 10px",
      borderRadius: "20px", background: c.bg, color: c.text,
      textTransform: "capitalize" }}>
      {status}
    </span>
  );
}

function formatTs(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleString(undefined, { month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit" });
}

function timeSince(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// ─── New Incident Modal ──────────────────────────────────────────────────────

function NewIncidentModal({ onClose, onCreate }: { onClose: () => void; onCreate: (data: object) => void }) {
  const [form, setForm] = useState({ title: "", description: "", service: "", severity: "p2" });
  const [loading, setLoading] = useState(false);

  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })); }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.title.trim()) return;
    setLoading(true);
    await onCreate({ ...form, started_at: new Date().toISOString() });
    setLoading(false);
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 100,
      display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "16px", padding: "28px", width: "480px", maxWidth: "95vw" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "24px" }}>
          <h2 style={{ color: "#f1f5f9", fontSize: "18px", fontWeight: 700, margin: 0 }}>
            Declare Incident
          </h2>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748b",
            cursor: "pointer", padding: "4px" }}>
            <X size={18} />
          </button>
        </div>

        <form onSubmit={submit}>
          {[
            { label: "Title *", key: "title", placeholder: "e.g. Payment service 500 errors spike" },
            { label: "Affected Service", key: "service", placeholder: "e.g. payments-api" },
          ].map(({ label, key, placeholder }) => (
            <div key={key} style={{ marginBottom: "16px" }}>
              <label style={{ display: "block", fontSize: "12px", color: "#94a3b8",
                marginBottom: "6px", fontWeight: 600 }}>{label}</label>
              <input value={form[key as keyof typeof form]}
                onChange={e => set(key, e.target.value)}
                placeholder={placeholder}
                style={{ width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: "8px", padding: "10px 12px", color: "#f1f5f9",
                  fontSize: "14px", outline: "none", boxSizing: "border-box" }} />
            </div>
          ))}

          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#94a3b8",
              marginBottom: "6px", fontWeight: 600 }}>Severity</label>
            <select value={form.severity} onChange={e => set("severity", e.target.value)}
              style={{ width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "8px", padding: "10px 12px", color: "#f1f5f9",
                fontSize: "14px", outline: "none" }}>
              {["p0", "p1", "p2", "p3", "p4"].map(s => (
                <option key={s} value={s}>{s.toUpperCase()} – {
                  { p0: "Critical", p1: "High", p2: "Medium", p3: "Low", p4: "Info" }[s]
                }</option>
              ))}
            </select>
          </div>

          <div style={{ marginBottom: "24px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#94a3b8",
              marginBottom: "6px", fontWeight: 600 }}>Description</label>
            <textarea value={form.description} onChange={e => set("description", e.target.value)}
              rows={3} placeholder="What's happening? What's the user impact?"
              style={{ width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "8px", padding: "10px 12px", color: "#f1f5f9",
                fontSize: "14px", outline: "none", resize: "vertical", boxSizing: "border-box" }} />
          </div>

          <div style={{ display: "flex", gap: "12px", justifyContent: "flex-end" }}>
            <button type="button" onClick={onClose}
              style={{ padding: "10px 20px", borderRadius: "8px",
                background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
                color: "#94a3b8", fontSize: "14px", cursor: "pointer" }}>
              Cancel
            </button>
            <button type="submit" disabled={loading || !form.title.trim()}
              style={{ padding: "10px 20px", borderRadius: "8px",
                background: loading ? "rgba(239,68,68,0.5)" : "#ef4444",
                border: "none", color: "#fff", fontSize: "14px",
                fontWeight: 600, cursor: "pointer", display: "flex", alignItems: "center", gap: "8px" }}>
              {loading && <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />}
              Declare Incident
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Timeline ────────────────────────────────────────────────────────────────

function Timeline({ events }: { events: TimelineEvent[] }) {
  if (!events.length) {
    return (
      <div style={{ textAlign: "center", padding: "40px", color: "#475569" }}>
        <Activity size={32} style={{ marginBottom: "12px", opacity: 0.5 }} />
        <p style={{ margin: 0 }}>No timeline events yet. Run an investigation to correlate signals.</p>
      </div>
    );
  }

  const EVENT_COLORS: Record<string, string> = {
    deploy:        "#14b8a6",
    code_change:   "#6366f1",
    alert:         "#ef4444",
    error:         "#f87171",
    incident:      "#f97316",
    ticket:        "#8b5cf6",
    discussion:    "#94a3b8",
    log_event:     "#475569",
    metric_anomaly:"#f59e0b",
    alarm:         "#ef4444",
  };

  return (
    <div style={{ position: "relative", paddingLeft: "28px" }}>
      {/* vertical line */}
      <div style={{ position: "absolute", left: "9px", top: "12px", bottom: "12px",
        width: "2px", background: "rgba(255,255,255,0.06)" }} />

      {events.map((e, i) => {
        const SourceIcon = SOURCE_ICONS[e.source_type] ?? Activity;
        const dotColor = EVENT_COLORS[e.event_type] ?? "#475569";
        return (
          <div key={i} style={{ position: "relative", marginBottom: "24px" }}>
            {/* dot */}
            <div style={{ position: "absolute", left: "-28px", top: "4px",
              width: "10px", height: "10px", borderRadius: "50%",
              background: dotColor, border: "2px solid #0f172a",
              boxShadow: `0 0 6px ${dotColor}` }} />

            <div style={{ background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: "10px", padding: "14px 16px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px",
                flexWrap: "wrap" }}>
                <span style={{ fontSize: "11px", color: "#475569",
                  fontFamily: "monospace" }}>{formatTs(e.timestamp)}</span>
                <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "4px",
                  background: "rgba(255,255,255,0.06)", color: "#94a3b8",
                  display: "flex", alignItems: "center", gap: "4px" }}>
                  <SourceIcon size={10} />
                  {e.source_type.toUpperCase()}
                </span>
                <span style={{ fontSize: "11px", color: dotColor, padding: "2px 8px",
                  borderRadius: "4px", background: `${dotColor}20` }}>
                  {e.event_type.replace("_", " ")}
                </span>
                {e.author && (
                  <span style={{ fontSize: "11px", color: "#64748b" }}>by {e.author}</span>
                )}
              </div>
              <div style={{ fontSize: "13px", color: "#e2e8f0", fontWeight: 500,
                marginBottom: e.detail ? "4px" : "0" }}>{e.title}</div>
              {e.detail && (
                <div style={{ fontSize: "12px", color: "#64748b", lineHeight: 1.5 }}>
                  {e.detail.slice(0, 300)}{e.detail.length > 300 ? "…" : ""}
                </div>
              )}
              {e.url && (
                <a href={e.url} target="_blank" rel="noreferrer"
                  style={{ display: "inline-flex", alignItems: "center", gap: "4px",
                    fontSize: "11px", color: "#2dd4bf", marginTop: "6px",
                    textDecoration: "none" }}>
                  <ExternalLink size={10} /> View source
                </a>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Detail Panel ────────────────────────────────────────────────────────────

function IncidentDetail({
  incident, onClose, onInvestigate, onStatusChange,
}: {
  incident: Incident;
  onClose: () => void;
  onInvestigate: () => Promise<void>;
  onStatusChange: (status: string) => Promise<void>;
}) {
  const [tab, setTab] = useState<"overview" | "timeline" | "signals">("overview");
  const [investigating, setInvestigating] = useState(false);

  async function handleInvestigate() {
    setInvestigating(true);
    await onInvestigate();
    setInvestigating(false);
  }

  const sev = SEVERITY_COLORS[incident.severity] ?? SEVERITY_COLORS.p4;

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 80,
      display: "flex", justifyContent: "flex-end" }}>
      <div style={{ width: "680px", maxWidth: "95vw", background: "#0f172a",
        borderLeft: "1px solid rgba(255,255,255,0.1)", height: "100%",
        overflowY: "auto", display: "flex", flexDirection: "column" }}>

        {/* Header */}
        <div style={{ padding: "24px 28px 0", borderBottom: "1px solid rgba(255,255,255,0.06)",
          paddingBottom: "20px", position: "sticky", top: 0, background: "#0f172a", zIndex: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start",
            marginBottom: "12px" }}>
            <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
              <SeverityBadge severity={incident.severity} />
              <StatusBadge status={incident.status} />
              {incident.service && (
                <span style={{ fontSize: "12px", color: "#64748b",
                  background: "rgba(255,255,255,0.05)", padding: "2px 10px",
                  borderRadius: "20px", border: "1px solid rgba(255,255,255,0.08)" }}>
                  {incident.service}
                </span>
              )}
            </div>
            <button onClick={onClose}
              style={{ background: "none", border: "none", color: "#64748b",
                cursor: "pointer", padding: "4px" }}>
              <X size={18} />
            </button>
          </div>
          <h2 style={{ color: "#f1f5f9", fontSize: "18px", fontWeight: 700,
            margin: "0 0 8px", lineHeight: 1.3 }}>{incident.title}</h2>
          <div style={{ display: "flex", gap: "20px", fontSize: "12px", color: "#475569" }}>
            <span><Clock size={11} style={{ marginRight: "4px", verticalAlign: "middle" }} />
              Started {formatTs(incident.started_at)}</span>
            {incident.resolved_at && (
              <span><CheckCircle size={11} style={{ marginRight: "4px", verticalAlign: "middle", color: "#4ade80" }} />
                Resolved {formatTs(incident.resolved_at)}</span>
            )}
          </div>

          {/* Tabs */}
          <div style={{ display: "flex", gap: "4px", marginTop: "16px" }}>
            {(["overview", "timeline", "signals"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                style={{ padding: "8px 16px", borderRadius: "8px 8px 0 0",
                  background: tab === t ? "#1e293b" : "transparent",
                  border: tab === t ? "1px solid rgba(255,255,255,0.08)" : "1px solid transparent",
                  borderBottom: tab === t ? "1px solid #1e293b" : "1px solid transparent",
                  color: tab === t ? "#f1f5f9" : "#64748b",
                  fontSize: "13px", fontWeight: 500, cursor: "pointer",
                  textTransform: "capitalize" }}>
                {t} {t === "timeline" && incident.timeline?.length ? `(${incident.timeline.length})` : ""}
                {t === "signals" && incident.signals?.length ? `(${incident.signals.length})` : ""}
              </button>
            ))}
          </div>
        </div>

        {/* Tab Body */}
        <div style={{ padding: "24px 28px", flex: 1 }}>

          {tab === "overview" && (
            <div>
              {/* Investigation button */}
              {["open", "investigating"].includes(incident.status) && (
                <div style={{ background: "rgba(99,102,241,0.1)", border: "1px solid rgba(99,102,241,0.25)",
                  borderRadius: "12px", padding: "16px 20px", marginBottom: "24px",
                  display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
                  <div>
                    <div style={{ color: "#a5b4fc", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                      AI Investigation
                    </div>
                    <div style={{ color: "#64748b", fontSize: "12px" }}>
                      Correlate signals from logs, code, tickets, and chat to generate root cause analysis.
                    </div>
                  </div>
                  <button onClick={handleInvestigate} disabled={investigating}
                    style={{ flexShrink: 0, padding: "10px 18px", borderRadius: "8px",
                      background: "#6366f1", border: "none", color: "#fff",
                      fontSize: "13px", fontWeight: 600, cursor: "pointer",
                      display: "flex", alignItems: "center", gap: "8px",
                      opacity: investigating ? 0.7 : 1 }}>
                    {investigating
                      ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Investigating…</>
                      : <><Play size={14} /> Investigate</>}
                  </button>
                </div>
              )}

              {incident.status === "analysing" && !incident.root_cause && (
                <div style={{ background: "rgba(167,139,250,0.1)", border: "1px solid rgba(167,139,250,0.25)",
                  borderRadius: "12px", padding: "16px 20px", marginBottom: "24px",
                  display: "flex", alignItems: "center", gap: "12px" }}>
                  <Loader2 size={18} style={{ color: "#a78bfa", animation: "spin 1s linear infinite" }} />
                  <div style={{ color: "#a78bfa", fontSize: "13px" }}>AI investigation in progress…</div>
                </div>
              )}

              {incident.description && (
                <div style={{ marginBottom: "24px" }}>
                  <h4 style={{ color: "#94a3b8", fontSize: "11px", fontWeight: 600,
                    textTransform: "uppercase", letterSpacing: "0.1em", margin: "0 0 10px" }}>
                    Description
                  </h4>
                  <p style={{ color: "#cbd5e1", fontSize: "14px", lineHeight: 1.6, margin: 0 }}>
                    {incident.description}
                  </p>
                </div>
              )}

              {incident.root_cause && (
                <div style={{ marginBottom: "24px" }}>
                  <h4 style={{ color: "#94a3b8", fontSize: "11px", fontWeight: 600,
                    textTransform: "uppercase", letterSpacing: "0.1em", margin: "0 0 10px" }}>
                    Root Cause
                  </h4>
                  <div style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)",
                    borderRadius: "10px", padding: "16px" }}>
                    <p style={{ color: "#fca5a5", fontSize: "14px", lineHeight: 1.6, margin: 0 }}>
                      {incident.root_cause}
                    </p>
                  </div>
                </div>
              )}

              {incident.contributing_factors?.length > 0 && (
                <div style={{ marginBottom: "24px" }}>
                  <h4 style={{ color: "#94a3b8", fontSize: "11px", fontWeight: 600,
                    textTransform: "uppercase", letterSpacing: "0.1em", margin: "0 0 10px" }}>
                    Contributing Factors
                  </h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {incident.contributing_factors.map((f, i) => (
                      <div key={i} style={{ display: "flex", gap: "10px", alignItems: "flex-start" }}>
                        <span style={{ flexShrink: 0, width: "20px", height: "20px", borderRadius: "50%",
                          background: "rgba(249,115,22,0.15)", border: "1px solid rgba(249,115,22,0.3)",
                          color: "#fb923c", fontSize: "11px", fontWeight: 700,
                          display: "flex", alignItems: "center", justifyContent: "center" }}>
                          {i + 1}
                        </span>
                        <p style={{ color: "#cbd5e1", fontSize: "13px", lineHeight: 1.5, margin: 0 }}>{f}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {incident.recommendations?.length > 0 && (
                <div style={{ marginBottom: "24px" }}>
                  <h4 style={{ color: "#94a3b8", fontSize: "11px", fontWeight: 600,
                    textTransform: "uppercase", letterSpacing: "0.1em", margin: "0 0 10px" }}>
                    Recommendations
                  </h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {incident.recommendations.map((r, i) => (
                      <div key={i} style={{ display: "flex", gap: "10px", alignItems: "flex-start" }}>
                        <CheckCircle size={16} style={{ flexShrink: 0, color: "#4ade80", marginTop: "2px" }} />
                        <p style={{ color: "#cbd5e1", fontSize: "13px", lineHeight: 1.5, margin: 0 }}>{r}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Status changer */}
              <div style={{ marginTop: "32px", paddingTop: "20px",
                borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                <h4 style={{ color: "#94a3b8", fontSize: "11px", fontWeight: 600,
                  textTransform: "uppercase", letterSpacing: "0.1em", margin: "0 0 12px" }}>
                  Update Status
                </h4>
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  {(["open", "investigating", "resolved", "closed"] as const)
                    .filter(s => s !== incident.status)
                    .map(s => (
                      <button key={s} onClick={() => onStatusChange(s)}
                        style={{ padding: "8px 16px", borderRadius: "8px",
                          background: "rgba(255,255,255,0.05)",
                          border: "1px solid rgba(255,255,255,0.1)",
                          color: STATUS_COLORS[s]?.text ?? "#94a3b8",
                          fontSize: "13px", cursor: "pointer", textTransform: "capitalize" }}>
                        Mark {s}
                      </button>
                    ))}
                </div>
              </div>
            </div>
          )}

          {tab === "timeline" && (
            <Timeline events={incident.timeline ?? []} />
          )}

          {tab === "signals" && (
            <div>
              {(incident.signals ?? []).length === 0 ? (
                <div style={{ textAlign: "center", padding: "40px", color: "#475569" }}>
                  <Zap size={32} style={{ marginBottom: "12px", opacity: 0.5 }} />
                  <p style={{ margin: 0 }}>No correlated signals yet.</p>
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  {incident.signals.map((sig, i) => {
                    // Special render for rrt_brief link
                    if (sig.type === "rrt_brief" && sig.brief_id) {
                      return (
                        <div key={i} style={{
                          background: "rgba(20,184,166,0.06)",
                          border: "1px solid rgba(20,184,166,0.2)",
                          borderRadius: "10px", padding: "14px 16px",
                          display: "flex", alignItems: "center", gap: "10px",
                        }}>
                          <FileText size={16} color="#2dd4bf" style={{ flexShrink: 0 }} />
                          <div style={{ flex: 1 }}>
                            <div style={{ fontSize: "12px", fontWeight: 600, color: "#2dd4bf", marginBottom: "2px" }}>
                              RRT Brief generated for this incident
                            </div>
                            <div style={{ fontSize: "11px", color: "#64748b" }}>
                              Full AI diagnosis, next actions, and Jira push available
                            </div>
                          </div>
                          <a href="/rrt-briefs" style={{
                            fontSize: "12px", color: "#2dd4bf", textDecoration: "none",
                            display: "flex", alignItems: "center", gap: "4px",
                            padding: "5px 12px", borderRadius: "6px",
                            border: "1px solid rgba(20,184,166,0.3)",
                            background: "rgba(20,184,166,0.1)",
                          }}>
                            View Brief <ExternalLink size={10} />
                          </a>
                        </div>
                      );
                    }

                    const SrcIcon = SOURCE_ICONS[sig.source_type] ?? Activity;
                    return (
                      <div key={i} style={{ background: "rgba(255,255,255,0.03)",
                        border: "1px solid rgba(255,255,255,0.07)",
                        borderRadius: "10px", padding: "14px 16px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
                          <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "4px",
                            background: "rgba(255,255,255,0.06)", color: "#94a3b8",
                            display: "flex", alignItems: "center", gap: "4px" }}>
                            <SrcIcon size={10} />{sig.source_type.toUpperCase()}
                          </span>
                          <span style={{ fontSize: "11px", color: "#14b8a6",
                            background: "rgba(20,184,166,0.1)", padding: "2px 8px", borderRadius: "4px" }}>
                            score {sig.score}
                          </span>
                          <span style={{ fontSize: "11px", color: "#475569", marginLeft: "auto" }}>
                            {formatTs(sig.timestamp)}
                          </span>
                        </div>
                        <div style={{ fontSize: "13px", color: "#e2e8f0", fontWeight: 500,
                          marginBottom: sig.detail ? "4px" : "0" }}>{sig.title}</div>
                        {sig.detail && (
                          <div style={{ fontSize: "12px", color: "#64748b", lineHeight: 1.5 }}>
                            {sig.detail.slice(0, 250)}{sig.detail.length > 250 ? "…" : ""}
                          </div>
                        )}
                        {sig.url && (
                          <a href={sig.url} target="_blank" rel="noreferrer"
                            style={{ display: "inline-flex", alignItems: "center", gap: "4px",
                              fontSize: "11px", color: "#2dd4bf", marginTop: "6px",
                              textDecoration: "none" }}>
                            <ExternalLink size={10} /> View source
                          </a>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Simulate Alert Modal ────────────────────────────────────────────────────

const DEMO_SCENARIOS = [
  {
    label: "Payment timeout",
    service: "payment-service",
    message: "PaymentError: Stripe API timeout after 30s — retries exhausted for card_id=card_abc123",
    count: 47,
  },
  {
    label: "Auth service 500s",
    service: "auth-service",
    message: "InternalServerError: JWT verification failed — database connection pool exhausted (pool_size=20)",
    count: 23,
  },
  {
    label: "Database OOM",
    service: "postgres-primary",
    message: "FATAL: out of memory (OOM) — shared_buffers exceeded, query killed: SELECT * FROM orders WHERE...",
    count: 8,
  },
  {
    label: "Webhook queue backed up",
    service: "webhook-worker",
    message: "QueueBacklogError: Webhook delivery queue depth > 10,000 — consumers stalled, backpressure detected",
    count: 34,
  },
];

function SimulateAlertModal({ onClose, onSimulate }: {
  onClose: () => void;
  onSimulate: (data: { service_name: string; error_message: string; error_count: number; severity: string }) => Promise<import("@/lib/api").SimulateAlertResponse>;
}) {
  const [form, setForm] = useState({
    service_name: "payment-service",
    error_message: "PaymentError: Stripe API timeout after 30s — retries exhausted for card_id=card_abc123",
    error_count: 47,
    severity: "p1",
  });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<import("@/lib/api").SimulateAlertResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  function applyScenario(s: typeof DEMO_SCENARIOS[0]) {
    setForm(f => ({ ...f, service_name: s.service, error_message: s.message, error_count: s.count }));
    setResult(null);
    setErr(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.service_name.trim() || !form.error_message.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      const res = await onSimulate(form);
      setResult(res);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Simulation failed — check that the Celery worker is running and a Slack webhook is configured.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", zIndex: 100,
      display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "16px", padding: "28px", width: "560px", maxWidth: "95vw",
        maxHeight: "90vh", overflowY: "auto" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
          <h2 style={{ color: "#f1f5f9", fontSize: "18px", fontWeight: 700, margin: 0, display: "flex", gap: "10px", alignItems: "center" }}>
            <Zap size={18} color="#14b8a6" />
            Simulate Alert
          </h2>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", padding: "4px" }}>
            <X size={18} />
          </button>
        </div>
        <p style={{ fontSize: "13px", color: "#64748b", marginTop: "4px", marginBottom: "20px", lineHeight: 1.5 }}>
          Injects a synthetic error through the full pipeline: Fast Alert → Enrichment (Jira/GitHub/Slack context) → RRT Brief sent to Slack.
        </p>

        {/* Quick scenarios */}
        <div style={{ marginBottom: "20px" }}>
          <div style={{ fontSize: "11px", fontWeight: 600, color: "#64748b", textTransform: "uppercase",
            letterSpacing: "0.08em", marginBottom: "8px" }}>Quick Scenarios</div>
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {DEMO_SCENARIOS.map(s => (
              <button
                key={s.label}
                type="button"
                onClick={() => applyScenario(s)}
                style={{
                  padding: "5px 12px", borderRadius: "6px", fontSize: "12px",
                  background: form.service_name === s.service ? "rgba(20,184,166,0.2)" : "rgba(255,255,255,0.05)",
                  border: form.service_name === s.service ? "1px solid rgba(20,184,166,0.4)" : "1px solid rgba(255,255,255,0.1)",
                  color: form.service_name === s.service ? "#2dd4bf" : "#94a3b8",
                  cursor: "pointer",
                }}
              >{s.label}</button>
            ))}
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          {/* Service name */}
          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#94a3b8",
              marginBottom: "6px", fontWeight: 600 }}>Service Name</label>
            <input
              value={form.service_name}
              onChange={e => setForm(f => ({ ...f, service_name: e.target.value }))}
              placeholder="e.g. payment-service"
              style={{ width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "8px", padding: "10px 12px", color: "#f1f5f9",
                fontSize: "14px", outline: "none", boxSizing: "border-box" }}
            />
          </div>

          {/* Error message */}
          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#94a3b8",
              marginBottom: "6px", fontWeight: 600 }}>Error Message</label>
            <textarea
              value={form.error_message}
              onChange={e => setForm(f => ({ ...f, error_message: e.target.value }))}
              rows={3}
              placeholder="PaymentError: Stripe API timeout after 30s"
              style={{ width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "8px", padding: "10px 12px", color: "#f1f5f9",
                fontSize: "13px", outline: "none", resize: "vertical",
                fontFamily: "monospace", boxSizing: "border-box" }}
            />
          </div>

          {/* Count + severity row */}
          <div style={{ display: "flex", gap: "12px", marginBottom: "20px" }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: "block", fontSize: "12px", color: "#94a3b8",
                marginBottom: "6px", fontWeight: 600 }}>Error Count (in window)</label>
              <input
                type="number"
                min={1}
                max={999}
                value={form.error_count}
                onChange={e => setForm(f => ({ ...f, error_count: parseInt(e.target.value) || 1 }))}
                style={{ width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: "8px", padding: "10px 12px", color: "#f1f5f9",
                  fontSize: "14px", outline: "none" }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: "block", fontSize: "12px", color: "#94a3b8",
                marginBottom: "6px", fontWeight: 600 }}>Severity</label>
              <select
                value={form.severity}
                onChange={e => setForm(f => ({ ...f, severity: e.target.value }))}
                style={{ width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: "8px", padding: "10px 12px", color: "#f1f5f9",
                  fontSize: "14px", outline: "none" }}
              >
                {["p0", "p1", "p2", "p3"].map(s => (
                  <option key={s} value={s}>{s.toUpperCase()} – {{ p0: "Critical", p1: "High", p2: "Medium", p3: "Low" }[s]}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Result / error */}
          {result && (
            <div style={{
              background: result.routing_used_fallback ? "rgba(251,146,60,0.08)" : "rgba(34,197,94,0.1)",
              border: `1px solid ${result.routing_used_fallback ? "rgba(251,146,60,0.3)" : "rgba(34,197,94,0.3)"}`,
              borderRadius: "8px", padding: "12px 16px", marginBottom: "16px",
            }}>
              <div style={{ display: "flex", gap: "8px", alignItems: "flex-start" }}>
                <CheckCircle size={16} color={result.routing_used_fallback ? "#fb923c" : "#4ade80"}
                  style={{ flexShrink: 0, marginTop: "1px" }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: "13px", fontWeight: 600,
                    color: result.routing_used_fallback ? "#fb923c" : "#4ade80", marginBottom: "4px" }}>
                    {result.routing_used_fallback ? "⚠️ No routing rules matched" : "Simulation running!"}
                  </div>
                  <div style={{ fontSize: "12px", color: "#94a3b8", lineHeight: 1.5 }}>{result.message}</div>
                  {!result.routing_used_fallback && result.routed_to.length > 0 && (
                    <div style={{ marginTop: "6px", display: "flex", gap: "6px", flexWrap: "wrap" }}>
                      {result.routed_to.map(team => (
                        <span key={team} style={{
                          fontSize: "11px", background: "rgba(20,184,166,0.15)",
                          border: "1px solid rgba(20,184,166,0.3)", color: "#2dd4bf",
                          borderRadius: "4px", padding: "2px 8px",
                        }}>✓ {team}</span>
                      ))}
                    </div>
                  )}
                  {result.routing_used_fallback && (
                    <div style={{ marginTop: "6px", fontSize: "11px", color: "#94a3b8" }}>
                      Add a routing rule matching <code style={{ color: "#fb923c" }}>{form.service_name}</code> in the{" "}
                      <a href="/routing-rules" style={{ color: "#2dd4bf" }}>Routing Rules page</a>.
                    </div>
                  )}
                  <div style={{ marginTop: "8px" }}>
                    <a href="/rrt-briefs" style={{ fontSize: "12px", color: "#2dd4bf", textDecoration: "none",
                      display: "inline-flex", alignItems: "center", gap: "4px" }}>
                      <ExternalLink size={11} /> View RRT Briefs →
                    </a>
                  </div>
                </div>
              </div>
            </div>
          )}
          {err && (
            <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
              borderRadius: "8px", padding: "12px 16px", marginBottom: "16px",
              fontSize: "13px", color: "#f87171" }}>{err}</div>
          )}

          <div style={{ display: "flex", gap: "12px", justifyContent: "flex-end" }}>
            <button type="button" onClick={onClose}
              style={{ padding: "10px 20px", borderRadius: "8px",
                background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
                color: "#94a3b8", fontSize: "14px", cursor: "pointer" }}>
              {result ? "Close" : "Cancel"}
            </button>
            {!result && (
              <button type="submit" disabled={loading || !form.service_name.trim() || !form.error_message.trim()}
                style={{ padding: "10px 20px", borderRadius: "8px",
                  background: loading ? "rgba(20,184,166,0.5)" : "rgba(20,184,166,0.9)",
                  border: "none", color: "#fff", fontSize: "14px",
                  fontWeight: 600, cursor: "pointer", display: "flex", alignItems: "center", gap: "8px",
                  opacity: (!form.service_name.trim() || !form.error_message.trim()) ? 0.5 : 1 }}>
                {loading && <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />}
                <Zap size={14} />
                {loading ? "Dispatching…" : "Run Simulation"}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}


// ─── Main Page ───────────────────────────────────────────────────────────────

export default function IncidentsPage() {
  const { getToken } = useAuth();
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterSeverity, setFilterSeverity] = useState("");
  const [selected, setSelected] = useState<Incident | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [showSimulate, setShowSimulate] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState<string | null>(null);
  const [seedStatus, setSeedStatus] = useState<null | { db_documents: { found: number; expected: number; done: number; pending: number; details: {source_id:string; source_type:string; embedding_status:string; chunk_count:number|null}[] }; qdrant: { url: string; reachable: boolean; collection: string; vector_count: number; error: string|null }; ready_for_demo: boolean; next_step: string }>(null);
  const [checkingStatus, setCheckingStatus] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const token = await getToken();
      if (!token) return;
      const data = await incidentsApi.list(token, {
        ...(filterStatus ? { status: filterStatus } : {}),
        ...(filterSeverity ? { severity: filterSeverity } : {}),
      });
      setIncidents(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }, [getToken, filterStatus, filterSeverity]);

  useEffect(() => { load(); }, [load]);

  async function handleCreate(data: object) {
    const token = await getToken();
    if (!token) return;
    const inc = await incidentsApi.create(token, data as Parameters<typeof incidentsApi.create>[1]);
    setIncidents(prev => [inc, ...prev]);
    setShowNew(false);
    setSelected(inc);
  }

  async function handleInvestigate() {
    if (!selected) return;
    const token = await getToken();
    if (!token) return;
    await incidentsApi.investigate(selected.id, token);
    // Refresh after a moment (investigation runs async)
    setTimeout(async () => {
      const token2 = await getToken();
      if (!token2) return;
      const updated = await incidentsApi.get(selected.id, token2);
      setSelected(updated);
      setIncidents(prev => prev.map(i => i.id === updated.id ? updated : i));
    }, 4000);
  }

  async function handleStatusChange(status: string) {
    if (!selected) return;
    const token = await getToken();
    if (!token) return;
    const updated = await incidentsApi.update(selected.id, token, {
      status: status as Incident["status"],
      ...(status === "resolved" ? { resolved_at: new Date().toISOString() } : {}),
    });
    setSelected(updated);
    setIncidents(prev => prev.map(i => i.id === updated.id ? updated : i));
  }

  async function handleSimulate(data: { service_name: string; error_message: string; error_count: number; severity: string }) {
    const token = await getToken();
    if (!token) throw new Error("Not authenticated");
    return await logOpsApi.simulate(token, data);
  }

  async function handleSeedDemo() {
    const token = await getToken();
    if (!token) return;
    setSeeding(true); setSeedMsg(null);
    try {
      const res = await demoApi.seedDemoData(token);
      setSeedMsg(res.message);
    } catch (e: unknown) {
      setSeedMsg(e instanceof Error ? e.message : "Seeding failed");
    } finally {
      setSeeding(false);
      setTimeout(() => setSeedMsg(null), 8000);
    }
  }

  async function handleCheckSeedStatus() {
    const token = await getToken();
    if (!token) return;
    setCheckingStatus(true);
    try {
      const s = await demoApi.seedStatus(token);
      setSeedStatus(s);
    } catch (e: unknown) {
      setSeedMsg(e instanceof Error ? e.message : "Status check failed");
    } finally {
      setCheckingStatus(false);
    }
  }

  async function refreshSelected() {
    if (!selected) return;
    const token = await getToken();
    if (!token) return;
    const updated = await incidentsApi.get(selected.id, token);
    setSelected(updated);
    setIncidents(prev => prev.map(i => i.id === updated.id ? updated : i));
  }

  const filtered = incidents.filter(inc => {
    if (search && !inc.title.toLowerCase().includes(search.toLowerCase()) &&
        !(inc.service ?? "").toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const stats = {
    open:     incidents.filter(i => i.status === "open").length,
    active:   incidents.filter(i => ["open", "investigating", "analysing"].includes(i.status)).length,
    resolved: incidents.filter(i => i.status === "resolved").length,
    p0p1:     incidents.filter(i => ["p0", "p1"].includes(i.severity) && i.status !== "closed").length,
  };

  return (
    <>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        * { box-sizing: border-box; }
      `}</style>

      <div style={{ minHeight: "100vh", background: "#0f172a", color: "#f1f5f9",
        padding: "32px", fontFamily: "system-ui, sans-serif" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start",
          marginBottom: "28px", flexWrap: "wrap", gap: "16px" }}>
          <div>
            <h1 style={{ margin: "0 0 6px", fontSize: "24px", fontWeight: 700,
              display: "flex", alignItems: "center", gap: "10px" }}>
              <AlertTriangle size={22} style={{ color: "#f97316" }} />
              Incidents
            </h1>
            <p style={{ margin: 0, color: "#475569", fontSize: "14px" }}>
              Declare, investigate, and resolve production incidents with AI-powered root cause analysis.
            </p>
          </div>
          <div style={{ display: "flex", gap: "10px" }}>
            <button onClick={load}
              style={{ padding: "10px 16px", borderRadius: "8px",
                background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
                color: "#94a3b8", fontSize: "13px", cursor: "pointer",
                display: "flex", alignItems: "center", gap: "6px" }}>
              <RefreshCw size={14} /> Refresh
            </button>
            <button onClick={handleSeedDemo} disabled={seeding}
              title="Seed Jira tickets, GitHub PRs, and Slack threads as demo context — they'll appear in RRT brief related items"
              style={{ padding: "10px 16px", borderRadius: "8px",
                background: "rgba(167,139,250,0.12)", border: "1px solid rgba(167,139,250,0.3)",
                color: "#a78bfa", fontSize: "13px", fontWeight: 600,
                cursor: seeding ? "not-allowed" : "pointer", opacity: seeding ? 0.6 : 1,
                display: "flex", alignItems: "center", gap: "6px" }}>
              <Database size={13} /> {seeding ? "Seeding…" : "Seed Demo Data"}
            </button>
            <button onClick={() => setShowSimulate(true)}
              style={{ padding: "10px 18px", borderRadius: "8px",
                background: "rgba(20,184,166,0.15)", border: "1px solid rgba(20,184,166,0.4)",
                color: "#2dd4bf", fontSize: "13px", fontWeight: 600, cursor: "pointer",
                display: "flex", alignItems: "center", gap: "8px" }}>
              <Zap size={14} /> Simulate Alert
            </button>
            <button onClick={() => setShowNew(true)}
              style={{ padding: "10px 18px", borderRadius: "8px",
                background: "#ef4444", border: "none", color: "#fff",
                fontSize: "13px", fontWeight: 600, cursor: "pointer",
                display: "flex", alignItems: "center", gap: "8px" }}>
              <Plus size={14} /> Declare Incident
            </button>
          </div>
        </div>

        {/* Seed result + status panel */}
        {(seedMsg || seedStatus) && (
          <div style={{ marginBottom: "16px", padding: "14px 16px", borderRadius: "8px",
            background: "rgba(167,139,250,0.07)", border: "1px solid rgba(167,139,250,0.2)",
            fontSize: "13px" }}>
            {seedMsg && (
              <div style={{ color: "#c4b5fd", display: "flex", gap: "8px", alignItems: "flex-start", marginBottom: seedStatus ? "12px" : 0 }}>
                <Database size={14} style={{ marginTop: "1px", flexShrink: 0 }} />
                {seedMsg}
              </div>
            )}
            {seedStatus && (
              <div>
                {/* Summary row */}
                <div style={{ display: "flex", gap: "20px", marginBottom: "10px", flexWrap: "wrap" }}>
                  <span style={{ color: seedStatus.db_documents.found === seedStatus.db_documents.expected ? "#4ade80" : "#f87171" }}>
                    DB: {seedStatus.db_documents.found}/{seedStatus.db_documents.expected} docs
                  </span>
                  <span style={{ color: seedStatus.db_documents.done === seedStatus.db_documents.expected ? "#4ade80" : "#fbbf24" }}>
                    Embedded: {seedStatus.db_documents.done}/{seedStatus.db_documents.expected}
                    {seedStatus.db_documents.pending > 0 && <span style={{ color: "#fbbf24" }}> ({seedStatus.db_documents.pending} pending)</span>}
                  </span>
                  <span style={{ color: seedStatus.qdrant.reachable ? "#4ade80" : "#f87171" }}>
                    Qdrant: {seedStatus.qdrant.reachable ? `✓ ${seedStatus.qdrant.vector_count} vectors` : `✗ unreachable`}
                  </span>
                  <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: "11px" }}>
                    {seedStatus.qdrant.collection}
                  </span>
                </div>
                {/* Next step */}
                <div style={{ color: seedStatus.ready_for_demo ? "#4ade80" : "#fbbf24", fontWeight: 600, marginBottom: "8px" }}>
                  {seedStatus.ready_for_demo ? "✓ Ready for demo" : `→ ${seedStatus.next_step}`}
                </div>
                {/* Qdrant error */}
                {seedStatus.qdrant.error && (
                  <div style={{ color: "#f87171", fontFamily: "monospace", fontSize: "11px", marginBottom: "8px" }}>
                    Qdrant error: {seedStatus.qdrant.error}
                  </div>
                )}
                {/* Doc details */}
                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                  {seedStatus.db_documents.details.map(d => (
                    <span key={d.source_id} style={{
                      fontSize: "11px", padding: "2px 8px", borderRadius: "4px", fontFamily: "monospace",
                      background: d.embedding_status === "done" ? "rgba(74,222,128,0.1)" : "rgba(251,191,36,0.1)",
                      border: `1px solid ${d.embedding_status === "done" ? "rgba(74,222,128,0.3)" : "rgba(251,191,36,0.3)"}`,
                      color: d.embedding_status === "done" ? "#4ade80" : "#fbbf24",
                    }}>
                      {d.source_type}:{d.source_id} · {d.embedding_status}{d.chunk_count != null ? ` (${d.chunk_count} chunks)` : ""}
                    </span>
                  ))}
                </div>
                <button onClick={() => setSeedStatus(null)} style={{ marginTop: "10px", background: "none",
                  border: "none", color: "#475569", cursor: "pointer", fontSize: "12px" }}>Dismiss</button>
              </div>
            )}
          </div>
        )}
        {/* Check status button (shown after seeding) */}
        {!seedStatus && (
          <div style={{ marginBottom: "8px" }}>
            <button onClick={handleCheckSeedStatus} disabled={checkingStatus}
              style={{ background: "none", border: "none", color: "#475569", fontSize: "12px",
                cursor: checkingStatus ? "not-allowed" : "pointer", textDecoration: "underline", padding: 0 }}>
              {checkingStatus ? "Checking…" : "Check demo data status"}
            </button>
          </div>
        )}

        {/* Stats strip */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "12px", marginBottom: "24px" }}>
          {[
            { label: "Active", value: stats.active, color: "#f87171" },
            { label: "Open", value: stats.open, color: "#fb923c" },
            { label: "Resolved", value: stats.resolved, color: "#4ade80" },
            { label: "P0/P1 Open", value: stats.p0p1, color: "#fbbf24" },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: "#1e293b",
              border: "1px solid rgba(255,255,255,0.07)",
              borderRadius: "12px", padding: "16px 20px" }}>
              <div style={{ fontSize: "26px", fontWeight: 700, color }}>{value}</div>
              <div style={{ fontSize: "12px", color: "#64748b", marginTop: "4px" }}>{label}</div>
            </div>
          ))}
        </div>

        {/* Filters */}
        <div style={{ display: "flex", gap: "12px", marginBottom: "20px", flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: "1 1 220px" }}>
            <Search size={14} style={{ position: "absolute", left: "12px", top: "50%",
              transform: "translateY(-50%)", color: "#475569" }} />
            <input value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Search incidents…"
              style={{ width: "100%", paddingLeft: "36px", paddingRight: "12px",
                paddingTop: "10px", paddingBottom: "10px",
                background: "#1e293b", border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: "8px", color: "#f1f5f9", fontSize: "14px", outline: "none" }} />
          </div>

          {[
            { label: "All Statuses", key: "filterStatus", value: filterStatus, setter: setFilterStatus,
              options: ["open", "investigating", "analysing", "resolved", "closed"] },
            { label: "All Severities", key: "filterSeverity", value: filterSeverity, setter: setFilterSeverity,
              options: ["p0", "p1", "p2", "p3", "p4"] },
          ].map(({ label, key, value, setter, options }) => (
            <select key={key} value={value} onChange={e => setter(e.target.value)}
              style={{ padding: "10px 12px", background: "#1e293b",
                border: "1px solid rgba(255,255,255,0.08)", borderRadius: "8px",
                color: value ? "#f1f5f9" : "#64748b", fontSize: "14px", outline: "none" }}>
              <option value="">{label}</option>
              {options.map(o => <option key={o} value={o}>{o.toUpperCase()}</option>)}
            </select>
          ))}
        </div>

        {/* Incident list */}
        {loading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: "80px",
            color: "#475569" }}>
            <Loader2 size={32} style={{ animation: "spin 1s linear infinite" }} />
          </div>
        ) : error ? (
          <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: "12px", padding: "20px", color: "#f87171", textAlign: "center" }}>
            {error}
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ textAlign: "center", padding: "80px 40px", color: "#475569" }}>
            <AlertTriangle size={48} style={{ marginBottom: "16px", opacity: 0.3 }} />
            <h3 style={{ margin: "0 0 8px", color: "#64748b" }}>No incidents found</h3>
            <p style={{ margin: "0 0 20px", fontSize: "14px" }}>
              {search || filterStatus || filterSeverity
                ? "Try adjusting your filters."
                : "Declare your first incident to get started."}
            </p>
            {!search && !filterStatus && !filterSeverity && (
              <button onClick={() => setShowNew(true)}
                style={{ padding: "12px 24px", borderRadius: "8px", background: "#ef4444",
                  border: "none", color: "#fff", fontSize: "14px",
                  fontWeight: 600, cursor: "pointer" }}>
                Declare Incident
              </button>
            )}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {filtered.map(inc => {
              const sev = SEVERITY_COLORS[inc.severity] ?? SEVERITY_COLORS.p4;
              const isSelected = selected?.id === inc.id;
              return (
                <div key={inc.id}
                  onClick={() => setSelected(isSelected ? null : inc)}
                  style={{ background: isSelected ? "#1e293b" : "rgba(30,41,59,0.5)",
                    border: isSelected
                      ? `1px solid ${sev.border}`
                      : "1px solid rgba(255,255,255,0.07)",
                    borderRadius: "12px", padding: "16px 20px", cursor: "pointer",
                    display: "flex", alignItems: "center", gap: "16px",
                    transition: "all 0.15s" }}>

                  {/* severity stripe */}
                  <div style={{ width: "3px", alignSelf: "stretch", borderRadius: "4px",
                    background: sev.text, flexShrink: 0 }} />

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px",
                      marginBottom: "6px", flexWrap: "wrap" }}>
                      <SeverityBadge severity={inc.severity} />
                      <StatusBadge status={inc.status} />
                      {inc.service && (
                        <span style={{ fontSize: "12px", color: "#64748b" }}>{inc.service}</span>
                      )}
                      <span style={{ fontSize: "12px", color: "#475569", marginLeft: "auto" }}>
                        {timeSince(inc.created_at)}
                      </span>
                    </div>
                    <div style={{ fontSize: "14px", fontWeight: 600, color: "#f1f5f9",
                      marginBottom: "4px", whiteSpace: "nowrap", overflow: "hidden",
                      textOverflow: "ellipsis" }}>
                      {inc.title}
                    </div>
                    {inc.root_cause && (
                      <div style={{ fontSize: "12px", color: "#64748b",
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        Root cause: {inc.root_cause.slice(0, 120)}
                      </div>
                    )}
                    {inc.timeline?.length > 0 && (
                      <div style={{ fontSize: "11px", color: "#475569", marginTop: "4px" }}>
                        {inc.timeline.length} signal{inc.timeline.length !== 1 ? "s" : ""} correlated
                      </div>
                    )}
                  </div>

                  <ChevronRight size={16} style={{ color: "#475569", flexShrink: 0 }} />
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Modals */}
      {showSimulate && (
        <SimulateAlertModal
          onClose={() => setShowSimulate(false)}
          onSimulate={handleSimulate}
        />
      )}
      {showNew && (
        <NewIncidentModal onClose={() => setShowNew(false)} onCreate={handleCreate} />
      )}
      {selected && (
        <IncidentDetail
          incident={selected}
          onClose={() => setSelected(null)}
          onInvestigate={handleInvestigate}
          onStatusChange={handleStatusChange}
        />
      )}
    </>
  );
}
