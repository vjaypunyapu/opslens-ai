"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { Plus, Pencil, Trash2, Zap, History, Bell } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { AlertRuleForm } from "@/components/alerts/AlertRuleForm";
import { cn } from "@/lib/utils";
import { AlertRule, AlertHistoryEntry } from "@/types";
import { alertsApi } from "@/lib/api";

type Tab = "rules" | "history";

export default function AlertsPage() {
  const { getToken } = useAuth();
  const [tab, setTab]             = useState<Tab>("rules");
  const [rules, setRules]         = useState<AlertRule[]>([]);
  const [history, setHistory]     = useState<AlertHistoryEntry[]>([]);
  const [showForm, setShowForm]   = useState(false);
  const [editing, setEditing]     = useState<AlertRule | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading]     = useState(true);

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const token = await getToken();
      setRules(await alertsApi.listRules(token!));
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load rules');
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  const loadHistory = useCallback(async () => {
    try {
      const token = await getToken();
      setHistory(await alertsApi.listHistory(token!));
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load history');
    }
  }, [getToken]);

  useEffect(() => { loadRules(); }, [loadRules]);
  useEffect(() => { if (tab === "history") loadHistory(); }, [tab, loadHistory]);

  const handleSave = async (data: Omit<AlertRule, "id" | "created_at" | "updated_at" | "last_triggered_at">) => {
    setSubmitting(true);
    try {
      const token = await getToken();
      if (editing) {
        await alertsApi.updateRule(editing.id, data, token!);
        toast.success("Rule updated");
      } else {
        await alertsApi.createRule(token!, data);
        toast.success("Rule created");
      }
      setShowForm(false);
      setEditing(null);
      loadRules();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to save rule');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this alert rule?")) return;
    try {
      const token = await getToken();
      await alertsApi.deleteRule(id, token!);
      toast.success("Rule deleted");
      loadRules();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete rule');
    }
  };

  const handleTest = async (id: string) => {
    try {
      const token = await getToken();
      const result = await alertsApi.testRule(id, token!);
      toast[result.fired ? "success" : "info"](result.message);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Test failed');
    }
  };

  const toggleActive = async (rule: AlertRule) => {
    try {
      const token = await getToken();
      await alertsApi.updateRule(rule.id, { is_active: !rule.is_active }, token!);
      loadRules();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to update rule');
    }
  };

  return (
    <AppShell>
      <div style={{ padding: "28px 32px", maxWidth: 1100, margin: "0 auto" }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 28 }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9", margin: 0 }}>Alerts</h1>
            <p style={{ fontSize: 13, color: "#64748b", margin: "4px 0 0" }}>
              Get notified when patterns match your rules
            </p>
          </div>
          {tab === "rules" && (
            <button
              onClick={() => { setEditing(null); setShowForm(true); }}
              style={{
                display: "flex", alignItems: "center", gap: 8,
                background: "rgba(20,184,166,0.15)", border: "1px solid rgba(20,184,166,0.3)",
                borderRadius: 8, padding: "9px 18px", color: "#2dd4bf",
                fontSize: 13, fontWeight: 500, cursor: "pointer",
              }}
            >
              <Plus size={15} />
              New rule
            </button>
          )}
        </div>

        {/* Tabs */}
        <div style={{
          display: "flex", background: "#1e293b", borderRadius: 8,
          border: "1px solid rgba(255,255,255,0.07)", overflow: "hidden",
          width: "fit-content", marginBottom: 24,
        }}>
          {(["rules", "history"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                padding: "8px 24px", fontSize: 13, fontWeight: 500,
                border: "none", cursor: "pointer", textTransform: "capitalize",
                background: tab === t ? "rgba(20,184,166,0.15)" : "transparent",
                color: tab === t ? "#2dd4bf" : "#64748b",
                borderRight: "1px solid rgba(255,255,255,0.06)",
              }}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Inline form */}
        {showForm && (
          <div style={{
            background: "#1e293b", border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 12, padding: "20px 24px", marginBottom: 20,
          }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, color: "#e2e8f0", margin: "0 0 16px" }}>
              {editing ? "Edit rule" : "New alert rule"}
            </h3>
            <AlertRuleForm
              initial={editing ?? undefined}
              onSubmit={handleSave}
              onCancel={() => { setShowForm(false); setEditing(null); }}
              submitting={submitting}
            />
          </div>
        )}

        {/* Rules list */}
        {tab === "rules" && (
          loading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {[0, 1, 2].map((i) => (
                <div key={i} style={{ height: 80, borderRadius: 12, background: "#1e293b", opacity: 0.5 }} />
              ))}
            </div>
          ) : rules.length === 0 ? (
            <div style={{ textAlign: "center", padding: "64px 0" }}>
              <Bell size={40} color="#334155" style={{ display: "block", margin: "0 auto 16px" }} />
              <p style={{ fontSize: 14, color: "#475569", margin: 0 }}>No alert rules yet.</p>
              <p style={{ fontSize: 12, color: "#334155", marginTop: 6 }}>
                Create your first rule to start getting notified.
              </p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {rules.map((rule) => (
                <div
                  key={rule.id}
                  style={{
                    background: "#1e293b", border: "1px solid rgba(255,255,255,0.07)",
                    borderRadius: 12, padding: "16px 20px",
                    display: "flex", alignItems: "flex-start", gap: 16,
                  }}
                >
                  {/* Active toggle */}
                  <button
                    onClick={() => toggleActive(rule)}
                    title={rule.is_active ? "Disable" : "Enable"}
                    style={{
                      width: 22, height: 22, borderRadius: "50%", flexShrink: 0, marginTop: 2,
                      border: rule.is_active ? "2px solid #14b8a6" : "2px solid #334155",
                      background: rule.is_active ? "#14b8a6" : "transparent",
                      cursor: "pointer",
                    }}
                  />
                  {/* Info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ fontSize: 14, fontWeight: 600, color: "#e2e8f0", margin: 0 }}>{rule.name}</p>
                    {rule.description && (
                      <p style={{ fontSize: 12, color: "#64748b", margin: "3px 0 0" }}>{rule.description}</p>
                    )}
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                      {rule.conditions.map((c, i) => (
                        <span key={i} style={{
                          fontSize: 11, background: "rgba(255,255,255,0.05)",
                          border: "1px solid rgba(255,255,255,0.08)",
                          borderRadius: 20, padding: "3px 10px", color: "#94a3b8",
                        }}>
                          {c.field} {c.operator} {String(c.value)}
                        </span>
                      ))}
                    </div>
                    {rule.last_triggered_at && (
                      <p style={{ fontSize: 11, color: "#475569", margin: "6px 0 0" }}>
                        Last fired {formatDistanceToNow(new Date(rule.last_triggered_at), { addSuffix: true })}
                      </p>
                    )}
                  </div>
                  {/* Actions */}
                  <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
                    <button onClick={() => handleTest(rule.id)} title="Test fire" style={{
                      padding: 8, borderRadius: 8, border: "none",
                      background: "transparent", color: "#475569", cursor: "pointer",
                    }}>
                      <Zap size={15} />
                    </button>
                    <button onClick={() => { setEditing(rule); setShowForm(true); }} title="Edit" style={{
                      padding: 8, borderRadius: 8, border: "none",
                      background: "transparent", color: "#475569", cursor: "pointer",
                    }}>
                      <Pencil size={15} />
                    </button>
                    <button onClick={() => handleDelete(rule.id)} title="Delete" style={{
                      padding: 8, borderRadius: 8, border: "none",
                      background: "transparent", color: "#475569", cursor: "pointer",
                    }}>
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )
        )}

        {/* History */}
        {tab === "history" && (
          history.length === 0 ? (
            <div style={{ textAlign: "center", padding: "64px 0" }}>
              <History size={40} color="#334155" style={{ display: "block", margin: "0 auto 16px" }} />
              <p style={{ fontSize: 14, color: "#475569", margin: 0 }}>No alerts have fired yet.</p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {history.map((h) => (
                <div key={h.id} style={{
                  background: "#1e293b", border: "1px solid rgba(255,255,255,0.07)",
                  borderRadius: 12, padding: "14px 20px",
                  display: "flex", alignItems: "center", gap: 16,
                }}>
                  <History size={16} color="#475569" style={{ flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ fontSize: 13, fontWeight: 500, color: "#e2e8f0", margin: 0 }}>{h.rule_name}</p>
                    <p style={{ fontSize: 11, color: "#64748b", margin: "3px 0 0" }}>
                      Notified: {h.channels_notified.join(", ")}
                    </p>
                  </div>
                  <span style={{ fontSize: 11, color: "#475569", flexShrink: 0 }}>
                    {formatDistanceToNow(new Date(h.triggered_at), { addSuffix: true })}
                  </span>
                </div>
              ))}
            </div>
          )
        )}
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </AppShell>
  );
}
