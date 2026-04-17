"use client";
import { useState } from "react";
import { Plus, X } from "lucide-react";
import { AlertCondition, AlertChannel, AlertRule } from "@/types";

type FormData = Omit<AlertRule, "id" | "created_at" | "updated_at" | "last_triggered_at">;

interface Props {
  initial?: Partial<FormData>;
  onSubmit: (data: FormData) => void;
  onCancel: () => void;
  submitting?: boolean;
}

const BLANK_CONDITION: AlertCondition = { field: "magnitude", operator: "eq", value: "high" };
const BLANK_CHANNEL: AlertChannel    = { type: "slack", webhook_url: "" };

const FIELDS    = ["magnitude", "insight_type", "confidence", "source_type"];
const OPERATORS = [
  { value: "eq",       label: "equals"   },
  { value: "gt",       label: ">"        },
  { value: "gte",      label: ">="       },
  { value: "lt",       label: "<"        },
  { value: "lte",      label: "<="       },
  { value: "contains", label: "contains" },
];

const label: React.CSSProperties = {
  display: "block", fontSize: 11, fontWeight: 600, color: "#475569",
  textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6,
};
const input: React.CSSProperties = {
  width: "100%", background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 8, padding: "8px 12px", fontSize: 13, color: "#f1f5f9",
  outline: "none", boxSizing: "border-box",
};
const select: React.CSSProperties = {
  background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 8, padding: "7px 10px", fontSize: 13, color: "#f1f5f9",
  outline: "none", cursor: "pointer",
};
const iconBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "#475569",
  cursor: "pointer", padding: 4, display: "flex", alignItems: "center",
};
const addBtn: React.CSSProperties = {
  display: "inline-flex", alignItems: "center", gap: 5,
  background: "transparent", border: "none", color: "#2dd4bf",
  fontSize: 12, fontWeight: 500, cursor: "pointer", padding: "4px 0", marginTop: 8,
};

