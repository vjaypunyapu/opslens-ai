"use client";
import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  Plus, Pencil, Trash2, ToggleLeft, ToggleRight, Zap, ChevronUp,
  ChevronDown, X, CheckCircle, AlertCircle, Route, FlaskConical,
} from "lucide-react";
import { routingRulesApi, RoutingRule, CreateRoutingRuleData, TestRoutingResult } from "@/lib/api";

// ─── helpers ──────────────────────────────────────────────────────────────────

function TagList({ items, color = "#14b8a6" }: { items: string[]; color?: string }) {
  if (!items.length) return <span style={{ color: "#475569", fontSize: "12px" }}>—</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
      {items.map((t) => (
        <span key={t} style={{
          background: `${color}18`, border: `1px solid ${color}33`,
          color, borderRadius: "4px", padding: "2px 7px", fontSize: "11px", fontFamily: "monospace",
        }}>{t}</span>
      ))}
    </div>
  );
}

function PriorityBadge({ priority }: { priority: number }) {
  const color = priority <= 20 ? "#ef4444" : priority <= 50 ? "#f97316" : priority >= 900 ? "#64748b" : "#14b8a6";
  const label = priority <= 20 ? "Critical" : priority <= 50 ? "High" : priority >= 900 ? "Catch-all" : `P${priority}`;
  return (
    <span style={{
      background: `${color}20`, border: `1px solid ${color}40`, color,
      borderRadius: "5px", padding: "2px 8px", fontSize: "11px", fontWeight: 700,
    }}>{label} · {priority}</span>
  );
}

// ─── Tag input ────────────────────────────────────────────────────────────────

function TagInput({
  label, hint, value, onChange,
}: { label: string; hint: string; value: string[]; onChange: (v: string[]) => void }) {
  const [input, setInput] = useState("");

  function addTag() {
    const t = input.trim();
    if (t && !value.includes(t)) onChange([...value, t]);
    setInput("");
  }

  return (
    <div style={{ marginBottom: "16px" }}>
      <label style={{ display: "block", fontSize: "12px", color: "#94a3b8", fontWeight: 600,
        textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "6px" }}>{label}</label>
      <p style={{ fontSize: "11px", color: "#475569", marginBottom: "8px" }}>{hint}</p>
      <div style={{ display: "flex", gap: "6px", marginBottom: "6px" }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }}
          placeholder="Type and press Enter"
          style={{
            flex: 1, background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: "6px", padding: "7px 10px", color: "#f1f5f9", fontSize: "13px",
            fontFamily: "monospace", outline: "none",
          }}
        />
        <button type="button" onClick={addTag} style={{
          padding: "7px 12px", borderRadius: "6px", background: "rgba(20,184,166,0.15)",
          border: "1px solid rgba(20,184,166,0.3)", color: "#14b8a6", cursor: "pointer", fontSize: "13px",
        }}>Add</button>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
        {value.map(t => (
          <span key={t} style={{
            background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.25)",
            color: "#2dd4bf", borderRadius: "4px", padding: "2px 8px", fontSize: "12px",
            fontFamily: "monospace", display: "flex", alignItems: "center", gap: "5px",
          }}>
            {t}
            <button type="button" onClick={() => onChange(value.filter(x => x !== t))}
              style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer",
                padding: "0", lineHeight: 1, display: "flex" }}>
              <X size={11} />
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}

// ─── Rule form modal ──────────────────────────────────────────────────────────

type FormState = {
  team_name: string; description: string; service_patterns: string[];
  error_patterns: string[]; source_containers: string[]; match_all: boolean;
  slack_webhook: string; email_recipients: string[]; priority: number;
  stop_on_match: boolean; is_active: boolean;
};

const EMPTY_FORM: FormState = {
  team_name: "", description: "", service_patterns: [], error_patterns: [],
  source_containers: [], match_all: false, slack_webhook: "",
  email_recipients: [], priority: 100, stop_on_match: false, is_active: true,
};

