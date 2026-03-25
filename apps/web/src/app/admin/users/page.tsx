"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { Users, Shield, ArrowLeft, ChevronDown } from "lucide-react";
import { adminApi, OrgUser } from "@/lib/api";

const ROLES = ["admin", "member", "viewer"] as const;
type Role = typeof ROLES[number];

const ROLE_COLOR: Record<Role, string> = {
  admin:   "#f59e0b",
  member:  "#2dd4bf",
  viewer:  "#64748b",
};

const S = {
  page: { padding: "32px 40px", maxWidth: "960px" } as React.CSSProperties,
  header: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "28px" } as React.CSSProperties,
  title: { fontSize: "22px", fontWeight: 700, color: "#f1f5f9", display: "flex", alignItems: "center", gap: "10px" } as React.CSSProperties,
  subtitle: { fontSize: "13px", color: "#64748b", marginTop: "4px" } as React.CSSProperties,
  btn: (variant: "ghost") => ({
    display: "inline-flex", alignItems: "center", gap: "6px",
    padding: "8px 14px", borderRadius: "8px", fontSize: "13px", fontWeight: 500, cursor: "pointer", border: "none",
    background: "rgba(255,255,255,0.06)", color: "#94a3b8", textDecoration: "none",
  } as React.CSSProperties),
  card: { background: "#1e293b", borderRadius: "12px", border: "1px solid rgba(255,255,255,0.07)" } as React.CSSProperties,
  row: { display: "flex", alignItems: "center", padding: "14px 20px", gap: "14px", borderBottom: "1px solid rgba(255,255,255,0.05)" } as React.CSSProperties,
  badge: (role: Role) => ({
    fontSize: "11px", padding: "2px 8px", borderRadius: "99px", fontWeight: 700,
    background: `${ROLE_COLOR[role]}20`, color: ROLE_COLOR[role],
  } as React.CSSProperties),
  error: { background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: "8px", padding: "10px 14px", color: "#fca5a5", fontSize: "13px", marginBottom: "16px" } as React.CSSProperties,
  success: { background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.3)", borderRadius: "8px", padding: "10px 14px", color: "#2dd4bf", fontSize: "13px", marginBottom: "16px" } as React.CSSProperties,
  select: { padding: "5px 8px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.1)", background: "#0f172a", color: "#f1f5f9", fontSize: "12px", cursor: "pointer" } as React.CSSProperties,
};

export default function UsersPage() {
  const { getToken } = useAuth();
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  async function load() {
    try {
      const token = await getToken();
      if (!token) return;
      setUsers(await adminApi.listUsers(token));
    } catch (e: any) {
      setError(e.message ?? "Failed to load users");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function changeRole(externalId: string, role: Role) {
    setUpdatingId(externalId);
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      const updated = await adminApi.updateUserRole(token, externalId, role);
      setUsers(prev => prev.map(u => u.external_id === externalId ? { ...u, role: updated.role } : u));
      setSuccess(`Role updated to ${role}.`);
    } catch (e: any) {
      setError(e.message ?? "Failed to update role");
    } finally {
      setUpdatingId(null);
    }
  }

  return (
    <div style={S.page}>
      <div style={S.header}>
        <div>
          <div style={S.title}><Users size={20} color="#14b8a6" /> User Directory</div>
          <div style={S.subtitle}>View all users in your organisation and manage their roles.</div>
        </div>
        <Link href="/admin/teams" style={S.btn("ghost")}>
          <ArrowLeft size={14} /> Teams
        </Link>
      </div>

      {error && <div style={S.error}>{error}</div>}
      {success && <div style={S.success} onClick={() => setSuccess("")}>{success}</div>}

      <div style={S.card}>
        {/* Header row */}
        <div style={{ ...S.row, background: "rgba(255,255,255,0.02)", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
          <div style={{ flex: 1, fontSize: "11px", fontWeight: 700, color: "#64748b", textTransform: "uppercase" as const }}>User</div>
          <div style={{ width: "120px", fontSize: "11px", fontWeight: 700, color: "#64748b", textTransform: "uppercase" as const }}>Joined</div>
          <div style={{ width: "130px", fontSize: "11px", fontWeight: 700, color: "#64748b", textTransform: "uppercase" as const }}>Role</div>
        </div>

        {loading ? (
          <div style={{ padding: "20px", color: "#64748b", fontSize: "13px" }}>Loading users…</div>
        ) : users.length === 0 ? (
          <div style={{ padding: "20px", color: "#64748b", fontSize: "13px" }}>No users found.</div>
        ) : (
          users.map(u => (
            <div key={u.external_id} style={{ ...S.row, ...(u.role === "admin" ? { background: "rgba(245,158,11,0.04)" } : {}) }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "14px", fontWeight: 600, color: "#f1f5f9" }}>
                  {u.name ?? u.email}
                </div>
                <div style={{ fontSize: "11px", color: "#64748b", marginTop: "1px" }}>{u.email}</div>
              </div>
              <div style={{ width: "120px", fontSize: "12px", color: "#64748b" }}>
                {new Date(u.created_at).toLocaleDateString()}
              </div>
              <div style={{ width: "130px", display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={S.badge(u.role as Role)}>{u.role}</span>
                <select
                  style={S.select}
                  value={u.role}
                  disabled={updatingId === u.external_id}
                  onChange={e => changeRole(u.external_id, e.target.value as Role)}
                >
                  {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            </div>
          ))
        )}
      </div>

      <div style={{ marginTop: "20px", padding: "14px 18px", background: "#1e293b", borderRadius: "10px", border: "1px solid rgba(255,255,255,0.07)" }}>
        <div style={{ fontSize: "12px", fontWeight: 700, color: "#64748b", marginBottom: "8px" }}>ROLE REFERENCE</div>
        <div style={{ display: "flex", gap: "20px", fontSize: "12px", color: "#64748b" }}>
          <div><span style={{ color: ROLE_COLOR.admin, fontWeight: 700 }}>admin</span> — full access, manages teams & users</div>
          <div><span style={{ color: ROLE_COLOR.member, fontWeight: 700 }}>member</span> — access scoped to their teams</div>
          <div><span style={{ color: ROLE_COLOR.viewer, fontWeight: 700 }}>viewer</span> — read-only, minimal access</div>
        </div>
      </div>
    </div>
  );
}
