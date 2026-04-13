"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { Plus, Trash2, BellOff, RefreshCw, AlertCircle, Clock, CheckCircle2, X } from "lucide-react";
import { formatDistanceToNow, format } from "date-fns";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { knownIssuesApi, KnownIssue, CreateKnownIssueData } from "@/lib/api";

// ── Add / Edit modal ──────────────────────────────────────────────────────────
function AddIssueModal({
  onSubmit,
  onCancel,
  submitting,
}: {
  onSubmit: (data: CreateKnownIssueData) => void;
  onCancel: () => void;
  submitting: boolean;
}) {
  const [matchType, setMatchType] = useState<"pattern" | "signature">("pattern");
  const [pattern, setPattern]     = useState("");
  const [signature, setSignature] = useState("");
  const [description, setDescription] = useState("");
  const [snoozeType, setSnoozeType]   = useState<"permanent" | "1h" | "24h" | "7d" | "custom">("permanent");
  const [customDate, setCustomDate]   = useState("");
  const [jiraKey, setJiraKey]         = useState("");

  const resolveSupressUntil = (): string | undefined => {
    const now = new Date();
    if (snoozeType === "1h")  return new Date(now.getTime() + 3600000).toISOString();
    if (snoozeType === "24h") return new Date(now.getTime() + 86400000).toISOString();
    if (snoozeType === "7d")  return new Date(now.getTime() + 604800000).toISOString();
    if (snoozeType === "custom" && customDate) return new Date(customDate).toISOString();
    return undefined; // permanent
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const value = matchType === "pattern" ? pattern.trim() : signature.trim();
    if (!value) {
      toast.error(matchType === "pattern" ? "Enter a keyword or regex pattern" : "Enter the error signature");
      return;
    }
    const data: CreateKnownIssueData = {
      description: description.trim() || undefined,
      suppress_until: resolveSupressUntil(),
      jira_ticket_key: jiraKey.trim() || undefined,
    };
    if (matchType === "pattern") data.match_pattern = value;
    else data.signature = value;
    onSubmit(data);
  };

  const inputStyle: React.CSSProperties = {
    width: "100%", padding: "0.5rem 0.75rem", borderRadius: "0.5rem",
    border: "1px solid rgba(255,255,255,0.1)", background: "#1e293b",
    color: "#f1f5f9", fontSize: "0.875rem", boxSizing: "border-box",
  };
  const labelStyle: React.CSSProperties = {
    display: "block", color: "#94a3b8", fontSize: "0.8rem", fontWeight: 500, marginBottom: "0.375rem",
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 50,
      display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem",
    }}>
      <div style={{
        background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "0.75rem",
        padding: "1.5rem", width: "100%", maxWidth: "520px",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <div>
            <h2 style={{ color: "#f1f5f9", fontSize: "1rem", fontWeight: 600, margin: 0 }}>Suppress alert</h2>
            <p style={{ color: "#64748b", fontSize: "0.75rem", margin: "0.25rem 0 0" }}>
              Match by keyword / regex or paste the signature from a Slack alert
            </p>
          </div>
          <button onClick={onCancel} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer" }}>
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {/* Match type toggle */}
          <div style={{ display: "flex", gap: "0.5rem" }}>
            {(["pattern", "signature"] as const).map((t) => (
              <button key={t} type="button" onClick={() => setMatchType(t)} style={{
                flex: 1, padding: "0.5rem", borderRadius: "0.5rem", cursor: "pointer",
                border: matchType === t ? "1px solid #14b8a6" : "1px solid rgba(255,255,255,0.1)",
                background: matchType === t ? "rgba(20,184,166,0.1)" : "#1e293b",
                color: matchType === t ? "#14b8a6" : "#94a3b8", fontSize: "0.8125rem",
              }}>
                {t === "pattern" ? "Keyword / Regex" : "Error Signature"}
              </button>
            ))}
          </div>

          {matchType === "pattern" ? (
            <div>
              <label style={labelStyle}>Pattern (keyword or regex) *</label>
              <input
                value={pattern} onChange={e => setPattern(e.target.value)}
                placeholder='e.g. ConnectionRefused  or  KeyError.*tenant_id'
                style={inputStyle}
              />
              <p style={{ color: "#475569", fontSize: "0.75rem", margin: "0.25rem 0 0" }}>
                Matches anywhere in the error line — plain text or regex both work
              </p>
            </div>
          ) : (
            <div>
              <label style={labelStyle}>Signature (from Slack alert) *</label>
              <input
                value={signature} onChange={e => setSignature(e.target.value)}
                placeholder="e.g. railway:a3f1c29d"
                style={inputStyle}
              />
              <p style={{ color: "#475569", fontSize: "0.75rem", margin: "0.25rem 0 0" }}>
                Copy the signature from the Slack alert footer
              </p>
            </div>
          )}

          <div>
            <label style={labelStyle}>Reason / description</label>
            <input
              value={description} onChange={e => setDescription(e.target.value)}
              placeholder="e.g. Known flaky test, tracked in OPS-42"
              style={inputStyle}
            />
          </div>

          <div>
            <label style={labelStyle}>Suppress for</label>
            <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
              {([
                { key: "permanent", label: "Forever" },
                { key: "1h",        label: "1 hour" },
                { key: "24h",       label: "24 hours" },
                { key: "7d",        label: "7 days" },
                { key: "custom",    label: "Custom…" },
              ] as const).map(({ key, label }) => (
                <button key={key} type="button" onClick={() => setSnoozeType(key)} style={{
                  padding: "0.375rem 0.625rem", borderRadius: "0.375rem", cursor: "pointer",
                  border: snoozeType === key ? "1px solid #14b8a6" : "1px solid rgba(255,255,255,0.1)",
                  background: snoozeType === key ? "rgba(20,184,166,0.1)" : "#1e293b",
                  color: snoozeType === key ? "#14b8a6" : "#94a3b8", fontSize: "0.75rem",
                }}>{label}</button>
              ))}
            </div>
            {snoozeType === "custom" && (
              <input
                type="datetime-local" value={customDate}
                onChange={e => setCustomDate(e.target.value)}
                style={{ ...inputStyle, marginTop: "0.5rem" }}
              />
            )}
          </div>

          <div>
            <label style={labelStyle}>Jira ticket (optional)</label>
            <input
              value={jiraKey} onChange={e => setJiraKey(e.target.value)}
              placeholder="e.g. OPS-42"
              style={inputStyle}
            />
            <p style={{ color: "#475569", fontSize: "0.75rem", margin: "0.25rem 0 0" }}>
              Auto-suppresses while the ticket is still open
            </p>
          </div>

          <div style={{ display: "flex", gap: "0.75rem", marginTop: "0.5rem" }}>
            <button type="button" onClick={onCancel} style={{
              flex: 1, padding: "0.625rem", borderRadius: "0.5rem",
              border: "1px solid rgba(255,255,255,0.1)", background: "none",
              color: "#94a3b8", cursor: "pointer", fontSize: "0.875rem",
            }}>Cancel</button>
            <button type="submit" disabled={submitting} style={{
              flex: 2, padding: "0.625rem", borderRadius: "0.5rem",
              background: submitting ? "#0f766e" : "#14b8a6",
              border: "none", color: "white", cursor: "pointer", fontSize: "0.875rem",
            }}>
              {submitting ? "Saving…" : "Suppress this alert"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Issue card ────────────────────────────────────────────────────────────────
function IssueCard({
  issue,
  onDelete,
  onSnooze,
  onRearm,
}: {
  issue: KnownIssue;
  onDelete: (id: string) => void;
  onSnooze: (id: string, until: string) => void;
  onRearm:  (id: string) => void;
}) {
  const isSnoozed = issue.suppress_until
    ? new Date(issue.suppress_until) > new Date()
    : false;
  const isExpired = issue.suppress_until
    ? new Date(issue.suppress_until) <= new Date()
    : false;

  const statusColor = !issue.is_active || isExpired
    ? "#475569"
    : isSnoozed
    ? "#f59e0b"
    : "#10b981";

  const statusLabel = !issue.is_active || isExpired
    ? "Expired / inactive"
    : isSnoozed
    ? `Snoozed until ${format(new Date(issue.suppress_until!), "MMM d, HH:mm")}`
    : "Active — suppressing";

  return (
    <div style={{
      background: "#0f172a", border: "1px solid rgba(255,255,255,0.08)",
      borderRadius: "0.75rem", padding: "1rem 1.25rem",
      display: "flex", flexDirection: "column", gap: "0.625rem",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {issue.match_pattern && (
            <code style={{
              display: "inline-block", background: "#1e293b", color: "#e2e8f0",
              padding: "0.125rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.8125rem",
              maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>{issue.match_pattern}</code>
          )}
          {issue.signature && (
            <code style={{
              display: "inline-block", background: "#1e293b", color: "#94a3b8",
              padding: "0.125rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.75rem",
            }}>{issue.signature}</code>
          )}
          {issue.description && (
            <p style={{ color: "#94a3b8", fontSize: "0.8125rem", margin: "0.375rem 0 0" }}>
              {issue.description}
            </p>
          )}
        </div>

        <div style={{ display: "flex", gap: "0.375rem", flexShrink: 0 }}>
          {/* Snooze 24h quick action */}
          <button
            title="Snooze 24h"
            onClick={() => onSnooze(issue.id, new Date(Date.now() + 86400000).toISOString())}
            style={{
              background: "none", border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: "0.375rem", color: "#f59e0b", cursor: "pointer", padding: "0.375rem",
            }}
          ><Clock size={14} /></button>

          {/* Re-arm if inactive */}
          {(!issue.is_active || isExpired) && (
            <button
              title="Re-arm (start alerting again)"
              onClick={() => onRearm(issue.id)}
              style={{
                background: "none", border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "0.375rem", color: "#10b981", cursor: "pointer", padding: "0.375rem",
              }}
            ><CheckCircle2 size={14} /></button>
          )}

          <button
            title="Delete suppression"
            onClick={() => onDelete(issue.id)}
            style={{
              background: "none", border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: "0.375rem", color: "#ef4444", cursor: "pointer", padding: "0.375rem",
            }}
          ><Trash2 size={14} /></button>
        </div>
      </div>

      <div style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
        {/* Status badge */}
        <span style={{
          display: "inline-flex", alignItems: "center", gap: "0.25rem",
          fontSize: "0.75rem", color: statusColor,
        }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: statusColor, display: "inline-block" }} />
          {statusLabel}
        </span>

        {/* Hit count */}
        <span style={{ fontSize: "0.75rem", color: "#475569" }}>
          Suppressed {issue.hit_count} alert{issue.hit_count !== 1 ? "s" : ""}
          {issue.last_hit_at && ` · last ${formatDistanceToNow(new Date(issue.last_hit_at), { addSuffix: true })}`}
        </span>

        {/* Jira badge */}
        {issue.jira_ticket_key && (
          <span style={{
            fontSize: "0.75rem", color: "#60a5fa",
            background: "rgba(96,165,250,0.1)", padding: "0.125rem 0.5rem", borderRadius: "0.25rem",
          }}>🎫 {issue.jira_ticket_key}</span>
        )}

        <span style={{ fontSize: "0.7rem", color: "#334155", marginLeft: "auto" }}>
          Added {formatDistanceToNow(new Date(issue.created_at), { addSuffix: true })}
        </span>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function KnownIssuesPage() {
  const { getToken } = useAuth();
  const [issues, setIssues]       = useState<KnownIssue[]>([]);
  const [loading, setLoading]     = useState(true);
  const [showAdd, setShowAdd]     = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = await getToken();
      const data  = await knownIssuesApi.list(token!);
      setIssues(data);
    } catch {
      toast.error("Failed to load suppression rules");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async (data: Parameters<typeof knownIssuesApi.create>[1]) => {
    setSubmitting(true);
    try {
      const token = await getToken();
      await knownIssuesApi.create(token!, data);
      toast.success("Suppression rule added — matching alerts will be silenced");
      setShowAdd(false);
      await load();
    } catch {
      toast.error("Failed to save suppression rule");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const token = await getToken();
      await knownIssuesApi.delete(token!, id);
      toast.success("Suppression removed — alerts will resume");
      setIssues(prev => prev.filter(i => i.id !== id));
    } catch {
      toast.error("Failed to remove suppression");
    }
  };

  const handleSnooze = async (id: string, until: string) => {
    try {
      const token = await getToken();
      await knownIssuesApi.update(token!, id, { suppress_until: until, is_active: true });
      toast.success("Snoozed for 24 hours");
      await load();
    } catch {
      toast.error("Failed to snooze");
    }
  };

  const handleRearm = async (id: string) => {
    try {
      const token = await getToken();
      await knownIssuesApi.update(token!, id, { is_active: true, suppress_until: undefined });
      toast.success("Re-armed — alerts will fire again for this error");
      await load();
    } catch {
      toast.error("Failed to re-arm");
    }
  };

  const active   = issues.filter(i => i.is_active && (!i.suppress_until || new Date(i.suppress_until) > new Date()));
  const inactive = issues.filter(i => !i.is_active || (i.suppress_until && new Date(i.suppress_until) <= new Date()));

  return (
    <AppShell>
      <div style={{ padding: "1.5rem 2rem", maxWidth: 800, margin: "0 auto" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1.5rem" }}>
          <div>
            <h1 style={{ color: "#f1f5f9", fontSize: "1.25rem", fontWeight: 700, margin: 0, display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <BellOff size={20} style={{ color: "#f59e0b" }} />
              Alert Suppressions
            </h1>
            <p style={{ color: "#64748b", fontSize: "0.875rem", margin: "0.375rem 0 0" }}>
              Mute known errors so they don't fire Slack alerts or RRT briefs.
              Records are still stored and searchable in chat.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button onClick={load} disabled={loading} style={{
              display: "flex", alignItems: "center", gap: "0.375rem",
              padding: "0.5rem 0.75rem", borderRadius: "0.5rem",
              border: "1px solid rgba(255,255,255,0.1)", background: "none",
              color: "#94a3b8", cursor: "pointer", fontSize: "0.875rem",
            }}>
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>
            <button onClick={() => setShowAdd(true)} style={{
              display: "flex", alignItems: "center", gap: "0.375rem",
              padding: "0.5rem 1rem", borderRadius: "0.5rem",
              background: "#14b8a6", border: "none",
              color: "white", cursor: "pointer", fontSize: "0.875rem",
            }}>
              <Plus size={14} /> Suppress error
            </button>
          </div>
        </div>

        {/* Stats bar */}
        <div style={{
          display: "flex", gap: "1rem", marginBottom: "1.5rem",
          padding: "0.875rem 1.25rem", borderRadius: "0.75rem",
          background: "#0f172a", border: "1px solid rgba(255,255,255,0.08)",
        }}>
          {[
            { label: "Active suppressions", value: active.length,   color: "#10b981" },
            { label: "Expired / inactive",  value: inactive.length, color: "#475569" },
            { label: "Total alerts silenced", value: issues.reduce((s, i) => s + i.hit_count, 0), color: "#f59e0b" },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ flex: 1 }}>
              <div style={{ color, fontSize: "1.5rem", fontWeight: 700 }}>{value}</div>
              <div style={{ color: "#475569", fontSize: "0.75rem" }}>{label}</div>
            </div>
          ))}
        </div>

        {/* Empty state */}
        {!loading && issues.length === 0 && (
          <div style={{
            textAlign: "center", padding: "3rem 1rem",
            border: "1px dashed rgba(255,255,255,0.1)", borderRadius: "0.75rem",
          }}>
            <AlertCircle size={32} style={{ color: "#334155", marginBottom: "0.75rem" }} />
            <p style={{ color: "#475569", margin: 0 }}>No suppressions yet.</p>
            <p style={{ color: "#334155", fontSize: "0.8125rem", margin: "0.375rem 0 0" }}>
              Suppress a recurring error to stop it generating Slack alerts.
            </p>
          </div>
        )}

        {/* Active rules */}
        {active.length > 0 && (
          <div style={{ marginBottom: "1.5rem" }}>
            <h2 style={{ color: "#94a3b8", fontSize: "0.75rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.75rem" }}>
              Active suppressions
            </h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem" }}>
              {active.map(i => (
                <IssueCard key={i.id} issue={i} onDelete={handleDelete} onSnooze={handleSnooze} onRearm={handleRearm} />
              ))}
            </div>
          </div>
        )}

        {/* Expired / inactive */}
        {inactive.length > 0 && (
          <div>
            <h2 style={{ color: "#475569", fontSize: "0.75rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.75rem" }}>
              Expired / inactive
            </h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem" }}>
              {inactive.map(i => (
                <IssueCard key={i.id} issue={i} onDelete={handleDelete} onSnooze={handleSnooze} onRearm={handleRearm} />
              ))}
            </div>
          </div>
        )}
      </div>

      {showAdd && (
        <AddIssueModal onSubmit={handleAdd} onCancel={() => setShowAdd(false)} submitting={submitting} />
      )}
    </AppShell>
  );
}