function RuleModal({
  rule, onClose, onSave,
}: {
  rule: RoutingRule | null;
  onClose: () => void;
  onSave: (data: CreateRoutingRuleData) => Promise<void>;
}) {
  const [form, setForm] = useState<FormState>(rule ? {
    team_name: rule.team_name, description: rule.description ?? "",
    service_patterns: rule.service_patterns, error_patterns: rule.error_patterns,
    source_containers: rule.source_containers, match_all: rule.match_all,
    slack_webhook: rule.slack_webhook ?? "", email_recipients: rule.email_recipients,
    priority: rule.priority, stop_on_match: rule.stop_on_match, is_active: rule.is_active,
  } : EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function set<K extends keyof FormState>(k: K, v: FormState[K]) {
    setForm(f => ({ ...f, [k]: v }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.team_name.trim()) return;
    if (!form.service_patterns.length && !form.error_patterns.length && !form.source_containers.length) {
      setErr("Add at least one service pattern, error pattern, or container name.");
      return;
    }
    setSaving(true); setErr(null);
    try {
      await onSave({
        team_name: form.team_name.trim(),
        description: form.description || undefined,
        service_patterns: form.service_patterns,
        error_patterns: form.error_patterns,
        source_containers: form.source_containers,
        match_all: form.match_all,
        slack_webhook: form.slack_webhook || undefined,
        email_recipients: form.email_recipients,
        priority: form.priority,
        stop_on_match: form.stop_on_match,
        is_active: form.is_active,
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to save rule");
      setSaving(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: "6px", padding: "8px 10px", color: "#f1f5f9", fontSize: "13px", outline: "none",
    boxSizing: "border-box",
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", zIndex: 100,
      display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" }}>
      <div style={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "16px", width: "640px", maxWidth: "100%", maxHeight: "90vh",
        overflowY: "auto", display: "flex", flexDirection: "column" }}>

        {/* Header */}
        <div style={{ padding: "24px 28px 0", display: "flex", justifyContent: "space-between",
          alignItems: "center", marginBottom: "4px" }}>
          <h2 style={{ color: "#f1f5f9", fontSize: "17px", fontWeight: 700, margin: 0 }}>
            {rule ? "Edit Routing Rule" : "New Routing Rule"}
          </h2>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748b",
            cursor: "pointer", padding: "4px" }}><X size={18} /></button>
        </div>
        <p style={{ color: "#64748b", fontSize: "13px", padding: "0 28px 20px", margin: 0,
          borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          Define which errors get routed to which team. Patterns support plain keywords or regex.
        </p>

        <form onSubmit={handleSubmit} style={{ padding: "20px 28px 24px" }}>
          {/* Team name */}
          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#94a3b8", fontWeight: 600,
              textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "6px" }}>Team Name *</label>
            <input value={form.team_name} onChange={e => set("team_name", e.target.value)}
              placeholder="e.g. Payments Team" required style={inputStyle} />
          </div>

          {/* Description */}
          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#94a3b8", fontWeight: 600,
              textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "6px" }}>Description</label>
            <input value={form.description} onChange={e => set("description", e.target.value)}
              placeholder="e.g. Routes all Stripe and billing errors to payments on-call"
              style={inputStyle} />
          </div>

          {/* Patterns */}
          <TagInput label="Service Patterns"
            hint='Match by service/log content keywords or regex. e.g. "payment", "stripe", "billing-worker"'
            value={form.service_patterns} onChange={v => set("service_patterns", v)} />
          <TagInput label="Error Patterns"
            hint='Match by exception class or message. e.g. "PaymentError", "StripeTimeout", "CardDeclined"'
            value={form.error_patterns} onChange={v => set("error_patterns", v)} />
          <TagInput label="Container Names"
            hint='Scope to specific Docker container names. e.g. "payment-service", "billing-worker"'
            value={form.source_containers} onChange={v => set("source_containers", v)} />

          {/* Match mode */}
          <div style={{ marginBottom: "20px", padding: "12px 14px", background: "rgba(255,255,255,0.03)",
            borderRadius: "8px", border: "1px solid rgba(255,255,255,0.07)" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "10px", cursor: "pointer" }}>
              <input type="checkbox" checked={form.match_all} onChange={e => set("match_all", e.target.checked)} />
              <div>
                <div style={{ fontSize: "13px", color: "#e2e8f0", fontWeight: 600 }}>Require all pattern types to match</div>
                <div style={{ fontSize: "12px", color: "#475569", marginTop: "2px" }}>
                  When off (default), any single pattern match triggers this rule. When on, service AND error AND container patterns must all match.
                </div>
              </div>
            </label>
          </div>

          {/* Slack webhook */}
          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#94a3b8", fontWeight: 600,
              textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "6px" }}>Slack Webhook URL</label>
            <input value={form.slack_webhook} onChange={e => set("slack_webhook", e.target.value)}
              placeholder="https://hooks.slack.com/services/..." style={inputStyle} />
          </div>

          {/* Email recipients */}
          <TagInput label="Email Recipients"
            hint="Team email addresses to notify alongside Slack."
            value={form.email_recipients} onChange={v => set("email_recipients", v)} />

          {/* Priority + stop_on_match */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px" }}>
            <div>
              <label style={{ display: "block", fontSize: "12px", color: "#94a3b8", fontWeight: 600,
                textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "6px" }}>Priority (1–999)</label>
              <input type="number" min={1} max={999} value={form.priority}
                onChange={e => set("priority", Number(e.target.value))} style={inputStyle} />
              <p style={{ fontSize: "11px", color: "#475569", marginTop: "4px" }}>
                Lower = evaluated first. Use 999 for catch-all.
              </p>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px", paddingTop: "24px" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                <input type="checkbox" checked={form.stop_on_match} onChange={e => set("stop_on_match", e.target.checked)} />
                <span style={{ fontSize: "13px", color: "#e2e8f0" }}>Stop on match</span>
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                <input type="checkbox" checked={form.is_active} onChange={e => set("is_active", e.target.checked)} />
                <span style={{ fontSize: "13px", color: "#e2e8f0" }}>Active</span>
              </label>
            </div>
          </div>

          {err && (
            <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
              borderRadius: "8px", padding: "10px 14px", color: "#f87171", fontSize: "13px", marginBottom: "16px" }}>
              {err}
            </div>
          )}

          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
            <button type="button" onClick={onClose} style={{
              padding: "9px 20px", borderRadius: "8px", background: "transparent",
              border: "1px solid rgba(255,255,255,0.1)", color: "#94a3b8", cursor: "pointer", fontSize: "14px",
            }}>Cancel</button>
            <button type="submit" disabled={saving} style={{
              padding: "9px 22px", borderRadius: "8px", background: "#14b8a6",
              border: "none", color: "#fff", cursor: saving ? "not-allowed" : "pointer",
              fontSize: "14px", fontWeight: 600, opacity: saving ? 0.7 : 1,
            }}>{saving ? "Saving…" : rule ? "Save Changes" : "Create Rule"}</button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Test panel ───────────────────────────────────────────────────────────────

