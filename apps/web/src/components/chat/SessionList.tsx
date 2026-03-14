"use client";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import { formatDistanceToNow } from "date-fns";
import { Plus, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { ChatSession } from "@/types";

interface Props {
  sessions: ChatSession[];
  currentId?: string;
  onCreated: (session: ChatSession) => void;
}

export function SessionList({ sessions, currentId, onCreated }: Props) {
  const router = useRouter();
  const { getToken } = useAuth();

  const createSession = async () => {
    const token = await getToken();
    const res = await fetch("/api/v1/rag/sessions", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    const session: ChatSession = await res.json();
    onCreated(session);
    router.push(`/chat/${session.id}`);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="p-3">
        <button
          onClick={createSession}
          className="w-full flex items-center gap-2 rounded-lg border border-dashed border-gray-300 px-3 py-2.5 text-sm text-gray-500 hover:border-brand-teal hover:text-brand-teal transition-colors"
        >
          <Plus className="h-4 w-4" />
          New conversation
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-1">
        {sessions.length === 0 && (
          <p className="text-xs text-gray-400 text-center py-6">
            No conversations yet
          </p>
        )}
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => router.push(`/chat/${s.id}`)}
            className={cn(
              "w-full text-left flex items-start gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-colors",
              s.id === currentId
                ? "bg-brand-ice text-brand-blue font-medium"
                : "text-gray-600 hover:bg-gray-100",
            )}
          >
            <MessageSquare className="h-4 w-4 mt-0.5 shrink-0 opacity-50" />
            <div className="min-w-0">
              <p className="truncate">{s.title ?? "New conversation"}</p>
              <p className="text-xs text-gray-400 mt-0.5">
                {formatDistanceToNow(new Date(s.updated_at), { addSuffix: true })}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
