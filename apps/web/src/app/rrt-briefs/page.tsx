"use client";
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  FileText, RefreshCw, CheckCircle, AlertCircle,
  Clock, ChevronDown, ChevronUp, ExternalLink, Zap,
  GitBranch, MessageSquare, Tag, X, Ticket
} from "lucide-react";
import { rrtBriefsApi, RRTBrief } from "@/lib/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, { bg: string; text: string; icon: typeof Clock }> = {
  open:          { bg: "rgba(239,68,68,0.12)",   text: "#f87171",  icon: AlertCircle },
  investigating: { bg: "rgba(249,115,22,0.12)",  text: "#fb923c",  icon: Clock },
  resolved:      { bg: "rgba(34,197,94,0.12)",   text: "#4ade80",  icon: CheckCircle },
};

const SOURCE_ICONS: Record<string, typeof GitBranch> = {
  github: GitBranch,
  slack:  MessageSquare,
  jira:   Tag,
};

function timeSince(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLES[status] ?? STATUS_STYLES.open;
  const Icon = s.icon;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: "5px",
      fontSize: "11px", fontWeight: 600, padding: "3px 10px",
      borderRadius: "20px", background: s.bg, color: s.text,
      textTransform: "capitalize",
    }}>
      <Icon size={11} />
      {status}
    </span>
  );
}

// ── Brief detail panel ───────────────────────────────────────────────────────

