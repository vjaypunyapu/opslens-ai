'use client';

import { useState } from 'react';
import BookDemoModal from '../_components/BookDemoModal';

const CSS = `
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --teal: #14B8A6;
  --teal-dark: #0D9488;
  --navy: #0F172A;
  --navy-mid: #1E293B;
  --navy-light: #334155;
  --slate: #64748B;
  --text: #F1F5F9;
  --text-muted: #94A3B8;
  --border: rgba(20, 184, 166, 0.15);
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--navy);
  color: var(--text);
  line-height: 1.6;
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

/* PAGE */
.page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 7rem 2rem 5rem;
}

.page-badge {
  display: inline-flex; align-items: center; gap: 0.5rem;
  background: rgba(20, 184, 166, 0.12); border: 1px solid rgba(20, 184, 166, 0.3);
  color: var(--teal); padding: 0.35rem 1rem;
  border-radius: 999px; font-size: 0.75rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  margin-bottom: 1.25rem;
}
.page-title {
  font-size: clamp(2rem, 5vw, 3.25rem);
  font-weight: 800; line-height: 1.15;
  letter-spacing: -0.02em;
  margin-bottom: 1rem;
}
.page-title span { color: var(--teal); }
.page-sub {
  font-size: 1.1rem; color: var(--text-muted);
  max-width: 560px; line-height: 1.7;
  margin-bottom: 3.5rem;
}

/* COMPARISON TABLE */
.compare-wrap {
  overflow-x: auto;
  border-radius: 16px;
  border: 1px solid var(--border);
  margin-bottom: 4rem;
}
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 640px;
}
thead tr {
  background: var(--navy-mid);
  border-bottom: 1px solid var(--border);
}
th {
  padding: 1.1rem 1.25rem;
  text-align: left;
  font-size: 0.8rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
}
th.col-opslens {
  color: var(--teal);
  background: rgba(20, 184, 166, 0.06);
  border-left: 2px solid var(--teal);
  border-right: 2px solid rgba(20, 184, 166, 0.2);
}
th:first-child { color: var(--text); font-size: 0.875rem; }

tbody tr {
  border-bottom: 1px solid rgba(255,255,255,0.05);
  transition: background 0.15s;
}
tbody tr:last-child { border-bottom: none; }
tbody tr:hover { background: rgba(255,255,255,0.02); }

td {
  padding: 1rem 1.25rem;
  font-size: 0.9rem;
  color: var(--text-muted);
  vertical-align: middle;
}
td:first-child {
  color: var(--text);
  font-weight: 500;
}
td.col-opslens {
  background: rgba(20, 184, 166, 0.04);
  border-left: 2px solid var(--teal);
  border-right: 2px solid rgba(20, 184, 166, 0.2);
  color: var(--text);
  font-weight: 500;
}

.check { color: #22C55E; font-size: 1.1rem; }
.cross { color: #475569; font-size: 1.1rem; }
.partial { color: #F59E0B; font-size: 0.8rem; font-weight: 600; }

/* WHY SECTION */
.why-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.25rem;
  margin-bottom: 4rem;
}
.why-card {
  background: var(--navy-mid);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
}
.why-icon { font-size: 1.5rem; margin-bottom: 0.75rem; }
.why-title { font-size: 1rem; font-weight: 700; color: var(--text); margin-bottom: 0.4rem; }
.why-desc { font-size: 0.875rem; color: var(--text-muted); line-height: 1.6; }

/* CTA */
.cta-box {
  background: var(--navy-mid);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 3rem 2rem;
  text-align: center;
}
.cta-title { font-size: 1.75rem; font-weight: 800; margin-bottom: 0.75rem; }
.cta-sub { color: var(--text-muted); margin-bottom: 2rem; font-size: 0.95rem; }
.btn-primary {
  display: inline-block;
  background: var(--teal); color: var(--navy);
  border: none; padding: 0.75rem 2rem;
  border-radius: 8px; font-weight: 700; font-size: 1rem;
  cursor: pointer; text-decoration: none; transition: background 0.2s;
}
.btn-primary:hover { background: var(--teal-dark); }

/* FOOTER */
footer {
  border-top: 1px solid var(--border);
  padding: 2rem;
  text-align: center;
  font-size: 0.8rem;
  color: var(--text-muted);
}
footer a { color: var(--teal); text-decoration: none; }

@media (max-width: 768px) {
  .nav-links { display: none; }
  .page { padding: 6rem 1.25rem 4rem; }
  .cta-box { padding: 2rem 1.25rem; }
}
`;

