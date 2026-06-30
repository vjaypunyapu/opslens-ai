import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ChatMessage } from "@/types";

const USER_MSG: ChatMessage = {
  id: "msg-1",
  session_id: "sess-1",
  role: "user",
  content: "What is causing the checkout errors?",
  sources: [],
  token_count: null,
  latency_ms: null,
  feedback: null,
  created_at: new Date().toISOString(),
};

const ASSISTANT_MSG: ChatMessage = {
  id: "msg-2",
  session_id: "sess-1",
  role: "assistant",
  content: "The checkout errors are caused by a payment gateway timeout.",
  sources: [
    { title: "Ticket #1234", url: "https://example.com/1234", source_type: "zendesk", score: 0.9 },
    { title: "JIRA-456", url: null, source_type: "jira", score: 0.8 },
  ],
  token_count: 50,
  latency_ms: 1200,
  feedback: null,
  created_at: new Date().toISOString(),
};

describe("MessageBubble", () => {
  it("renders user message content", () => {
    render(<MessageBubble message={USER_MSG} />);
    expect(screen.getByText("What is causing the checkout errors?")).toBeInTheDocument();
  });

  it("renders assistant message content", () => {
    render(<MessageBubble message={ASSISTANT_MSG} />);
    expect(screen.getByText(/payment gateway timeout/)).toBeInTheDocument();
  });

  it("shows latency for assistant messages", () => {
    render(<MessageBubble message={ASSISTANT_MSG} />);
    expect(screen.getByText("1.2s")).toBeInTheDocument();
  });

  it("shows a sources toggle button when sources exist", () => {
    render(<MessageBubble message={ASSISTANT_MSG} />);
    expect(screen.getByText("2 sources")).toBeInTheDocument();
  });

  it("expands to show source titles when sources button is clicked", () => {
    render(<MessageBubble message={ASSISTANT_MSG} />);
    fireEvent.click(screen.getByText("2 sources"));
    expect(screen.getByText("Ticket #1234")).toBeInTheDocument();
    expect(screen.getByText("JIRA-456")).toBeInTheDocument();
  });

  it("collapses sources panel on second click", () => {
    render(<MessageBubble message={ASSISTANT_MSG} />);
    fireEvent.click(screen.getByText("2 sources"));
    expect(screen.getByText("Hide sources")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Hide sources"));
    expect(screen.queryByText("Ticket #1234")).not.toBeInTheDocument();
  });

  it("renders feedback buttons when onFeedback is provided", () => {
    const onFeedback = jest.fn();
    render(<MessageBubble message={ASSISTANT_MSG} onFeedback={onFeedback} />);
    expect(screen.getByTitle("Good response")).toBeInTheDocument();
    expect(screen.getByTitle("Bad response")).toBeInTheDocument();
  });

  it("calls onFeedback with thumbs_up when thumbs-up button is clicked", () => {
    const onFeedback = jest.fn();
    render(<MessageBubble message={ASSISTANT_MSG} onFeedback={onFeedback} />);
    fireEvent.click(screen.getByTitle("Good response"));
    expect(onFeedback).toHaveBeenCalledWith("thumbs_up");
  });

  it("calls onFeedback with thumbs_down when thumbs-down button is clicked", () => {
    const onFeedback = jest.fn();
    render(<MessageBubble message={ASSISTANT_MSG} onFeedback={onFeedback} />);
    fireEvent.click(screen.getByTitle("Bad response"));
    expect(onFeedback).toHaveBeenCalledWith("thumbs_down");
  });

  it("does not render feedback buttons when onFeedback is not provided", () => {
    render(<MessageBubble message={ASSISTANT_MSG} />);
    expect(screen.queryByTitle("Good response")).not.toBeInTheDocument();
  });

  it("renders streaming dots when assistant content is empty", () => {
    const streaming: ChatMessage = { ...ASSISTANT_MSG, content: "", sources: [], latency_ms: null };
    const { container } = render(<MessageBubble message={streaming} />);
    // Three animated dots rendered as <span> elements
    const dots = container.querySelectorAll("span[style*='border-radius: 50%']");
    expect(dots.length).toBe(3);
  });
});
