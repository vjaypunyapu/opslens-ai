"use client";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ThumbsUp, ThumbsDown, ExternalLink, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
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
    <div className={cn("flex gap-3", isUser ? "justify-end" : "justify-start")}>
      {/* Avatar */}
      {!isUser && (
        <div className="h-7 w-7 rounded-full bg-brand-navy flex items-center justify-center text-white text-xs font-bold shrink-0 mt-1">
          AI
        </div>
      )}

      <div className={cn("max-w-[75%] space-y-1", isUser && "items-end flex flex-col")}>
        {/* Bubble */}
        <div
          className={cn(
            "rounded-2xl px-4 py-3 text-sm",
            isUser
              ? "bg-brand-navy text-white rounded-tr-sm"
              : "bg-white border border-gray-200 text-gray-800 rounded-tl-sm shadow-sm",
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : message.content ? (
            <div className="prose-chat">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          ) : (
            // Streaming cursor
            <span className="inline-flex gap-1">
              <span className="h-2 w-2 rounded-full bg-gray-400 animate-bounce [animation-delay:0ms]" />
              <span className="h-2 w-2 rounded-full bg-gray-400 animate-bounce [animation-delay:150ms]" />
              <span className="h-2 w-2 rounded-full bg-gray-400 animate-bounce [animation-delay:300ms]" />
            </span>
          )}
        </div>

        {/* Sources + feedback row */}
        {!isUser && message.content && (
          <div className="flex items-center gap-3 px-1">
            {/* Sources toggle */}
            {message.sources.length > 0 && (
              <button
                onClick={() => setShowSources((v) => !v)}
                className="text-xs text-gray-400 hover:text-brand-teal transition-colors"
              >
                {showSources ? "Hide" : `${message.sources.length} source${message.sources.length > 1 ? "s" : ""}`}
              </button>
            )}

            {/* Latency */}
            {message.latency_ms && (
              <span className="flex items-center gap-1 text-xs text-gray-300">
                <Clock className="h-3 w-3" />
                {(message.latency_ms / 1000).toFixed(1)}s
              </span>
            )}

            {/* Feedback */}
            {onFeedback && (
              <div className="flex items-center gap-1 ml-auto">
                <button
                  onClick={() => onFeedback("thumbs_up")}
                  className={cn(
                    "p-1 rounded hover:bg-gray-100 transition-colors",
                    message.feedback === "thumbs_up" ? "text-green-600" : "text-gray-300",
                  )}
                >
                  <ThumbsUp className="h-3 w-3" />
                </button>
                <button
                  onClick={() => onFeedback("thumbs_down")}
                  className={cn(
                    "p-1 rounded hover:bg-gray-100 transition-colors",
                    message.feedback === "thumbs_down" ? "text-red-500" : "text-gray-300",
                  )}
                >
                  <ThumbsDown className="h-3 w-3" />
                </button>
              </div>
            )}
          </div>
        )}

        {/* Sources list */}
        {showSources && message.sources.length > 0 && (
          <div className="bg-gray-50 border border-gray-200 rounded-xl p-3 space-y-2">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
              Sources
            </p>
            {message.sources.map((src, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span>{SOURCE_TYPE_ICONS[src.source_type as SourceType] ?? "📎"}</span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-gray-700 truncate">{src.title}</p>
                  <p className="text-gray-400">{SOURCE_TYPE_LABELS[src.source_type as SourceType]}</p>
                </div>
                {src.url && (
                  <a
                    href={src.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-brand-teal hover:underline shrink-0"
                  >
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {isUser && (
        <div className="h-7 w-7 rounded-full bg-gray-200 flex items-center justify-center text-gray-600 text-xs font-bold shrink-0 mt-1">
          U
        </div>
      )}
    </div>
  );
}
