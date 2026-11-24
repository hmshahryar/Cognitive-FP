import { KeyRound } from "lucide-react";
import type { Metadata } from "next";
import { Suspense } from "react";

import { ConnectorCard } from "@/components/connectors/connector-card";
import { listConnectors, type Connector } from "@/lib/connectors";
import { requireOnboarded } from "@/lib/onboarding";

export const metadata: Metadata = { title: "Connectors · Cognitive Mesh" };

// Most-used first; anything new from the backend goes at the end.
const ORDER = ["Email", "Calendar", "CRM", "Docs & notes", "Social media", "Design", "Team chat"];

function group(connectors: Connector[]) {
  const byCategory = new Map<string, Connector[]>();
  for (const c of connectors) byCategory.set(c.category, [...(byCategory.get(c.category) ?? []), c]);
  const rank = (cat: string) => (ORDER.includes(cat) ? ORDER.indexOf(cat) : ORDER.length);
  return [...byCategory].sort(([a], [b]) => rank(a) - rank(b));
}

async function ConnectorList() {
  const { connectors, degraded } = await listConnectors();
  const connected = connectors.filter((c) => c.connected).length;
  const available = connectors.filter((c) => c.available).length;

  return (
    <div className="flex flex-col gap-10">
      <p className="text-sm text-muted-foreground">
        {connected} of {available} apps connected
      </p>
      {degraded && (
        <p role="status" className="rounded-xl bg-amber-wash px-4 py-3 text-sm text-amber-800">
          Can&apos;t reach the integration service right now. Showing your last known connections.
        </p>
      )}
      {group(connectors).map(([category, items]) => (
        <section key={category} aria-labelledby={`cat-${category}`} className="flex flex-col gap-3">
          <h2 id={`cat-${category}`} className="text-[15px] font-semibold tracking-tight">
            {category}
          </h2>
          <ul className="divide-y overflow-hidden rounded-2xl border bg-card">
            {items.map((c) => (
              <ConnectorCard key={c.slug} connector={c} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export default async function ConnectorsPage() {
  await requireOnboarded();
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-4 py-8 sm:px-8 lg:py-12">
      <header className="flex flex-col gap-3">
        <h1 className="heading text-[clamp(2rem,4vw,2.8rem)]">
          The apps your team <em className="text-indigo">can use.</em>
        </h1>
        <p className="max-w-2xl text-muted-foreground">
          Connect an app and your AI employees can prepare work in it. Anything that changes
          something still waits for your approval.
        </p>
        <p className="flex items-start gap-2 text-sm text-muted-foreground">
          <KeyRound className="mt-0.5 size-4 shrink-0 text-indigo" aria-hidden />
          You sign in on each app&apos;s own page. Cognitive Mesh never sees your password, and
          your access keys stay on the server.
        </p>
      </header>
      <Suspense
        fallback={
          <div className="flex flex-col gap-3" aria-busy>
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[74px] animate-pulse rounded-2xl border bg-card" />
            ))}
          </div>
        }
      >
        <ConnectorList />
      </Suspense>
    </main>
  );
}
