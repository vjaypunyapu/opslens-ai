// Stub for react-markdown (ESM package — not transformed by jest by default).
// Tests that use MessageBubble don't need real markdown rendering.
import React from "react";

export default function ReactMarkdown({ children }: { children: string }) {
  return <span>{children}</span>;
}
