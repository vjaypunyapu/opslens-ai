"use client";

/**
 * OpsLens AI — Founder / Platform Admin: Client Workspace Manager
 *
 * Lets the founder (anyone in PLATFORM_ADMIN_EMAILS) see all client
 * workspaces, create new ones, and send first-admin invite links without
 * needing to be inside any particular workspace.
 *
 * Route: /admin/clients
 */

import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  Building2, Users, Plug, Lightbulb, AlertTriangle,
  Plus, Mail, RefreshCw, ChevronRight, CheckCircle2,
  Clock, Copy, Check, X, Loader2, ExternalLink, Activity,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";

// ─── Types ────────────────────────────────────────────────────────────────────

interface TenantSummary {
  id: string;
  name: string;
  slug: string;
  plan: string;
  created_at: string;
  user_count: number;
  integration_count: number;
  insight_count: number;
  incident_count: number;
  last_activity_at: string | null;
}

interface PlatformInviteOut {
  invite_id: string;
  email: string;
  invite_url: string;
  expires_at: string;
  email_sent: boolean;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const PLAN_COLORS: Record<string, string> = {
  starter:    "#64748b",
  growth:     "#6366f1",
  enterprise: "#f59e0b",
};

function planBadge(plan: string) {
  const color = PLAN_COLORS[plan] ?? "#64748b";
  return (
    <span style={{
      fontSize: "0.7rem", padding: "0.125rem 0.5rem",
      borderRadius: "9999px", fontWeight: 600, letterSpacing: "0.02em",
      background: `${color}22`, color,
    }}>
      {plan.toUpperCase()}
    </span>
  );
}

function StatPill({ icon: Icon, value, label, color = "#64748b" }: {
  icon: React.ElementType; value: number; label: string; color?: string;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: "0.375rem",
      color, fontSize: "0.8125rem",
    }}>
      <Icon size={13} />
      <span style={{ fontWeight: 600, color: "#f1f5f9" }}>{value}</span>
      <span style={{ color: "#64748b" }}>{label}</span>
    </div>
  );
}

// ─── Invite Modal ─────────────────────────────────────────────────────────────

