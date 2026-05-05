'use client';

import { useState } from 'react';

const CSS = `
  :root {
    --bg: #09090b;
    --bg2: #111114;
    --bg3: #18181c;
    --bg4: #1f1f24;
    --border: rgba(255,255,255,0.06);
    --border2: rgba(255,255,255,0.1);
    --text: #fafafa;
    --text2: #a1a1aa;
    --text3: #52525b;
    --ol: #22d3ee;
    --ol-dim: rgba(34,211,238,0.08);
    --ol-border: rgba(34,211,238,0.2);
    --ol-glow: rgba(34,211,238,0.12);
    --other: #6b7280;
    --other-dim: rgba(107,114,128,0.08);
    --other-border: rgba(107,114,128,0.2);
    --green: #4ade80;
    --green-dim: rgba(74,222,128,0.08);
    --red: #f87171;
    --amber: #fbbf24;
    --amber-dim: rgba(251,191,36,0.08);
    --serif: 'Instrument Serif', Georgia, serif;
    --sans: 'Instrument Sans', sans-serif;
    --mono: 'Fira Code', monospace;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }
  html { scroll-behavior: smooth; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
  }

  body::before {
    content: '';
    position: fixed;
    top: -200px; left: 50%;
    transform: translateX(-50%);
    width: 900px; height: 600px;
    background: radial-gradient(ellipse, rgba(34,211,238,0.05) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  .pricing-page { position: relative; z-index: 1; }

  .pricing-header {
    text-align: center;
    padding: 88px 24px 64px;
    max-width: 760px;
    margin: 0 auto;
  }

  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text3);
    margin-bottom: 28px;
  }

  .eyebrow-dot { width: 4px; height: 4px; border-radius: 50%; background: var(--ol); }

  .pricing-h1 {
    font-family: var(--serif);
    font-size: clamp(36px, 6vw, 58px);
    font-weight: 400;
    line-height: 1.1;
    margin-bottom: 20px;
  }

  .pricing-h1 em { font-style: italic; color: var(--ol); }

  .header-sub {
    font-size: 17px;
    color: var(--text2);
    line-height: 1.75;
    max-width: 520px;
    margin: 0 auto 40px;
  }

  /* Toggle billing */
  .billing-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0;
    background: var(--bg3);
    border: 1px solid var(--border2);
    border-radius: 8px;
    padding: 4px;
    margin-bottom: 0;
  }

  .toggle-btn {
    padding: 7px 18px;
    border-radius: 5px;
    font-family: var(--mono);
    font-size: 12px;
    cursor: pointer;
    border: none;
    background: transparent;
    color: var(--text3);
    transition: all 0.15s;
    font-weight: 500;
  }

  .toggle-btn.active {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border2);
  }

  .save-badge {
    font-family: var(--mono);
    font-size: 10px;
    padding: 2px 7px;
    border-radius: 4px;
    background: var(--green-dim);
    border: 1px solid rgba(74,222,128,0.2);
    color: var(--green);
    margin-left: 6px;
  }

  .pricing-container {
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 24px 100px;
  }

  .why-higher {
    background: var(--bg2);
    border: 1px solid var(--border2);
    border-radius: 12px;
    padding: 36px;
    margin-bottom: 56px;
    position: relative;
    overflow: hidden;
  }

  .why-higher::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, var(--ol), transparent);
  }

  .why-title {
    font-family: var(--serif);
    font-size: 22px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .why-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
    margin-bottom: 24px;
  }

  .why-item {
    padding: 18px;
    background: var(--bg3);
    border-radius: 8px;
    border: 1px solid var(--border);
  }

  .why-icon { font-size: 20px; margin-bottom: 10px; }

  .why-item-title {
    font-weight: 600;
    font-size: 13px;
    color: var(--text);
    margin-bottom: 6px;
  }

  .why-item-desc {
    font-size: 12px;
    color: var(--text2);
    line-height: 1.6;
  }

  .why-item-desc strong { color: var(--ol); }

  .incident-math {
    background: var(--bg3);
    border: 1px solid var(--ol-border);
    border-radius: 8px;
    padding: 20px 24px;
  }

  .math-title {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ol);
    margin-bottom: 14px;
  }

  .math-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }

  .math-row:last-child { border-bottom: none; }

  .math-label { color: var(--text2); }
  .math-value { font-family: var(--mono); color: var(--text); font-weight: 500; }
  .math-value.total { color: var(--ol); font-size: 15px; }
  .math-value.save { color: var(--green); }

  .plans {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 16px;
    align-items: start;
  }

  .plan {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px 20px;
    position: relative;
    transition: all 0.2s;
    display: flex;
    flex-direction: column;
  }

  .plan:hover { border-color: var(--ol-border); }

  .plan.featured {
    border-color: var(--ol-border);
    background: linear-gradient(180deg, rgba(34,211,238,0.06) 0%, var(--bg2) 100%);
    box-shadow: 0 0 40px var(--ol-glow);
  }

  .plan-badge {
    position: absolute;
    top: -11px; left: 50%;
    transform: translateX(-50%);
    background: var(--ol);
    color: #000;
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    padding: 3px 12px;
    border-radius: 20px;
    white-space: nowrap;
  }

  .plan-name {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text3);
    margin-bottom: 14px;
  }

  .plan-price-row {
    display: flex;
    align-items: baseline;
    gap: 4px;
    margin-bottom: 4px;
  }

  .plan-currency {
    font-size: 18px;
    font-family: var(--sans);
    color: var(--text2);
  }

  .plan-price {
    font-family: var(--serif);
    font-size: 40px;
    font-weight: 400;
    line-height: 1;
    color: var(--text);
  }

  .plan-price.free-price { color: var(--green); font-size: 32px; }
  .plan-price.custom-price { font-size: 24px; color: var(--text2); }

  .plan-period {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text3);
    margin-bottom: 6px;
  }

  .plan-annual {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--green);
    margin-bottom: 18px;
    min-height: 16px;
  }

  .plan-desc {
    font-size: 13px;
    color: var(--text2);
    margin-bottom: 18px;
    line-height: 1.5;
    font-style: italic;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--border);
  }

  .plan-features {
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 9px;
    flex: 1;
  }

  .plan-features li {
    font-size: 12px;
    color: var(--text2);
    display: flex;
    align-items: flex-start;
    gap: 8px;
    line-height: 1.4;
  }

  .plan-features .check {
    color: var(--ol);
    font-size: 11px;
    flex-shrink: 0;
    margin-top: 1px;
    font-family: var(--mono);
  }

  .plan-features .dim { color: var(--text3); }

  .plan-cta {
    display: block;
    text-align: center;
    margin-top: 20px;
    padding: 10px;
    border-radius: 7px;
    font-family: var(--sans);
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    transition: all 0.15s;
    border: 1px solid;
  }

  .plan.featured .plan-cta {
    background: var(--ol);
    color: #000;
    border-color: var(--ol);
  }

  .plan.featured .plan-cta:hover {
    background: #67e8f9;
    transform: translateY(-1px);
    box-shadow: 0 8px 24px var(--ol-glow);
  }

  .plan:not(.featured) .plan-cta {
    background: transparent;
    color: var(--text2);
    border-color: var(--border2);
  }

  .plan:not(.featured) .plan-cta:hover {
    border-color: var(--ol-border);
    color: var(--ol);
  }

  .early-note {
    background: linear-gradient(135deg, rgba(34,211,238,0.06), transparent);
    border: 1px solid var(--ol-border);
    border-radius: 8px;
    padding: 16px 22px;
    margin-bottom: 56px;
    font-size: 13px;
    color: var(--text2);
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
  }

  .early-note strong { color: var(--ol); }

  .early-note a {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--ol);
    text-decoration: none;
    border-bottom: 1px solid var(--ol-border);
  }

  .section-title {
    font-family: var(--serif);
    font-size: 22px;
    font-weight: 400;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 14px;
  }

  .section-title::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border2);
  }

  .comp-wrap {
    background: var(--bg2);
    border: 1px solid var(--border2);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 56px;
  }

  .comp-table {
    width: 100%;
    border-collapse: collapse;
  }

  .comp-table th {
    padding: 16px 20px;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    text-align: left;
    background: var(--bg3);
    border-bottom: 1px solid var(--border2);
    font-weight: 500;
  }

  .comp-table th:nth-child(2) { color: var(--other); }
  .comp-table th:nth-child(3) { color: var(--ol); background: rgba(34,211,238,0.03); }

  .comp-table td {
    padding: 12px 20px;
    font-size: 13px;
    color: var(--text2);
    border-bottom: 1px solid var(--border);
    vertical-align: top;
    line-height: 1.5;
  }

  .comp-table td:nth-child(3) { background: rgba(34,211,238,0.02); }
  .comp-table tr:last-child td { border-bottom: none; }

  .comp-table td:first-child {
    font-weight: 500;
    color: var(--text);
    width: 200px;
  }

  .comp-table .cat-row td {
    background: var(--bg3);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text3);
    padding: 8px 20px;
  }

  .comp-table .cat-row td:nth-child(3) { background: var(--bg3); }

  .yes { color: var(--green); font-weight: 500; }
  .no { color: var(--text3); }
  .ol-yes { color: var(--ol); font-weight: 500; }

  .tag-better {
    display: inline-block;
    font-family: var(--mono);
    font-size: 9px;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--ol-dim);
    border: 1px solid var(--ol-border);
    color: var(--ol);
    margin-left: 6px;
    vertical-align: middle;
  }

  .cost-section { margin-bottom: 56px; }

  .cost-subtitle {
    font-size: 14px;
    color: var(--text2);
    margin-bottom: 24px;
    line-height: 1.6;
  }

  .cost-subtitle strong { color: var(--text); }

  .cost-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
  }

  .cost-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }

  .cost-head {
    padding: 14px 18px;
    background: var(--bg3);
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    font-weight: 600;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .cost-body { padding: 16px 18px; }

  .cost-block {
    margin-bottom: 14px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border);
  }

  .cost-block:last-child { margin-bottom: 0; border-bottom: none; padding-bottom: 0; }

  .cost-block-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  .cost-block-label.other { color: var(--other); }
  .cost-block-label.opslens { color: var(--ol); }

  .cost-line {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    padding: 4px 0;
    color: var(--text2);
  }

  .cost-line .amount { font-family: var(--mono); font-weight: 500; }

  .cost-line.total {
    border-top: 1px solid var(--border);
    margin-top: 6px;
    padding-top: 8px;
    font-weight: 600;
    color: var(--text);
    font-size: 13px;
  }

  .cost-line.total .amount.other-total { color: var(--other); }
  .cost-line.total .amount.ol-total { color: var(--ol); }

  .cost-note {
    font-size: 11px;
    color: var(--green);
    font-family: var(--mono);
    text-align: center;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }

  .cost-warning {
    font-size: 11px;
    color: var(--amber);
    font-family: var(--mono);
    margin-top: 4px;
    font-style: italic;
  }

  .faq { margin-bottom: 56px; }

  .faq-item {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 8px;
    overflow: hidden;
  }

  .faq-q {
    padding: 16px 20px;
    font-weight: 500;
    font-size: 14px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    transition: background 0.15s;
    user-select: none;
  }

  .faq-q:hover { background: var(--bg3); }
  .faq-q.open { color: var(--ol); }

  .faq-chevron {
    font-family: var(--mono);
    font-size: 14px;
    color: var(--text3);
    transition: transform 0.2s;
    flex-shrink: 0;
  }

  .faq-q.open .faq-chevron { transform: rotate(90deg); color: var(--ol); }

  .faq-a {
    display: none;
    padding: 16px 20px 18px;
    font-size: 13px;
    color: var(--text2);
    line-height: 1.7;
    border-top: 1px solid var(--border);
  }

  .faq-a.open { display: block; }
  .faq-a strong { color: var(--text); }
  .faq-a .ol { color: var(--ol); }

  .pricing-cta {
    background: var(--bg2);
    border: 1px solid var(--ol-border);
    border-radius: 14px;
    padding: 56px 48px;
    text-align: center;
    position: relative;
    overflow: hidden;
  }

  .pricing-cta::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 50% 0%, rgba(34,211,238,0.08) 0%, transparent 70%);
    pointer-events: none;
  }

  .cta-inner { position: relative; z-index: 1; }

  .cta-title {
    font-family: var(--serif);
    font-size: clamp(28px, 4vw, 42px);
    font-weight: 400;
    line-height: 1.15;
    margin-bottom: 14px;
  }

  .cta-title em { font-style: italic; color: var(--ol); }

  .cta-sub {
    font-size: 15px;
    color: var(--text2);
    margin-bottom: 32px;
    max-width: 420px;
    margin-left: auto;
    margin-right: auto;
  }

  .cta-btns { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }

  .btn-main {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: var(--ol);
    color: #000;
    font-weight: 700;
    font-size: 14px;
    padding: 13px 30px;
    border-radius: 8px;
    text-decoration: none;
    transition: all 0.15s;
  }

  .btn-main:hover {
    background: #67e8f9;
    transform: translateY(-2px);
    box-shadow: 0 12px 40px var(--ol-glow);
  }

  .btn-sec {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: transparent;
    color: var(--text2);
    font-size: 14px;
    padding: 13px 30px;
    border-radius: 8px;
    text-decoration: none;
    border: 1px solid var(--border2);
    transition: all 0.15s;
  }

  .btn-sec:hover { border-color: var(--ol-border); color: var(--ol); }

  @media (max-width: 860px) {
    .plans { grid-template-columns: 1fr 1fr; }
    .why-grid { grid-template-columns: 1fr; }
    .cost-grid { grid-template-columns: 1fr; }
  }

  @media (max-width: 540px) {
    .plans { grid-template-columns: 1fr; }
    .pricing-header { padding: 56px 20px 40px; }
    .pricing-container { padding: 0 16px 80px; }
    .pricing-cta { padding: 36px 20px; }
    .why-higher { padding: 24px; }
  }
`;

