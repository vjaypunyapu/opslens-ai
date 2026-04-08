import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-navy">
      <div className="flex flex-col items-center gap-6">
        <div className="text-center text-white">
          <h1 className="text-2xl font-bold">OpsLens AI</h1>
          <p className="text-white/60 text-sm mt-1">Operational Intelligence Copilot</p>
        </div>
        <SignIn
          appearance={{
            elements: {
              // Hide the "Don't have an account? Sign up" footer link.
              // OpsLens is invite-only — sign-up is not publicly available.
              footerAction: { display: "none" },
              footer: { display: "none" },
            },
          }}
        />
      </div>
    </div>
  );
}
