import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { InsightCard } from "@/components/insights/InsightCard";
import { Insight } from "@/types";

const BASE_INSIGHT: Insight = {
  id: "insight-1",
  tenant_id: "tenant-1",
  insight_type: "complaint_spike",
  title: "Checkout errors up 35% this week",
  summary: "Payment gateway errors spiked significantly in the checkout flow.",
  magnitude: "high",
  confidence: 0.9,
  status: "active",
  source_types: ["zendesk", "jira"],
  generated_at: new Date(Date.now() - 60_000).toISOString(),
  created_at: new Date(Date.now() - 60_000).toISOString(),
  snoozed_until: null,
};

describe("InsightCard", () => {
  it("renders the insight title and type label", () => {
    const onStatusChange = jest.fn();
    render(<InsightCard insight={BASE_INSIGHT} onStatusChange={onStatusChange} />);
    expect(screen.getByText("Checkout errors up 35% this week")).toBeInTheDocument();
    expect(screen.getByText("Complaint Spike")).toBeInTheDocument();
  });

  it("renders the magnitude badge", () => {
    render(<InsightCard insight={BASE_INSIGHT} onStatusChange={jest.fn()} />);
    expect(screen.getByText("High")).toBeInTheDocument();
  });

  it("renders source pills for each source_type", () => {
    render(<InsightCard insight={BASE_INSIGHT} onStatusChange={jest.fn()} />);
    expect(screen.getByText(/Zendesk/)).toBeInTheDocument();
    expect(screen.getByText(/Jira/)).toBeInTheDocument();
  });

  it("expands to show summary when expand button is clicked", () => {
    render(<InsightCard insight={BASE_INSIGHT} onStatusChange={jest.fn()} />);
    expect(screen.queryByText(BASE_INSIGHT.summary)).not.toBeInTheDocument();
    const expandBtn = screen.getByRole("button", { name: "" }); // chevron button
    fireEvent.click(expandBtn);
    expect(screen.getByText(BASE_INSIGHT.summary)).toBeInTheDocument();
  });

  it("calls onStatusChange with 'resolved' when Resolve is clicked", () => {
    const onStatusChange = jest.fn();
    render(<InsightCard insight={BASE_INSIGHT} onStatusChange={onStatusChange} />);
    fireEvent.click(screen.getByText("Resolve"));
    expect(onStatusChange).toHaveBeenCalledWith("insight-1", "resolved");
  });

  it("calls onStatusChange with 'snoozed' and 24h when Snooze is clicked", () => {
    const onStatusChange = jest.fn();
    render(<InsightCard insight={BASE_INSIGHT} onStatusChange={onStatusChange} />);
    fireEvent.click(screen.getByText("Snooze 24h"));
    expect(onStatusChange).toHaveBeenCalledWith("insight-1", "snoozed", 24);
  });

  it("shows Reopen button and hides Resolve/Snooze when status is resolved", () => {
    const resolved = { ...BASE_INSIGHT, status: "resolved" as const };
    render(<InsightCard insight={resolved} onStatusChange={jest.fn()} />);
    expect(screen.queryByText("Resolve")).not.toBeInTheDocument();
    expect(screen.queryByText("Snooze 24h")).not.toBeInTheDocument();
    expect(screen.getByText("Reopen")).toBeInTheDocument();
  });

  it("calls onStatusChange with 'active' when Reopen is clicked on a snoozed insight", () => {
    const onStatusChange = jest.fn();
    const snoozed = { ...BASE_INSIGHT, status: "snoozed" as const };
    render(<InsightCard insight={snoozed} onStatusChange={onStatusChange} />);
    fireEvent.click(screen.getByText("Reopen"));
    expect(onStatusChange).toHaveBeenCalledWith("insight-1", "active");
  });

  it("renders confidence percentage when confidence is provided", () => {
    render(<InsightCard insight={BASE_INSIGHT} onStatusChange={jest.fn()} />);
    expect(screen.getByText("90% confidence")).toBeInTheDocument();
  });

  it("does not render confidence label when confidence is null", () => {
    const noConf = { ...BASE_INSIGHT, confidence: null };
    render(<InsightCard insight={noConf} onStatusChange={jest.fn()} />);
    expect(screen.queryByText(/confidence/)).not.toBeInTheDocument();
  });
});
