"use client";
import { useState } from "react";
import { Plus, Trash2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { AlertCondition, AlertChannel, AlertRule } from "@/types";

type FormData = Omit<AlertRule, "id" | "created_at" | "updated_at" | "last_triggered_at">;

interface Props {
  initial?: Partial<FormData>;
  onSubmit: (data: FormData) => void;
  onCancel: () => void;
  submitting?: boolean;
}

const BLANK_CONDITION: AlertCondition = { field: "magnitude", operator: "eq", value: "critical" };
const BLANK_CHANNEL: AlertChannel = { type: "slack", webhook_url: "" };

const FIELDS = ["magnitude", "insight_type", "confidence", "source_type"];
const OPERATORS = [
  { value: "eq",       label: "equals" },
  { value: "gt",       label: ">" },
  { value: "gte",      label: ">=" },
  { value: "lt",       label: "<" },
  { value: "lte",      label: "<=" },
  { value: "contains", label: "contains" },
];

export function AlertRuleForm({ initial, onSubmit, onCancel, submitting }: Props) {
  const [name, setName]         = useState(initial?.name ?? "");
  const [desc, setDesc]         = useState(initial?.description ?? "");
  const [conditions, setConds]  = useState<AlertCondition[]>(
    initial?.conditions ?? [{ ...BLANK_CONDITION }],
  );
  const [channels, setChannels] = useState<AlertChannel[]>(
    initial?.channels ?? [{ ...BLANK_CHANNEL }],
  );
  const [cooldown, setCooldown] = useState(initial?.cooldown_minutes ?? 60);
  const [active, setActive]     = useState(initial?.is_active ?? true);

  const updateCond = (i: number, patch: Partial<AlertCondition>) =>
    setConds((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));

  const updateChan = (i: number, patch: Partial<AlertChannel>) =>
    setChannels((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));

  const handleSubmit = () => {
    onSubmit({
      name,
      description: desc || null,
      conditions,
      channels,
      cooldown_minutes: cooldown,
      is_active: active,
    });
  };

  return (
    <div className="space-y-5">
      {/* Name */}
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">Rule name *</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Critical insight alert"
          className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-teal"
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">Description</label>
        <input
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="Optional description"
          className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-teal"
        />
      </div>

      {/* Conditions */}
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-2">
          Conditions (ALL must match)
        </label>
        <div className="space-y-2">
          {conditions.map((c, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                value={c.field}
                onChange={(e) => updateCond(i, { field: e.target.value })}
                className="text-sm border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-teal"
              >
                {FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
              <select
                value={c.operator}
                onChange={(e) => updateCond(i, { operator: e.target.value as AlertCondition["operator"] })}
                className="text-sm border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-teal"
              >
                {OPERATORS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <input
                value={String(c.value)}
                onChange={(e) => updateCond(i, { value: e.target.value })}
                placeholder="value"
                className="flex-1 text-sm border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-teal"
              />
              <button
                onClick={() => setConds((prev) => prev.filter((_, idx) => idx !== i))}
                disabled={conditions.length === 1}
                className="text-gray-400 hover:text-red-500 disabled:opacity-30 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={() => setConds((prev) => [...prev, { ...BLANK_CONDITION }])}
          className="mt-2 flex items-center gap-1 text-xs text-brand-teal hover:underline"
        >
          <Plus className="h-3 w-3" /> Add condition
        </button>
      </div>

      {/* Channels */}
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-2">
          Notification channels
        </label>
        <div className="space-y-2">
          {channels.map((ch, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                value={ch.type}
                onChange={(e) => updateChan(i, { type: e.target.value as "slack" | "email" })}
                className="text-sm border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-teal"
              >
                <option value="slack">Slack</option>
                <option value="email">Email</option>
              </select>
              {ch.type === "slack" ? (
                <input
                  value={ch.webhook_url ?? ""}
                  onChange={(e) => updateChan(i, { webhook_url: e.target.value })}
                  placeholder="https://hooks.slack.com/..."
                  className="flex-1 text-sm border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-teal"
                />
              ) : (
                <input
                  value={ch.email ?? ""}
                  onChange={(e) => updateChan(i, { email: e.target.value })}
                  placeholder="alerts@example.com"
                  className="flex-1 text-sm border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-teal"
                />
              )}
              <button
                onClick={() => setChannels((prev) => prev.filter((_, idx) => idx !== i))}
                disabled={channels.length === 1}
                className="text-gray-400 hover:text-red-500 disabled:opacity-30 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={() => setChannels((prev) => [...prev, { ...BLANK_CHANNEL }])}
          className="mt-2 flex items-center gap-1 text-xs text-brand-teal hover:underline"
        >
          <Plus className="h-3 w-3" /> Add channel
        </button>
      </div>

      {/* Cooldown + Active */}
      <div className="flex items-center gap-6">
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1">
            Cooldown (minutes)
          </label>
          <input
            type="number"
            value={cooldown}
            min={5}
            onChange={(e) => setCooldown(Number(e.target.value))}
            className="w-24 text-sm border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-teal"
          />
        </div>
        <label className="flex items-center gap-2 cursor-pointer mt-4">
          <input
            type="checkbox"
            checked={active}
            onChange={(e) => setActive(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-brand-teal"
          />
          <span className="text-sm text-gray-700">Active</span>
        </label>
      </div>

      {/* Buttons */}
      <div className="flex justify-end gap-2 pt-2 border-t border-gray-100">
        <button
          onClick={onCancel}
          className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={!name.trim() || submitting}
          className="text-sm px-4 py-2 rounded-lg bg-brand-navy text-white hover:bg-brand-blue transition-colors disabled:opacity-50"
        >
          {submitting ? "Saving…" : "Save rule"}
        </button>
      </div>
    </div>
  );
}
