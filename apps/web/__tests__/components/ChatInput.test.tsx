import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInput } from "@/components/chat/ChatInput";

describe("ChatInput", () => {
  const defaultProps = {
    onSend: jest.fn(),
    onStop: jest.fn(),
    isStreaming: false,
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("renders the textarea with placeholder text", () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByPlaceholderText("Ask anything about your operations…")).toBeInTheDocument();
  });

  it("calls onSend with the message when send button is clicked", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    await user.type(screen.getByRole("textbox"), "What is the error rate?");
    fireEvent.click(screen.getByTitle("Send (Enter)"));
    expect(defaultProps.onSend).toHaveBeenCalledWith("What is the error rate?", undefined);
  });

  it("calls onSend when Enter is pressed (without Shift)", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    await user.type(screen.getByRole("textbox"), "How many alerts?");
    await user.keyboard("{Enter}");
    expect(defaultProps.onSend).toHaveBeenCalledWith("How many alerts?", undefined);
  });

  it("does not call onSend when Shift+Enter is pressed", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    await user.type(screen.getByRole("textbox"), "Line one");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  it("clears the textarea after sending", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    const textarea = screen.getByRole("textbox");
    await user.type(textarea, "A question");
    await user.keyboard("{Enter}");
    expect(textarea).toHaveValue("");
  });

  it("does not call onSend when input is empty or whitespace", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    await user.type(screen.getByRole("textbox"), "   ");
    await user.keyboard("{Enter}");
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  it("shows Stop button when isStreaming is true", () => {
    render(<ChatInput {...defaultProps} isStreaming={true} />);
    expect(screen.getByTitle("Stop")).toBeInTheDocument();
    expect(screen.queryByTitle("Send (Enter)")).not.toBeInTheDocument();
  });

  it("calls onStop when Stop button is clicked", () => {
    render(<ChatInput {...defaultProps} isStreaming={true} />);
    fireEvent.click(screen.getByTitle("Stop"));
    expect(defaultProps.onStop).toHaveBeenCalled();
  });

  it("disables the textarea when isStreaming is true", () => {
    render(<ChatInput {...defaultProps} isStreaming={true} />);
    expect(screen.getByRole("textbox")).toBeDisabled();
  });

  it("shows source filter chips when the filter button is clicked", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    await user.click(screen.getByTitle("Filter by source"));
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.getByText("Jira")).toBeInTheDocument();
    expect(screen.getByText("Zendesk")).toBeInTheDocument();
  });

  it("passes selected source types to onSend", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    await user.click(screen.getByTitle("Filter by source"));
    await user.click(screen.getByText("Slack"));
    await user.type(screen.getByRole("textbox"), "Latest alerts");
    await user.keyboard("{Enter}");
    expect(defaultProps.onSend).toHaveBeenCalledWith("Latest alerts", ["slack"]);
  });

  it("shows a count badge on the filter button when sources are selected", async () => {
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} />);
    await user.click(screen.getByTitle("Filter by source"));
    await user.click(screen.getByText("Slack"));
    await user.click(screen.getByText("Jira"));
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