function BriefDetail({ brief, getToken, onClose, onStatusChange, onJiraPush }: {
  brief: RRTBrief;
  getToken: () => Promise<string | null>;
  onClose: () => void;
  onStatusChange: (id: string, status: string) => void;
  onJiraPush: (id: string, key: string, url: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [notes, setNotes] = useState(brief.resolution_notes ?? "");
  const [updateText, setUpdateText] = useState("");
  const [posting, setPosting] = useState(false);
  const [postMsg, setPostMsg] = useState<string | null>(null);
  const [pushing, setPushing] = useState(false);
  const [jiraMsg, setJiraMsg] = useState<string | null>(null);

  async function handleStatusChange(newStatus: string) {
    setSaving(true);
    try {
      const token = await getToken();
      if (!token) return;
      await rrtBriefsApi.update(token, brief.id, { status: newStatus });
      onStatusChange(brief.id, newStatus);
    } finally { setSaving(false); }
  }

  async function handlePostUpdate() {
    if (!updateText.trim()) return;
    setPosting(true);
    try {
      const token = await getToken();
      if (!token) return;
      const res = await rrtBriefsApi.postUpdate(token, brief.id, { update_text: updateText });
      setPostMsg(res.slack_notified ? "Posted to Slack ✓" : "Saved (no Slack webhook configured)");
      setUpdateText("");
      setTimeout(() => setPostMsg(null), 3000);
    } catch { setPostMsg("Failed to post update"); }
    finally { setPosting(false); }
  }

  async function handlePushToJira() {
    setPushing(true);
    setJiraMsg(null);
    try {
      const token = await getToken();
      if (!token) { setJiraMsg("Not authenticated — please refresh the page"); return; }
      const res = await rrtBriefsApi.pushToJira(token, brief.id);
      onJiraPush(brief.id, res.ticket_key, res.ticket_url);
      setJiraMsg(res.already_existed ? `Already linked: ${res.ticket_key}` : `Ticket created: ${res.ticket_key}`);
    } catch (e: unknown) {
      setJiraMsg(e instanceof Error ? e.message : "Failed to create Jira ticket");
    } finally {
      setPushing(false);
    }
  }

  const cell = (label: string, value: string | null | undefined) =>
    value ? (
      <div style={{ marginBottom: "20px" }}>
        <div style={{ fontSize: "11px", fontWeight: 600, color: "#64748b", textTransform: "uppercase",
          letterSpacing: "0.08em", marginBottom: "6px" }}>{label}</div>
        <div style={{ fontSize: "14px", color: "#e2e8f0", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{value}</div>
      </div>
    ) : null;

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 50,
      background: "rgba(0,0,0,0.6)", display: "flex", justifyContent: "flex-end",
    }} onClick={onClose}>
      <div style={{
        width: "620px", maxWidth: "100vw", height: "100%",
        background: "#0f172a", borderLeft: "1px solid rgba(255,255,255,0.1)",
        overflowY: "auto", padding: "32px",
      }} onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "24px" }}>
          <div style={{ flex: 1, paddingRight: "16px" }}>
            <div style={{ marginBottom: "8px" }}><StatusBadge status={brief.status} /></div>
            <h2 style={{ fontSize: "18px", fontWeight: 700, color: "#f1f5f9", margin: 0, lineHeight: 1.3 }}>
              {brief.title}
            </h2>
            <div style={{ fontSize: "12px", color: "#64748b", marginTop: "6px" }}>
              Detected {timeSince(brief.detected_at)} · {brief.owner_team ?? "Unassigned"}
            </div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", padding: "4px" }}>
            <X size={20} />
          </button>
        </div>

        {/* Action bar — status + Jira */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "24px", flexWrap: "wrap" }}>
          {["open", "investigating", "resolved"].map(s => (
            <button
              key={s}
              disabled={saving || brief.status === s}
              onClick={() => handleStatusChange(s)}
              style={{
                padding: "6px 14px", borderRadius: "6px", fontSize: "12px", fontWeight: 600,
                border: "1px solid",
                cursor: brief.status === s ? "default" : "pointer",
                background: brief.status === s ? "rgba(20,184,166,0.2)" : "transparent",
                borderColor: brief.status === s ? "#14b8a6" : "rgba(255,255,255,0.12)",
                color: brief.status === s ? "#2dd4bf" : "#94a3b8",
                textTransform: "capitalize",
                opacity: saving ? 0.6 : 1,
              }}
            >{s}</button>
          ))}

          {/* Jira pill — right side of action bar */}
          <div style={{ marginLeft: "auto" }}>
            {brief.jira_ticket_key ? (
              <a
                href={brief.jira_ticket_url ?? "#"}
                target="_blank"
                rel="noreferrer"
                style={{
                  display: "inline-flex", alignItems: "center", gap: "6px",
                  padding: "6px 12px", borderRadius: "6px", fontSize: "12px", fontWeight: 600,
                  background: "rgba(99,102,241,0.15)", border: "1px solid rgba(99,102,241,0.35)",
                  color: "#a5b4fc", textDecoration: "none",
                }}
              >
                <Ticket size={12} />
                {brief.jira_ticket_key}
                <ExternalLink size={10} />
              </a>
            ) : (
              <button
                onClick={handlePushToJira}
                disabled={pushing}
                style={{
                  display: "inline-flex", alignItems: "center", gap: "6px",
                  padding: "6px 14px", borderRadius: "6px", fontSize: "12px", fontWeight: 600,
                  background: "rgba(99,102,241,0.15)", border: "1px solid rgba(99,102,241,0.35)",
                  color: "#a5b4fc", cursor: pushing ? "default" : "pointer",
                  opacity: pushing ? 0.6 : 1,
                }}
              >
                <Ticket size={12} />
                {pushing ? "Creating…" : "Push to Jira"}
              </button>
            )}
          </div>
        </div>

        {/* Jira error message */}
        {jiraMsg && !brief.jira_ticket_key && (
          <div style={{
            fontSize: "12px", color: "#f87171", marginBottom: "16px",
            background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)",
            borderRadius: "6px", padding: "8px 12px",
          }}>{jiraMsg}</div>
        )}

        {/* Main fields */}
        {cell("What Happened", brief.what_happened)}
        {cell("Impact", brief.impact)}
        {cell("Suspected Cause", brief.suspected_cause)}

        {/* Next Actions */}
        {brief.next_actions.length > 0 && (
          <div style={{ marginBottom: "20px" }}>
            <div style={{ fontSize: "11px", fontWeight: 600, color: "#64748b", textTransform: "uppercase",
              letterSpacing: "0.08em", marginBottom: "8px" }}>Next Actions</div>
            {brief.next_actions.map((action, i) => (
              <div key={i} style={{ display: "flex", gap: "10px", marginBottom: "6px", alignItems: "flex-start" }}>
                <span style={{ fontSize: "11px", fontWeight: 700, color: "#14b8a6",
                  background: "rgba(20,184,166,0.15)", borderRadius: "50%",
                  width: "20px", height: "20px", display: "flex", alignItems: "center",
                  justifyContent: "center", flexShrink: 0, marginTop: "1px" }}>{i + 1}</span>
                <span style={{ fontSize: "14px", color: "#e2e8f0", lineHeight: 1.5 }}>{action}</span>
              </div>
            ))}
          </div>
        )}

        {/* Related context */}
        {brief.related_items.length > 0 && (
          <div style={{ marginBottom: "20px" }}>
            <div style={{ fontSize: "11px", fontWeight: 600, color: "#64748b", textTransform: "uppercase",
              letterSpacing: "0.08em", marginBottom: "8px" }}>Related Context</div>
            {brief.related_items.map((item, i) => {
              const Icon = SOURCE_ICONS[item.source_type] ?? Tag;
              return (
                <div key={i} style={{
                  background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: "8px", padding: "12px", marginBottom: "8px",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                    <Icon size={13} color="#64748b" />
                    <span style={{ fontSize: "13px", fontWeight: 600, color: "#cbd5e1" }}>{item.title}</span>
                    {item.url && (
                      <a href={item.url} target="_blank" rel="noreferrer"
                        style={{ marginLeft: "auto", color: "#64748b" }}><ExternalLink size={12} /></a>
                    )}
                  </div>
                  <p style={{ fontSize: "12px", color: "#64748b", margin: 0, lineHeight: 1.5 }}>{item.snippet}</p>
                </div>
              );
            })}
          </div>
        )}

        {/* Post status update */}
        <div style={{
          borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "20px", marginTop: "8px",
        }}>
          <div style={{ fontSize: "11px", fontWeight: 600, color: "#64748b", textTransform: "uppercase",
            letterSpacing: "0.08em", marginBottom: "8px" }}>Post Update to Slack</div>
          <textarea
            value={updateText}
            onChange={e => setUpdateText(e.target.value)}
            placeholder="Root cause confirmed: PR #87 removed tenant_id. Rollback in progress..."
            rows={3}
            style={{
              width: "100%", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: "8px", color: "#e2e8f0", padding: "10px 12px", fontSize: "13px",
              resize: "vertical", fontFamily: "inherit", boxSizing: "border-box",
            }}
          />
          {postMsg && (
            <div style={{ fontSize: "12px", color: "#4ade80", marginTop: "6px" }}>{postMsg}</div>
          )}
          <button
            onClick={handlePostUpdate}
            disabled={!updateText.trim() || posting}
            style={{
              marginTop: "10px", padding: "8px 18px", borderRadius: "8px",
              background: "rgba(20,184,166,0.2)", border: "1px solid rgba(20,184,166,0.4)",
              color: "#2dd4bf", fontSize: "13px", fontWeight: 600, cursor: "pointer",
              opacity: (!updateText.trim() || posting) ? 0.5 : 1,
            }}
          >{posting ? "Posting…" : "Post Update"}</button>
        </div>

      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function RRTBriefsPage() {
  const { getToken } = useAuth();
  const [briefs, setBriefs] = useState<RRTBrief[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [selected, setSelected] = useState<RRTBrief | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      if (!token) return;
      const data = await rrtBriefsApi.list(token, {
        status: statusFilter || undefined,
        days: 14,
        limit: 50,
      });
      setBriefs(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load briefs");
    } finally {
      setLoading(false);
    }
  }, [getToken, statusFilter]);

  useEffect(() => { load(); }, [load]);

  function handleStatusChange(id: string, newStatus: string) {
    setBriefs(prev => prev.map(b => b.id === id ? { ...b, status: newStatus } : b));
    if (selected?.id === id) setSelected(prev => prev ? { ...prev, status: newStatus } : prev);
  }

  function handleJiraPush(id: string, key: string, url: string) {
    setBriefs(prev => prev.map(b => b.id === id ? { ...b, jira_ticket_key: key, jira_ticket_url: url } : b));
    if (selected?.id === id) setSelected(prev => prev ? { ...prev, jira_ticket_key: key, jira_ticket_url: url } : prev);
  }

  const counts = {
    open:          briefs.filter(b => b.status === "open").length,
    investigating: briefs.filter(b => b.status === "investigating").length,
    resolved:      briefs.filter(b => b.status === "resolved").length,
  };

  return (
    <div style={{ padding: "32px", maxWidth: "1100px" }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "28px" }}>
        <div>
          <h1 style={{ fontSize: "22px", fontWeight: 700, color: "#f1f5f9", margin: 0 }}>RRT Briefs</h1>
          <p style={{ fontSize: "13px", color: "#64748b", marginTop: "4px" }}>
            AI-generated Rapid Response Team incident artifacts
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          style={{
            display: "flex", alignItems: "center", gap: "6px",
            padding: "8px 16px", borderRadius: "8px", fontSize: "13px", fontWeight: 600,
            background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
            color: "#94a3b8", cursor: "pointer",
          }}
        >
          <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
          Refresh
        </button>
      </div>

      {/* Stats row */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "24px" }}>
        {[
          { label: "Open",          count: counts.open,          color: "#f87171", bg: "rgba(239,68,68,0.1)" },
          { label: "Investigating", count: counts.investigating,  color: "#fb923c", bg: "rgba(249,115,22,0.1)" },
          { label: "Resolved",      count: counts.resolved,       color: "#4ade80", bg: "rgba(34,197,94,0.1)" },
        ].map(s => (
          <div key={s.label} style={{
            flex: 1, background: s.bg, border: `1px solid ${s.color}33`,
            borderRadius: "10px", padding: "16px", textAlign: "center",
          }}>
            <div style={{ fontSize: "26px", fontWeight: 700, color: s.color }}>{s.count}</div>
            <div style={{ fontSize: "11px", color: "#64748b", marginTop: "4px" }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Filter tabs */}
      <div style={{ display: "flex", gap: "6px", marginBottom: "20px" }}>
        {["", "open", "investigating", "resolved"].map(f => (
          <button
            key={f || "all"}
            onClick={() => setStatusFilter(f)}
            style={{
              padding: "6px 14px", borderRadius: "20px", fontSize: "12px", fontWeight: 600,
              cursor: "pointer",
              background: statusFilter === f ? "rgba(20,184,166,0.2)" : "rgba(255,255,255,0.05)",
              border: statusFilter === f ? "1px solid rgba(20,184,166,0.4)" : "1px solid rgba(255,255,255,0.1)",
              color: statusFilter === f ? "#2dd4bf" : "#64748b",
              textTransform: "capitalize",
            }}
          >{f || "All"}</button>
        ))}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: "8px", padding: "12px 16px", color: "#f87171",
          fontSize: "14px", marginBottom: "16px",
        }}>{error}</div>
      )}

      {/* Loading */}
      {loading && !briefs.length && (
        <div style={{ textAlign: "center", padding: "60px", color: "#475569" }}>
          <RefreshCw size={24} style={{ animation: "spin 1s linear infinite", marginBottom: "12px" }} />
          <div style={{ fontSize: "14px" }}>Loading RRT briefs…</div>
        </div>
      )}

      {/* Empty */}
      {!loading && !briefs.length && !error && (
        <div style={{
          textAlign: "center", padding: "60px",
          border: "1px dashed rgba(255,255,255,0.12)", borderRadius: "12px",
        }}>
          <FileText size={36} color="#334155" style={{ marginBottom: "12px" }} />
          <div style={{ fontSize: "15px", fontWeight: 600, color: "#475569", marginBottom: "6px" }}>
            No RRT briefs yet
          </div>
          <p style={{ fontSize: "13px", color: "#334155", maxWidth: "380px", margin: "0 auto" }}>
            Briefs are generated automatically when the log scanner detects a critical error.
            Use the <strong style={{ color: "#64748b" }}>Simulate Alert</strong> button on the
            Incidents page to trigger one for a demo.
          </p>
        </div>
      )}

      {/* Brief cards */}
      {briefs.map(brief => (
        <div
          key={brief.id}
          onClick={() => setSelected(brief)}
          style={{
            background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: "10px", padding: "18px 20px", marginBottom: "10px",
            cursor: "pointer", transition: "border-color 0.15s",
          }}
          onMouseEnter={e => (e.currentTarget.style.borderColor = "rgba(20,184,166,0.4)")}
          onMouseLeave={e => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)")}
        >
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                <StatusBadge status={brief.status} />
                {brief.owner_team && (
                  <span style={{ fontSize: "11px", color: "#64748b",
                    background: "rgba(255,255,255,0.05)", padding: "2px 8px",
                    borderRadius: "4px", border: "1px solid rgba(255,255,255,0.08)" }}>
                    {brief.owner_team}
                  </span>
                )}
                <span style={{ fontSize: "11px", color: "#475569", marginLeft: "auto" }}>
                  {timeSince(brief.detected_at)}
                </span>
              </div>
              <h3 style={{ fontSize: "15px", fontWeight: 600, color: "#e2e8f0", margin: "0 0 6px" }}>
                {brief.title}
              </h3>
              <p style={{
                fontSize: "13px", color: "#64748b", margin: 0, lineHeight: 1.5,
                display: "-webkit-box", WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical", overflow: "hidden",
              }}>
                {brief.what_happened}
              </p>
            </div>
            <ChevronDown size={16} color="#475569" style={{ marginLeft: "16px", flexShrink: 0, marginTop: "2px" }} />
          </div>

          {/* Next actions preview */}
          {brief.next_actions.length > 0 && (
            <div style={{ marginTop: "12px", display: "flex", gap: "6px", flexWrap: "wrap" }}>
              {brief.next_actions.slice(0, 3).map((a, i) => (
                <span key={i} style={{
                  fontSize: "11px", color: "#94a3b8",
                  background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
                  padding: "2px 8px", borderRadius: "4px",
                }}>
                  {i + 1}. {a.length > 50 ? a.slice(0, 50) + "…" : a}
                </span>
              ))}
              {brief.next_actions.length > 3 && (
                <span style={{ fontSize: "11px", color: "#475569" }}>
                  +{brief.next_actions.length - 3} more
                </span>
              )}
            </div>
          )}

          {/* Footer meta */}
          <div style={{ marginTop: "10px", fontSize: "11px", color: "#475569", display: "flex", gap: "12px", alignItems: "center" }}>
            {brief.related_items.length > 0 && (
              <span><Zap size={10} style={{ marginRight: "4px" }} />{brief.related_items.length} related items</span>
            )}
            {brief.error_signature && (
              <span style={{ fontFamily: "monospace" }}>sig: {brief.error_signature.slice(0, 8)}…</span>
            )}
            {brief.jira_ticket_key && (
              <span style={{
                marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: "4px",
                color: "#a5b4fc", background: "rgba(99,102,241,0.12)",
                border: "1px solid rgba(99,102,241,0.25)", borderRadius: "4px", padding: "1px 7px",
              }}>
                <Ticket size={10} /> {brief.jira_ticket_key}
              </span>
            )}
          </div>
        </div>
      ))}

      {/* Detail panel */}
      {selected && (
        <BriefDetail
          brief={selected}
          getToken={getToken}
          onClose={() => setSelected(null)}
          onStatusChange={handleStatusChange}
          onJiraPush={handleJiraPush}
        />
      )}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
