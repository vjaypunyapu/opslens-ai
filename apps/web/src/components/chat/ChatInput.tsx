"use client";
import { KeyboardEvent, useRef, useState } from "react";
import { Send, Square, Filter } from "lucide-react";
import { cn } from "@/lib/utils";
import { SOURCE_TYPE_LABELS } from "@/lib/utils";
import { SourceType } from "@/types";

const ALL_SOURCES: SourceType[] = ["slack", "jira", "google_drive", "zendesk", "github", "hubspot"];

interface Props {
  onSend: (question: string, sourceTypes?: string[]) => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled?: boolean;
}

export function ChatInput({ onSend, onStop, isStreaming, disabled }: Props) {
  const [value, setValue] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [selectedSources, setSelectedSources] = useState<SourceType[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    const q = value.trim();
    if (!q || isStreaming) return;
    onSend(q, selectedSources.length ? selectedSources : undefined);
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const toggleSource = (src: SourceType) =>
    setSelectedSources((prev) =>
      prev.includes(src) ? prev.filter((s) => s !== src) : [...prev, src],
    );

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  return (
    <div className="border-t border-gray-200 bg-white px-4 py-3 space-y-2">
      {/* Source filter chips */}
      {showFilters && (
        <div className="flex flex-wrap gap-2">
          {ALL_SOURCES.map((src) => (
            <button
              key={src}
              onClick={() => toggleSource(src)}
              className={cn(
                "text-xs px-2.5 py-1 rounded-full border transition-colors",
                selectedSources.includes(src)
                  ? "bg-brand-navy text-white border-brand-navy"
                  : "border-gray-200 text-gray-600 hover:border-brand-teal",
              )}
            >
              {SOURCE_TYPE_LABELS[src]}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* Filter toggle */}
        <button
          onClick={() => setShowFilters((v) => !v)}
          className={cn(
            "p-2 rounded-lg border transition-colors shrink-0",
            showFilters || selectedSources.length > 0
              ? "border-brand-teal text-brand-teal bg-brand-ice"
              : "border-gray-200 text-gray-400 hover:border-gray-300",
          )}
          title="Filter by source"
        >
          <Filter className="h-4 w-4" />
          {selectedSources.length > 0 && (
            <span className="sr-only">{selectedSources.length} active</span>
          )}
        </button>

        {/* Text area */}
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => { setValue(e.target.value); autoResize(); }}
            onKeyDown={handleKeyDown}
            disabled={disabled || isStreaming}
            placeholder="Ask anything about your operations…"
            rows={1}
            className="w-full resize-none rounded-xl border border-gray-200 bg-gray-50 px-4 py-2.5 pr-12 text-sm text-gray-800 placeholder:text-gray-400 focus:border-brand-teal focus:bg-white focus:outline-none transition-colors disabled:opacity-50"
            style={{ minHeight: "42px", maxHeight: "160px" }}
          />
        </div>

        {/* Send / Stop */}
        {isStreaming ? (
          <button
            onClick={onStop}
            className="p-2 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors shrink-0"
            title="Stop generating"
          >
            <Square className="h-4 w-4 fill-current" />
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!value.trim() || disabled}
            className="p-2 rounded-lg bg-brand-navy text-white hover:bg-brand-blue transition-colors shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
            title="Send (Enter)"
          >
            <Send className="h-4 w-4" />
          </button>
        )}
      </div>

      <p className="text-center text-xs text-gray-300">
        OpsLens AI can make mistakes. Verify critical information.
      </p>
    </div>
  );
}
