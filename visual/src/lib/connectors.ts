import "server-only";

import { backendFetch } from "@/lib/api";

export type Connector = {
  slug: string;
  name: string;
  logo_url: string | null;
  description: string | null;
  category: string;
  available: boolean;
  connected: boolean;
  status: string | null;
  display_name: string | null;
  connected_at: string | null;
  granted_scopes: string[];
  permissions: string[];
  consented_at: string | null;
};

export type ConnectorList = { connectors: Connector[]; degraded: boolean };

export type CallbackOutcome = "connected" | "pending" | "failed";

export function listConnectors() {
  return backendFetch<ConnectorList>("/connectors");
}

export function completeConnection(attemptId: string, connectedAccountId?: string) {
  return backendFetch<{ outcome: CallbackOutcome; connector: Connector }>(
    "/connectors/callback",
    {
      method: "POST",
      body: JSON.stringify({
        attempt_id: attemptId,
        connected_account_id: connectedAccountId ?? null,
      }),
    },
  );
}
