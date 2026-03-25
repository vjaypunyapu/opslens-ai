"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, UserPlus, Trash2, Shield, Database, Check, X } from "lucide-react";
import { adminApi, TeamMember, PermissionEntry } from "@/lib/api";

const S = {
  page: { padding: "32px 40px", maxWidth: "960px" } as React.CSSProperties,
  back: { display: "inline-flex", alignItems: "center", gap: "6px", color: "#64748b", fontSize: "13px", cursor: "pointer", marginBottom: "24px", textDecoration: "none", background: "none", border: "none" } as React.CSSProperties,
  section: { marginBottom: "32px" } as React.CSSProperties,
  sectionTitle: { fontSize: "14px", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase" as const, letterSpacing: "0.08em", marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" },
  card: { background: "#1e293b", borderRadius: "12px", border: "1px solid rgba(255,255,255,0.07)" } as React.CSSProperties,
  row: { display: "flex", alignItems: "center", padding: "14px 18px", gap: "12px", borderBottom: "1px solid rgba(255,255,255,0.05)" } as React.CSSProperties,
  btn: (variant: "primary" | "danger" | "ghost") => ({
    display: "inline-flex", alignItems: "center", gap: "6px",
    padding: "8px 14px", borderRadius: "8px", fontSize: "13px", fontWeight: 500, cursor: "pointer", border: "none",
    background: variant === "primary" ? "#14b8a6" : variant === "danger" ? "rgba(239,68,68,0.15)" : "rgba(255,255,255,0.06)",
    color: variant === "primary" ? "#fff" : variant === "danger" ? "#f87171" : "#94a3b8",
  } as React.CSSProperties),
  input: { padding: "9px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.1)", background: "#0f172a", color: "#f1f5f9", fontSize: "13px", flex: 1 } as React.CSSProperties,
  toggle: (on: boolean) => ({
    width: "20px", height: "20px", borderRadius: "4px", border: "none", cursor: "pointer", flexShrink: 0,
    display: "flex", alignItems: "center", justifyContent: "center",
    background: on ? "rgba(20,184,166,0.2)" : "rgba(255,255,255,0.05)",
    color: on ? "#2dd4bf" : "#475569",
  } as React.CSSProperties),
  error: { background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: "8px", padding: "10px 14px", color: "#fca5a5", fontSize: "13px", marginBottom: "16px" } as React.CSSProperties,
  success: { background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.3)", borderRadius: "8px", padding: "10px 14px", color: "#2dd4bf", fontSize: "13px", marginBottom: "16px" } as React.CSSProperties,
};

const SOURCE_TYPES = ["github", "jira", "slack", "confluence", "gdrive", "pagerduty", "datadog"];

