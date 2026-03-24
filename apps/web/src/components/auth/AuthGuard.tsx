"use client";
import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { userId, isLoaded } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoaded && userId === null) {
      router.replace("/sign-in");
    }
  }, [userId, isLoaded, router]);

  // Show nothing while Clerk loads or while redirecting
  if (!isLoaded || userId === null) return null;
  return <>{children}</>;
}
