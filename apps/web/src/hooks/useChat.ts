"use client";
import { useCallback, useRef, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { ChatMessage, MessageSource } from "@/types";

interface StreamEvent {
  type: "token" | "sources" | "done" | "error";
  data?: string | MessageSource[];
  latency_ms?: number;
  message?: string;
}

export function useChat(sessionId: string) {
  const { getToken } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const loadMessages = useCallback(async () => {
    try {
      const token = await getToken();
      const res = await fetch(`/api/v1/chat/sessions/${sessionId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setMessages(data.messages ?? []);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load messages');
    }
  }, [sessionId, getToken]);

  const sendMessage = useCallback(
    async (question: string, sourceTypes?: string[]) => {
      if (isStreaming) return;
      setError(null);

      // Optimistically add user message
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        session_id: sessionId,
        role: "user",
        content: question,
        sources: [],
        token_count: null,
        latency_ms: null,
        feedback: null,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMsg]);

      // Placeholder for the assistant reply
      const placeholderId = crypto.randomUUID();
      const placeholder: ChatMessage = {
        id: placeholderId,
        session_id: sessionId,
        role: "assistant",
        content: "",
        sources: [],
        token_count: null,
        latency_ms: null,
        feedback: null,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, placeholder]);
      setIsStreaming(true);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        const token = await getToken();
        const res = await fetch(`/api/v1/chat/sessions/${sessionId}/query`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            content: question,
            filters: sourceTypes?.length ? { source_types: sourceTypes } : undefined,
          }),
          signal: ctrl.signal,
        });

        if (!res.ok) throw new Error(`Query failed: ${res.statusText}`);
        if (!res.body) throw new Error("No response body");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const raw = line.slice(6).trim();
            if (!raw) continue;

            const event: StreamEvent = JSON.parse(raw);

            if (event.type === "token") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === placeholderId
                    ? { ...m, content: m.content + (event.data as string) }
                    : m,
                ),
              );
            } else if (event.type === "sources") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === placeholderId
                    ? { ...m, sources: event.data as MessageSource[] }
                    : m,
                ),
              );
            } else if (event.type === "done") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === placeholderId
                    ? { ...m, latency_ms: event.latency_ms ?? null }
                    : m,
                ),
              );
            } else if (event.type === "error") {
              setError(event.message ?? "Streaming error");
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== "AbortError") {
          setError(err.message);
          setMessages((prev) => prev.filter((m) => m.id !== placeholderId));
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [sessionId, isStreaming, getToken],
  );

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const sendFeedback = useCallback(
    async (messageId: string, feedback: "thumbs_up" | "thumbs_down") => {
      const token = await getToken();
      await fetch("/api/v1/chat/feedback", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          message_id: messageId,
          score: feedback === "thumbs_up" ? 1 : -1,
        }),
      });
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, feedback } : m)),
      );
    },
    [sessionId, getToken],
  );

  return { messages, isStreaming, error, loadMessages, sendMessage, stopStreaming, sendFeedback, setMessages };
}
