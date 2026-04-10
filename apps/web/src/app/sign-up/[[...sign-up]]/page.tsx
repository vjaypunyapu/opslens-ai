"use client";

import { SignUp } from "@clerk/nextjs";
import { useSearchParams, usePathname } from "next/navigation";
import Link from "next/link";
import { Suspense } from "react";

/**
 * Sign-up is invite-only.
 *
 * - If the URL contains ?token=<invite_id>, show the Clerk sign-up flow.
 *   Clerk stores its own flow state, so sub-paths like /sign-up/verify-email-address
 *   also render the SignUp component unconditionally (the token is gone from
 *   the URL at that point but Clerk has already started the flow).
 *
 * - If there is no token AND we are exactly at /sign-up (not a Clerk sub-path),
 *   show the "access by invitation only" gate.
 */
function SignUpInner() {
  const params   = useSearchParams();
  const pathname = usePathname();
  const token    = params.get("token");

  // Any sub-path under /sign-up (e.g. /sign-up/verify-email-address,
  // /sign-up/continue) means the user is mid-flow — show Clerk widget.
  const isSubPath = pathname !== "/sign-up";

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

          <Link
            href="/sign-in"
            className="text-white/60 text-sm hover:text-white transition-colors"
          >
            Already have an account? Sign in →
          </Link>
        </div>
      </div>
    );
  }

  // Token present, or user is mid-Clerk-flow (sub-path). Render the Clerk
  // widget. forceRedirectUrl is only set when we have the token.
  const redirectUrl = token ? `/join?token=${token}` : undefined;

  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-navy">
      <div className="flex flex-col items-center gap-6">
        <div className="text-center text-white">
          <h1 className="text-2xl font-bold">OpsLens AI</h1>
          <p className="text-white/60 text-sm mt-1">Create your account</p>
        </div>
        <SignUp
          forceRedirectUrl={redirectUrl}
          fallbackRedirectUrl={redirectUrl ?? "/chat"}
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
