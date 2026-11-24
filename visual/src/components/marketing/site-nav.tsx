import { Show } from "@clerk/nextjs";
import Link from "next/link";
import { Suspense } from "react";

import { Logo } from "@/components/brand/logo";

const sections = [
  { href: "#how", label: "How it works" },
  { href: "#approval", label: "Approval" },
  { href: "#apps", label: "Apps" },
  { href: "#security", label: "Security" },
];

const dark =
  "rounded-lg bg-ink px-3.5 py-2 font-medium text-paper transition-colors hover:bg-indigo";

export function SiteNav() {
  return (
    <header className="sticky top-0 z-30 bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center gap-6 px-4 sm:px-6">
        <Logo />
        <nav aria-label="Page sections" className="hidden flex-1 items-center gap-1 text-sm md:flex">
          {sections.map((s) => (
            <a
              key={s.href}
              href={s.href}
              className="rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:text-foreground"
            >
              {s.label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1 text-sm">
          <Suspense>
            <Show when="signed-out">
              <Link
                href="/sign-in"
                className="rounded-lg px-3 py-2 text-muted-foreground hover:text-foreground"
              >
                Sign in
              </Link>
              <Link href="/sign-up" className={dark}>
                Get started
              </Link>
            </Show>
            <Show when="signed-in">
              <Link href="/dashboard" className={dark}>
                Open workspace
              </Link>
            </Show>
          </Suspense>
        </div>
      </div>
    </header>
  );
}
