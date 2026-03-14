"use client";
import { useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { ChevronDown, ChevronUp, CheckCircle2, BellOff, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  MAGNITUDE_COLORS,
  MAGNITUDE_DOT,
  INSIGHT_TYPE_LABELS,
  SOURCE_TYPE_ICONS,
  SOURCE_TYPE_LABELS,
} from "@/lib/utils";
import { Insight, InsightMagnitude, InsightType, SourceType } from "@/types";

interface Props {
  insight: Insight;
  onStatusChange: (id: string, status: "resolved" | "snoozed" | "active", snoozeHours?: number) => void;
}

export function InsightCard({ insight, onStatusChange }: Props) {
  const [expanded, setExpanded] = useState(false);

  const isResolved = insight.status === "resolved";
  const isSnoozed  = insight.status === "snoozed";

  return (
    <div
      className={cn(
        "bg-white border rounded-xl overflow-hidden transition-shadow hover:shadow-md",
        isResolved ? "opacity-60 border-gray-100" : "border-gray-200",
      )}
    >
      {/* Header */}
      <div className="p-4">
        <div className="flex items-start gap-3">
          {/* Magnitude dot */}
          <span
            className={cn(
              "mt-1.5 h-2.5 w-2.5 rounded-full shrink-0",
              MAGNITUDE_DOT[insight.magnitude as InsightMagnitude],
            )}
          />

          <div className="flex-1 min-w-0">
            {/* Type + magnitude badges */}
            <div className="flex flex-wrap items-center gap-2 mb-1.5">
              <span className="text-xs font-medium text-gray-400">
                {INSIGHT_TYPE_LABELS[insight.insight_type as InsightType]}
              </span>
              <span
                className={cn(
                  "text-xs px-2 py-0.5 rounded-full border font-medium",
                  MAGNITUDE_COLORS[insight.magnitude as InsightMagnitude],
                )}
              >
                {insight.magnitude.charAt(0).toUpperCase() + insight.magnitude.slice(1)}
              </span>
              {insight.confidence !== null && (
                <span className="text-xs text-gray-400">
                  {Math.round((insight.confidence ?? 0) * 100)}% confidence
                </span>
              )}
            </div>

            {/* Title */}
            <h3 className="text-sm font-semibold text-gray-800 leading-snug">
              {insight.title}
            </h3>

            {/* Source type pills */}
            <div className="flex flex-wrap gap-1.5 mt-2">
              {insight.source_types.map((st) => (
                <span
                  key={st}
                  className="inline-flex items-center gap-1 text-xs bg-gray-50 border border-gray-100 rounded-full px-2 py-0.5"
                >
                  {SOURCE_TYPE_ICONS[st as SourceType]}
                  {SOURCE_TYPE_LABELS[st as SourceType]}
                </span>
              ))}
            </div>
          </div>

          {/* Timestamp + expand */}
          <div className="flex flex-col items-end gap-2 shrink-0">
            <span className="text-xs text-gray-400">
              {formatDistanceToNow(new Date(insight.created_at), { addSuffix: true })}
            </span>
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-gray-400 hover:text-gray-600 transition-colors"
            >
              {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </div>

      {/* Expanded summary */}
      {expanded && (
        <div className="border-t border-gray-100 px-4 py-3 bg-gray-50">
          <p className="text-sm text-gray-600 leading-relaxed">{insight.summary}</p>
        </div>
      )}

      {/* Actions */}
      <div className="border-t border-gray-100 px-4 py-2.5 flex items-center gap-2">
        {!isResolved && (
          <button
            onClick={() => onStatusChange(insight.id, "resolved")}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-green-600 transition-colors"
          >
            <CheckCircle2 className="h-3.5 w-3.5" />
            Resolve
          </button>
        )}
        {!isSnoozed && !isResolved && (
          <button
            onClick={() => onStatusChange(insight.id, "snoozed", 24)}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-yellow-600 transition-colors"
          >
            <BellOff className="h-3.5 w-3.5" />
            Snooze 24h
          </button>
        )}
        {(isResolved || isSnoozed) && (
          <button
            onClick={() => onStatusChange(insight.id, "active")}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-brand-teal transition-colors"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reopen
          </button>
        )}
        <span className="ml-auto text-xs text-gray-300 capitalize">{insight.status}</span>
      </div>
    </div>
  );
}
