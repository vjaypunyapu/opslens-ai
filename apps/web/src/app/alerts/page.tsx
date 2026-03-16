"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { Plus, Pencil, Trash2, Zap, History } from "lucide-react";
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
      <div className="p-6 max-w-4xl mx-auto w-full space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Alerts</h1>
            <p className="text-sm text-gray-400 mt-0.5">
              Get notified when patterns match your rules
            </p>
          </div>
          {tab === "rules" && (
            <button
              onClick={() => { setEditing(null); setShowForm(true); }}
              className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg bg-brand-navy text-white hover:bg-brand-blue transition-colors"
            >
              <Plus className="h-4 w-4" />
              New rule
            </button>
          )}
        </div>

        {/* Tabs */}
        <div className="flex rounded-lg border border-gray-200 overflow-hidden bg-white w-fit">
          {(["rules", "history"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "px-4 py-1.5 text-sm capitalize transition-colors",
                tab === t ? "bg-brand-navy text-white font-medium" : "text-gray-600 hover:bg-gray-50",
              )}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Inline form */}
        {showForm && (
          <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-800 mb-4">
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
            <div className="space-y-3">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="h-20 bg-gray-100 rounded-xl animate-pulse" />
              ))}
            </div>
          ) : rules.length === 0 ? (
            <div className="text-center py-16 text-gray-400">
              <p className="text-sm">No alert rules yet. Create your first one.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {rules.map((rule) => (
                <div
                  key={rule.id}
                  className="bg-white border border-gray-200 rounded-xl p-4 flex items-start gap-4"
                >
                  {/* Active toggle */}
                  <button
                    onClick={() => toggleActive(rule)}
                    className={cn(
                      "mt-0.5 h-5 w-5 rounded-full border-2 shrink-0 transition-colors",
                      rule.is_active
                        ? "bg-brand-teal border-brand-teal"
                        : "bg-white border-gray-300",
                    )}
                    title={rule.is_active ? "Disable" : "Enable"}
                  />

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-gray-800">{rule.name}</p>
                    {rule.description && (
                      <p className="text-xs text-gray-400 mt-0.5">{rule.description}</p>
                    )}
                    <div className="flex flex-wrap gap-2 mt-2">
                      {rule.conditions.map((c, i) => (
                        <span
                          key={i}
                          className="text-xs bg-gray-50 border border-gray-100 rounded-full px-2 py-0.5 text-gray-600"
                        >
                          {c.field} {c.operator} {String(c.value)}
                        </span>
                      ))}
                    </div>
                    {rule.last_triggered_at && (
                      <p className="text-xs text-gray-400 mt-1.5">
                        Last fired{" "}
                        {formatDistanceToNow(new Date(rule.last_triggered_at), { addSuffix: true })}
                      </p>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => handleTest(rule.id)}
                      title="Test fire"
                      className="p-1.5 text-gray-400 hover:text-brand-teal rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <Zap className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => { setEditing(rule); setShowForm(true); }}
                      title="Edit"
                      className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(rule.id)}
                      title="Delete"
                      className="p-1.5 text-gray-400 hover:text-red-500 rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <Trash2 className="h-4 w-4" />
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
            <div className="text-center py-16 text-gray-400">
              <p className="text-sm">No alerts have fired yet.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {history.map((h) => (
                <div
                  key={h.id}
                  className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4"
                >
                  <History className="h-4 w-4 text-gray-400 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800">{h.rule_name}</p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      Notified: {h.channels_notified.join(", ")}
                    </p>
                  </div>
                  <span className="text-xs text-gray-400 shrink-0">
                    {formatDistanceToNow(new Date(h.triggered_at), { addSuffix: true })}
                  </span>
                </div>
              ))}
            </div>
          )
        )}
      </div>
    </AppShell>
  );
}
