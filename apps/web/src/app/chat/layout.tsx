"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { AppShell } from "@/components/layout/AppShell";
import { SessionList } from "@/components/chat/SessionList";
import { ChatSession } from "@/types";

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const { getToken } = useAuth();
  const [sessions, setSessions] = useState<ChatSession[]>([]);

  useEffect(() => {
    (async () => {
      const token = await getToken();
      const res = await fetch("/api/v1/rag/sessions", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) setSessions(await res.json());
    })();
  }, [getToken]);

  return (
    <AppShell>
      <div className="flex h-full">
        {/* Chat sidebar */}
        <div className="w-64 border-r border-gray-200 bg-white flex flex-col shrink-0">
          <div className="px-4 py-3 border-b border-gray-100">
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
              Conversations
            </h2>
          </div>
          <div className="flex-1 overflow-hidden">
            <SessionList
              sessions={sessions}
              onCreated={(s) => setSessions((prev) => [s, ...prev])}
            />
          </div>
        </div>

        {/* Chat content */}
        <div className="flex-1 flex flex-col min-w-0">{children}</div>
      </div>
    </AppShell>
  );
}
