"use client";

import { SignUp } from "@clerk/nextjs";
import { useSearchParams, usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Suspense } from "react";

const INVITE_TOKEN_KEY = "opslens_invite_token";

/**
 * Sign-up is invite-only.
 *
 * Flow:
 * 1. User lands on /sign-up?token=<id>  → token saved to sessionStorage
 * 2. Clerk moves user to /sign-up/verify-email-address (token gone from URL)
 * 3. We read token back from sessionStorage → forceRedirectUrl stays correct
 * 4. After verification, Clerk redirects to /join?token=<id>
 * 5. /join redeems the invite and sends user to /chat
 *
 * Without sessionStorage the token is lost at step 2, forceRedirectUrl
 * becomes undefined, and Clerk falls back to /chat — skipping /join entirely
 * so the invite is never redeemed and the user has no workspace access.
 */
function SignUpInner() {
  const params   = useSearchParams();
  const pathname = usePathname();
  const urlToken = params.get("token");

  // Resolved token: URL param (first visit) or sessionStorage (mid-flow steps)
  const [token, setToken] = useState<string | null>(urlToken);

  useEffect(() => {
    if (urlToken) {
      // Fresh invite link — persist token for the multi-step Clerk flow
      sessionStorage.setItem(INVITE_TOKEN_KEY, urlToken);
      setToken(urlToken);
    } else {
      // Sub-path (verify-email, continue, etc.) — recover from sessionStorage
      const stored = sessionStorage.getItem(INVITE_TOKEN_KEY);
      setToken(stored);
    }
  }, [urlToken]);

  const isSubPath = pathname !== "/sign-up";

  // No token and not mid-flow → show the invite-only gate
  if (!token && !isSubPath) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-brand-navy">
        <div className="flex flex-col items-center gap-6 text-center max-w-sm px-6">
          <div className="text-white">
            <h1 className="text-2xl font-bold">OpsLens AI</h1>
            <p className="text-white/60 text-sm mt-1">Operational Intelligence Copilot</p>
          </div>
          <div className="bg-white/10 border border-white/20 rounded-xl p-6 text-white space-y-3">
            <div className="text-4xl">🔒</div>
            <h2 className="font-semibold text-lg">Access by invitation only</h2>
            <p className="text-white/70 text-sm leading-relaxed">
              OpsLens AI workspaces are invite-only. Ask your workspace admin
              to send you an invite link, then follow the link in your email.
            </p>
          </div>
          <Link href="/sign-in" className="text-white/60 text-sm hover:text-white transition-colors">
            Already have an account? Sign in →
          </Link>
        </div>
      </div>
    );
  }

  // Token available (from URL or sessionStorage), or user is mid-flow.
  // Always provide forceRedirectUrl so Clerk never falls back to /chat.
  const redirectUrl = token ? `/join?token=${token}` : "/chat";

  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-navy">
      <div className="flex flex-col items-center gap-6">
        <div className="text-center text-white">
          <h1 className="text-2xl font-bold">OpsLens AI</h1>
          <p className="text-white/60 text-sm mt-1">Create your account</p>
        </div>
        <SignUp
          forceRedirectUrl={redirectUrl}
          fallbackRedirectUrl={redirectUrl}
        />
      </div>
    </div>
  );
}

export default function SignUpPage() {
  return (
    <Suspense>
      <SignUpInner />
    </Suspense>
  );
}
