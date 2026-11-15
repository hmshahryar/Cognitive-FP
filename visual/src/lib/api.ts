import "server-only";

import { auth } from "@clerk/nextjs/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Call the FastAPI backend from server code.
 * Attaches the signed-in user's Clerk session token as a Bearer token.
 */
export async function backendFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const { getToken } = await auth();
  const token = await getToken();

  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${BACKEND_URL}/api${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body?.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}
