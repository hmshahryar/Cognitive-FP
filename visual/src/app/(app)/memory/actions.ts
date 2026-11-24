"use server";

import { refresh } from "next/cache";

import { ApiError, backendFetch } from "@/lib/api";

export type MemoryActionState = { error: string | null };

export async function deleteMemoryAction(memoryId: string): Promise<MemoryActionState> {
  try {
    await backendFetch(`/memories/${encodeURIComponent(memoryId)}`, { method: "DELETE" });
  } catch (e) {
    return {
      error: e instanceof ApiError ? e.message : "Something went wrong. Please try again.",
    };
  }
  refresh();
  return { error: null };
}
