import type { Metadata } from "next";
import { Check, RotateCw } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { ApiError } from "@/lib/api";
import { completeConnection } from "@/lib/connectors";
import { getOnboarding } from "@/lib/onboarding";

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

  // Mid-setup users go back to onboarding instead of the Connectors page.
  const inSetup = await getOnboarding()
    .then((o) => !o.completed)
    .catch(() => false);

  const target = inSetup ? "/onboarding?step=connect" : "/connectors";
  return (
    <section className="flex w-full max-w-md flex-col items-center gap-4 rounded-3xl border bg-card p-10 text-center shadow-[0_24px_60px_-34px_rgba(55,48,163,0.45)]">
      <span
        className={`flex size-14 items-center justify-center rounded-full text-white ${
          ok ? "bg-mint shadow-[0_4px_0_#0a8a60]" : "bg-amber shadow-[0_4px_0_#c27c05]"
        }`}
        aria-hidden
      >
        {ok ? <Check className="size-7" strokeWidth={3} /> : <RotateCw className="size-6" strokeWidth={2.5} />}
      </span>
      <h1 className="heading mt-2 text-2xl">{title}</h1>
      <p className="text-muted-foreground">{detail}</p>
      <Link
        href={target}
        className="mt-2 inline-flex h-11 items-center rounded-xl bg-indigo px-5 font-medium text-white shadow-[0_3px_0_var(--indigo-deep)] hover:bg-indigo-deep active:translate-y-px active:shadow-none"
      >
        {inSetup ? "Continue setup" : "Back to Connectors"}
      </Link>
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
