"use client";

import { Brain, House, PlugZap, SquareTerminal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/dashboard", label: "Home", icon: House },
  { href: "/command", label: "Command Center", icon: SquareTerminal },
  { href: "/connectors", label: "Connectors", icon: PlugZap },
  { href: "/memory", label: "Memory", icon: Brain },
];

/** Sidebar links (desktop) or a scrolling tab row (phone). */
export function AppNav({ pending, layout }: { pending: number; layout: "sidebar" | "bar" }) {
  const path = usePathname();
  return (
    <nav
      aria-label="Workspace"
      className={cn(
        layout === "sidebar" ? "flex flex-col gap-0.5" : "-mx-4 flex gap-1 overflow-x-auto px-4",
      )}
    >
      {LINKS.map(({ href, label, icon: Icon }) => {
        const active = path === href || path.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex shrink-0 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors",
              active
                ? "bg-card font-medium text-ink shadow-[0_1px_2px_rgba(22,22,29,0.08)] ring-1 ring-border"
                : "text-muted-foreground hover:bg-sidebar-accent hover:text-foreground",
            )}
          >
            <Icon className={cn("size-4", active && "text-indigo")} aria-hidden />
            {label}
            {href === "/command" && pending > 0 && (
              <span
                className="ml-auto rounded-full bg-amber px-1.5 text-[11px] leading-[18px] font-semibold text-white"
                aria-label={`${pending} waiting for approval`}
              >
                {pending}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
