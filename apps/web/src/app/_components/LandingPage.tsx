'use client';

import { useState } from 'react';
import BookDemoModal from './BookDemoModal';

const CSS = `
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --teal: #14B8A6;
  --teal-dark: #0D9488;
  --teal-dim: #0F766E;
  --navy: #0F172A;
  --navy-mid: #1E293B;
  --navy-light: #334155;
  --slate: #64748B;
  --text: #F1F5F9;
  --text-muted: #94A3B8;
  --white: #FFFFFF;
  --card-bg: #1E293B;
  --border: rgba(20, 184, 166, 0.15);
}

html { scroll-behavior: smooth; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--navy);
  color: var(--text);
  line-height: 1.6;
  overflow-x: hidden;
}

/* NAV */
nav {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 2rem; height: 64px;
  background: rgba(15, 23, 42, 0.92);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
}
.nav-logo { display: flex; align-items: center; gap: 0.5rem; text-decoration: none; }
.nav-logo-icon {
  width: 32px; height: 32px; background: var(--teal);
  border-radius: 8px; display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 16px; color: var(--navy);
}
.nav-logo-text { font-size: 1.1rem; font-weight: 700; color: var(--text); }
.nav-links { display: flex; gap: 2rem; list-style: none; }
.nav-links a { color: var(--text-muted); text-decoration: none; font-size: 0.9rem; transition: color 0.2s; }
.nav-links a:hover { color: var(--teal); }
.nav-right { display: flex; align-items: center; gap: 0.75rem; }
.nav-login {
  background: transparent; color: var(--text-muted);
  border: 1px solid var(--navy-light); padding: 0.45rem 1.1rem;
  border-radius: 6px; font-weight: 600; font-size: 0.875rem;
  cursor: pointer; text-decoration: none; transition: all 0.2s;
}
.nav-login:hover { border-color: var(--teal); color: var(--teal); }
.nav-cta {
  background: var(--teal); color: var(--navy);
  border: none; padding: 0.5rem 1.25rem;
  border-radius: 6px; font-weight: 600; font-size: 0.875rem;
  cursor: pointer; text-decoration: none; transition: background 0.2s;
}
.nav-cta:hover { background: var(--teal-dark); }

/* HERO */
.hero {
  min-height: 100vh;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  text-align: center;
  padding: 8rem 2rem 4rem;
  position: relative; overflow: hidden;
}
.hero::before {
  content: '';
  position: absolute; top: -20%; left: 50%; transform: translateX(-50%);
  width: 800px; height: 800px;
  background: radial-gradient(circle, rgba(20, 184, 166, 0.12) 0%, transparent 65%);
  pointer-events: none;
}
.hero-badge {
  display: inline-flex; align-items: center; gap: 0.5rem;
  background: rgba(20, 184, 166, 0.12); border: 1px solid rgba(20, 184, 166, 0.3);
  color: var(--teal); padding: 0.35rem 1rem;
  border-radius: 999px; font-size: 0.8rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  margin-bottom: 1.5rem;
}
.hero-badge-dot {
  width: 6px; height: 6px; background: var(--teal); border-radius: 50%;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; } 50% { opacity: 0.3; }
}
h1 {
  font-size: clamp(2.5rem, 6vw, 4.5rem);
  font-weight: 800; line-height: 1.1;
  letter-spacing: -0.02em;
  max-width: 900px; margin: 0 auto 1.5rem;
}
h1 span { color: var(--teal); }
.hero-sub {
  font-size: clamp(1rem, 2vw, 1.25rem);
  color: var(--text-muted); max-width: 600px;
  margin: 0 auto 2.5rem; line-height: 1.7;
}
.hero-actions { display: flex; gap: 1rem; flex-wrap: wrap; justify-content: center; }
.btn-primary {
  background: var(--teal); color: var(--navy);
  padding: 0.85rem 2rem; border-radius: 8px;
  font-weight: 700; font-size: 1rem; text-decoration: none;
  transition: all 0.2s; border: none; cursor: pointer;
  box-shadow: 0 0 24px rgba(20, 184, 166, 0.3);
}
.btn-primary:hover { background: var(--teal-dark); transform: translateY(-1px); box-shadow: 0 0 32px rgba(20, 184, 166, 0.45); }
.btn-secondary {
  background: transparent; color: var(--text);
  padding: 0.85rem 2rem; border-radius: 8px;
  font-weight: 600; font-size: 1rem; text-decoration: none;
  border: 1px solid var(--navy-light); transition: all 0.2s;
}
.btn-secondary:hover { border-color: var(--teal); color: var(--teal); }

/* PRODUCT MOCKUP */
.hero-mockup {
  margin-top: 4rem; width: 100%; max-width: 900px;
  background: var(--navy-mid); border: 1px solid var(--border);
  border-radius: 16px; padding: 1.5rem;
  box-shadow: 0 40px 80px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(20, 184, 166, 0.08);
}
.mockup-bar {
  display: flex; align-items: center; gap: 0.5rem;
  margin-bottom: 1rem; padding-bottom: 0.75rem;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.mockup-dot { width: 10px; height: 10px; border-radius: 50%; }
.mockup-title { font-size: 0.75rem; color: var(--text-muted); margin-left: auto; }
.incident-card {
  background: var(--navy); border-radius: 10px; padding: 1rem 1.25rem;
  border-left: 3px solid #EF4444; margin-bottom: 0.75rem;
  display: flex; gap: 1rem; align-items: flex-start;
}
.incident-severity {
  background: rgba(239, 68, 68, 0.15); color: #EF4444;
  padding: 0.2rem 0.6rem; border-radius: 4px;
  font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
  white-space: nowrap; margin-top: 2px;
}
.incident-name { font-weight: 600; font-size: 0.9rem; margin-bottom: 0.2rem; }
.incident-meta { font-size: 0.75rem; color: var(--text-muted); }
.incident-route { margin-left: auto; display: flex; align-items: center; gap: 0.5rem; white-space: nowrap; }
.route-tag {
  background: rgba(20, 184, 166, 0.12); color: var(--teal);
  padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.7rem; font-weight: 600;
}
.ai-analysis {
  background: rgba(20, 184, 166, 0.06); border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem 1.25rem;
}
.ai-label { font-size: 0.7rem; color: var(--teal); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem; }
.ai-text { font-size: 0.8rem; color: var(--text-muted); line-height: 1.6; }

/* SECTIONS */
section { padding: 5rem 2rem; }
.container { max-width: 1100px; margin: 0 auto; }
.section-label {
  font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--teal); margin-bottom: 0.75rem;
}
.section-title {
  font-size: clamp(1.8rem, 4vw, 2.75rem);
  font-weight: 800; line-height: 1.2; margin-bottom: 1rem;
  letter-spacing: -0.02em;
}
.section-sub { font-size: 1.1rem; color: var(--text-muted); max-width: 560px; line-height: 1.7; }

/* STATS */
.stats-section { background: var(--navy-mid); border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 2rem; text-align: center; }
.stat-num { font-size: 3rem; font-weight: 800; color: var(--teal); line-height: 1; margin-bottom: 0.4rem; }
.stat-label { font-size: 0.875rem; color: var(--text-muted); }

/* HOW IT WORKS */
.steps { display: flex; flex-direction: column; gap: 0; margin-top: 3rem; position: relative; }
.steps::before {
  content: ''; position: absolute; left: 24px; top: 40px; bottom: 40px;
  width: 2px; background: linear-gradient(to bottom, var(--teal), transparent);
}
.step { display: flex; gap: 2rem; padding: 2rem 0; position: relative; }
.step-num {
  width: 50px; height: 50px; border-radius: 50%; flex-shrink: 0;
  background: var(--teal); color: var(--navy);
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 1.1rem; position: relative; z-index: 1;
}
.step-content { padding-top: 0.5rem; }
.step-title { font-size: 1.15rem; font-weight: 700; margin-bottom: 0.4rem; }
.step-desc { font-size: 0.9rem; color: var(--text-muted); line-height: 1.6; max-width: 520px; }

/* FEATURES */
.features-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem; margin-top: 3rem; }
.feature-card {
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.75rem;
  transition: border-color 0.2s, transform 0.2s;
}
.feature-card:hover { border-color: var(--teal); transform: translateY(-2px); }
.feature-icon {
  width: 44px; height: 44px; border-radius: 10px;
  background: rgba(20, 184, 166, 0.12);
  display: flex; align-items: center; justify-content: center;
  font-size: 1.25rem; margin-bottom: 1rem;
}
.feature-title { font-weight: 700; margin-bottom: 0.5rem; }
.feature-desc { font-size: 0.875rem; color: var(--text-muted); line-height: 1.6; }

/* INTEGRATIONS */
.integrations-section { background: var(--navy-mid); }
.integrations-logos {
  display: flex; gap: 1.5rem; flex-wrap: wrap;
  align-items: center; justify-content: center; margin-top: 2.5rem;
}
.integration-badge {
  display: flex; align-items: center; gap: 0.75rem;
  background: var(--navy); border: 1px solid var(--border);
  border-radius: 10px; padding: 0.75rem 1.5rem;
  font-weight: 600; font-size: 0.95rem; transition: border-color 0.2s;
}
.integration-badge:hover { border-color: var(--teal); }
.integration-badge-icon { font-size: 1.4rem; }

/* PRICING */
.pricing-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.5rem; margin-top: 3rem; align-items: start; }
.pricing-card {
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: 16px; padding: 2rem; position: relative;
}
.pricing-card.featured { border-color: var(--teal); box-shadow: 0 0 40px rgba(20, 184, 166, 0.15); }
.pricing-popular {
  position: absolute; top: -12px; left: 50%; transform: translateX(-50%);
  background: var(--teal); color: var(--navy);
  padding: 0.2rem 0.9rem; border-radius: 999px;
  font-size: 0.75rem; font-weight: 700; white-space: nowrap;
}
.pricing-tier { font-size: 0.8rem; color: var(--teal); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.5rem; }
.pricing-price { font-size: 2.5rem; font-weight: 800; margin-bottom: 0.25rem; }
.pricing-price sup { font-size: 1.2rem; vertical-align: super; font-weight: 600; }
.pricing-price sub { font-size: 0.9rem; color: var(--text-muted); vertical-align: baseline; }
.pricing-desc { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 1.5rem; line-height: 1.5; }
.pricing-features { list-style: none; display: flex; flex-direction: column; gap: 0.65rem; margin-bottom: 2rem; }
.pricing-features li { display: flex; gap: 0.6rem; font-size: 0.875rem; align-items: flex-start; }
.check { color: var(--teal); flex-shrink: 0; margin-top: 1px; }
.pricing-btn {
  width: 100%; padding: 0.75rem; border-radius: 8px;
  font-weight: 600; font-size: 0.925rem; cursor: pointer;
  text-align: center; text-decoration: none; display: block; transition: all 0.2s;
}
.pricing-btn-outline { background: transparent; color: var(--text); border: 1px solid var(--navy-light); }
.pricing-btn-outline:hover { border-color: var(--teal); color: var(--teal); }
.pricing-btn-filled { background: var(--teal); color: var(--navy); border: none; }
.pricing-btn-filled:hover { background: var(--teal-dark); }

/* SOCIAL PROOF */
.quote-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; margin-top: 3rem; }
.quote-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.75rem; }
.quote-text { font-size: 0.95rem; line-height: 1.7; color: var(--text-muted); margin-bottom: 1.25rem; font-style: italic; }
.quote-author { display: flex; align-items: center; gap: 0.75rem; }
.quote-avatar {
  width: 38px; height: 38px; border-radius: 50%;
  background: var(--teal-dim); display: flex; align-items: center;
  justify-content: center; font-weight: 700; font-size: 0.85rem; color: var(--white);
}
.quote-name { font-weight: 600; font-size: 0.875rem; }
.quote-role { font-size: 0.75rem; color: var(--text-muted); }

/* CTA FOOTER */
.cta-section {
  text-align: center; padding: 6rem 2rem;
  background: linear-gradient(135deg, rgba(20, 184, 166, 0.06) 0%, transparent 60%);
  border-top: 1px solid var(--border);
}
.cta-title { font-size: clamp(2rem, 4vw, 3.5rem); font-weight: 800; margin-bottom: 1rem; line-height: 1.2; }
.cta-sub { font-size: 1.1rem; color: var(--text-muted); margin-bottom: 2.5rem; }

/* FOOTER */
footer {
  background: var(--navy-mid); border-top: 1px solid var(--border);
  padding: 2rem; text-align: center;
  font-size: 0.8rem; color: var(--text-muted);
}
footer a { color: var(--teal); text-decoration: none; }

@media (max-width: 768px) {
  .nav-links { display: none; }
  .steps::before { display: none; }
  .step { flex-direction: column; gap: 1rem; }

  .hero-mockup { padding: 1rem; margin-top: 2.5rem; }
  .mockup-title { display: none; }

  .incident-card {
    flex-direction: column;
    gap: 0.5rem;
  }
  .incident-severity { align-self: flex-start; }
  .incident-name { font-size: 0.85rem; }
  .incident-route { margin-left: 0; }
}
`;