const faqs = [
  {
    q: 'Does OpsLens replace my existing error tracker?',
    a: (
      <>
        No — and we&apos;d tell you not to. Traditional error trackers are excellent at what they do:
        capturing exceptions, stack traces, and performance data at the code level. OpsLens operates
        at a different layer. It ingests your Jira, GitHub, Slack, and log history to answer the
        question that comes <em>after</em> you see the error:{' '}
        <strong>why did this happen, and has it happened before?</strong> Most teams run both side
        by side.
      </>
    ),
  },
  {
    q: 'Why no per-event pricing?',
    a: (
      <>
        Per-event pricing creates a perverse incentive: your costs spike exactly when things are
        going wrong. A bad deploy generates millions of events and your bill doubles overnight — at
        the worst possible time.{' '}
        <strong>
          Flat monthly pricing means your costs are predictable regardless of how many incidents you
          have.
        </strong>{' '}
        We think that&apos;s the right model for an incident intelligence tool.
      </>
    ),
  },
  {
    q: 'Is AI really included? No hidden add-ons?',
    a: (
      <>
        Yes, fully included. AI diagnosis, RRT brief generation, conversational RAG chat,
        HyDE-augmented retrieval, the three-node validation graph — all of it is included in the
        Team plan and above.{' '}
        <strong>
          We don&apos;t believe in charging extra for the thing that makes the product work.
        </strong>{' '}
        The pricing you see is the pricing you pay.
      </>
    ),
  },
  {
    q: 'What counts as an "integration"?',
    a: (
      <>
        Each connected source counts as one integration — Jira is one, GitHub is one, Slack is one,
        Datadog is one, and so on. The Starter plan includes 1. Team includes up to 5. Business and
        Enterprise include unlimited integrations. The more sources you connect, the richer
        OpsLens&apos;s institutional memory becomes.
      </>
    ),
  },
  {
    q: 'What\'s the private deployment option?',
    a: (
      <>
        OpsLens supports <strong className="ol">Ollama</strong> as an LLM provider — a locally-run
        model that means your data never leaves your infrastructure. No calls to OpenAI. No data
        egress. This is particularly valuable for fintech, healthtech, and govtech teams with strict
        data governance requirements. Contact us to discuss private deployment on any plan.
      </>
    ),
  },
  {
    q: 'What does early access mean right now?',
    a: (
      <>
        OpsLens is fully built and production-ready. We&apos;re accepting the first 10 teams at no
        cost in exchange for weekly feedback sessions. You get full Business-tier access —
        unlimited integrations, the full AI suite, insights dashboard, everything. In return, we
        ask for 30 minutes a week to understand what&apos;s working and what isn&apos;t. Email{' '}
        <strong className="ol">admin@opslensai.com</strong> to apply.
      </>
    ),
  },
];

