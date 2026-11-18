import type { Metadata } from "next";
import { Suspense } from "react";

import { ConnectorCard } from "@/components/connectors/connector-card";
import { listConnectors } from "@/lib/connectors";

export const metadata: Metadata = { title: "Connectors · Cognitive Mesh" };

async function ConnectorGrid() {
  const { connectors, degraded } = await listConnectors();

  const byCategory = new Map<string, typeof connectors>();
  for (const c of connectors) {
    byCategory.set(c.category, [...(byCategory.get(c.category) ?? []), c]);
  }

  return (
    <div className="flex flex-col gap-8">
      {degraded && (
        <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          Can&apos;t reach the integration service right now. Showing your last known connections.
        </p>
      )}
      {[...byCategory].map(([category, items]) => (
        <section key={category} className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">{category}</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((c) => (
              <ConnectorCard key={c.slug} connector={c} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export default function ConnectorsPage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-10 sm:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Connectors</h1>
        <p className="text-muted-foreground">
          Connect the apps your AI employees may use. You sign in on each app&apos;s own page;
          Cognitive Mesh never sees your passwords or tokens.
        </p>
      </header>
      <Suspense fallback={<p className="text-muted-foreground">Loading connectors…</p>}>
        <ConnectorGrid />
      </Suspense>
    </main>
  );
}