export default function TeamDetailPage() {
  const { getToken } = useAuth();
  const { teamId } = useParams<{ teamId: string }>();
  const router = useRouter();

  const [members, setMembers] = useState<TeamMember[]>([]);
  const [permissions, setPermissions] = useState<PermissionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Add member form
  const [addId, setAddId] = useState("");
  const [addEmail, setAddEmail] = useState("");
  const [addRole, setAddRole] = useState("member");
  const [adding, setAdding] = useState(false);

  // Add permission form
  const [newSourceType, setNewSourceType] = useState("github");
  const [newSourceId, setNewSourceId] = useState("");
  const [permSaving, setPermSaving] = useState(false);

  async function loadAll() {
    try {
      const token = await getToken();
      if (!token) return;
      const [m, p] = await Promise.all([
        adminApi.listMembers(token, teamId),
        adminApi.getPermissions(token, teamId),
      ]);
      setMembers(m);
      setPermissions(p);
    } catch (e: any) {
      setError(e.message ?? "Failed to load team data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadAll(); }, [teamId]);

  async function addMember() {
    if (!addId.trim()) return;
    setAdding(true);
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      await adminApi.addMember(token, teamId, { user_external_id: addId.trim(), user_email: addEmail.trim() || undefined, role: addRole });
      setAddId(""); setAddEmail("");
      setSuccess("Member added.");
      loadAll();
    } catch (e: any) {
      setError(e.message ?? "Failed to add member");
    } finally {
      setAdding(false);
    }
  }

  async function removeMember(uid: string) {
    if (!confirm("Remove this member from the team?")) return;
    try {
      const token = await getToken();
      if (!token) return;
      await adminApi.removeMember(token, teamId, uid);
      loadAll();
    } catch (e: any) {
      setError(e.message ?? "Failed to remove member");
    }
  }

  async function addPermission() {
    if (!newSourceId.trim()) return;
    setPermSaving(true);
    setError("");
    try {
      const token = await getToken();
      if (!token) return;
      const updated = [
        ...permissions.filter(p => !(p.source_type === newSourceType && p.source_id === newSourceId.trim())),
        { source_type: newSourceType, source_id: newSourceId.trim(), can_read: true, can_see_metrics: false, can_see_logs: false },
      ];
      await adminApi.setPermissions(token, teamId, updated);
      setNewSourceId("");
      setSuccess("Permission added.");
      loadAll();
    } catch (e: any) {
      setError(e.message ?? "Failed to add permission");
    } finally {
      setPermSaving(false);
    }
  }

  async function togglePermFlag(idx: number, flag: "can_read" | "can_see_metrics" | "can_see_logs") {
    const updated = permissions.map((p, i) => i === idx ? { ...p, [flag]: !p[flag] } : p);
    setPermissions(updated);
    try {
      const token = await getToken();
      if (!token) return;
      await adminApi.setPermissions(token, teamId, updated);
      setSuccess("Permissions saved.");
    } catch (e: any) {
      setError(e.message ?? "Failed to save permissions");
      loadAll(); // revert
    }
  }

  async function removePermission(idx: number) {
    const updated = permissions.filter((_, i) => i !== idx);
    try {
      const token = await getToken();
      if (!token) return;
      await adminApi.setPermissions(token, teamId, updated);
      setPermissions(updated);
      setSuccess("Permission removed.");
    } catch (e: any) {
      setError(e.message ?? "Failed to remove permission");
    }
  }

  return (
    <div style={S.page}>
      <button style={S.back} onClick={() => router.push("/admin/teams")}>
        <ArrowLeft size={14} /> Back to Teams
      </button>

      {error && <div style={S.error}>{error}</div>}
      {success && <div style={S.success} onClick={() => setSuccess("")}>{success}</div>}

      {/* ── Members ── */}
      <div style={S.section}>
        <div style={S.sectionTitle}><UserPlus size={14} /> Members</div>
        <div style={S.card}>
          {loading ? (
            <div style={{ padding: "20px", color: "#64748b", fontSize: "13px" }}>Loading…</div>
          ) : members.length === 0 ? (
            <div style={{ padding: "20px", color: "#64748b", fontSize: "13px" }}>No members yet.</div>
          ) : (
            members.map(m => (
              <div key={m.user_external_id} style={{ ...S.row }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "13px", fontWeight: 600, color: "#f1f5f9" }}>
                    {m.user_email ?? m.user_external_id}
                  </div>
                  <div style={{ fontSize: "11px", color: "#64748b", marginTop: "1px" }}>
                    ID: {m.user_external_id} · Role: {m.role}
                  </div>
                </div>
                <button style={S.btn("danger")} onClick={() => removeMember(m.user_external_id)}>
                  <Trash2 size={12} />
                </button>
              </div>
            ))
          )}
          {/* Add member row */}
          <div style={{ padding: "14px 18px", display: "flex", gap: "8px", flexWrap: "wrap" as const }}>
            <input style={S.input} placeholder="Clerk user ID (user_2abc…)" value={addId} onChange={e => setAddId(e.target.value)} />
            <input style={{ ...S.input, flex: "0 0 180px" }} placeholder="Email (optional)" value={addEmail} onChange={e => setAddEmail(e.target.value)} />
            <select value={addRole} onChange={e => setAddRole(e.target.value)}
              style={{ padding: "9px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.1)", background: "#0f172a", color: "#f1f5f9", fontSize: "13px" }}>
              <option value="member">Member</option>
              <option value="admin">Team Admin</option>
            </select>
            <button style={S.btn("primary")} onClick={addMember} disabled={adding || !addId.trim()}>
              <UserPlus size={13} /> {adding ? "Adding…" : "Add"}
            </button>
          </div>
        </div>
      </div>

      {/* ── Data Source Permissions ── */}
      <div style={S.section}>
        <div style={S.sectionTitle}><Database size={14} /> Data Source Access</div>
        <div style={{ fontSize: "12px", color: "#64748b", marginBottom: "10px" }}>
          Toggle what each source allows: <strong style={{ color: "#94a3b8" }}>Query</strong> (can ask questions about it), <strong style={{ color: "#94a3b8" }}>Metrics</strong> (can see cost/latency data), <strong style={{ color: "#94a3b8" }}>Logs</strong> (can see log scan alerts).
        </div>
        <div style={S.card}>
          {/* Header */}
          <div style={{ ...S.row, borderBottom: "1px solid rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.02)" }}>
            <div style={{ flex: 1, fontSize: "11px", fontWeight: 700, color: "#64748b", textTransform: "uppercase" as const }}>Source</div>
            <div style={{ width: "64px", textAlign: "center" as const, fontSize: "11px", fontWeight: 700, color: "#64748b" }}>Query</div>
            <div style={{ width: "64px", textAlign: "center" as const, fontSize: "11px", fontWeight: 700, color: "#64748b" }}>Metrics</div>
            <div style={{ width: "64px", textAlign: "center" as const, fontSize: "11px", fontWeight: 700, color: "#64748b" }}>Logs</div>
            <div style={{ width: "36px" }} />
          </div>

          {permissions.length === 0 && (
            <div style={{ padding: "16px 18px", color: "#64748b", fontSize: "13px" }}>No sources granted yet.</div>
          )}

          {permissions.map((p, idx) => (
            <div key={`${p.source_type}-${p.source_id}`} style={S.row}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "rgba(255,255,255,0.07)", color: "#94a3b8", marginRight: "8px" }}>
                  {p.source_type}
                </span>
                <span style={{ fontSize: "13px", color: "#f1f5f9" }}>{p.source_id}</span>
              </div>
              {(["can_read", "can_see_metrics", "can_see_logs"] as const).map(flag => (
                <div key={flag} style={{ width: "64px", display: "flex", justifyContent: "center" }}>
                  <button style={S.toggle(p[flag])} onClick={() => togglePermFlag(idx, flag)}>
                    {p[flag] ? <Check size={12} /> : <X size={12} />}
                  </button>
                </div>
              ))}
              <button style={{ ...S.btn("danger"), padding: "5px 8px" }} onClick={() => removePermission(idx)}>
                <Trash2 size={12} />
              </button>
            </div>
          ))}

          {/* Add permission row */}
          <div style={{ padding: "14px 18px", display: "flex", gap: "8px", alignItems: "center" }}>
            <select value={newSourceType} onChange={e => setNewSourceType(e.target.value)}
              style={{ padding: "9px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.1)", background: "#0f172a", color: "#f1f5f9", fontSize: "13px" }}>
              {SOURCE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <input style={S.input} placeholder="Repo / project / channel ID" value={newSourceId} onChange={e => setNewSourceId(e.target.value)}
              onKeyDown={e => e.key === "Enter" && addPermission()} />
            <button style={S.btn("primary")} onClick={addPermission} disabled={permSaving || !newSourceId.trim()}>
              <Shield size={13} /> {permSaving ? "Saving…" : "Grant Access"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
