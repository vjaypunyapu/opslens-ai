"use client";
import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { ChatInput } from "./ChatInput";
import { useChat } from "@/hooks/useChat";

const SUGGESTED = [
  "What are the top customer complaints this week?",
  "Which Jira tickets have been blocked the longest?",
  "Are there any patterns between recent releases and support tickets?",
  "Which GitHub repos have the most open issues?",
];

interface Props { sessionId: string; }

export function ChatWindow({ sessionId }: Props) {
  const { messages, isStreaming, error, loadMessages, sendMessage, stopStreaming, sendFeedback } =
    useChat(sessionId);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { loadMessages(); }, [loadMessages]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const isEmpty = messages.length === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#0f172a" }}>
      {/* Messages area */}
      <div style={{ flex: 1, overflowY: "auto", padding: "1.5rem 1rem" }}>
        {isEmpty ? (
          /* Empty state */
          <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "1.5rem", padding: "0 1rem" }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "0.75rem", textAlign: "center" }}>
              <div style={{
                width: 52, height: 52, borderRadius: "1rem",
                background: "linear-gradient(135deg, #0f766e, #14b8a6)",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                <Sparkles size={24} color="white" />
              </div>
              <h2 style={{ color: "#f1f5f9", fontSize: "1.125rem", fontWeight: 600, margin: 0 }}>
                Ask about your operations
              </h2>
              <p style={{ color: "#64748b", fontSize: "0.875rem", maxWidth: 360, margin: 0, lineHeight: 1.6 }}>
                I have access to your connected sources — Slack, Jira, GitHub, and more. Ask anything.
              </p>
            </div>

            {/* Suggested prompts */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", width: "100%", maxWidth: 520 }}>
              {SUGGESTED.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  style={{
                    textAlign: "left", fontSize: "0.8125rem", padding: "0.75rem",
                    borderRadius: "0.75rem", border: "1px solid rgba(255,255,255,0.08)",
                    background: "#1e293b", color: "#94a3b8", cursor: "pointer",
                    lineHeight: 1.5,
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ maxWidth: 720, margin: "0 auto", width: "100%", display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                onFeedback={msg.role === "assistant" ? (f) => sendFeedback(msg.id, f) : undefined}
              />
            ))}
            {error && (
              <p style={{ fontSize: "0.875rem", color: "#f87171", textAlign: "center", margin: 0 }}>
                ⚠ {error}
              </p>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <ChatInput onSend={sendMessage} onStop={stopStreaming} isStreaming={isStreaming} />
    </div>
  );
}
