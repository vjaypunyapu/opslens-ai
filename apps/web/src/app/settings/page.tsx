"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  Settings, Users, Building2, User, Save, Trash2,
  Plus, Shield, CheckCircle, AlertTriangle, RefreshCw,
} from "lucide-react";
import { ApiError } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────
interface TenantData { id: string; name: string; slug: string; plan: string; settings: Record<string, unknown> }
interface MemberData  { id: string; email: string; name: string | null; role: string; external_id: string }

type Tab = "workspace" | "members" | "profile";

const PLAN_COLOR: Record<string, string> = {
  starter: "#14b8a6", pro: "#6366f1", enterprise: "#f59e0b",
};
const ROLE_COLOR: Record<string, string> = {
  admin: "#ef4444", member: "#3b82f6", viewer: "#94a3b8",
};

// ── API helpers ───────────────────────────────────────────────────────────────
async function apiFetch<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...options.headers },
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, b.detail ?? res.statusText);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function SettingsPage() {
  const { getToken } = useAuth();
  const [tab, setTab] = useState<Tab>("workspace");

  const [tenant, setTenant]   = useState<TenantData | null>(null);
  const [members, setMembers] = useState<MemberData[]>([]);
  const [profile, setProfile] = useState<MemberData | null>(null);

  const [tenantName, setTenantName]   = useState("");
  const [profileName, setProfileName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole]   = useState<"member" | "viewer">("member");

  const [saving, setSaving]   = useState(false);
  const [loading, setLoading] = useState(true);
  const [toast, setToast]     = useState<{ msg: string; ok: boolean } | null>(null);

  const showToast = (msg: string, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const token = await getToken();
      if (!token) return;
      const [t, m, p] = await Promise.all([
        apiFetch<TenantData>("/settings/tenant", token),
        apiFetch<MemberData[]>("/settings/members", token),
        apiFetch<MemberData>("/settings/profile", token).catch(() => null),
      ]);
      setTenant(t); setTenantName(t.name);
      setMembers(m);
      if (p) { setProfile(p); setProfileName(p.name ?? ""); }
    } catch (e: unknown) {
      showToast((e as Error).message, false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line

  const saveTenant = async () => {
    setSaving(true);
    try {
      const token = await getToken();
      if (!token) return;
      const updated = await apiFetch<TenantData>("/settings/tenant", token, {
        method: "PATCH",
        body: JSON.stringify({ name: tenantName }),
      });
      setTenant(updated);
      showToast("Workspace name updated.");
    } catch (e: unknown) { showToast((e as Error).message, false); }
    finally { setSaving(false); }
  };

  const saveProfile = async () => {
    setSaving(true);
    try {
      const token = await getToken();
      if (!token) return;
      const updated = await apiFetch<MemberData>("/settings/profile", token, {
        method: "PATCH",
        body: JSON.stringify({ name: profileName }),
      });
      setProfile(updated);
      showToast("Profile updated.");
    } catch (e: unknown) { showToast((e as Error).message, false); }
    finally { setSaving(false); }
  };

  const inviteMember = async () => {
    if (!inviteEmail.trim()) return;
    setSaving(true);
    try {
      const token = await getToken();
      if (!token) return;
      const newMember = await apiFetch<MemberData>("/settings/members", token, {
        method: "POST",
        body: JSON.stringify({ email: inviteEmail.trim(), role: inviteRole }),
      });
      setMembers(prev => [...prev.filter(m => m.id !== newMember.id), newMember]);
      setInviteEmail("");
      showToast(`${inviteEmail} added as ${inviteRole}.`);
    } catch (e: unknown) { showToast((e as Error).message, false); }
    finally { setSaving(false); }
  };

  const updateRole = async (memberId: string, role: string) => {
    try {
      const token = await getToken();
      if (!token) return;
      const updated = await apiFetch<MemberData>(`/settings/members/${memberId}`, token, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      });
      setMembers(prev => prev.map(m => m.id === memberId ? updated : m));
      showToast("Role updated.");
    } catch (e: unknown) { showToast((e as Error).message, false); }
  };

  const removeMember = async (memberId: string) => {
    if (!confirm("Remove this member from the workspace?")) return;
    try {
      const token = await getToken();
      if (!token) return;
      await apiFetch(`/settings/members/${memberId}`, token, { method: "DELETE" });
      setMembers(prev => prev.filter(m => m.id !== memberId));
      showToast("Member removed.");
    } catch (e: unknown) { showToast((e as Error).message, false); }
  };

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
      height: "100%", color: "#64748b", gap: 12 }}>
      <RefreshCw size={18} style={{ animation: "spin 1s linear infinite" }} />
      Loading settings…
    </div>
  );

  return (
    <div style={{ padding: "28px 32px", maxWidth: 900, margin: "0 auto" }}>
      {/* Header */}
      <div style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9", margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
          <Settings size={20} color="#14b8a6" /> Settings
        </h1>
        <p style={{ fontSize: 13, color: "#64748b", margin: "4px 0 0" }}>
          Manage your workspace, team members, and personal profile.
        </p>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 24, borderBottom: "1px solid rgba(255,255,255,0.07)", paddingBottom: 0 }}>
        {([
          ["workspace", Building2, "Workspace"],
          ["members",   Users,     "Team Members"],
          ["profile",   User,      "My Profile"],
        ] as [Tab, React.ElementType, string][]).map(([id, Icon, label]) => (
          <button key={id} onClick={() => setTab(id)} style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "8px 16px", fontSize: 13, fontWeight: 500, cursor: "pointer",
            background: "transparent", border: "none",
            borderBottom: tab === id ? "2px solid #14b8a6" : "2px solid transparent",
            color: tab === id ? "#2dd4bf" : "#64748b",
            marginBottom: -1,
          }}>
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {/* ── Workspace tab ── */}
      {tab === "workspace" && tenant && (
        <Card title="Workspace Settings" icon={Building2}>
          <Field label="Workspace Name">
            <input
              value={tenantName}
              onChange={e => setTenantName(e.target.value)}
              style={inputStyle}
            />
          </Field>
          <Field label="Plan">
            <span style={{
              display: "inline-block", padding: "4px 12px", borderRadius: 20,
              fontSize: 12, fontWeight: 600,
              background: `${PLAN_COLOR[tenant.plan] || "#475569"}22`,
              color: PLAN_COLOR[tenant.plan] || "#94a3b8",
              border: `1px solid ${PLAN_COLOR[tenant.plan] || "#475569"}44`,
            }}>
              {tenant.plan.charAt(0).toUpperCase() + tenant.plan.slice(1)}
            </span>
          </Field>
          <Field label="Workspace ID">
            <code style={{ fontSize: 12, color: "#64748b", fontFamily: "monospace" }}>{tenant.id}</code>
          </Field>
          <Field label="Slug">
            <code style={{ fontSize: 12, color: "#64748b", fontFamily: "monospace" }}>{tenant.slug}</code>
          </Field>
          <div style={{ marginTop: 20 }}>
            <SaveButton onClick={saveTenant} saving={saving} label="Save Changes" />
          </div>
        </Card>
      )}

      {/* ── Members tab ── */}
      {tab === "members" && (
        <>
          <Card title="Invite Team Member" icon={Plus}>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <input
                placeholder="colleague@company.com"
                value={inviteEmail}
                onChange={e => setInviteEmail(e.target.value)}
                onKeyDown={e => e.key === "Enter" && inviteMember()}
                style={{ ...inputStyle, flex: 1, minWidth: 200 }}
              />
              <select value={inviteRole} onChange={e => setInviteRole(e.target.value as "member" | "viewer")}
                style={{ ...inputStyle, width: "auto" }}>
                <option value="member">Member</option>
                <option value="viewer">Viewer</option>
              </select>
              <SaveButton onClick={inviteMember} saving={saving} label="Add Member" icon={Plus} />
            </div>
          </Card>

          <Card title={`Team Members (${members.length})`} icon={Users} style={{ marginTop: 16 }}>
            {members.length === 0 ? (
              <p style={{ color: "#64748b", fontSize: 13, textAlign: "center", padding: "16px 0" }}>
                No members yet. Invite your team above.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {members.map(m => (
                  <div key={m.id} style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 14px", background: "rgba(255,255,255,0.03)",
                    borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)",
                  }}>
                    <div style={{
                      width: 36, height: 36, borderRadius: "50%",
                      background: `${ROLE_COLOR[m.role] || "#475569"}33`,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 14, fontWeight: 700, color: ROLE_COLOR[m.role] || "#94a3b8",
                      flexShrink: 0,
                    }}>
                      {(m.name || m.email).charAt(0).toUpperCase()}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 500 }}>
                        {m.name || m.email}
                      </div>
                      {m.name && <div style={{ fontSize: 11, color: "#64748b" }}>{m.email}</div>}
                    </div>
                    <select
                      value={m.role}
                      onChange={e => updateRole(m.id, e.target.value)}
                      style={{
                        ...inputStyle, padding: "4px 10px", fontSize: 12,
                        width: "auto", color: ROLE_COLOR[m.role] || "#94a3b8",
                      }}
                    >
                      <option value="admin">Admin</option>
                      <option value="member">Member</option>
                      <option value="viewer">Viewer</option>
                    </select>
                    <button onClick={() => removeMember(m.id)} style={{
                      background: "transparent", border: "none", cursor: "pointer",
                      color: "#64748b", padding: 4, display: "flex",
                    }}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* Role descriptions */}
          <Card title="Role Permissions" icon={Shield} style={{ marginTop: 16 }}>
            {[
              { role: "admin",  desc: "Full access — manage workspace, members, integrations, and all content." },
              { role: "member", desc: "Can use chat, view and manage insights, configure alerts." },
              { role: "viewer", desc: "Read-only access to chat history, insights, and alerts." },
            ].map(({ role, desc }) => (
              <div key={role} style={{ display: "flex", gap: 12, padding: "8px 0",
                borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                <span style={{
                  display: "inline-block", width: 64, textAlign: "center",
                  padding: "3px 0", borderRadius: 20, fontSize: 11, fontWeight: 600,
                  background: `${ROLE_COLOR[role]}22`, color: ROLE_COLOR[role],
                  flexShrink: 0,
                }}>
                  {role}
                </span>
                <span style={{ fontSize: 13, color: "#94a3b8" }}>{desc}</span>
              </div>
            ))}
          </Card>
        </>
      )}

      {/* ── Profile tab ── */}
      {tab === "profile" && profile && (
        <Card title="My Profile" icon={User}>
          <Field label="Email">
            <span style={{ fontSize: 13, color: "#94a3b8" }}>{profile.email}</span>
            <span style={{ fontSize: 11, color: "#475569", marginLeft: 8 }}>(managed by Clerk)</span>
          </Field>
          <Field label="Display Name">
            <input
              value={profileName}
              onChange={e => setProfileName(e.target.value)}
              placeholder="Your name"
              style={inputStyle}
            />
          </Field>
          <Field label="Role">
            <span style={{
              display: "inline-block", padding: "3px 10px", borderRadius: 20,
              fontSize: 11, fontWeight: 600,
              background: `${ROLE_COLOR[profile.role] || "#475569"}22`,
              color: ROLE_COLOR[profile.role] || "#94a3b8",
            }}>
              {profile.role}
            </span>
          </Field>
          <div style={{ marginTop: 20 }}>
            <SaveButton onClick={saveProfile} saving={saving} label="Save Profile" />
          </div>
        </Card>
      )}

      {/* Toast */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24,
          background: toast.ok ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
          border: `1px solid ${toast.ok ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}`,
          borderRadius: 10, padding: "12px 18px",
          display: "flex", alignItems: "center", gap: 10,
          color: toast.ok ? "#22c55e" : "#ef4444", fontSize: 13, fontWeight: 500,
          boxShadow: "0 8px 32px rgba(0,0,0,0.4)", zIndex: 1000,
        }}>
          {toast.ok ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
          {toast.msg}
        </div>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────
function Card({ title, icon: Icon, children, style }: {
  title: string; icon: React.ElementType;
  children: React.ReactNode; style?: React.CSSProperties;
}) {
  return (
    <div style={{
      background: "#1e293b", borderRadius: 12, padding: 24,
      border: "1px solid rgba(255,255,255,0.07)", ...style,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20,
        paddingBottom: 14, borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
        <Icon size={15} color="#14b8a6" />
        <span style={{ fontSize: 14, fontWeight: 600, color: "#cbd5e1" }}>{title}</span>
      </div>
      {children}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16 }}>
      <label style={{ width: 140, fontSize: 13, color: "#64748b", fontWeight: 500, flexShrink: 0 }}>
        {label}
      </label>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

function SaveButton({ onClick, saving, label, icon: Icon = Save }: {
  onClick: () => void; saving: boolean; label: string; icon?: React.ElementType;
}) {
  return (
    <button onClick={onClick} disabled={saving} style={{
      display: "flex", alignItems: "center", gap: 8,
      background: saving ? "rgba(20,184,166,0.1)" : "rgba(20,184,166,0.15)",
      border: "1px solid rgba(20,184,166,0.3)", borderRadius: 8,
      padding: "8px 18px", color: "#2dd4bf", fontSize: 13, fontWeight: 500,
      cursor: saving ? "not-allowed" : "pointer",
    }}>
      {saving
        ? <RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />
        : <Icon size={14} />
      }
      {saving ? "Saving…" : label}
    </button>
  );
}

const inputStyle: React.CSSProperties = {
  background: "rgba(255,255,255,0.05)",
  border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 8, padding: "8px 12px",
  color: "#e2e8f0", fontSize: 13, width: "100%",
  outline: "none",
};