const rows = [
  {
    feature: 'AI Root Cause Analysis',
    opslens: '✅',
    traditional: '❌',
    observability: '⚠️ Partial',
    note: 'Most tools alert you — OpsLens tells you why it broke.',
  },
  {
    feature: 'Auto-generated RRT Briefs',
    opslens: '✅',
    traditional: '❌',
    observability: '❌',
    note: null,
  },
  {
    feature: 'Jira & GitHub context enrichment',
    opslens: '✅',
    traditional: '❌',
    observability: '❌',
    note: null,
  },
  {
    feature: 'Slack alert routing',
    opslens: '✅',
    traditional: '✅',
    observability: '⚠️ Partial',
    note: null,
  },
  {
    feature: 'Log ingestion & deduplication',
    opslens: '✅',
    traditional: '❌',
    observability: '✅',
    note: null,
  },
  {
    feature: 'Alert noise reduction',
    opslens: '✅',
    traditional: '⚠️ Partial',
    observability: '⚠️ Partial',
    note: null,
  },
  {
    feature: 'Runbook / knowledge base RAG',
    opslens: '✅',
    traditional: '❌',
    observability: '❌',
    note: null,
  },
  {
    feature: 'Built for small engineering teams',
    opslens: '✅',
    traditional: '❌',
    observability: '❌',
    note: 'Enterprise tools carry enterprise complexity and price tags.',
  },
  {
    feature: 'Setup time',
    opslens: 'Minutes',
    traditional: 'Days',
    observability: 'Weeks',
    note: null,
  },
  {
    feature: 'On-call scheduling',
    opslens: '❌',
    traditional: '✅',
    observability: '❌',
    note: 'We focus on diagnosis, not scheduling — integrates with your existing on-call tool.',
  },
];

function Cell({ value }: { value: string }) {
  if (value === '✅') return <span className="check">✅</span>;
  if (value === '❌') return <span className="cross">—</span>;
  if (value.startsWith('⚠️')) return <span className="partial">{value.replace('⚠️ ', '')}*</span>;
  return <>{value}</>;
}

export default function ComparePage() {
  const [demoOpen, setDemoOpen] = useState(false);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <BookDemoModal open={demoOpen} onClose={() => setDemoOpen(false)} />

      <nav>
        <a href="/" className="nav-logo">
          <div className="nav-logo-icon">O</div>
          <span className="nav-logo-text">OpsLens AI</span>
        </a>
        <div className="nav-right">
          <a href="/sign-in" className="nav-login">Log In</a>
          <button onClick={() => setDemoOpen(true)} className="nav-cta">Book a Demo</button>
        </div>
      </nav>

      <div className="page">
        <div className="page-badge">Why OpsLens AI</div>
        <h1 className="page-title">
          Built for how modern<br />
          <span>engineering teams actually work</span>
        </h1>
        <p className="page-sub">
          Traditional alerting tools tell you something broke. OpsLens AI tells you why it broke,
          who should fix it, and hands your team a brief before they even open Slack.
        </p>

        {/* TABLE */}
        <div className="compare-wrap">
          <table>
            <thead>
              <tr>
                <th>Feature</th>
                <th className="col-opslens">OpsLens AI</th>
                <th>Traditional Alert Tools</th>
                <th>Observability Platforms</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.feature}>
                  <td>
                    {row.feature}
                    {row.note && (
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.2rem', fontWeight: 400 }}>
                        {row.note}
                      </div>
                    )}
                  </td>
                  <td className="col-opslens"><Cell value={row.opslens} /></td>
                  <td><Cell value={row.traditional} /></td>
                  <td><Cell value={row.observability} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '-3rem', marginBottom: '3.5rem' }}>
          * Partial support — available but limited or requires significant configuration.
        </p>

        {/* WHY CARDS */}
        <div style={{ marginBottom: '1.5rem' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--teal)', marginBottom: '0.75rem' }}>
            The OpsLens Difference
          </div>
          <h2 style={{ fontSize: 'clamp(1.5rem, 3vw, 2rem)', fontWeight: 800, marginBottom: '2rem', letterSpacing: '-0.02em' }}>
            What you get that others don&apos;t
          </h2>
        </div>
        <div className="why-grid">
          {[
            ['🧠', 'Diagnosis, not just detection', 'Every incident comes with an AI-generated root cause hypothesis, pulled from your logs, recent deploys, and related Jira tickets — before your engineer even looks at it.'],
            ['📋', 'RRT Briefs on autopilot', 'Rapid Response Triage docs are auto-generated the moment an incident fires — structured, actionable, and ready for your team to pick up instantly.'],
            ['🔗', 'Context from your entire stack', 'OpsLens pulls in GitHub PRs, Jira tickets, Slack threads, and your runbooks so no engineer ever starts from zero at 3am.'],
            ['⚡', 'Up in minutes, not weeks', 'No complex instrumentation. Connect your log source, set up your routing rules, and you\'re live. Built for lean teams without a dedicated observability engineer.'],
          ].map(([icon, title, desc]) => (
            <div className="why-card" key={title as string}>
              <div className="why-icon">{icon}</div>
              <div className="why-title">{title}</div>
              <div className="why-desc">{desc}</div>
            </div>
          ))}
        </div>

        {/* CTA */}
        <div className="cta-box">
          <div className="cta-title">See it live in 30 minutes</div>
          <div className="cta-sub">
            We&apos;ll walk through your stack and show exactly what OpsLens AI would look like for your team.
          </div>
          <button onClick={() => setDemoOpen(true)} className="btn-primary">Book a Demo →</button>
        </div>
      </div>

      <footer>
        <p>© 2025 OpsLens AI &nbsp;·&nbsp; <a href="/">Home</a> &nbsp;·&nbsp; <a href="mailto:admin@opslensai.com">admin@opslensai.com</a></p>
      </footer>
    </>
  );
}
