import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-navy">
      <div className="flex flex-col items-center gap-6">
        <div className="text-center text-white">
          <h1 className="text-2xl font-bold">OpsLens AI</h1>
          <p className="text-white/60 text-sm mt-1">Create your account</p>
        </div>
        <SignUp />
      </div>
    </div>
  );
}
