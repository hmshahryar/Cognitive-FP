"use server";

import { refresh } from "next/cache";
import { redirect } from "next/navigation";

import { ApiError, backendFetch } from "@/lib/api";

export type ActionState = { error: string | null };

function message(e: unknown) {
  return e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
}

/** Ask the backend for a Composio Connect Link, then send the browser there. */
export async function connectAction(slug: string): Promise<ActionState> {
  let url: string;
  try {
    ({ redirect_url: url } = await backendFetch<{ redirect_url: string }>(
      `/connectors/${encodeURIComponent(slug)}/connect`,
      { method: "POST" },
    ));
  } catch (e) {
    return { error: message(e) };
  }
  // Must be outside try/catch: redirect() works by throwing.
  redirect(url);
}

export async function disconnectAction(slug: string): Promise<ActionState> {
  try {
    await backendFetch(`/connectors/${encodeURIComponent(slug)}`, { method: "DELETE" });
  } catch (e) {
    return { error: message(e) };
  }
  refresh();
  return { error: null };
}
