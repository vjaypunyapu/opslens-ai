"use client";

import { SignUp } from "@clerk/nextjs";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense } from "react";

/**
 * Sign-up is invite-only.
 *
 * - If the URL contains ?token=<invite_id>, show the Clerk sign-up flow.
 *   After completing sign-up, the frontend /join page will call
 *   POST /api/v1/admin/invites/{token}/redeem to activate the membership.
 *
 * - If there is no token, show an "access by invitation only" message and
 *   a link back to sign-in.  This prevents public self-registration while
 *   keeping the Clerk route intact for invited users.
 */
function SignUpInner() {
  const params = useSearchParams();
  const token = params.get("token");

  if (!token) {
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

  // Token present — valid invite flow. After Clerk sign-up the app will
  // redirect to /join?token=<token> to call the redeem endpoint.
  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-navy">
      <div className="flex flex-col items-center gap-6">
        <div className="text-center text-white">
          <h1 className="text-2xl font-bold">OpsLens AI</h1>
          <p className="text-white/60 text-sm mt-1">Create your account</p>
        </div>
        <SignUp
          forceRedirectUrl={`/join?token=${token}`}
          fallbackRedirectUrl={`/join?token=${token}`}
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