export default function PricingPage() {
  const [openFaq, setOpenFaq] = useState<number | null>(null);

  const toggleFaq = (index: number) => {
    setOpenFaq(openFaq === index ? null : index);
  };

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="pricing-page">

        <header className="pricing-header">
          <div className="eyebrow">
            <span className="eyebrow-dot"></span>
            Pricing · Simple. Flat. Predictable.
            <span className="eyebrow-dot"></span>
          </div>
          <h1 className="pricing-h1">
            Pay for <em>understanding</em>,<br />not for error counts.
          </h1>
          <p className="header-sub">
            OpsLens charges a flat monthly rate — no per-event metering, no surprise overages, no
            AI features locked behind expensive add-ons. Everything included.
          </p>
        </header>

        <div className="pricing-container">

          {/* PLANS */}
          <div className="plans">

            {/* Starter */}
            <div className="plan">
              <div className="plan-name">Starter</div>
              <div className="plan-price-row">
                <div className="plan-price free-price">Free</div>
              </div>
              <div className="plan-period">forever</div>
              <div className="plan-annual">&nbsp;</div>
              <div className="plan-desc">
                Solo engineers and small teams evaluating OpsLens in production.
              </div>
              <ul className="plan-features">
                <li><span className="check">→</span> 1 integration (Slack, Jira, or GitHub)</li>
                <li><span className="check">→</span> Up to 500 alerts/month</li>
                <li><span className="check">→</span> Tier 1 + Tier 2 AI alerts</li>
                <li><span className="check">→</span> Basic routing rules</li>
                <li><span className="check">→</span> 7-day history</li>
                <li><span className="check dim">→</span> <span className="dim">Chat interface — Team+</span></li>
                <li><span className="check dim">→</span> <span className="dim">RRT briefs — Team+</span></li>
              </ul>
              <a href="https://www.opslensai.com" className="plan-cta">Get started free</a>
            </div>

            {/* Team */}
            <div className="plan featured">
              <div className="plan-badge">Most popular</div>
              <div className="plan-name">Team</div>
              <div className="plan-price-row">
                <span className="plan-currency">$</span>
                <div className="plan-price">299</div>
              </div>
              <div className="plan-period">/month</div>
              <div className="plan-annual">Save $360/year with annual billing</div>
              <div className="plan-desc">
                Mid-size SaaS teams — 50 to 200 engineers — who feel the 2am archaeology problem
                every week.
              </div>
              <ul className="plan-features">
                <li><span className="check">→</span> Up to 5 integrations</li>
                <li><span className="check">→</span> Unlimited alerts</li>
                <li><span className="check">→</span> Full AI diagnosis included</li>
                <li><span className="check">→</span> RRT incident briefs</li>
                <li><span className="check">→</span> Conversational RAG chat</li>
                <li><span className="check">→</span> Advanced routing rules + suppression</li>
                <li><span className="check">→</span> 90-day history</li>
                <li><span className="check">→</span> Up to 25 users</li>
              </ul>
              <a href="mailto:admin@opslensai.com" className="plan-cta">Start free trial</a>
            </div>

            {/* Business */}
            <div className="plan">
              <div className="plan-name">Business</div>
              <div className="plan-price-row">
                <span className="plan-currency">$</span>
                <div className="plan-price">999</div>
              </div>
              <div className="plan-period">/month</div>
              <div className="plan-annual">Save $1,188/year with annual billing</div>
              <div className="plan-desc">
                Growing orgs — 200 to 500 engineers — who need insights, SSO, and extended history.
              </div>
              <ul className="plan-features">
                <li><span className="check">→</span> Unlimited integrations</li>
                <li><span className="check">→</span> Insights + sprint retrospectives</li>
                <li><span className="check">→</span> 1-year history</li>
                <li><span className="check">→</span> SSO / SAML</li>
                <li><span className="check">→</span> Up to 100 users</li>
                <li><span className="check">→</span> SLA support</li>
                <li><span className="check">→</span> RBAC + audit log</li>
                <li><span className="check">→</span> Priority Slack support</li>
              </ul>
              <a href="mailto:admin@opslensai.com" className="plan-cta">Contact us</a>
            </div>

            {/* Enterprise */}
            <div className="plan">
              <div className="plan-name">Enterprise</div>
              <div className="plan-price-row">
                <div className="plan-price custom-price">Custom</div>
              </div>
              <div className="plan-period">contact sales</div>
              <div className="plan-annual">&nbsp;</div>
              <div className="plan-desc">
                Regulated industries — fintech, healthtech, govtech — that require zero data egress.
              </div>
              <ul className="plan-features">
                <li><span className="check">→</span> Private deployment (Ollama)</li>
                <li><span className="check">→</span> No data egress — ever</li>
                <li><span className="check">→</span> Dedicated Qdrant instance</li>
                <li><span className="check">→</span> Custom data retention</li>
                <li><span className="check">→</span> Unlimited users</li>
                <li><span className="check">→</span> Custom SLA</li>
                <li><span className="check">→</span> On-prem deployment available</li>
              </ul>
              <a href="mailto:admin@opslensai.com" className="plan-cta">Talk to us</a>
            </div>

          </div>

          {/* EARLY ACCESS */}
          <div className="early-note">
            🎯 <strong>Early access is open now.</strong> The first 10 teams get full
            Business-tier access free in exchange for weekly feedback. No credit card. No
            contracts. Just honest conversation. →{' '}
            <a href="mailto:admin@opslensai.com">admin@opslensai.com</a>
          </div>

          {/* WHY HIGHER */}
          <div className="why-higher">
            <div className="why-title">💡 Why OpsLens costs more than a basic error tracker</div>
            <div className="why-grid">
              <div className="why-item">
                <div className="why-icon">🧠</div>
                <div className="why-item-title">AI is included — not an add-on</div>
                <div className="why-item-desc">
                  Most tools charge <strong>$40 per active developer per month</strong> for their
                  AI features — billed separately on top of your base plan. On a 20-engineer team
                  that&apos;s $800/month extra just for AI diagnosis. OpsLens includes all AI
                  features in every plan. No add-ons. No surprises.
                </div>
              </div>
              <div className="why-item">
                <div className="why-icon">📊</div>
                <div className="why-item-title">Flat pricing — no event metering</div>
                <div className="why-item-desc">
                  Per-event pricing sounds cheap until a bad deploy generates 10M events in a day.
                  Your bill spikes unpredictably. OpsLens charges a{' '}
                  <strong>flat monthly rate regardless of incident volume</strong> — the same cost
                  whether you have 1 incident or 100.
                </div>
              </div>
              <div className="why-item">
                <div className="why-icon">🏛️</div>
                <div className="why-item-title">A different layer entirely</div>
                <div className="why-item-desc">
                  Basic error trackers tell you <em>where</em> in your code something broke.
                  OpsLens tells you <em>why</em> — using your full Jira, GitHub, Slack, and log
                  history. That&apos;s a different product solving a different problem.{' '}
                  <strong>Most teams use both together.</strong>
                </div>
              </div>
            </div>

            <div className="incident-math">
              <div className="math-title">⚡ The real cost of 2am archaeology — per month</div>
              <div className="math-row">
                <span className="math-label">Average engineer hourly rate</span>
                <span className="math-value">$75/hr</span>
              </div>
              <div className="math-row">
                <span className="math-label">Time spent on context gathering per incident</span>
                <span className="math-value">20 min</span>
              </div>
              <div className="math-row">
                <span className="math-label">Incidents per month (typical mid-size team)</span>
                <span className="math-value">~40</span>
              </div>
              <div className="math-row">
                <span className="math-label">Engineers involved per incident (avg)</span>
                <span className="math-value">2</span>
              </div>
              <div className="math-row">
                <span className="math-label">Monthly cost of manual archaeology</span>
                <span className="math-value total">$4,000/mo</span>
              </div>
              <div className="math-row">
                <span className="math-label">OpsLens Team plan</span>
                <span className="math-value save">$299/mo</span>
              </div>
              <div className="math-row" style={{ borderBottom: 'none' }}>
                <span className="math-label" style={{ fontWeight: 600, color: 'var(--text)' }}>
                  Monthly savings
                </span>
                <span className="math-value save" style={{ fontSize: '16px' }}>~$3,700/mo</span>
              </div>
            </div>
          </div>

          {/* COMPARISON TABLE */}
          <div className="section-title">OpsLens vs traditional error trackers</div>
          <div className="comp-wrap">
            <table className="comp-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Traditional error trackers</th>
                  <th>OpsLens AI</th>
                </tr>
              </thead>
              <tbody>
                <tr className="cat-row"><td colSpan={3}>Pricing model</td></tr>
                <tr>
                  <td>Pricing structure</td>
                  <td className="no">Per-event metering — costs spike with bad deploys</td>
                  <td className="ol-yes">Flat monthly — same cost regardless of incident volume <span className="tag-better">better</span></td>
                </tr>
                <tr>
                  <td>AI features included</td>
                  <td className="no">✗ Separate add-on ($40+/dev/month)</td>
                  <td className="ol-yes">✓ Fully included in all paid plans <span className="tag-better">better</span></td>
                </tr>
                <tr>
                  <td>Cost predictability</td>
                  <td className="no">Low — overages are common</td>
                  <td className="ol-yes">High — no overages, ever</td>
                </tr>

                <tr className="cat-row"><td colSpan={3}>Alert intelligence</td></tr>
                <tr>
                  <td>Tells you what broke</td>
                  <td className="yes">✓ Stack traces, breadcrumbs</td>
                  <td className="ol-yes">✓ Log-level pattern detection</td>
                </tr>
                <tr>
                  <td>Tells you why it broke</td>
                  <td className="no">✗ Code context only</td>
                  <td className="ol-yes">✓ Full historical context from Jira, Slack, GitHub <span className="tag-better">better</span></td>
                </tr>
                <tr>
                  <td>Immediate alert (no AI)</td>
                  <td className="yes">✓ Instant</td>
                  <td className="ol-yes">✓ Tier 1 — seconds, no LLM dependency</td>
                </tr>
                <tr>
                  <td>Known issue suppression</td>
                  <td className="no">✗ Not available</td>
                  <td className="ol-yes">✓ Signature + regex + snooze + Jira-linked <span className="tag-better">better</span></td>
                </tr>
                <tr>
                  <td>Smart routing to teams</td>
                  <td>Basic (by project)</td>
                  <td className="ol-yes">✓ Regex rules, priority ordering, fan-out <span className="tag-better">better</span></td>
                </tr>

                <tr className="cat-row"><td colSpan={3}>Institutional memory</td></tr>
                <tr>
                  <td>Searches your Jira history</td>
                  <td className="no">✗</td>
                  <td className="ol-yes">✓ Full history, any date</td>
                </tr>
                <tr>
                  <td>Searches your Slack history</td>
                  <td className="no">✗</td>
                  <td className="ol-yes">✓ Full history, any date</td>
                </tr>
                <tr>
                  <td>Correlates with recent PRs</td>
                  <td>Release tracking only</td>
                  <td className="ol-yes">✓ 60-min timeline window + full PR history <span className="tag-better">better</span></td>
                </tr>
                <tr>
                  <td>Incident brief generation</td>
                  <td className="no">✗</td>
                  <td className="ol-yes">✓ Full RRT brief — cause, impact, next actions</td>
                </tr>
                <tr>
                  <td>Conversational Q&amp;A</td>
                  <td className="no">✗</td>
                  <td className="ol-yes">✓ Ask anything about your incident history</td>
                </tr>

                <tr className="cat-row"><td colSpan={3}>Data &amp; privacy</td></tr>
                <tr>
                  <td>Private / on-prem deployment</td>
                  <td>Enterprise only (custom)</td>
                  <td className="ol-yes">✓ Ollama — available on all plans</td>
                </tr>
                <tr>
                  <td>No data egress option</td>
                  <td className="no">✗</td>
                  <td className="ol-yes">✓ Full on-prem with Ollama</td>
                </tr>
                <tr>
                  <td>Per-tenant data isolation</td>
                  <td className="yes">✓</td>
                  <td className="ol-yes">✓ Per-tenant Qdrant collections</td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* COST COMPARISON */}
          <div className="cost-section">
            <div className="section-title">Real cost examples</div>
            <div className="cost-subtitle">
              Traditional error trackers look cheap at base price — but{' '}
              <strong>AI add-ons and event overages change the math quickly.</strong> These examples
              use published list prices for a typical team setup.
            </div>
            <div className="cost-grid">

              <div className="cost-card">
                <div className="cost-head">🏃 Small team · 10 engineers</div>
                <div className="cost-body">
                  <div className="cost-block">
                    <div className="cost-block-label other">Typical error tracker</div>
                    <div className="cost-line"><span>Base plan</span><span className="amount">$26/mo</span></div>
                    <div className="cost-line"><span>Event overages</span><span className="amount">+$15/mo</span></div>
                    <div className="cost-line"><span>AI add-on (5 devs × $40)</span><span className="amount">+$200/mo</span></div>
                    <div className="cost-line total"><span>Total</span><span className="amount other-total">~$241/mo</span></div>
                    <div className="cost-warning">⚠ AI cost spikes with active contributors</div>
                  </div>
                  <div className="cost-block">
                    <div className="cost-block-label opslens">OpsLens AI (Starter → Team)</div>
                    <div className="cost-line"><span>All features, AI included</span><span className="amount">$0 → $299/mo</span></div>
                    <div className="cost-line"><span>Event overages</span><span className="amount">$0</span></div>
                    <div className="cost-line"><span>AI add-on</span><span className="amount">$0</span></div>
                    <div className="cost-line total"><span>Total</span><span className="amount ol-total">Free → $299/mo</span></div>
                  </div>
                  <div className="cost-note">→ Start free · upgrade when ready</div>
                </div>
              </div>

              <div className="cost-card">
                <div className="cost-head">🏢 Mid-size · 50 engineers</div>
                <div className="cost-body">
                  <div className="cost-block">
                    <div className="cost-block-label other">Typical error tracker</div>
                    <div className="cost-line"><span>Base plan</span><span className="amount">$80/mo</span></div>
                    <div className="cost-line"><span>Event overages (1M/mo)</span><span className="amount">+$160/mo</span></div>
                    <div className="cost-line"><span>AI add-on (20 devs × $40)</span><span className="amount">+$800/mo</span></div>
                    <div className="cost-line total"><span>Total</span><span className="amount other-total">~$1,040/mo</span></div>
                    <div className="cost-warning">⚠ Overages unpredictable month-to-month</div>
                  </div>
                  <div className="cost-block">
                    <div className="cost-block-label opslens">OpsLens AI (Team)</div>
                    <div className="cost-line"><span>All features, AI included</span><span className="amount">$499/mo</span></div>
                    <div className="cost-line"><span>Event overages</span><span className="amount">$0</span></div>
                    <div className="cost-line"><span>AI add-on</span><span className="amount">$0</span></div>
                    <div className="cost-line total"><span>Total</span><span className="amount ol-total">$499/mo</span></div>
                  </div>
                  <div className="cost-note">→ ~52% lower than comparable stack</div>
                </div>
              </div>

              <div className="cost-card">
                <div className="cost-head">🏦 Large team · 200 engineers</div>
                <div className="cost-body">
                  <div className="cost-block">
                    <div className="cost-block-label other">Typical error tracker</div>
                    <div className="cost-line"><span>Base plan</span><span className="amount">$80/mo</span></div>
                    <div className="cost-line"><span>Event overages (10M/mo)</span><span className="amount">+$2,850/mo</span></div>
                    <div className="cost-line"><span>AI add-on (50 devs × $40)</span><span className="amount">+$2,000/mo</span></div>
                    <div className="cost-line total"><span>Total</span><span className="amount other-total">~$4,930/mo</span></div>
                    <div className="cost-warning">⚠ Negotiated enterprise pricing may vary</div>
                  </div>
                  <div className="cost-block">
                    <div className="cost-block-label opslens">OpsLens AI (Business)</div>
                    <div className="cost-line"><span>All features, AI included</span><span className="amount">$1,499/mo</span></div>
                    <div className="cost-line"><span>Event overages</span><span className="amount">$0</span></div>
                    <div className="cost-line"><span>AI add-on</span><span className="amount">$0</span></div>
                    <div className="cost-line total"><span>Total</span><span className="amount ol-total">$1,499/mo</span></div>
                  </div>
                  <div className="cost-note">→ ~70% lower on comparable AI-enabled stack</div>
                </div>
              </div>

            </div>
          </div>

          {/* FAQ */}
          <div className="faq">
            <div className="section-title">Common questions</div>
            {faqs.map((faq, i) => (
              <div className="faq-item" key={i}>
                <div
                  className={`faq-q ${openFaq === i ? 'open' : ''}`}
                  onClick={() => toggleFaq(i)}
                >
                  {faq.q}
                  <span className="faq-chevron">▶</span>
                </div>
                <div className={`faq-a ${openFaq === i ? 'open' : ''}`}>
                  {faq.a}
                </div>
              </div>
            ))}
          </div>

          {/* CTA */}
          <div className="pricing-cta">
            <div className="cta-inner">
              <div className="cta-title">
                Stop paying for<br /><em>archaeology</em>.
              </div>
              <div className="cta-sub">
                Early access is open. First 10 teams get full Business-tier access free.
              </div>
              <div className="cta-btns">
                <a href="https://www.opslensai.com" className="btn-main">Get early access →</a>
                <a href="mailto:admin@opslensai.com" className="btn-sec">Email us directly</a>
              </div>
            </div>
          </div>

        </div>
      </div>
    </>
  );
}