function TestPanel({ getToken }: { getToken: () => Promise<string | null> }) {
  const [errorLine, setErrorLine] = useState("");
  const [container, setContainer] = useState("");
  const [result, setResult] = useState<TestRoutingResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleTest(e: React.FormEvent) {
    e.preventDefault();
    if (!errorLine.trim()) return;
    setLoading(true); setErr(null); setResult(null);
    try {
      const tok = await getToken();
      if (!tok) { setErr("Not authenticated"); setLoading(false); return; }
      const r = await routingRulesApi.test(tok, errorLine, container || undefined);
      setResult(r);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Test failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.08)",
      borderRadius: "12px", padding: "20px 24px", marginBottom: "28px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "14px" }}>
        <FlaskConical size={16} color="#a78bfa" />
        <h3 style={{ margin: 0, fontSize: "14px", fontWeight: 700, color: "#e2e8f0" }}>Test Your Rules</h3>
        <span style={{ fontSize: "12px", color: "#475569" }}>
          — paste an error line to see which teams would be notified
        </span>
      </div>

      <form onSubmit={handleTest} style={{ display: "flex", gap: "10px", marginBottom: result || err ? "16px" : 0 }}>
        <input
          value={errorLine}
          onChange={e => setErrorLine(e.target.value)}
          placeholder='e.g. PaymentError: Stripe API timeout after 30s — retries exhausted'
          style={{
            flex: 1, background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: "8px", padding: "9px 12px", color: "#f1f5f9", fontSize: "13px",
            fontFamily: "monospace", outline: "none",
          }}
        />
        <input
          value={container}
          onChange={e => setContainer(e.target.value)}
          placeholder="container (optional)"
          style={{
            width: "180px", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: "8px", padding: "9px 12px", color: "#f1f5f9", fontSize: "13px",
            fontFamily: "monospace", outline: "none",
          }}
        />
        <button type="submit" disabled={loading || !errorLine.trim()} style={{
          padding: "9px 18px", borderRadius: "8px", background: "rgba(167,139,250,0.2)",
          border: "1px solid rgba(167,139,250,0.4)", color: "#a78bfa",
          cursor: loading || !errorLine.trim() ? "not-allowed" : "pointer",
          fontSize: "13px", fontWeight: 600, whiteSpace: "nowrap",
        }}>{loading ? "Testing…" : "Run Test"}</button>
      </form>

      {err && (
        <div style={{ color: "#f87171", fontSize: "13px", padding: "8px 12px",
          background: "rgba(239,68,68,0.08)", borderRadius: "6px", border: "1px solid rgba(239,68,68,0.2)" }}>
          {err}
        </div>
      )}

      {result && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
          <div style={{ background: result.matched_rules.length ? "rgba(20,184,166,0.06)" : "rgba(255,255,255,0.03)",
            border: `1px solid ${result.matched_rules.length ? "rgba(20,184,166,0.2)" : "rgba(255,255,255,0.07)"}`,
            borderRadius: "8px", padding: "14px 16px" }}>
            <div style={{ fontSize: "12px", fontWeight: 700, color: result.matched_rules.length ? "#14b8a6" : "#475569",
              textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: "8px" }}>
              {result.matched_rules.length} Rule{result.matched_rules.length !== 1 ? "s" : ""} Matched
            </div>
            {result.matched_rules.length === 0
              ? <p style={{ color: "#475569", fontSize: "13px", margin: 0 }}>No active rules matched this error.</p>
              : result.matched_rules.map((r, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: "8px",
                  marginBottom: "6px", fontSize: "13px", color: "#e2e8f0" }}>
                  <CheckCircle size={13} color="#14b8a6" />
                  <span style={{ fontWeight: 600 }}>{r.team_name}</span>
                  <span style={{ color: "#475569" }}>· priority {r.priority}</span>
                </div>
              ))
            }
          </div>
          <div style={{ background: result.would_notify.length ? "rgba(34,197,94,0.06)" : "rgba(255,255,255,0.03)",
            border: `1px solid ${result.would_notify.length ? "rgba(34,197,94,0.2)" : "rgba(255,255,255,0.07)"}`,
            borderRadius: "8px", padding: "14px 16px" }}>
            <div style={{ fontSize: "12px", fontWeight: 700, color: result.would_notify.length ? "#22c55e" : "#475569",
              textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: "8px" }}>
              Would Notify
            </div>
            {result.would_notify.length === 0
              ? <p style={{ color: "#475569", fontSize: "13px", margin: 0 }}>No notifications would be sent.</p>
              : result.would_notify.map((n, i) => (
                <div key={i} style={{ fontSize: "12px", color: "#86efac", fontFamily: "monospace",
                  marginBottom: "4px", wordBreak: "break-all" }}>
                  {n.startsWith("https://hooks.slack.com")
                    ? `Slack: ${n.slice(0, 45)}…`
                    : n}
                </div>
              ))
            }
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Rule card ────────────────────────────────────────────────────────────────

function RuleCard({
  rule, onEdit, onDelete, onToggle, onMoveUp, onMoveDown,
}: {
  rule: RoutingRule;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <div style={{
      background: rule.is_active ? "#1e293b" : "rgba(255,255,255,0.02)",
      border: rule.is_active ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(255,255,255,0.04)",
      borderRadius: "10px", marginBottom: "8px",
      opacity: rule.is_active ? 1 : 0.6,
    }}>
      {/* Main row */}
      <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "14px 16px" }}>

        {/* Priority reorder */}
        <div style={{ display: "flex", flexDirection: "column", gap: "2px", flexShrink: 0 }}>
          <button onClick={onMoveUp} style={{ background: "none", border: "none", color: "#475569",
            cursor: "pointer", padding: "1px", display: "flex" }}>
            <ChevronUp size={13} />
          </button>
          <button onClick={onMoveDown} style={{ background: "none", border: "none", color: "#475569",
            cursor: "pointer", padding: "1px", display: "flex" }}>
            <ChevronDown size={13} />
          </button>
        </div>

        <PriorityBadge priority={rule.priority} />

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
            <span style={{ fontWeight: 700, color: "#f1f5f9", fontSize: "14px" }}>{rule.team_name}</span>
            {rule.stop_on_match && (
              <span style={{ fontSize: "10px", background: "rgba(251,146,60,0.15)",
                border: "1px solid rgba(251,146,60,0.3)", color: "#fb923c",
                borderRadius: "4px", padding: "1px 6px" }}>stop-on-match</span>
            )}
            {!rule.is_active && (
              <span style={{ fontSize: "10px", background: "rgba(100,116,139,0.15)",
                border: "1px solid rgba(100,116,139,0.3)", color: "#64748b",
                borderRadius: "4px", padding: "1px 6px" }}>disabled</span>
            )}
          </div>
          {rule.description && (
            <p style={{ margin: 0, fontSize: "12px", color: "#64748b" }}>{rule.description}</p>
          )}
          {/* Pattern preview */}
          <div style={{ display: "flex", gap: "6px", marginTop: "6px", flexWrap: "wrap" }}>
            {rule.service_patterns.slice(0, 4).map(p => (
              <span key={p} style={{ fontSize: "11px", background: "rgba(20,184,166,0.1)",
                color: "#2dd4bf", borderRadius: "4px", padding: "1px 6px", fontFamily: "monospace" }}>{p}</span>
            ))}
            {rule.error_patterns.slice(0, 3).map(p => (
              <span key={p} style={{ fontSize: "11px", background: "rgba(167,139,250,0.1)",
                color: "#a78bfa", borderRadius: "4px", padding: "1px 6px", fontFamily: "monospace" }}>{p}</span>
            ))}
            {(rule.service_patterns.length + rule.error_patterns.length) > 7 && (
              <span style={{ fontSize: "11px", color: "#475569" }}>+{(rule.service_patterns.length + rule.error_patterns.length) - 7} more</span>
            )}
          </div>
        </div>

        {/* Slack indicator */}
        {rule.slack_webhook && (
          <div style={{ fontSize: "12px", color: "#4ade80", display: "flex", alignItems: "center", gap: "4px",
            flexShrink: 0 }}>
            <div style={{ width: "6px", height: "6px", borderRadius: "50%", background: "#4ade80" }} />
            Slack
          </div>
        )}

        {/* Actions */}
        <div style={{ display: "flex", gap: "4px", flexShrink: 0 }}>
          <button onClick={() => setExpanded(e => !e)} title="Details"
            style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: "6px", color: "#94a3b8", cursor: "pointer", padding: "5px 7px", display: "flex" }}>
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
          <button onClick={onToggle} title={rule.is_active ? "Disable" : "Enable"}
            style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: "6px", color: rule.is_active ? "#14b8a6" : "#475569",
              cursor: "pointer", padding: "5px 7px", display: "flex" }}>
            {rule.is_active ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
          </button>
          <button onClick={onEdit} title="Edit"
            style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: "6px", color: "#94a3b8", cursor: "pointer", padding: "5px 7px", display: "flex" }}>
            <Pencil size={13} />
          </button>
          {confirmDelete ? (
            <div style={{ display: "flex", gap: "4px" }}>
              <button onClick={onDelete} style={{ background: "rgba(239,68,68,0.15)",
                border: "1px solid rgba(239,68,68,0.3)", borderRadius: "6px", color: "#f87171",
                cursor: "pointer", padding: "5px 10px", fontSize: "11px", fontWeight: 700 }}>Confirm</button>
              <button onClick={() => setConfirmDelete(false)} style={{ background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.08)", borderRadius: "6px", color: "#64748b",
                cursor: "pointer", padding: "5px 7px", display: "flex" }}><X size={13} /></button>
            </div>
          ) : (
            <button onClick={() => setConfirmDelete(true)} title="Delete"
              style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: "6px", color: "#64748b", cursor: "pointer", padding: "5px 7px", display: "flex" }}>
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div style={{ padding: "0 16px 16px", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
          <div style={{ paddingTop: "14px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
            <div>
              <p style={{ fontSize: "11px", color: "#475569", fontWeight: 700, textTransform: "uppercase",
                letterSpacing: "0.07em", marginBottom: "6px" }}>Service Patterns</p>
              <TagList items={rule.service_patterns} color="#14b8a6" />
            </div>
            <div>
              <p style={{ fontSize: "11px", color: "#475569", fontWeight: 700, textTransform: "uppercase",
                letterSpacing: "0.07em", marginBottom: "6px" }}>Error Patterns</p>
              <TagList items={rule.error_patterns} color="#a78bfa" />
            </div>
            {rule.source_containers.length > 0 && (
              <div>
                <p style={{ fontSize: "11px", color: "#475569", fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.07em", marginBottom: "6px" }}>Container Names</p>
                <TagList items={rule.source_containers} color="#fb923c" />
              </div>
            )}
            {rule.email_recipients.length > 0 && (
              <div>
                <p style={{ fontSize: "11px", color: "#475569", fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.07em", marginBottom: "6px" }}>Email Recipients</p>
                <TagList items={rule.email_recipients} color="#38bdf8" />
              </div>
            )}
            {rule.slack_webhook && (
              <div style={{ gridColumn: "1 / -1" }}>
                <p style={{ fontSize: "11px", color: "#475569", fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.07em", marginBottom: "6px" }}>Slack Webhook</p>
                <span style={{ fontSize: "12px", color: "#4ade80", fontFamily: "monospace" }}>
                  {rule.slack_webhook.replace(/\/[^/]{6,}$/, "/•••••••••")}
                </span>
              </div>
            )}
          </div>
          <div style={{ marginTop: "12px", display: "flex", gap: "16px", fontSize: "12px", color: "#475569" }}>
            <span>Match mode: <strong style={{ color: "#94a3b8" }}>{rule.match_all ? "All patterns required" : "Any pattern"}</strong></span>
            <span>Stop on match: <strong style={{ color: "#94a3b8" }}>{rule.stop_on_match ? "Yes" : "No"}</strong></span>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function RoutingRulesPage() {
  const { getToken } = useAuth();
  const [token, setToken] = useState<string | null>(null);
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editingRule, setEditingRule] = useState<RoutingRule | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const tok = await getToken();
      if (!tok) return;
      setToken(tok); // keep token state fresh for TestPanel gate check
      setRules(await routingRulesApi.list(tok));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load rules");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave(data: CreateRoutingRuleData) {
    const tok = await getToken();
    if (!tok) return;
    if (editingRule) {
      await routingRulesApi.update(tok, editingRule.id, data);
    } else {
      await routingRulesApi.create(tok, data);
    }
    setShowModal(false);
    setEditingRule(null);
    load();
  }

  async function handleDelete(id: string) {
    const tok = await getToken();
    if (!tok) return;
    await routingRulesApi.delete(tok, id);
    load();
  }

  async function handleToggle(rule: RoutingRule) {
    const tok = await getToken();
    if (!tok) return;
    await routingRulesApi.update(tok, rule.id, { is_active: !rule.is_active });
    load();
  }

  async function handleMoveUp(idx: number) {
    if (idx === 0) return;
    const tok = await getToken();
    if (!tok) return;
    const a = rules[idx], b = rules[idx - 1];
    // Swap priorities
    const pa = a.priority, pb = b.priority;
    if (pa === pb) {
      await routingRulesApi.update(tok, a.id, { priority: pb - 1 });
    } else {
      await routingRulesApi.update(tok, a.id, { priority: pb });
      await routingRulesApi.update(tok, b.id, { priority: pa });
    }
    load();
  }

  async function handleMoveDown(idx: number) {
    if (idx >= rules.length - 1) return;
    const tok = await getToken();
    if (!tok) return;
    const a = rules[idx], b = rules[idx + 1];
    const pa = a.priority, pb = b.priority;
    if (pa === pb) {
      await routingRulesApi.update(tok, b.id, { priority: pa + 1 });
    } else {
      await routingRulesApi.update(tok, a.id, { priority: pb });
      await routingRulesApi.update(tok, b.id, { priority: pa });
    }
    load();
  }

  const activeCount = rules.filter(r => r.is_active).length;

  return (
    <div style={{ padding: "32px 40px", maxWidth: "900px" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "28px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
            <Route size={20} color="#14b8a6" />
            <h1 style={{ margin: 0, fontSize: "22px", fontWeight: 700, color: "#f1f5f9" }}>Alert Routing Rules</h1>
          </div>
          <p style={{ margin: 0, fontSize: "14px", color: "#64748b", lineHeight: 1.5 }}>
            Define which errors go to which team. Rules are evaluated in priority order (lowest first). <br />
            <span style={{ color: "#2dd4bf" }}>{activeCount} active rule{activeCount !== 1 ? "s" : ""}</span>
            {rules.length > activeCount && <span style={{ color: "#475569" }}> · {rules.length - activeCount} disabled</span>}
          </p>
        </div>
        <button onClick={() => { setEditingRule(null); setShowModal(true); }} style={{
          display: "flex", alignItems: "center", gap: "8px",
          padding: "9px 18px", borderRadius: "8px", background: "#14b8a6",
          border: "none", color: "#fff", cursor: "pointer", fontSize: "14px", fontWeight: 600,
        }}>
          <Plus size={15} /> New Rule
        </button>
      </div>

      {/* Test panel */}
      {token && <TestPanel getToken={getToken} />}

      {/* Empty state */}
      {!loading && rules.length === 0 && (
        <div style={{ textAlign: "center", padding: "60px 20px", color: "#475569" }}>
          <Route size={40} color="#334155" style={{ marginBottom: "12px" }} />
          <p style={{ fontSize: "15px", fontWeight: 600, color: "#64748b", margin: "0 0 8px" }}>No routing rules yet</p>
          <p style={{ fontSize: "13px", margin: "0 0 20px" }}>
            Create your first rule to start routing log alerts to the right teams.
          </p>
          <button onClick={() => { setEditingRule(null); setShowModal(true); }} style={{
            padding: "9px 20px", borderRadius: "8px", background: "rgba(20,184,166,0.15)",
            border: "1px solid rgba(20,184,166,0.3)", color: "#14b8a6", cursor: "pointer", fontSize: "14px",
          }}>
            <Plus size={14} style={{ verticalAlign: "middle", marginRight: "6px" }} />
            Create first rule
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: "8px", padding: "14px 18px", color: "#f87171", fontSize: "13px",
          display: "flex", alignItems: "center", gap: "8px" }}>
          <AlertCircle size={15} /> {error}
        </div>
      )}

      {/* Rules list */}
      {loading
        ? Array.from({ length: 3 }).map((_, i) => (
            <div key={i} style={{ background: "#1e293b", borderRadius: "10px", height: "72px",
              marginBottom: "8px", animation: "pulse 1.5s ease-in-out infinite",
              border: "1px solid rgba(255,255,255,0.06)" }} />
          ))
        : rules.map((rule, idx) => (
            <RuleCard
              key={rule.id}
              rule={rule}
              onEdit={() => { setEditingRule(rule); setShowModal(true); }}
              onDelete={() => handleDelete(rule.id)}
              onToggle={() => handleToggle(rule)}
              onMoveUp={() => handleMoveUp(idx)}
              onMoveDown={() => handleMoveDown(idx)}
            />
          ))
      }

      {/* Priority legend */}
      {rules.length > 0 && (
        <div style={{ marginTop: "20px", padding: "14px 16px", background: "rgba(255,255,255,0.02)",
          border: "1px solid rgba(255,255,255,0.05)", borderRadius: "8px",
          display: "flex", gap: "20px", flexWrap: "wrap", fontSize: "12px", color: "#475569" }}>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ color: "#ef4444", fontWeight: 700 }}>P1–20</span> Critical (evaluated first)
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ color: "#f97316", fontWeight: 700 }}>P21–50</span> High
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ color: "#14b8a6", fontWeight: 700 }}>P51–899</span> Standard
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ color: "#64748b", fontWeight: 700 }}>P900–999</span> Catch-all (evaluated last)
          </span>
          <span style={{ marginLeft: "auto" }}>
            <Zap size={12} color="#14b8a6" style={{ verticalAlign: "middle", marginRight: "4px" }} />
            teal = service · purple = error
          </span>
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <RuleModal
          rule={editingRule}
          onClose={() => { setShowModal(false); setEditingRule(null); }}
          onSave={handleSave}
        />
      )}
    </div>
  );
}
