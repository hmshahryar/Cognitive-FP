import { UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { Suspense, cache } from "react";

import { AppNav } from "@/components/app/app-nav";
import { LogoMark } from "@/components/brand/logo";
import { getMe, listPendingApprovals } from "@/lib/dashboard";

/** Fetched once per request, shared by the sidebar and the phone bar. */
const chrome = cache(async () => {
  const [me, pending] = await Promise.all([
    getMe().catch(() => null),
    listPendingApprovals().catch(() => []),
  ]);
  return { workspace: me?.workspace.name ?? "Your workspace", pending: pending.length };
});

async function Sidebar() {
  const { workspace, pending } = await chrome();
  return (
    <>
      <Link
        href="/dashboard"
        className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-sidebar-accent"
      >
        <LogoMark className="size-6" />
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold tracking-tight">{workspace}</span>
          <span className="block text-xs text-muted-foreground">Cognitive Mesh</span>
        </span>
      </Link>
      <div className="mt-6 flex-1">
        <AppNav pending={pending} layout="sidebar" />
      </div>
    </>
  );
}

async function TopBar() {
  const { pending } = await chrome();
  return <AppNav pending={pending} layout="bar" />;
}

/** Signed-in app chrome: a calm left sidebar (Notion/Linear style) and the page beside it. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-1">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-sidebar p-3 lg:flex">
        <Suspense fallback={<div className="h-10" />}>
          <Sidebar />
        </Suspense>
        <div className="flex items-center gap-2 border-t pt-3">
          <UserButton />
          <span className="text-xs text-muted-foreground">Account</span>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex flex-col gap-2 border-b bg-background/90 px-4 pt-3 pb-2 backdrop-blur lg:hidden">
          <div className="flex items-center justify-between">
            <Link href="/dashboard" className="flex items-center gap-2 font-semibold tracking-tight">
              <LogoMark className="size-6" />
              Cognitive Mesh
            </Link>
            <UserButton />
          </div>
          <Suspense fallback={<div className="h-8" />}>
            <TopBar />
          </Suspense>
        </header>
        {children}
      </div>
    </div>
  );
}
