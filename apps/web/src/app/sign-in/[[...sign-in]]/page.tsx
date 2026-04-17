'use client';

import * as Clerk from '@clerk/elements/common';
import * as SignIn from '@clerk/elements/sign-in';

const c = {
  page: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    justifyContent: 'center',
    background: '#0F172A',
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    position: 'relative' as const,
    overflow: 'hidden',
    padding: '2rem',
  },
  glow: {
    position: 'absolute' as const,
    top: '-20%',
    left: '50%',
    transform: 'translateX(-50%)',
    width: '700px',
    height: '700px',
    background: 'radial-gradient(circle, rgba(20,184,166,0.12) 0%, transparent 65%)',
    pointerEvents: 'none' as const,
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.6rem',
    marginBottom: '1.75rem',
    position: 'relative' as const,
    zIndex: 1,
    textDecoration: 'none',
  },
  logoIcon: {
    width: '36px',
    height: '36px',
    background: '#14B8A6',
    borderRadius: '9px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: 800,
    fontSize: '18px',
    color: '#0F172A',
  },
  logoText: {
    fontSize: '1.2rem',
    fontWeight: 700,
    color: '#F1F5F9',
  },
  card: {
    background: '#1E293B',
    border: '1px solid rgba(20,184,166,0.15)',
    boxShadow: '0 40px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(20,184,166,0.08)',
    borderRadius: '16px',
    padding: '2.25rem',
    width: '100%',
    maxWidth: '420px',
    position: 'relative' as const,
    zIndex: 1,
    boxSizing: 'border-box' as const,
  },
  title: {
    fontSize: '1.4rem',
    fontWeight: 800,
    color: '#F1F5F9',
    marginBottom: '0.35rem',
    margin: '0 0 0.35rem',
  },
  subtitle: {
    fontSize: '0.875rem',
    color: '#94A3B8',
    marginBottom: '1.75rem',
    margin: '0 0 1.75rem',
  },
  field: {
    marginBottom: '1rem',
  },
  label: {
    display: 'block',
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#94A3B8',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
    marginBottom: '0.4rem',
  },
  input: {
    width: '100%',
    background: '#0F172A',
    border: '1px solid #334155',
    borderRadius: '8px',
    padding: '0.7rem 0.9rem',
    color: '#F1F5F9',
    fontSize: '0.925rem',
    outline: 'none',
    fontFamily: 'inherit',
    boxSizing: 'border-box' as const,
    display: 'block',
  },
  error: {
    fontSize: '0.8rem',
    color: '#EF4444',
    marginTop: '0.3rem',
  },
  globalError: {
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: '8px',
    padding: '0.6rem 0.9rem',
    fontSize: '0.85rem',
    color: '#EF4444',
    marginBottom: '1rem',
  },
  btnPrimary: {
    width: '100%',
    background: '#14B8A6',
    color: '#0F172A',
    border: 'none',
    borderRadius: '8px',
    padding: '0.85rem',
    fontWeight: 700,
    fontSize: '1rem',
    cursor: 'pointer',
    marginTop: '0.5rem',
    boxShadow: '0 0 20px rgba(20,184,166,0.25)',
    fontFamily: 'inherit',
    display: 'block',
    textAlign: 'center' as const,
    boxSizing: 'border-box' as const,
  },
  btnGhost: {
    width: '100%',
    background: 'transparent',
    color: '#94A3B8',
    border: '1px solid #334155',
    borderRadius: '8px',
    padding: '0.75rem',
    fontWeight: 600,
    fontSize: '0.9rem',
    cursor: 'pointer',
    marginTop: '0.65rem',
    fontFamily: 'inherit',
    display: 'block',
    textAlign: 'center' as const,
    boxSizing: 'border-box' as const,
  },
  forgotBtn: {
    background: 'none',
    border: 'none',
    padding: 0,
    color: '#14B8A6',
    fontWeight: 600,
    fontSize: '0.8rem',
    cursor: 'pointer',
    fontFamily: 'inherit',
    display: 'block',
    marginLeft: 'auto',
    marginBottom: '1.25rem',
  },
  infoText: {
    fontSize: '0.875rem',
    color: '#94A3B8',
    lineHeight: 1.6,
    marginBottom: '1.5rem',
  },
  footer: {
    marginTop: '1.75rem',
    fontSize: '0.8rem',
    color: '#64748B',
    position: 'relative' as const,
    zIndex: 1,
    textAlign: 'center' as const,
  },
};

