'use client';

import { useState, useEffect, useRef } from 'react';

const MODAL_CSS = `
.demo-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  padding: 1rem;
  animation: fadeIn 0.15s ease;
}
@keyframes fadeIn { from { opacity: 0 } to { opacity: 1 } }

.demo-modal {
  background: #1E293B;
  border: 1px solid rgba(20, 184, 166, 0.2);
  border-radius: 16px;
  padding: 2rem;
  width: 100%; max-width: 480px;
  position: relative;
  animation: slideUp 0.2s ease;
  max-height: 90vh;
  overflow-y: auto;
}
@keyframes slideUp { from { transform: translateY(16px); opacity: 0 } to { transform: translateY(0); opacity: 1 } }

.demo-modal-close {
  position: absolute; top: 1rem; right: 1rem;
  background: transparent; border: none;
  color: #64748B; font-size: 1.25rem;
  cursor: pointer; line-height: 1;
  padding: 0.25rem 0.5rem;
  border-radius: 6px;
  transition: color 0.15s, background 0.15s;
}
.demo-modal-close:hover { color: #F1F5F9; background: rgba(255,255,255,0.06); }

.demo-modal-header { margin-bottom: 1.5rem; }
.demo-modal-badge {
  display: inline-flex; align-items: center; gap: 0.4rem;
  background: rgba(20, 184, 166, 0.12); border: 1px solid rgba(20, 184, 166, 0.3);
  color: #14B8A6; padding: 0.25rem 0.75rem;
  border-radius: 999px; font-size: 0.75rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  margin-bottom: 0.75rem;
}
.demo-modal-title { font-size: 1.4rem; font-weight: 700; color: #F1F5F9; margin-bottom: 0.35rem; }
.demo-modal-sub { font-size: 0.875rem; color: #94A3B8; }

.demo-form { display: flex; flex-direction: column; gap: 1rem; }

.demo-field { display: flex; flex-direction: column; gap: 0.35rem; }
.demo-label { font-size: 0.8rem; font-weight: 600; color: #94A3B8; text-transform: uppercase; letter-spacing: 0.04em; }
.demo-label span { color: #14B8A6; }

.demo-input, .demo-select, .demo-textarea {
  background: #0F172A;
  border: 1px solid rgba(100, 116, 139, 0.3);
  border-radius: 8px;
  color: #F1F5F9;
  font-size: 0.9rem;
  padding: 0.6rem 0.875rem;
  width: 100%;
  outline: none;
  transition: border-color 0.15s;
  font-family: inherit;
}
.demo-input:focus, .demo-select:focus, .demo-textarea:focus {
  border-color: #14B8A6;
  box-shadow: 0 0 0 3px rgba(20, 184, 166, 0.12);
}
.demo-input::placeholder, .demo-textarea::placeholder { color: #475569; }
.demo-select { appearance: none; cursor: pointer; }
.demo-select option { background: #1E293B; }
.demo-textarea { resize: vertical; min-height: 80px; }
.demo-input[type="date"]::-webkit-calendar-picker-indicator {
  filter: invert(0.6) sepia(1) saturate(3) hue-rotate(130deg);
  cursor: pointer;
}

.demo-field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }

.demo-submit {
  background: #14B8A6; color: #0F172A;
  border: none; border-radius: 8px;
  padding: 0.7rem 1.5rem;
  font-size: 0.95rem; font-weight: 700;
  cursor: pointer;
  transition: background 0.2s, opacity 0.2s;
  margin-top: 0.25rem;
}
.demo-submit:hover:not(:disabled) { background: #0D9488; }
.demo-submit:disabled { opacity: 0.55; cursor: not-allowed; }

.demo-success {
  text-align: center;
  padding: 1.5rem 0;
}
.demo-success-icon {
  font-size: 2.5rem; margin-bottom: 0.75rem;
}
.demo-success-title { font-size: 1.25rem; font-weight: 700; color: #F1F5F9; margin-bottom: 0.5rem; }
.demo-success-sub { font-size: 0.875rem; color: #94A3B8; }

.demo-error {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #FCA5A5;
  border-radius: 8px;
  padding: 0.6rem 0.875rem;
  font-size: 0.85rem;
}
`;

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function BookDemoModal({ open, onClose }: Props) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [company, setCompany] = useState('');
  const [role, setRole] = useState('');
  const [teamSize, setTeamSize] = useState('');
  const [preferredDate, setPreferredDate] = useState('');
  const [message, setMessage] = useState('');

  const today = new Date().toISOString().split('T')[0];
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Reset form when re-opened
  useEffect(() => {
    if (open) {
      setSuccess(false);
      setError('');
    }
  }, [open]);

  if (!open) return null;

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch('/api/book-demo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, company, role, teamSize, preferredDate, message }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Something went wrong.');
      setSuccess(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: MODAL_CSS }} />
      <div className="demo-overlay" ref={overlayRef} onClick={handleOverlayClick}>
        <div className="demo-modal" role="dialog" aria-modal="true" aria-labelledby="demo-modal-title">
          <button className="demo-modal-close" onClick={onClose} aria-label="Close">✕</button>

          {success ? (
            <div className="demo-success">
              <div className="demo-success-icon">✅</div>
              <div className="demo-success-title">Request Received!</div>
              <div className="demo-success-sub">
                Thanks, {name.split(' ')[0]}! We&apos;ll reach out to{' '}
                <strong style={{ color: '#14B8A6' }}>{email}</strong> within one business day
                to schedule your demo.
              </div>
            </div>
          ) : (
            <>
              <div className="demo-modal-header">
                <div className="demo-modal-badge">
                  <span style={{ width: 6, height: 6, background: '#14B8A6', borderRadius: '50%', display: 'inline-block' }}></span>
                  30-Minute Live Demo
                </div>
                <div className="demo-modal-title" id="demo-modal-title">Book Your Demo</div>
                <div className="demo-modal-sub">
                  We&apos;ll walk through your stack and show exactly how OpsLens AI would look for your team.
                </div>
              </div>

              <form className="demo-form" onSubmit={handleSubmit} noValidate>
                <div className="demo-field-row">
                  <div className="demo-field">
                    <label className="demo-label" htmlFor="demo-name">Full Name <span>*</span></label>
                    <input
                      id="demo-name"
                      className="demo-input"
                      type="text"
                      placeholder="Jane Smith"
                      value={name}
                      onChange={e => setName(e.target.value)}
                      required
                      autoFocus
                    />
                  </div>
                  <div className="demo-field">
                    <label className="demo-label" htmlFor="demo-company">Company <span>*</span></label>
                    <input
                      id="demo-company"
                      className="demo-input"
                      type="text"
                      placeholder="Acme Corp"
                      value={company}
                      onChange={e => setCompany(e.target.value)}
                      required
                    />
                  </div>
                </div>

                <div className="demo-field">
                  <label className="demo-label" htmlFor="demo-email">Work Email <span>*</span></label>
                  <input
                    id="demo-email"
                    className="demo-input"
                    type="email"
                    placeholder="jane@acmecorp.com"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    required
                  />
                </div>

                <div className="demo-field-row">
                  <div className="demo-field">
                    <label className="demo-label" htmlFor="demo-role">Your Role</label>
                    <input
                      id="demo-role"
                      className="demo-input"
                      type="text"
                      placeholder="Engineering Manager"
                      value={role}
                      onChange={e => setRole(e.target.value)}
                    />
                  </div>
                  <div className="demo-field">
                    <label className="demo-label" htmlFor="demo-team-size">Team Size</label>
                    <select
                      id="demo-team-size"
                      className="demo-select"
                      value={teamSize}
                      onChange={e => setTeamSize(e.target.value)}
                    >
                      <option value="">Select…</option>
                      <option value="1-10">1–10 engineers</option>
                      <option value="11-50">11–50 engineers</option>
                      <option value="51-200">51–200 engineers</option>
                      <option value="201+">200+ engineers</option>
                    </select>
                  </div>
                </div>

                <div className="demo-field">
                  <label className="demo-label" htmlFor="demo-date">Preferred Date</label>
                  <input
                    id="demo-date"
                    className="demo-input"
                    type="date"
                    min={today}
                    value={preferredDate}
                    onChange={e => setPreferredDate(e.target.value)}
                  />
                </div>

                <div className="demo-field">
                  <label className="demo-label" htmlFor="demo-message">Anything you&apos;d like us to know?</label>
                  <textarea
                    id="demo-message"
                    className="demo-textarea"
                    placeholder="Tell us about your current alerting setup, pain points, or specific questions…"
                    value={message}
                    onChange={e => setMessage(e.target.value)}
                    rows={3}
                  />
                </div>

                {error && <div className="demo-error">{error}</div>}

                <button
                  type="submit"
                  className="demo-submit"
                  disabled={loading || !name.trim() || !email.trim() || !company.trim()}
                >
                  {loading ? 'Sending…' : 'Request Demo →'}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </>
  );
}
