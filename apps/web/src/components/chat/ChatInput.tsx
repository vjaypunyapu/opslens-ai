"use client";
import { KeyboardEvent, useRef, useState } from "react";
import { Send, Square, Filter } from "lucide-react";
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
  const [value, setValue]                   = useState("");
  const [showFilters, setShowFilters]       = useState(false);
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
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
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

  const filterActive = showFilters || selectedSources.length > 0;

  return (
    <div style={{
      borderTop: "1px solid rgba(255,255,255,0.07)",
      background: "#0f172a",
      padding: "0.75rem 1rem",
      display: "flex", flexDirection: "column", gap: "0.5rem",
    }}>
      {/* Source filter chips */}
      {showFilters && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {ALL_SOURCES.map((src) => {
            const active = selectedSources.includes(src);
            return (
              <button
                key={src}
                onClick={() => toggleSource(src)}
                style={{
                  fontSize: "0.75rem", padding: "0.25rem 0.625rem", borderRadius: "9999px",
                  border: `1px solid ${active ? "#14b8a6" : "rgba(255,255,255,0.1)"}`,
                  background: active ? "rgba(20,184,166,0.15)" : "none",
                  color: active ? "#2dd4bf" : "#64748b",
                  cursor: "pointer",
                }}
              >
                {SOURCE_TYPE_LABELS[src]}
              </button>
            );
          })}
        </div>
      )}

      {/* Input row */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: "0.5rem" }}>
        {/* Filter toggle */}
        <button
          onClick={() => setShowFilters((v) => !v)}
          title="Filter by source"
          style={{
            padding: "0.5rem", borderRadius: "0.5rem", flexShrink: 0,
            border: `1px solid ${filterActive ? "#14b8a6" : "rgba(255,255,255,0.1)"}`,
            background: filterActive ? "rgba(20,184,166,0.1)" : "none",
            color: filterActive ? "#2dd4bf" : "#64748b",
            cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
            position: "relative",
          }}
        >
          <Filter size={15} />
          {selectedSources.length > 0 && (
            <span style={{
              position: "absolute", top: -5, right: -5,
              background: "#14b8a6", color: "white",
              width: 14, height: 14, borderRadius: "50%",
              fontSize: "0.625rem", display: "flex", alignItems: "center", justifyContent: "center",
              fontWeight: 700,
            }}>
              {selectedSources.length}
            </span>
          )}
        </button>

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => { setValue(e.target.value); autoResize(); }}
          onKeyDown={handleKeyDown}
          disabled={disabled || isStreaming}
          placeholder="Ask anything about your operations…"
          rows={1}
          style={{
            flex: 1, resize: "none", borderRadius: "0.75rem",
            border: "1px solid rgba(255,255,255,0.1)",
            background: "#1e293b",
            padding: "0.625rem 0.875rem",
            fontSize: "0.875rem", color: "#f1f5f9",
            outline: "none", minHeight: 42, maxHeight: 160,
            fontFamily: "inherit", lineHeight: 1.5,
            opacity: (disabled || isStreaming) ? 0.6 : 1,
          }}
        />

        {/* Send / Stop */}
        {isStreaming ? (
          <button
            onClick={onStop}
            title="Stop"
            style={{
              padding: "0.5rem", borderRadius: "0.5rem", flexShrink: 0,
              background: "#ef4444", border: "none", color: "white", cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <Square size={15} fill="white" />
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!value.trim() || !!disabled}
            title="Send (Enter)"
            style={{
              padding: "0.5rem", borderRadius: "0.5rem", flexShrink: 0,
              background: !value.trim() || disabled ? "rgba(20,184,166,0.3)" : "#14b8a6",
              border: "none", color: "white", cursor: !value.trim() ? "not-allowed" : "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <Send size={15} />
          </button>
        )}
      </div>

      <p style={{ textAlign: "center", fontSize: "0.6875rem", color: "#334155", margin: 0 }}>
        OpsLens AI can make mistakes. Verify critical information.
      </p>
    </div>
  );
}
