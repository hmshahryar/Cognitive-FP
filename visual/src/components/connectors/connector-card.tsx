"use client";

import Image from "next/image";
import { useActionState } from "react";

import { connectAction, disconnectAction, type ActionState } from "@/app/connectors/actions";
import { Button } from "@/components/ui/button";
import type { Connector } from "@/lib/connectors";

const initial: ActionState = { error: null };

const NEEDS_RECONNECT = new Set(["EXPIRED", "REVOKED", "INACTIVE", "FAILED"]);

function StatusBadge({ connector }: { connector: Connector }) {
  if (!connector.available)
    return <span className="rounded-full bg-muted px-2 py-0.5 text-xs">Coming soon</span>;
  if (connector.connected)
    return (
      <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-400">
        Connected
      </span>
    );
  if (connector.status && NEEDS_RECONNECT.has(connector.status))
    return (
      <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-400">
        Reconnect needed
      </span>
    );
  return null;
}

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

  return (
    <article className="flex flex-col gap-4 rounded-xl border bg-card p-5">
      <div className="flex items-start gap-3">
        {connector.logo_url ? (
          <Image
            src={connector.logo_url}
            alt=""
            width={40}
            height={40}
            className="size-10 rounded-md object-contain"
          />
        ) : (
          <div className="size-10 rounded-md bg-muted" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium">{connector.name}</h3>
            <StatusBadge connector={connector} />
          </div>
          <p className="text-xs text-muted-foreground">{connector.category}</p>
        </div>
      </div>

      {connector.connected && connector.display_name ? (
        <p className="text-sm">
          Connected as <span className="font-medium">{connector.display_name}</span>
        </p>
      ) : (
        connector.description && (
          <p className="line-clamp-2 text-sm text-muted-foreground">{connector.description}</p>
        )
      )}

      {connector.connected && connector.permissions.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Permissions you granted ({connector.permissions.length})
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
            {connector.permissions.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          {connector.consented_at && (
            <p className="mt-2 text-xs text-muted-foreground">
              Granted {new Date(connector.consented_at).toLocaleDateString()}
            </p>
          )}
        </details>
      )}

      <div className="mt-auto flex items-center gap-2">
        {connector.connected ? (
          <form action={disconnect}>
            <Button type="submit" variant="outline" size="sm" disabled={disconnecting}>
              {disconnecting ? "Disconnecting…" : "Disconnect"}
            </Button>
          </form>
        ) : (
          <form action={connect}>
            <Button type="submit" size="sm" disabled={!connector.available || connecting}>
              {connecting
                ? "Opening…"
                : connector.status && NEEDS_RECONNECT.has(connector.status)
                  ? "Reconnect"
                  : "Connect"}
            </Button>
          </form>
        )}
      </div>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </article>
  );
}
