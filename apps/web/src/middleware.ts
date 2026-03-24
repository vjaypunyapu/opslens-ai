import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Auth is handled in each protected layout via auth() from @clerk/nextjs/server
// which runs in Node.js runtime (not Edge). Clerk's clerkMiddleware requires
// Node.js crypto which is unavailable in Next.js 14 Edge middleware.
export function middleware(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
  ],
};
