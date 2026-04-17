'use client';

import * as Clerk from '@clerk/elements/common';
import * as SignIn from '@clerk/elements/sign-in';

const styles = {
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
  },
  title: {
    fontSize: '1.4rem',
    fontWeight: 800,
    color: '#F1F5F9',
    marginBottom: '0.35rem',
  },
  subtitle: {
    fontSize: '0.875rem',
    color: '#94A3B8',
    marginBottom: '1.75rem',
  },
  fieldGroup: {
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
  },
  fieldError: {
    fontSize: '0.8rem',
    color: '#EF4444',
    marginTop: '0.35rem',
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
  forgotLink: {
    display: 'block',
    textAlign: 'right' as const,
    fontSize: '0.8rem',
    color: '#14B8A6',
    fontWeight: 600,
    marginTop: '-0.5rem',
    marginBottom: '1.25rem',
    cursor: 'pointer',
    background: 'none',
    border: 'none',
    padding: 0,
    fontFamily: 'inherit',
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
    transition: 'background 0.2s',
  },
  btnSecondary: {
    width: '100%',
    background: 'transparent',
    color: '#94A3B8',
    border: '1px solid #334155',
    borderRadius: '8px',
    padding: '0.75rem',
    fontWeight: 600,
    fontSize: '0.9rem',
    cursor: 'pointer',
    marginTop: '0.75rem',
    fontFamily: 'inherit',
  },
  divider: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.75rem',
    margin: '1.25rem 0',
  },
  dividerLine: {
    flex: 1,
    height: '1px',
    background: '#334155',
  },
  dividerText: {
    fontSize: '0.75rem',
    color: '#94A3B8',
  },
  socialBtn: {
    width: '100%',
    background: '#0F172A',
    border: '1px solid #334155',
    borderRadius: '8px',
    padding: '0.7rem',
    color: '#F1F5F9',
    fontSize: '0.9rem',
    fontWeight: 600,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '0.5rem',
    marginBottom: '0.5rem',
    fontFamily: 'inherit',
  },
  footer: {
    marginTop: '1.75rem',
    fontSize: '0.8rem',
    color: '#64748B',
    position: 'relative' as const,
    zIndex: 1,
    textAlign: 'center' as const,
  },
  footerLink: {
    color: '#14B8A6',
    textDecoration: 'none',
  },
  infoText: {
    fontSize: '0.85rem',
    color: '#94A3B8',
    marginBottom: '1.25rem',
    lineHeight: 1.6,
  },
};