export default function LandingPage() {
  const [demoOpen, setDemoOpen] = useState(false);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <BookDemoModal open={demoOpen} onClose={() => setDemoOpen(false)} />

      {/* NAV */}
      <nav>
        <a href="#" className="nav-logo">
          <div className="nav-logo-icon">O</div>
          <span className="nav-logo-text">OpsLens AI</span>
        </a>
        <ul className="nav-links">
          <li><a href="#how-it-works">How It Works</a></li>
          <li><a href="#features">Features</a></li>
          <li><a href="#integrations">Integrations</a></li>
        </ul>
        <div className="nav-right">
          <a href="/sign-in" className="nav-login">Log In</a>
          <button onClick={() => setDemoOpen(true)} className="nav-cta">Book a Demo</button>
        </div>
      </nav>


      {/* HERO */}
      <section className="hero">
        <div className="hero-badge">
          <span className="hero-badge-dot"></span>
          Now Live — Book a Demo
        </div>
        <h1>Stop Guessing.<br /><span>Start Resolving.</span></h1>
        <p className="hero-sub">
          OpsLens AI turns noisy logs into instant, AI-diagnosed incident briefs — automatically routed to the right team via Slack, with full context from Jira, GitHub, and your runbooks.
        </p>
        <div className="hero-actions">
          <button onClick={() => setDemoOpen(true)} className="btn-primary">Book a Demo →</button>
          <a href="#how-it-works" className="btn-secondary">See How It Works</a>
        </div>
        <div className="hero-mockup">
          <div className="mockup-bar">
            <div className="mockup-dot" style={{ background: '#EF4444' }}></div>
            <div className="mockup-dot" style={{ background: '#F59E0B' }}></div>
            <div className="mockup-dot" style={{ background: '#22C55E' }}></div>
            <span className="mockup-title">OpsLens AI — Incident Feed</span>
          </div>
          <div className="incident-card">
            <span className="incident-severity">P1 Critical</span>
            <div>
              <div className="incident-name">PaymentService — ConnectionPoolExhaustedException</div>
              <div className="incident-meta">payment-service · prod · 2 min ago · 847 occurrences</div>
            </div>
            <div className="incident-route">
              <span className="route-tag">→ #payment-team</span>
            </div>
          </div>
          <div className="ai-analysis">
            <div className="ai-label">⚡ AI Root Cause Analysis</div>
            <div className="ai-text">
              Connection pool exhausted after deploy <strong style={{ color: 'var(--text)' }}>feat/checkout-v2</strong> (PR #412, 18 min ago). Related Jira ticket <strong style={{ color: 'var(--teal)' }}>PAY-2041</strong> raised a similar issue in staging last week. Recommend rolling back or increasing pool size to 50. RRT brief dispatched to <strong style={{ color: 'var(--text)' }}>#payment-team</strong>.
            </div>
          </div>
        </div>
      </section>

      {/* STATS */}
      <section className="stats-section">
        <div className="container">
          <div className="stats-grid">
            <div><div className="stat-num">73%</div><div className="stat-label">of P1s take &gt;1 hour to diagnose</div></div>
            <div><div className="stat-num">30%</div><div className="stat-label">of engineer time spent on alert noise</div></div>
            <div><div className="stat-num">$1.5M</div><div className="stat-label">average annual cost of downtime</div></div>
            <div><div className="stat-num">2.4hrs</div><div className="stat-label">mean time to resolve without tooling</div></div>
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how-it-works">
        <div className="container">
          <div className="section-label">How It Works</div>
          <h2 className="section-title">From log line to Slack alert<br />in under 60 seconds</h2>
          <p className="section-sub">OpsLens AI runs a continuous intelligence loop so your team always knows what broke, why it broke, and who should fix it.</p>
          <div className="steps">
            {[
              ['Ingest Logs', 'Connect any log source — CloudWatch, Datadog, direct API, or our SDK. OpsLens ingests, normalizes, and indexes every log line in real time.'],
              ['Detect & Deduplicate', 'AI-powered error detection groups related log lines into incidents, eliminating duplicate noise before it ever hits your team\'s Slack.'],
              ['RAG Enrichment', 'Our retrieval engine pulls relevant Jira tickets, GitHub PRs, commit history, and Slack threads to build full context around each incident.'],
              ['AI Diagnosis', 'Claude and GPT-4 analyze the enriched context to generate a root cause hypothesis, severity assessment, and suggested remediation steps.'],
              ['Smart Routing', 'Rule-based routing sends the incident to the right Slack channel — payment team, infra team, or on-call — with zero false channels.'],
              ['RRT Brief Auto-Generated', 'A Rapid Response Triage brief is created automatically, giving your engineers a structured starting point the moment they engage.'],
            ].map(([title, desc], i) => (
              <div className="step" key={i}>
                <div className="step-num">{i + 1}</div>
                <div className="step-content">
                  <div className="step-title">{title}</div>
                  <div className="step-desc">{desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FEATURES */}
      <section id="features" style={{ background: 'var(--navy-mid)' }}>
        <div className="container">
          <div className="section-label">Features</div>
          <h2 className="section-title">Everything your on-call team needs</h2>
          <div className="features-grid">
            {[
              ['🔍', 'Log Intelligence', 'Ingest logs from any source. AI groups related errors, surfaces patterns, and identifies anomalies before they become outages.'],
              ['🧠', 'AI Root Cause Analysis', 'Every incident gets an AI-generated diagnosis with a root cause hypothesis, context from your codebase, and a suggested fix.'],
              ['📡', 'Smart Routing Rules', 'Define priority-based routing rules. The right team gets the alert — no more "who owns this?" in Slack at 3am.'],
              ['📋', 'Auto RRT Briefs', 'Rapid Response Triage briefs are generated instantly — structured docs your engineers can act on, not wall-of-text logs.'],
              ['🔗', 'Deep Integrations', 'Pulls context from Jira tickets, GitHub PRs, Slack threads, and your runbooks so no engineer starts from zero.'],
              ['🔒', 'Enterprise Ready', 'SOC 2 audit trail, RBAC, GDPR compliance, and SSO via SAML or LDAP. Built for security-conscious engineering orgs.'],
            ].map(([icon, title, desc]) => (
              <div className="feature-card" key={title}>
                <div className="feature-icon">{icon}</div>
                <div className="feature-title">{title}</div>
                <div className="feature-desc">{desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* INTEGRATIONS */}
      <section id="integrations" className="integrations-section">
        <div className="container" style={{ textAlign: 'center' }}>
          <div className="section-label">Integrations</div>
          <h2 className="section-title">Works with your existing stack</h2>
          <p className="section-sub" style={{ margin: '0 auto 0' }}>No rip-and-replace. OpsLens AI plugs into the tools your team already uses.</p>
          <div className="integrations-logos">
            {[['💬', 'Slack'], ['🐙', 'GitHub'], ['📌', 'Jira'], ['☁️', 'AWS CloudWatch'], ['📊', 'Datadog'], ['🔐', 'Okta SSO']].map(([icon, name]) => (
              <div className="integration-badge" key={name}>
                <span className="integration-badge-icon">{icon}</span> {name}
              </div>
            ))}
          </div>
          <p style={{ marginTop: '1.5rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            More integrations coming. <a href="#cta" style={{ color: 'var(--teal)' }}>Request yours →</a>
          </p>
        </div>
      </section>

      {/* PRICING — hidden until finalized */}

      {/* SOCIAL PROOF — hidden until real testimonials are ready */}

      {/* CTA */}
      <section className="cta-section" id="cta">
        <div className="container">
          <h2 className="cta-title">Ready to end alert fatigue?</h2>
          <p className="cta-sub">See OpsLens AI live in 30 minutes. We&apos;ll walk through your stack, your alerts, and how briefs would look for your team.</p>
          <div className="hero-actions" style={{ justifyContent: 'center' }}>
            <button onClick={() => setDemoOpen(true)} className="btn-primary">Book a Demo →</button>
            <a href="mailto:admin@opslensai.com" className="btn-secondary">Email Us Instead</a>
          </div>
          <p style={{ marginTop: '1.5rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            Questions? Reach us at <a href="mailto:admin@opslensai.com" style={{ color: 'var(--teal)' }}>admin@opslensai.com</a>
          </p>
        </div>
      </section>

      {/* FOOTER */}
      <footer>
        <p>© 2025 OpsLens AI. Built for engineering teams who ship fast. &nbsp;·&nbsp;
          <a href="#">Privacy</a> &nbsp;·&nbsp; <a href="#">Terms</a> &nbsp;·&nbsp;
          <a href="mailto:admin@opslensai.com">admin@opslensai.com</a>
        </p>
      </footer>
    </>
  );
}
