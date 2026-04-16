import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import LandingPage from "./_components/LandingPage";

export const metadata = {
  title: "OpsLens AI — AI-Powered Incident Response",
  description:
    "Stop guessing. Start resolving. OpsLens AI turns noisy logs into instant, routed, AI-diagnosed incident briefs — so your engineers fix problems, not find them.",
};

export default async function Home() {
  try {
    const { userId } = await auth();
    if (userId) redirect("/dashboard");
  } catch {
    // auth() unavailable on this route — treat as unauthenticated
  }
  return <LandingPage />;
}