export default function SignInPage() {
  return (
    <div style={styles.page}>
      <div style={styles.glow} />

      {/* Logo */}
      <a href="/" style={styles.logo}>
        <div style={styles.logoIcon}>O</div>
        <span style={styles.logoText}>OpsLens AI</span>
      </a>

      <SignIn.Root>

        {/* ── STEP 1: START ── */}
        <SignIn.Step name="start" style={styles.card}>
          <h2 style={styles.title}>Welcome back</h2>
          <p style={styles.subtitle}>Sign in to your OpsLens AI workspace</p>

          <Clerk.GlobalError style={styles.globalError} />

          {/* Social logins */}
          <Clerk.Connection name="google" style={styles.socialBtn}>
            <svg width="18" height="18" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
            Continue with Google
          </Clerk.Connection>

          <div style={styles.divider}>
            <div style={styles.dividerLine} />
            <span style={styles.dividerText}>or</span>
            <div style={styles.dividerLine} />
          </div>

          {/* Email field */}
          <Clerk.Field name="identifier" style={styles.fieldGroup}>
            <Clerk.Label style={styles.label}>Work Email</Clerk.Label>
            <Clerk.Input style={styles.input} placeholder="you@company.com" />
            <Clerk.FieldError style={styles.fieldError} />
          </Clerk.Field>

          {/* Password field */}
          <Clerk.Field name="password" style={styles.fieldGroup}>
            <Clerk.Label style={styles.label}>Password</Clerk.Label>
            <Clerk.Input type="password" style={styles.input} placeholder="••••••••" />
            <Clerk.FieldError style={styles.fieldError} />
          </Clerk.Field>

          {/* Forgot password */}
          <SignIn.Action navigate="forgot-password" style={styles.forgotLink}>
            Forgot password?
          </SignIn.Action>

          <SignIn.Action submit style={styles.btnPrimary}>
            Sign In →
          </SignIn.Action>
        </SignIn.Step>

        {/* ── STEP 2: VERIFICATIONS (OTP / code) ── */}
        <SignIn.Step name="verifications" style={styles.card}>
          <h2 style={styles.title}>Check your email</h2>
          <p style={styles.subtitle}>Enter the verification code we sent you</p>

          <Clerk.GlobalError style={styles.globalError} />

          <SignIn.Strategy name="email_code">
            <Clerk.Field name="code" style={styles.fieldGroup}>
              <Clerk.Label style={styles.label}>Verification Code</Clerk.Label>
              <Clerk.Input style={styles.input} placeholder="Enter code" />
              <Clerk.FieldError style={styles.fieldError} />
            </Clerk.Field>
            <SignIn.Action submit style={styles.btnPrimary}>Verify →</SignIn.Action>
            <SignIn.Action resend style={styles.btnSecondary}>Resend code</SignIn.Action>
          </SignIn.Strategy>

          <SignIn.Strategy name="password">
            <Clerk.Field name="password" style={styles.fieldGroup}>
              <Clerk.Label style={styles.label}>Password</Clerk.Label>
              <Clerk.Input type="password" style={styles.input} placeholder="••••••••" />
              <Clerk.FieldError style={styles.fieldError} />
            </Clerk.Field>
            <SignIn.Action navigate="forgot-password" style={styles.forgotLink}>
              Forgot password?
            </SignIn.Action>
            <SignIn.Action submit style={styles.btnPrimary}>Sign In →</SignIn.Action>
          </SignIn.Strategy>
        </SignIn.Step>

        {/* ── STEP 3: FORGOT PASSWORD ── */}
        <SignIn.Step name="forgot-password" style={styles.card}>
          <h2 style={styles.title}>Reset your password</h2>
          <p style={styles.infoText}>
            Enter your email address and we&apos;ll send you a link to reset your password.
          </p>

          <Clerk.GlobalError style={styles.globalError} />

          <Clerk.Field name="identifier" style={styles.fieldGroup}>
            <Clerk.Label style={styles.label}>Work Email</Clerk.Label>
            <Clerk.Input style={styles.input} placeholder="you@company.com" />
            <Clerk.FieldError style={styles.fieldError} />
          </Clerk.Field>

          <SignIn.Action submit style={styles.btnPrimary}>
            Send Reset Link →
          </SignIn.Action>
          <SignIn.Action navigate="previous" style={styles.btnSecondary}>
            ← Back to Sign In
          </SignIn.Action>
        </SignIn.Step>

        {/* ── STEP 4: RESET PASSWORD ── */}
        <SignIn.Step name="reset-password" style={styles.card}>
          <h2 style={styles.title}>Choose a new password</h2>
          <p style={styles.subtitle}>Make sure it&apos;s at least 8 characters</p>

          <Clerk.GlobalError style={styles.globalError} />

          <Clerk.Field name="password" style={styles.fieldGroup}>
            <Clerk.Label style={styles.label}>New Password</Clerk.Label>
            <Clerk.Input type="password" style={styles.input} placeholder="••••••••" />
            <Clerk.FieldError style={styles.fieldError} />
          </Clerk.Field>

          <Clerk.Field name="confirmPassword" style={styles.fieldGroup}>
            <Clerk.Label style={styles.label}>Confirm Password</Clerk.Label>
            <Clerk.Input type="password" style={styles.input} placeholder="••••••••" />
            <Clerk.FieldError style={styles.fieldError} />
          </Clerk.Field>

          <SignIn.Action submit style={styles.btnPrimary}>
            Reset Password →
          </SignIn.Action>
        </SignIn.Step>

      </SignIn.Root>

      <p style={styles.footer}>
        © 2025 OpsLens AI &nbsp;·&nbsp; <a href="/" style={styles.footerLink}>Back to home</a>
      </p>
    </div>
  );
}
