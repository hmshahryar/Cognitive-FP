import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { completeConnection } from "@/lib/connectors";

export const metadata: Metadata = { title: "Connecting… · Cognitive Mesh" };

type Search = Promise<{ [key: string]: string | string[] | undefined }>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

async function Result({ searchParams }: { searchParams: Search }) {
  const params = await searchParams;
  const attemptId = one(params.attempt);
  const accountId = one(params.connected_account_id);

  let title: string;
  let detail: string;
  let ok = false;

  if (!attemptId) {
    title = "Missing connection details";
    detail = "Start the connection again from the Connectors page.";
  } else {
    try {
      // The backend verifies this with Composio; these URL params are not trusted.
      const { outcome, connector } = await completeConnection(attemptId, accountId);
      ok = outcome === "connected";
      title =
        outcome === "connected"
          ? `${connector.name} connected`
          : outcome === "pending"
            ? `${connector.name} is still connecting`
            : `${connector.name} wasn't connected`;
      detail =
        outcome === "connected"
          ? connector.display_name
            ? `Connected as ${connector.display_name}. Your AI employees can now use it.`
            : "Your AI employees can now use it."
          : outcome === "pending"
            ? "Authorization hasn't finished yet. Refresh this page in a moment."
            : "Authorization was cancelled or failed. You can try again.";
    } catch (e) {
      title = "We couldn't verify this connection";
      detail = e instanceof ApiError ? e.message : "Please try connecting again.";
    }
  }

  return (
    <section className="flex w-full max-w-md flex-col items-center gap-4 rounded-xl border p-8 text-center">
      <span
        className={`size-3 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`}
        aria-hidden
      />
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-muted-foreground">{detail}</p>
      <Button render={<Link href="/connectors" />}>Back to Connectors</Button>
    </section>
  );
}

export default function ConnectorCallbackPage({ searchParams }: { searchParams: Search }) {
  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <Suspense fallback={<p className="text-muted-foreground">Verifying your connection…</p>}>
        <Result searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
