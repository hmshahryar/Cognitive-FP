"use client";

import { ChevronDown, ShieldCheck } from "lucide-react";
import Image from "next/image";
import { useActionState } from "react";

import { connectAction, disconnectAction, type ActionState } from "@/app/(app)/connectors/actions";
import type { Connector } from "@/lib/connectors";
import { cn } from "@/lib/utils";

// Fixed locale and zone so the server and browser render the same text.
const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const initial: ActionState = { error: null };

const NEEDS_RECONNECT = new Set(["EXPIRED", "REVOKED", "INACTIVE", "FAILED"]);

function Status({ connector }: { connector: Connector }) {
  if (!connector.available)
    return <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">Coming soon</span>;
  if (connector.connected)
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-mint-wash px-2 py-0.5 text-xs font-medium text-emerald-700">
        <span className="size-1.5 rounded-full bg-mint" aria-hidden />
        Connected
      </span>
    );
  if (connector.status && NEEDS_RECONNECT.has(connector.status))
    return (
      <span className="rounded-full bg-amber-wash px-2 py-0.5 text-xs font-medium text-amber-700">
        Reconnect needed
      </span>
    );
  return null;
}

/** One app in the Connectors list: status, who it's connected as, what it may do, and its action. */
export function ConnectorCard({ connector }: { connector: Connector }) {
  const [connectState, connect, connecting] = useActionState(
    connectAction.bind(null, connector.slug),
    initial,
  );
  const [disconnectState, disconnect, disconnecting] = useActionState(
    disconnectAction.bind(null, connector.slug),
    initial,
  );
  const error = connectState.error ?? disconnectState.error;
  const reconnect = !!connector.status && NEEDS_RECONNECT.has(connector.status);

  return (
    <li className={cn("flex flex-col gap-3 px-4 py-4 sm:px-5", !connector.available && "opacity-60")}>
      <div className="flex items-center gap-4">
        {connector.logo_url ? (
          <Image
            src={connector.logo_url}
            alt=""
            width={40}
            height={40}
            unoptimized
            className="size-10 shrink-0 rounded-xl border bg-white object-contain p-1.5"
          />
        ) : (
          <span className="size-10 shrink-0 rounded-xl bg-muted" />
        )}

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold tracking-tight">{connector.name}</h3>
            <Status connector={connector} />
          </div>
          <p className="truncate text-sm text-muted-foreground">
            {connector.connected && connector.display_name
              ? `Connected as ${connector.display_name}`
              : (connector.description ?? "")}
          </p>
        </div>

        <div className="shrink-0">
          {connector.connected ? (
            <form action={disconnect}>
              <button
                type="submit"
                disabled={disconnecting}
                className="h-9 rounded-lg border bg-card px-3.5 text-sm font-medium text-muted-foreground transition-colors hover:border-[#b23a1f]/40 hover:text-[#b23a1f] disabled:opacity-50"
              >
                {disconnecting ? "Disconnecting…" : "Disconnect"}
              </button>
            </form>
          ) : (
            <form action={connect}>
              <button
                type="submit"
                disabled={!connector.available || connecting}
                className="h-9 rounded-lg bg-indigo px-4 text-sm font-medium text-white shadow-[0_2px_0_var(--indigo-deep)] transition-colors hover:bg-indigo-deep active:translate-y-px active:shadow-none disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none"
              >
                {connecting ? "Opening…" : reconnect ? "Reconnect" : "Connect"}
              </button>
            </form>
          )}
        </div>
      </div>

      {connector.connected && connector.permissions.length > 0 && (
        <details className="group ml-14 text-sm">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 text-muted-foreground hover:text-foreground">
            <ShieldCheck className="size-3.5 text-indigo" aria-hidden />
            What it can do ({connector.permissions.length})
            <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" aria-hidden />
          </summary>
          <ul className="mt-2 flex flex-col gap-1 rounded-lg bg-muted/60 px-3 py-2.5 text-muted-foreground">
            {connector.permissions.map((p) => (
              <li key={p} className="flex gap-2">
                <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/60" />
                {p}
              </li>
            ))}
          </ul>
          {connector.consented_at && (
            <p className="mt-2 text-xs text-muted-foreground">
              You allowed this on {DATE.format(new Date(connector.consented_at))}
            </p>
          )}
        </details>
      )}

      {error && (
        <p role="alert" className="ml-14 text-sm text-[#b23a1f]">
          {error}
        </p>
      )}
    </li>
  );
}
