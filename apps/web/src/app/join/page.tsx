"use client";

import { useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import { Suspense } from "react";

/**
 * /join?token=<invite_id>
 *
 * Landing page for invited users after they complete Clerk sign-up.
 * Calls POST /api/v1/admin/invites/{token}/redeem to activate their
 * membership, then redirects to /dashboard.
 *
 * If the token is missing or invalid, shows a clear error message.
 */
function JoinInner() {
  const params              = useSearchParams();
  const router              = useRouter();
  const token               = params.get("token");
  const { isLoaded, isSignedIn, getToken } = useAuth();

  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    // Wait for Clerk to finish loading
    if (!isLoaded) return;

    // Not signed in — send to sign-in, preserving the token so we come back here
    if (!isSignedIn) {
      router.replace(`/sign-in?redirect_url=${encodeURIComponent(`/join?token=${token}`)}`);
      return;
    }

    if (!token) {
      setStatus("error");
      setMessage("No invite token found. Please use the link from your invite email.");
      return;
    }

    async function redeem() {
      try {
        const jwt = await getToken();
        const res = await fetch(`/api/v1/admin/invites/${token}/redeem`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${jwt}`,
          },
        });

        if (res.ok) {
          setStatus("success");
          // Brief pause so the user sees the success state before redirect
          setTimeout(() => router.replace("/dashboard"), 1200);
        } else {
          const body = await res.json().catch(() => ({}));
          setStatus("error");
          setMessage(
            body?.detail ||
              "This invite link is invalid or has already been used. Contact your workspace admin."
          );
        }
      } catch {
        setStatus("error");
        setMessage("Something went wrong. Please try again or contact support.");
      }
    }

    redeem();
  }, [token, router, isLoaded, isSignedIn, getToken]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-navy">
      <div className="flex flex-col items-center gap-6 text-center max-w-sm px-6">
        <div className="text-white">
          <h1 className="text-2xl font-bold">OpsLens AI</h1>
          <p className="text-white/60 text-sm mt-1">Operational Intelligence Copilot</p>
        </div>

        {status === "loading" && (
          <div className="bg-white/10 border border-white/20 rounded-xl p-6 text-white space-y-3">
            <div className="animate-spin text-3xl">⚙️</div>
            <p className="text-white/80 text-sm">Activating your workspace access…</p>
          </div>
        )}

        {status === "success" && (
          <div className="bg-white/10 border border-white/20 rounded-xl p-6 text-white space-y-3">
            <div className="text-4xl">✅</div>
            <h2 className="font-semibold text-lg">You're in!</h2>
            <p className="text-white/70 text-sm">Taking you to your dashboard…</p>
          </div>
        )}

        {status === "error" && (
          <div className="bg-red-500/20 border border-red-400/30 rounded-xl p-6 text-white space-y-3">
            <div className="text-4xl">❌</div>
            <h2 className="font-semibold text-lg">Invite error</h2>
            <p className="text-white/70 text-sm leading-relaxed">{message}</p>
            <a
              href="/sign-in"
              className="inline-block mt-2 text-sm text-white/60 hover:text-white transition-colors"
            >
              Go to sign-in →
            </a>
          </div>
        )}
      </div>
    </div>
  );
}

export default function JoinPage() {
  return (
    <Suspense>
      <JoinInner />
    </Suspense>
  );
}