export default function SignInPage() {
  return (
    <div style={c.page}>
      <div style={c.glow} />

      {/* Logo */}
      <a href="/" style={c.logo}>
        <div style={c.logoIcon}>O</div>
        <span style={c.logoText}>OpsLens AI</span>
      </a>

      <SignIn.Root>

        {/* ── START ── */}
        <SignIn.Step name="start">
          <div style={c.card}>
            <h2 style={c.title}>Welcome back</h2>
            <p style={c.subtitle}>Sign in to your OpsLens AI workspace</p>

            <Clerk.GlobalError style={c.globalError} />

            <Clerk.Field name="identifier" style={c.field}>
              <Clerk.Label style={c.label}>Work Email</Clerk.Label>
              <Clerk.Input style={c.input} placeholder="you@company.com" />
              <Clerk.FieldError style={c.error} />
            </Clerk.Field>

            <Clerk.Field name="password" style={c.field}>
              <Clerk.Label style={c.label}>Password</Clerk.Label>
              <Clerk.Input type="password" style={c.input} placeholder="••••••••" />
              <Clerk.FieldError style={c.error} />
            </Clerk.Field>

            <SignIn.Action navigate="forgot-password" style={c.forgotBtn}>
              Forgot password?
            </SignIn.Action>

            <SignIn.Action submit style={c.btnPrimary}>
              Sign In →
            </SignIn.Action>
          </div>
        </SignIn.Step>

        {/* ── VERIFICATIONS ── */}
        <SignIn.Step name="verifications">
          <div style={c.card}>
            <h2 style={c.title}>Verify your identity</h2>
            <p style={c.subtitle}>Enter the code sent to your email</p>

            <Clerk.GlobalError style={c.globalError} />

            <SignIn.Strategy name="email_code">
              <Clerk.Field name="code" style={c.field}>
                <Clerk.Label style={c.label}>Verification Code</Clerk.Label>
                <Clerk.Input style={c.input} placeholder="Enter code" />
                <Clerk.FieldError style={c.error} />
              </Clerk.Field>
              <SignIn.Action submit style={c.btnPrimary}>Verify →</SignIn.Action>
              <SignIn.Action resend style={c.btnGhost}>Resend code</SignIn.Action>
            </SignIn.Strategy>

            <SignIn.Strategy name="password">
              <Clerk.Field name="password" style={c.field}>
                <Clerk.Label style={c.label}>Password</Clerk.Label>
                <Clerk.Input type="password" style={c.input} placeholder="••••••••" />
                <Clerk.FieldError style={c.error} />
              </Clerk.Field>
              <SignIn.Action navigate="forgot-password" style={c.forgotBtn}>
                Forgot password?
              </SignIn.Action>
              <SignIn.Action submit style={c.btnPrimary}>Sign In →</SignIn.Action>
            </SignIn.Strategy>
          </div>
        </SignIn.Step>

        {/* ── FORGOT PASSWORD ── */}
        <SignIn.Step name="forgot-password">
          <div style={c.card}>
            <h2 style={c.title}>Reset your password</h2>
            <p style={c.infoText}>
              Enter your email and we&apos;ll send you a link to reset your password.
            </p>

            <Clerk.GlobalError style={c.globalError} />

            <Clerk.Field name="identifier" style={c.field}>
              <Clerk.Label style={c.label}>Work Email</Clerk.Label>
              <Clerk.Input style={c.input} placeholder="you@company.com" />
              <Clerk.FieldError style={c.error} />
            </Clerk.Field>

            <SignIn.Action submit style={c.btnPrimary}>Send Reset Link →</SignIn.Action>
            <SignIn.Action navigate="previous" style={c.btnGhost}>← Back to Sign In</SignIn.Action>
          </div>
        </SignIn.Step>

        {/* ── RESET PASSWORD ── */}
        <SignIn.Step name="reset-password">
          <div style={c.card}>
            <h2 style={c.title}>Choose a new password</h2>
            <p style={c.subtitle}>Must be at least 8 characters</p>

            <Clerk.GlobalError style={c.globalError} />

            <Clerk.Field name="password" style={c.field}>
              <Clerk.Label style={c.label}>New Password</Clerk.Label>
              <Clerk.Input type="password" style={c.input} placeholder="••••••••" />
              <Clerk.FieldError style={c.error} />
            </Clerk.Field>

            <Clerk.Field name="confirmPassword" style={c.field}>
              <Clerk.Label style={c.label}>Confirm Password</Clerk.Label>
              <Clerk.Input type="password" style={c.input} placeholder="••••••••" />
              <Clerk.FieldError style={c.error} />
            </Clerk.Field>

            <SignIn.Action submit style={c.btnPrimary}>Reset Password →</SignIn.Action>
          </div>
        </SignIn.Step>

      </SignIn.Root>

      <p style={c.footer}>
        © 2025 OpsLens AI &nbsp;·&nbsp;
        <a href="/" style={{ color: '#14B8A6', textDecoration: 'none' }}>Back to home</a>
      </p>
    </div>
  );
}
