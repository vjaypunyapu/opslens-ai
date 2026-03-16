"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useRouter, usePathname } from "next/navigation";
import { Plus, MessageSquare, Clock } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { ChatSession } from "@/types";
import { formatDistanceToNow } from "date-fns";

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [creating, setCreating] = useState(false);

  const refreshSessions = async () => {
    const token = await getToken();
    const res = await fetch("/api/v1/chat/sessions", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (res.ok) setSessions(await res.json());
  };

  useEffect(() => {
    refreshSessions();
  }, [getToken]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fetch sessions whenever the URL changes (user sends first message → title gets set)
  useEffect(() => {
    refreshSessions();
  }, [pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  // Listen for a custom event fired by the chat page after the first message is sent
  useEffect(() => {
    const handler = () => refreshSessions();
    window.addEventListener("opslens:session-updated", handler);
    return () => window.removeEventListener("opslens:session-updated", handler);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const createSession = async () => {
    setCreating(true);
    try {
      const token = await getToken();
      const res = await fetch("/api/v1/chat/sessions", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error("Failed");
      const session: ChatSession = await res.json();
      setSessions((prev) => [session, ...prev]);
      router.push(`/chat/${session.id}`);
    } finally {
      setCreating(false);
    }
  };

  return (
    <AppShell>
      <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
        {/* Session sidebar */}
        <div style={{
          width: 256, flexShrink: 0, display: "flex", flexDirection: "column",
          background: "#0f172a", borderRight: "1px solid rgba(255,255,255,0.06)",
          height: "100%", overflow: "hidden",
        }}>
          <div style={{ padding: "16px 16px 12px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: "#475569",
              textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>
              Conversations
            </div>
            <button
              onClick={createSession}
              disabled={creating}
              style={{
                width: "100%", display: "flex", alignItems: "center", gap: 8,
                padding: "9px 12px", borderRadius: 8,
                border: "1px dashed rgba(20,184,166,0.4)",
                background: "rgba(20,184,166,0.06)", color: "#2dd4bf",
                fontSize: 13, fontWeight: 500, cursor: "pointer",
                opacity: creating ? 0.6 : 1,
              }}
            >
              <Plus size={14} />
              {creating ? "Creating…" : "New conversation"}
            </button>
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: "8px" }}>
            {sessions.length === 0 ? (
              <div style={{ textAlign: "center", padding: "40px 16px", color: "#475569" }}>
                <MessageSquare size={28} style={{ opacity: 0.3, margin: "0 auto 10px", display: "block" }} />
                <p style={{ fontSize: 12, margin: 0 }}>No conversations yet</p>
              </div>
            ) : (
              sessions.map((s) => {
                const active = pathname === `/chat/${s.id}`;
                return (
                  <button
                    key={s.id}
                    onClick={() => router.push(`/chat/${s.id}`)}
                    style={{
                      width: "100%", display: "flex", alignItems: "flex-start", gap: 10,
                      padding: "10px 12px", borderRadius: 8, marginBottom: 2,
                      border: active ? "1px solid rgba(20,184,166,0.25)" : "1px solid transparent",
                      background: active ? "rgba(20,184,166,0.1)" : "transparent",
                      cursor: "pointer", textAlign: "left",
                    }}
                  >
                    <MessageSquare size={14} color={active ? "#2dd4bf" : "#475569"}
                      style={{ marginTop: 2, flexShrink: 0 }} />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <p style={{
                        fontSize: 13, fontWeight: 500, margin: 0,
                        color: active ? "#e2e8f0" : "#94a3b8",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>
                        {s.title ?? "New conversation"}
                      </p>
                      <p style={{ fontSize: 11, color: "#475569", margin: "3px 0 0",
                        display: "flex", alignItems: "center", gap: 4 }}>
                        <Clock size={10} />
                        {formatDistanceToNow(new Date(s.updated_at), { addSuffix: true })}
                      </p>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Chat content area */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0,
          overflow: "hidden", background: "#0f172a" }}>
          {children}
        </div>
      </div>
    </AppShell>
  );
}
