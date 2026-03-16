"use client";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ThumbsUp, ThumbsDown, ExternalLink, Clock, Sparkles } from "lucide-react";
import { SOURCE_TYPE_ICONS, SOURCE_TYPE_LABELS } from "@/lib/utils";
import { ChatMessage, SourceType } from "@/types";

interface Props {
  message: ChatMessage;
  onFeedback?: (feedback: "thumbs_up" | "thumbs_down") => void;
}

export function MessageBubble({ message, onFeedback }: Props) {
  const [showSources, setShowSources] = useState(false);
  const isUser = message.role === "user";

  return (
    <div style={{
      display: "flex",
      gap: "0.625rem",
      justifyContent: isUser ? "flex-end" : "flex-start",
      alignItems: "flex-start",
    }}>
      {/* AI avatar */}
      {!isUser && (
        <div style={{
          width: 30, height: 30, borderRadius: "50%", flexShrink: 0, marginTop: 2,
          background: "linear-gradient(135deg, #0f766e, #14b8a6)",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <Sparkles size={14} color="white" />
        </div>
      )}

      <div style={{ maxWidth: "75%", display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : "flex-start", gap: "0.375rem" }}>
        {/* Bubble */}
        <div style={{
          borderRadius: isUser ? "1rem 1rem 0.25rem 1rem" : "1rem 1rem 1rem 0.25rem",
          padding: "0.625rem 0.875rem",
          fontSize: "0.875rem",
          lineHeight: 1.6,
          background: isUser ? "#14b8a6" : "#1e293b",
          color: isUser ? "white" : "#e2e8f0",
          border: isUser ? "none" : "1px solid rgba(255,255,255,0.08)",
        }}>
          {isUser ? (
            <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{message.content}</p>
          ) : message.content ? (
            <div style={{ margin: 0 }} className="prose-chat">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          ) : (
            <span style={{ display: "inline-flex", gap: "0.25rem", alignItems: "center", padding: "0.25rem 0" }}>
              {[0, 150, 300].map((delay, i) => (
                <span key={i} style={{
                  width: 7, height: 7, borderRadius: "50%", background: "#64748b",
                  display: "inline-block", animation: `pulse 1.2s ${delay}ms ease-in-out infinite`,
                }} />
              ))}
            </span>
          )}
        </div>

        {/* Feedback + latency + sources row */}
        {!isUser && message.content && (
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", paddingLeft: "0.25rem", flexWrap: "wrap" }}>
            {message.sources && message.sources.length > 0 && (
              <button
                onClick={() => setShowSources((v) => !v)}
                style={{ fontSize: "0.75rem", color: "#2dd4bf", background: "none", border: "none", cursor: "pointer", padding: 0 }}
              >
                {showSources ? "Hide sources" : `${message.sources.length} source${message.sources.length > 1 ? "s" : ""}`}
              </button>
            )}

            {message.latency_ms && (
              <span style={{ display: "flex", alignItems: "center", gap: "0.25rem", fontSize: "0.75rem", color: "#475569" }}>
                <Clock size={11} />
                {(message.latency_ms / 1000).toFixed(1)}s
              </span>
            )}

            {onFeedback && (
              <div style={{ display: "flex", alignItems: "center", gap: "0.25rem", marginLeft: "auto" }}>
                <button
                  onClick={() => onFeedback("thumbs_up")}
                  title="Good response"
                  style={{
                    padding: "0.25rem", borderRadius: "0.375rem", border: "none", cursor: "pointer",
                    background: message.feedback === 1 ? "rgba(34,197,94,0.15)" : "none",
                    color: message.feedback === 1 ? "#22c55e" : "#475569",
                  }}
                >
                  <ThumbsUp size={13} />
                </button>
                <button
                  onClick={() => onFeedback("thumbs_down")}
                  title="Bad response"
                  style={{
                    padding: "0.25rem", borderRadius: "0.375rem", border: "none", cursor: "pointer",
                    background: message.feedback === -1 ? "rgba(239,68,68,0.15)" : "none",
                    color: message.feedback === -1 ? "#ef4444" : "#475569",
                  }}
                >
                  <ThumbsDown size={13} />
                </button>
              </div>
            )}
          </div>
        )}

        {/* Sources panel */}
        {showSources && message.sources && message.sources.length > 0 && (
          <div style={{
            background: "#0f172a", border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: "0.75rem", padding: "0.75rem",
            display: "flex", flexDirection: "column", gap: "0.5rem", width: "100%",
          }}>
            <p style={{ fontSize: "0.6875rem", fontWeight: 600, color: "#475569", textTransform: "uppercase", letterSpacing: "0.05em", margin: 0 }}>
              Sources
            </p>
            {message.sources.map((src, i) => (
              <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", fontSize: "0.8125rem" }}>
                <span style={{ fontSize: "1rem", lineHeight: 1, marginTop: 1 }}>
                  {SOURCE_TYPE_ICONS[src.source_type as SourceType] ?? "📎"}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p style={{ color: "#f1f5f9", fontWeight: 500, margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{src.title}</p>
                  <p style={{ color: "#64748b", margin: 0, fontSize: "0.75rem" }}>{SOURCE_TYPE_LABELS[src.source_type as SourceType]}</p>
                </div>
                {src.url && (
                  <a href={src.url} target="_blank" rel="noopener noreferrer" style={{ color: "#2dd4bf", flexShrink: 0 }}>
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* User avatar */}
      {isUser && (
        <div style={{
          width: 30, height: 30, borderRadius: "50%", flexShrink: 0, marginTop: 2,
          background: "#334155",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: "0.75rem", fontWeight: 700, color: "#94a3b8",
        }}>
          U
        </div>
      )}
    </div>
  );
}
