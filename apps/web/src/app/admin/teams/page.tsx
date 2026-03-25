"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { Users, Plus, Trash2, ChevronRight, Shield } from "lucide-react";
import { adminApi, Team } from "@/lib/api";

const S = {
  page: { padding: "32px 40px", maxWidth: "960px" } as React.CSSProperties,
  header: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "28px" } as React.CSSProperties,
  title: { fontSize: "22px", fontWeight: 700, color: "#f1f5f9", display: "flex", alignItems: "center", gap: "10px" } as React.CSSProperties,
  subtitle: { fontSize: "13px", color: "#64748b", marginTop: "4px" } as React.CSSProperties,
  btn: (variant: "primary" | "danger" | "ghost") => ({
    display: "inline-flex", alignItems: "center", gap: "6px",
    padding: variant === "ghost" ? "6px 10px" : "9px 16px",
    borderRadius: "8px", fontSize: "13px", fontWeight: 500, cursor: "pointer", border: "none",
    background: variant === "primary" ? "#14b8a6" : variant === "danger" ? "rgba(239,68,68,0.15)" : "rgba(255,255,255,0.06)",
    color: variant === "primary" ? "#fff" : variant === "danger" ? "#f87171" : "#94a3b8",
  } as React.CSSProperties),
  card: { background: "#1e293b", borderRadius: "12px", border: "1px solid rgba(255,255,255,0.07)", marginBottom: "10px" } as React.CSSProperties,
  row: { display: "flex", alignItems: "center", padding: "16px 20px", gap: "14px" } as React.CSSProperties,
  badge: { fontSize: "11px", padding: "2px 8px", borderRadius: "99px", background: "rgba(20,184,166,0.15)", color: "#2dd4bf", fontWeight: 600 } as React.CSSProperties,
  input: { width: "100%", padding: "10px 14px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.1)", background: "#0f172a", color: "#f1f5f9", fontSize: "14px" } as React.CSSProperties,
  modal: { position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 } as React.CSSProperties,
  modalBox: { background: "#1e293b", borderRadius: "14px", padding: "28px", width: "420px", border: "1px solid rgba(255,255,255,0.1)" } as React.CSSProperties,
  error: { background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: "8px", padding: "10px 14px", color: "#fca5a5", fontSize: "13px", marginBottom: "16px" } as React.CSSProperties,
};

export default function TeamsPage() {
  const { getToken } = useAuth();
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const token = await getToken();
      if (!token) return;
      const data = await adminApi.listTeams(token);
      setTeams(data);
    } catch (e: any) {
      setError(e.message ?? "Failed to load teams");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function createTeam() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const token = await getToken();
      if (!token) return;
      await adminApi.createTeam(token, { name: newName.trim(), description: newDesc.trim() || undefined });
      setShowCreate(false);
      setNewName(""); setNewDesc("");
      load();
    } catch (e: any) {
      setError(e.message ?? "Failed to create team");
    } finally {
      setCreating(false);
    }
  }

  async function deleteTeam(id: string, name: string) {
    if (!confirm(`Delete team "${name}"? This removes all member access and permissions.`)) return;
    try {
      const token = await getToken();
      if (!token) return;
      await adminApi.deleteTeam(token, id);
      load();
    } catch (e: any) {
      setError(e.message ?? "Failed to delete team");
    }
  }

  return (
    <div style={S.page}>
      <div style={S.header}>
        <div>
          <div style={S.title}><Shield size={20} color="#14b8a6" /> Team Management</div>
          <div style={S.subtitle}>Create teams and control which data sources each team can access.</div>
        </div>
        <div style={{ display: "flex", gap: "10px" }}>
          <Link href="/admin/users" style={{ ...S.btn("ghost"), textDecoration: "none" }}>
            <Users size={14} /> Users
          </Link>
          <button style={S.btn("primary")} onClick={() => setShowCreate(true)}>
            <Plus size={14} /> New Team
          </button>
        </div>
      </div>

      {error && <div style={S.error}>{error}</div>}

      {loading ? (
        <div style={{ color: "#64748b", fontSize: "14px" }}>Loading teams…</div>
      ) : teams.length === 0 ? (
        <div style={{ ...S.card, padding: "40px", textAlign: "center" }}>
          <Shield size={32} color="#334155" style={{ marginBottom: "12px" }} />
          <div style={{ color: "#94a3b8", fontSize: "15px", fontWeight: 600 }}>No teams yet</div>
          <div style={{ color: "#64748b", fontSize: "13px", marginTop: "6px" }}>
            Create a team to start controlling which sources each group can access.
          </div>
          <button style={{ ...S.btn("primary"), marginTop: "20px" }} onClick={() => setShowCreate(true)}>
            <Plus size={14} /> Create first team
          </button>
        </div>
      ) : (
        teams.map(team => (
          <div key={team.id} style={S.card}>
            <div style={S.row}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: "#f1f5f9" }}>{team.name}</div>
                {team.description && (
                  <div style={{ fontSize: "12px", color: "#64748b", marginTop: "2px" }}>{team.description}</div>
                )}
              </div>
              <span style={S.badge}>{team.member_count} member{team.member_count !== 1 ? "s" : ""}</span>
              <Link
                href={`/admin/teams/${team.id}`}
                style={{ ...S.btn("ghost"), textDecoration: "none", gap: "4px" }}
              >
                Manage <ChevronRight size={13} />
              </Link>
              <button style={S.btn("danger")} onClick={() => deleteTeam(team.id, team.name)}>
                <Trash2 size={13} />
              </button>
            </div>
          </div>
        ))
      )}

      {/* Create team modal */}
      {showCreate && (
        <div style={S.modal} onClick={e => e.target === e.currentTarget && setShowCreate(false)}>
          <div style={S.modalBox}>
            <div style={{ fontSize: "17px", fontWeight: 700, color: "#f1f5f9", marginBottom: "20px" }}>
              Create Team
            </div>
            <div style={{ marginBottom: "14px" }}>
              <label style={{ fontSize: "12px", color: "#94a3b8", fontWeight: 600, display: "block", marginBottom: "6px" }}>
                TEAM NAME *
              </label>
              <input
                style={S.input}
                placeholder="e.g. Backend Engineers"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => e.key === "Enter" && createTeam()}
                autoFocus
              />
            </div>
            <div style={{ marginBottom: "24px" }}>
              <label style={{ fontSize: "12px", color: "#94a3b8", fontWeight: 600, display: "block", marginBottom: "6px" }}>
                DESCRIPTION
              </label>
              <input
                style={S.input}
                placeholder="Optional description"
                value={newDesc}
                onChange={e => setNewDesc(e.target.value)}
              />
            </div>
            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button style={S.btn("ghost")} onClick={() => setShowCreate(false)}>Cancel</button>
              <button style={S.btn("primary")} onClick={createTeam} disabled={creating || !newName.trim()}>
                {creating ? "Creating…" : "Create Team"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