export function AlertRuleForm({ initial, onSubmit, onCancel, submitting }: Props) {
  const [name,       setName]       = useState(initial?.name ?? "");
  const [desc,       setDesc]       = useState(initial?.description ?? "");
  const [conditions, setConds]      = useState<AlertCondition[]>(initial?.conditions ?? [{ ...BLANK_CONDITION }]);
  const [channels,   setChannels]   = useState<AlertChannel[]>(initial?.channels ?? [{ ...BLANK_CHANNEL }]);
  const [cooldown,   setCooldown]   = useState(initial?.cooldown_minutes ?? 60);
  const [active,     setActive]     = useState(initial?.is_active ?? true);

  const updateCond = (i: number, patch: Partial<AlertCondition>) =>
    setConds(prev => prev.map((c, idx) => idx === i ? { ...c, ...patch } : c));
  const updateChan = (i: number, patch: Partial<AlertChannel>) =>
    setChannels(prev => prev.map((c, idx) => idx === i ? { ...c, ...patch } : c));

  const row: React.CSSProperties = { marginBottom: 18 };
  const flexRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8, marginBottom: 8 };
  const divider: React.CSSProperties = { borderTop: "1px solid rgba(255,255,255,0.07)", marginTop: 20, paddingTop: 16 };

  return (
    <div>
      {/* Name */}
      <div style={row}>
        <label style={label}>Rule name *</label>
        <input style={input} value={name} onChange={e => setName(e.target.value)}
          placeholder="e.g. High severity alert" />
      </div>

      {/* Description */}
      <div style={row}>
        <label style={label}>Description</label>
        <input style={input} value={desc} onChange={e => setDesc(e.target.value)}
          placeholder="Optional description" />
      </div>

      {/* Conditions */}
      <div style={row}>
        <label style={label}>Conditions (ALL must match)</label>
        {conditions.map((c, i) => (
          <div key={i} style={flexRow}>
            <select style={select} value={c.field}
              onChange={e => updateCond(i, { field: e.target.value })}>
              {FIELDS.map(f => <option key={f} value={f} style={{ background: "#1e293b" }}>{f}</option>)}
            </select>
            <select style={select} value={c.operator}
              onChange={e => updateCond(i, { operator: e.target.value as AlertCondition["operator"] })}>
              {OPERATORS.map(o => <option key={o.value} value={o.value} style={{ background: "#1e293b" }}>{o.label}</option>)}
            </select>
            <input style={{ ...input, flex: 1 }} value={String(c.value)}
              onChange={e => updateCond(i, { value: e.target.value })} placeholder="value" />
            <button style={iconBtn} disabled={conditions.length === 1}
              onClick={() => setConds(prev => prev.filter((_, idx) => idx !== i))}>
              <X size={14} />
            </button>
          </div>
        ))}
        <button style={addBtn} onClick={() => setConds(prev => [...prev, { ...BLANK_CONDITION }])}>
          <Plus size={12} /> Add condition
        </button>
      </div>

      {/* Channels */}
      <div style={row}>
        <label style={label}>Notification channels</label>
        {channels.map((ch, i) => (
          <div key={i} style={flexRow}>
            <select style={select} value={ch.type}
              onChange={e => updateChan(i, { type: e.target.value as "slack" | "email" })}>
              <option value="slack" style={{ background: "#1e293b" }}>Slack</option>
              <option value="email" style={{ background: "#1e293b" }}>Email</option>
            </select>
            {ch.type === "slack" ? (
              <input style={{ ...input, flex: 1 }} value={ch.webhook_url ?? ""}
                onChange={e => updateChan(i, { webhook_url: e.target.value })}
                placeholder="https://hooks.slack.com/..." />
            ) : (
              <input style={{ ...input, flex: 1 }} value={ch.email ?? ""}
                onChange={e => updateChan(i, { email: e.target.value })}
                placeholder="alerts@example.com" />
            )}
            <button style={iconBtn} disabled={channels.length === 1}
              onClick={() => setChannels(prev => prev.filter((_, idx) => idx !== i))}>
              <X size={14} />
            </button>
          </div>
        ))}
        <button style={addBtn} onClick={() => setChannels(prev => [...prev, { ...BLANK_CHANNEL }])}>
          <Plus size={12} /> Add channel
        </button>
      </div>

      {/* Cooldown + Active */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 24, marginBottom: 4 }}>
        <div>
          <label style={label}>Cooldown (minutes)</label>
          <input type="number" style={{ ...input, width: 100 }} value={cooldown} min={5}
            onChange={e => setCooldown(Number(e.target.value))} />
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", paddingBottom: 2 }}>
          <div
            onClick={() => setActive(v => !v)}
            style={{
              width: 36, height: 20, borderRadius: 10, cursor: "pointer",
              background: active ? "#14b8a6" : "#334155",
              position: "relative", transition: "background 0.2s",
            }}
          >
            <div style={{
              position: "absolute", top: 3, left: active ? 18 : 3,
              width: 14, height: 14, borderRadius: "50%", background: "#fff",
              transition: "left 0.2s",
            }} />
          </div>
          <span style={{ fontSize: 13, color: "#94a3b8" }}>Active</span>
        </label>
      </div>

      {/* Buttons */}
      <div style={{ ...divider, display: "flex", justifyContent: "flex-end", gap: 10 }}>
        <button onClick={onCancel} style={{
          padding: "8px 18px", borderRadius: 8, fontSize: 13, fontWeight: 500,
          background: "transparent", border: "1px solid rgba(255,255,255,0.1)",
          color: "#64748b", cursor: "pointer",
        }}>Cancel</button>
        <button onClick={() => onSubmit({ name, description: desc || null, conditions, channels, cooldown_minutes: cooldown, is_active: active })}
          disabled={!name.trim() || submitting} style={{
            padding: "8px 18px", borderRadius: 8, fontSize: 13, fontWeight: 600,
            background: name.trim() && !submitting ? "#14b8a6" : "rgba(20,184,166,0.3)",
            border: "none", color: "#0f172a", cursor: name.trim() ? "pointer" : "not-allowed",
          }}>
          {submitting ? "Saving…" : "Save rule"}
        </button>
      </div>
    </div>
  );
}