function InviteModal({
  tenant,
  token,
  onClose,
}: {
  tenant: TenantSummary;
  token: string;
  onClose: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "member" | "viewer">("admin");
  const [expiry, setExpiry] = useState(72);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<PlatformInviteOut | null>(null);
  const [copied, setCopied] = useState(false);

  const handleSend = async () => {
    if (!email.trim()) { toast.error("Email is required"); return; }
    setSending(true);
    try {
      const res = await fetch(`/api/v1/platform/tenants/${tenant.id}/invite`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ email: email.trim(), role, expires_in_hours: expiry }),
      });
      if (!res.ok) {
        const err = await res.json();
        toast.error(err.detail || "Failed to send invite");
        return;
      }
      const data: PlatformInviteOut = await res.json();
      setResult(data);
      toast.success(data.email_sent ? "Invite sent!" : "Invite created (email not sent — check RESEND config)");
    } catch {
      toast.error("Network error — please try again");
    } finally {
      setSending(false);
    }
  };

  const handleCopy = () => {
    if (!result) return;
    navigator.clipboard.writeText(result.invite_url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 50,
      background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem",
    }}>
      <div style={{
        background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "1rem", padding: "1.75rem", width: "100%", maxWidth: "480px",
        boxShadow: "0 25px 60px rgba(0,0,0,0.6)",
      }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "1.25rem" }}>
          <div>
            <h2 style={{ color: "#f1f5f9", fontSize: "1rem", fontWeight: 700, margin: 0 }}>
              Invite to {tenant.name}
            </h2>
            <p style={{ color: "#64748b", fontSize: "0.75rem", margin: "0.25rem 0 0" }}>
              Generate a first-admin invite link for this workspace
            </p>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer" }}>
            <X size={18} />
          </button>
        </div>

        {result ? (
          /* ── Success state ── */
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div style={{
              display: "flex", alignItems: "center", gap: "0.625rem",
              padding: "0.75rem 1rem", borderRadius: "0.625rem",
              background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.25)",
            }}>
              {result.email_sent
                ? <CheckCircle2 size={16} color="#22c55e" />
                : <Clock size={16} color="#eab308" />}
              <p style={{ color: "#f1f5f9", fontSize: "0.875rem", margin: 0 }}>
                {result.email_sent
                  ? `Invite emailed to ${result.email}`
                  : `Email not sent — copy the link below`}
              </p>
            </div>

            <div>
              <p style={{ color: "#94a3b8", fontSize: "0.75rem", marginBottom: "0.375rem" }}>
                Invite link (share this with the client if email failed)
              </p>
              <div style={{
                display: "flex", alignItems: "center", gap: "0.5rem",
                background: "#0f172a", borderRadius: "0.5rem",
                border: "1px solid rgba(255,255,255,0.08)", padding: "0.625rem 0.75rem",
              }}>
                <code style={{ flex: 1, color: "#14b8a6", fontSize: "0.75rem",
                  wordBreak: "break-all", fontFamily: "monospace" }}>
                  {result.invite_url}
                </code>
                <button
                  onClick={handleCopy}
                  style={{ background: "none", border: "none", cursor: "pointer",
                    color: copied ? "#22c55e" : "#64748b", flexShrink: 0 }}
                >
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                </button>
              </div>
            </div>

            <p style={{ color: "#475569", fontSize: "0.75rem", margin: 0 }}>
              Expires {formatDistanceToNow(new Date(result.expires_at), { addSuffix: true })}
            </p>

            <button
              onClick={onClose}
              style={{
                padding: "0.625rem", borderRadius: "0.5rem",
                background: "#14b8a6", border: "none",
                color: "#fff", fontWeight: 600, fontSize: "0.875rem", cursor: "pointer",
              }}
            >
              Done
            </button>
          </div>
        ) : (
          /* ── Form state ── */
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div>
              <label style={{ color: "#94a3b8", fontSize: "0.8125rem", display: "block", marginBottom: "0.375rem" }}>
                Client email *
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="cto@clientcompany.com"
                autoFocus
                style={{
                  width: "100%", boxSizing: "border-box",
                  background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: "0.5rem", padding: "0.625rem 0.75rem",
                  color: "#f1f5f9", fontSize: "0.875rem", outline: "none",
                }}
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
              <div>
                <label style={{ color: "#94a3b8", fontSize: "0.8125rem", display: "block", marginBottom: "0.375rem" }}>
                  Role
                </label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as typeof role)}
                  style={{
                    width: "100%", background: "#0f172a",
                    border: "1px solid rgba(255,255,255,0.1)", borderRadius: "0.5rem",
                    padding: "0.625rem 0.75rem", color: "#f1f5f9", fontSize: "0.875rem",
                    outline: "none",
                  }}
                >
                  <option value="admin">Admin</option>
                  <option value="member">Member</option>
                  <option value="viewer">Viewer</option>
                </select>
              </div>
              <div>
                <label style={{ color: "#94a3b8", fontSize: "0.8125rem", display: "block", marginBottom: "0.375rem" }}>
                  Expires in (hours)
                </label>
                <input
                  type="number"
                  min={1}
                  max={720}
                  value={expiry}
                  onChange={(e) => setExpiry(+e.target.value)}
                  style={{
                    width: "100%", boxSizing: "border-box",
                    background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: "0.5rem", padding: "0.625rem 0.75rem",
                    color: "#f1f5f9", fontSize: "0.875rem", outline: "none",
                  }}
                />
              </div>
            </div>

            <div style={{ display: "flex", gap: "0.75rem", marginTop: "0.25rem" }}>
              <button
                onClick={onClose}
                style={{
                  flex: 1, padding: "0.625rem", borderRadius: "0.5rem",
                  background: "none", border: "1px solid rgba(255,255,255,0.1)",
                  color: "#94a3b8", fontSize: "0.875rem", cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleSend}
                disabled={sending}
                style={{
                  flex: 2, display: "flex", alignItems: "center", justifyContent: "center", gap: "0.5rem",
                  padding: "0.625rem", borderRadius: "0.5rem",
                  background: sending ? "#0f766e" : "#14b8a6",
                  border: "none", color: "#fff", fontWeight: 600,
                  fontSize: "0.875rem", cursor: sending ? "not-allowed" : "pointer",
                }}
              >
                {sending && <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />}
                {sending ? "Sending…" : <><Mail size={14} /> Send invite</>}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── New Workspace Modal ──────────────────────────────────────────────────────

function NewWorkspaceModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: (tenant: TenantSummary) => void;
}) {
  const [name, setName] = useState("");
  const [plan, setPlan] = useState("starter");
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!name.trim()) { toast.error("Company name is required"); return; }
    setCreating(true);
    try {
      const res = await fetch("/api/v1/platform/tenants", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name: name.trim(), plan }),
      });
      if (!res.ok) {
        const err = await res.json();
        toast.error(err.detail || "Failed to create workspace");
        return;
      }
      const data = await res.json();
      toast.success(`Workspace "${name}" created!`);
      onCreated({
        id: data.tenant_id,
        name: data.name,
        slug: data.slug,
        plan: data.plan,
        created_at: data.created_at,
        user_count: 0,
        integration_count: 0,
        insight_count: 0,
        incident_count: 0,
        last_activity_at: null,
      });
    } catch {
      toast.error("Network error — please try again");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 50,
      background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem",
    }}>
      <div style={{
        background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "1rem", padding: "1.75rem", width: "100%", maxWidth: "440px",
        boxShadow: "0 25px 60px rgba(0,0,0,0.6)",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "1.25rem" }}>
          <div>
            <h2 style={{ color: "#f1f5f9", fontSize: "1rem", fontWeight: 700, margin: 0 }}>
              New client workspace
            </h2>
            <p style={{ color: "#64748b", fontSize: "0.75rem", margin: "0.25rem 0 0" }}>
              Create the workspace, then invite the client admin
            </p>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer" }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label style={{ color: "#94a3b8", fontSize: "0.8125rem", display: "block", marginBottom: "0.375rem" }}>
              Client company name *
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Acme Corp"
              autoFocus
              style={{
                width: "100%", boxSizing: "border-box",
                background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "0.5rem", padding: "0.625rem 0.75rem",
                color: "#f1f5f9", fontSize: "0.875rem", outline: "none",
              }}
            />
          </div>

          <div>
            <label style={{ color: "#94a3b8", fontSize: "0.8125rem", display: "block", marginBottom: "0.375rem" }}>
              Plan
            </label>
            <select
              value={plan}
              onChange={(e) => setPlan(e.target.value)}
              style={{
                width: "100%", background: "#0f172a",
                border: "1px solid rgba(255,255,255,0.1)", borderRadius: "0.5rem",
                padding: "0.625rem 0.75rem", color: "#f1f5f9", fontSize: "0.875rem", outline: "none",
              }}
            >
              <option value="starter">Starter</option>
              <option value="growth">Growth</option>
              <option value="enterprise">Enterprise</option>
            </select>
          </div>

          <div style={{ display: "flex", gap: "0.75rem", marginTop: "0.25rem" }}>
            <button
              onClick={onClose}
              style={{
                flex: 1, padding: "0.625rem", borderRadius: "0.5rem",
                background: "none", border: "1px solid rgba(255,255,255,0.1)",
                color: "#94a3b8", fontSize: "0.875rem", cursor: "pointer",
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating}
              style={{
                flex: 2, display: "flex", alignItems: "center", justifyContent: "center", gap: "0.5rem",
                padding: "0.625rem", borderRadius: "0.5rem",
                background: creating ? "#0f766e" : "#14b8a6",
                border: "none", color: "#fff", fontWeight: 600,
                fontSize: "0.875rem", cursor: creating ? "not-allowed" : "pointer",
              }}
            >
              {creating && <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />}
              {creating ? "Creating…" : <><Plus size={14} /> Create workspace</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ClientsPage() {
  const { getToken } = useAuth();
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);
  const [token, setToken] = useState<string>("");

  const [inviteTenant, setInviteTenant] = useState<TenantSummary | null>(null);
  const [showNewWorkspace, setShowNewWorkspace] = useState(false);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const t = await getToken();
      if (!t) return;
      setToken(t);
      const res = await fetch("/api/v1/platform/tenants", {
        headers: { Authorization: `Bearer ${t}` },
      });
      if (res.status === 403 || res.status === 503) {
        setAccessDenied(true);
        return;
      }
      if (!res.ok) throw new Error(await res.text());
      setTenants(await res.json());
    } catch (e) {
      toast.error((e as Error).message || "Failed to load workspaces");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => { load(); }, [load]);

  const filtered = tenants.filter((t) =>
    t.name.toLowerCase().includes(search.toLowerCase()) ||
    t.slug.toLowerCase().includes(search.toLowerCase())
  );

  // ── Access denied ──────────────────────────────────────────────────────────
  if (!loading && accessDenied) {
    return (
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", height: "60vh", gap: "1rem", textAlign: "center",
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: 12,
          background: "rgba(239,68,68,0.12)", display: "flex",
          alignItems: "center", justifyContent: "center",
        }}>
          <AlertTriangle size={24} color="#ef4444" />
        </div>
        <div>
          <p style={{ color: "#f1f5f9", fontWeight: 600, fontSize: "1rem", margin: 0 }}>
            Platform admin access required
          </p>
          <p style={{ color: "#64748b", fontSize: "0.875rem", marginTop: "0.5rem" }}>
            Your email must be in the PLATFORM_ADMIN_EMAILS environment variable.<br />
            Contact the platform owner to be granted access.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      {inviteTenant && (
        <InviteModal
          tenant={inviteTenant}
          token={token}
          onClose={() => setInviteTenant(null)}
        />
      )}
      {showNewWorkspace && (
        <NewWorkspaceModal
          token={token}
          onClose={() => setShowNewWorkspace(false)}
          onCreated={(t) => {
            setTenants((prev) => [t, ...prev]);
            setShowNewWorkspace(false);
            // Open the invite modal immediately for the new workspace
            setInviteTenant(t);
          }}
        />
      )}

      <div style={{ padding: "1.5rem", maxWidth: "64rem", margin: "0 auto", width: "100%" }}>
        {/* ── Header ── */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.5rem" }}>
          <div>
            <h1 style={{ color: "#f1f5f9", fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>
              Client Workspaces
            </h1>
            <p style={{ color: "#64748b", fontSize: "0.875rem", margin: "0.25rem 0 0" }}>
              {tenants.length} workspace{tenants.length !== 1 ? "s" : ""} across all clients
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.75rem" }}>
            <button
              onClick={load}
              disabled={loading}
              style={{
                display: "flex", alignItems: "center", gap: "0.375rem",
                fontSize: "0.875rem", padding: "0.5rem 0.75rem", borderRadius: "0.5rem",
                border: "1px solid rgba(255,255,255,0.1)", background: "none",
                color: "#94a3b8", cursor: "pointer",
              }}
            >
              <RefreshCw size={14} style={loading ? { animation: "spin 1s linear infinite" } : {}} />
              Refresh
            </button>
            <button
              onClick={() => setShowNewWorkspace(true)}
              style={{
                display: "flex", alignItems: "center", gap: "0.5rem",
                fontSize: "0.875rem", padding: "0.5rem 1rem", borderRadius: "0.5rem",
                background: "#14b8a6", border: "none",
                color: "#fff", fontWeight: 600, cursor: "pointer",
              }}
            >
              <Plus size={14} />
              Onboard new client
            </button>
          </div>
        </div>

        {/* ── Search ── */}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or slug…"
          style={{
            width: "100%", boxSizing: "border-box",
            background: "#1e293b", border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: "0.625rem", padding: "0.625rem 1rem",
            color: "#f1f5f9", fontSize: "0.875rem", outline: "none",
            marginBottom: "1.25rem",
          }}
        />

        {/* ── Loading ── */}
        {loading && (
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", color: "#64748b", padding: "2rem 0" }}>
            <Loader2 size={18} style={{ animation: "spin 1s linear infinite" }} />
            Loading workspaces…
          </div>
        )}

        {/* ── Empty ── */}
        {!loading && filtered.length === 0 && (
          <div style={{
            textAlign: "center", padding: "3rem 1.5rem",
            background: "#1e293b", borderRadius: "0.75rem",
            border: "1px dashed rgba(255,255,255,0.08)",
          }}>
            <Building2 size={32} color="#334155" style={{ margin: "0 auto 1rem" }} />
            <p style={{ color: "#64748b", fontSize: "0.875rem", margin: 0 }}>
              {search ? "No workspaces match your search" : "No client workspaces yet — click \"Onboard new client\" to get started"}
            </p>
          </div>
        )}

        {/* ── Workspace list ── */}
        <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          {filtered.map((tenant) => (
            <div
              key={tenant.id}
              style={{
                background: "#1e293b", borderRadius: "0.875rem",
                border: "1px solid rgba(255,255,255,0.07)",
                padding: "1.25rem 1.5rem",
                display: "flex", alignItems: "center", gap: "1.25rem",
              }}
            >
              {/* Icon */}
              <div style={{
                width: 44, height: 44, borderRadius: 10, flexShrink: 0,
                background: "rgba(20,184,166,0.1)", display: "flex",
                alignItems: "center", justifyContent: "center",
              }}>
                <Building2 size={20} color="#14b8a6" />
              </div>

              {/* Info */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.625rem", flexWrap: "wrap" }}>
                  <h3 style={{ color: "#f1f5f9", fontWeight: 600, fontSize: "0.9375rem", margin: 0 }}>
                    {tenant.name}
                  </h3>
                  {planBadge(tenant.plan)}
                  <span style={{ color: "#475569", fontSize: "0.75rem" }}>
                    /{tenant.slug}
                  </span>
                </div>
                <div style={{ display: "flex", gap: "1rem", marginTop: "0.5rem", flexWrap: "wrap" }}>
                  <StatPill icon={Users}     value={tenant.user_count}        label="users"        color="#6366f1" />
                  <StatPill icon={Plug}      value={tenant.integration_count} label="integrations" color="#14b8a6" />
                  <StatPill icon={Lightbulb} value={tenant.insight_count}     label="insights"     color="#f59e0b" />
                  <StatPill icon={Activity}  value={tenant.incident_count}    label="incidents"    color="#ef4444" />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.375rem" }}>
                  <Clock size={11} color="#475569" />
                  <span style={{ color: "#475569", fontSize: "0.75rem" }}>
                    Created {formatDistanceToNow(new Date(tenant.created_at), { addSuffix: true })}
                    {tenant.last_activity_at && (
                      <> · Last active {formatDistanceToNow(new Date(tenant.last_activity_at), { addSuffix: true })}</>
                    )}
                    {!tenant.last_activity_at && tenant.integration_count === 0 && (
                      <> · <span style={{ color: "#eab308" }}>No integrations yet</span></>
                    )}
                  </span>
                </div>
              </div>

              {/* Actions */}
              <div style={{ display: "flex", gap: "0.5rem", flexShrink: 0 }}>
                <button
                  onClick={() => setInviteTenant(tenant)}
                  style={{
                    display: "flex", alignItems: "center", gap: "0.375rem",
                    padding: "0.5rem 0.875rem", borderRadius: "0.5rem",
                    background: "rgba(20,184,166,0.1)", border: "1px solid rgba(20,184,166,0.25)",
                    color: "#14b8a6", fontSize: "0.8125rem", fontWeight: 500, cursor: "pointer",
                  }}
                >
                  <Mail size={13} />
                  Invite admin
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* ── Footer tip ── */}
        {!loading && tenants.length > 0 && (
          <p style={{ color: "#334155", fontSize: "0.75rem", textAlign: "center", marginTop: "1.5rem" }}>
            Access is restricted to emails listed in{" "}
            <code style={{ color: "#475569" }}>PLATFORM_ADMIN_EMAILS</code>.
            Each invite link is valid for 72 hours by default.
          </p>
        )}
      </div>
    </>
  );
}
