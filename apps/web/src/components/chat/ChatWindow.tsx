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
  "Which customers are at risk of churning?",
];

interface Props {
  sessionId: string;
}

export function ChatWindow({ sessionId }: Props) {
  const { messages, isStreaming, error, loadMessages, sendMessage, stopStreaming, sendFeedback } =
    useChat(sessionId);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const isEmpty = messages.length === 0;

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto">
        {isEmpty ? (
          /* Empty state */
          <div className="h-full flex flex-col items-center justify-center gap-6 px-4">
            <div className="flex flex-col items-center gap-2 text-center">
              <div className="h-12 w-12 rounded-2xl bg-brand-navy flex items-center justify-center">
                <Sparkles className="h-6 w-6 text-brand-teal" />
              </div>
              <h2 className="text-xl font-semibold text-gray-800">
                Ask about your operations
              </h2>
              <p className="text-sm text-gray-400 max-w-sm">
                I have access to your Slack, Jira, Zendesk, GitHub, and more.
                Ask anything.
              </p>
            </div>

            {/* Suggested prompts */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
              {SUGGESTED.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  className="text-left text-sm px-4 py-3 rounded-xl border border-gray-200 text-gray-600 hover:border-brand-teal hover:text-brand-teal bg-white transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="px-4 py-6 space-y-6 max-w-3xl mx-auto w-full">
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                onFeedback={
                  msg.role === "assistant"
                    ? (f) => sendFeedback(msg.id, f)
                    : undefined
                }
              />
            ))}
            {error && (
              <p className="text-sm text-red-500 text-center">
                ⚠ {error}
              </p>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <ChatInput
        onSend={sendMessage}
        onStop={stopStreaming}
        isStreaming={isStreaming}
      />
    </div>
  );
}
