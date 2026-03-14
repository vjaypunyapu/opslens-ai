"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserButton } from "@clerk/nextjs";
import {
  MessageSquare,
  Lightbulb,
  Bell,
  Plug,
  BarChart2,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/chat",         label: "Chat",          icon: MessageSquare },
  { href: "/insights",     label: "Insights",      icon: Lightbulb },
  { href: "/alerts",       label: "Alerts",        icon: Bell },
  { href: "/integrations", label: "Integrations",  icon: Plug },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-[var(--sidebar-width)] flex-col bg-brand-navy text-white">
      {/* Logo */}
      <div className="flex h-16 items-center gap-2.5 px-5 border-b border-white/10">
        <BarChart2 className="h-6 w-6 text-brand-teal" />
        <span className="text-lg font-semibold tracking-tight">OpsLens AI</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                active
                  ? "bg-white/15 text-white"
                  : "text-white/60 hover:bg-white/10 hover:text-white",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="border-t border-white/10 p-4 flex items-center justify-between">
        <UserButton
          appearance={{
            elements: {
              avatarBox: "h-8 w-8",
              userButtonPopoverCard: "shadow-xl",
            },
          }}
        />
        <Link
          href="/settings"
          className="text-white/50 hover:text-white transition-colors"
        >
          <Settings className="h-4 w-4" />
        </Link>
      </div>
    </aside>
  );
}
