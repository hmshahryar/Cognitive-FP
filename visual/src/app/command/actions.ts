"use server";

import { refresh } from "next/cache";
import { redirect } from "next/navigation";

import { ApiError, backendFetch } from "@/lib/api";
import type { Chat, Task } from "@/lib/command";

export type FormState = { error: string | null };

const message = (e: unknown) =>
  e instanceof ApiError ? e.message : "Something went wrong. Please try again.";

/** Send a command. Starts a new chat when none is selected. */
export async function sendCommandAction(
  chatId: string | null,
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const request = String(formData.get("request") ?? "").trim();
  if (!request) return { error: "Type a command first." };

  let targetChat = chatId;
  try {
    if (!targetChat) {
      const chat = await backendFetch<Chat>("/chats", {
        method: "POST",
        body: JSON.stringify({ title: request.slice(0, 60) }),
      });
      targetChat = chat.id;
    }
    await backendFetch<Task>(`/chats/${encodeURIComponent(targetChat)}/tasks`, {
      method: "POST",
      body: JSON.stringify({ request }),
    });
  } catch (e) {
    return { error: message(e) };
  }
  if (targetChat !== chatId) redirect(`/command?chat=${targetChat}`);
  refresh();
  return { error: null };
}

export async function cancelTaskAction(taskId: string): Promise<FormState> {
  try {
    await backendFetch(`/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
  } catch (e) {
    return { error: message(e) };
  }
  refresh();
  return { error: null };
}

// ---------- approvals ----------
async function approvalCall(path: string, body: unknown): Promise<FormState> {
  try {
    await backendFetch(path, { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    refresh(); // the approval may have changed (e.g. superseded): show the current one
    return { error: message(e) };
  }
  refresh();
  return { error: null };
}

/** Approve exactly what was shown: the hash and version come from the rendered card. */
export async function approveAction(
  approvalId: string,
  payloadHash: string,
  contentVersion: number,
): Promise<FormState> {
  return approvalCall(`/approvals/${encodeURIComponent(approvalId)}/approve`, {
    payload_hash: payloadHash,
    content_version: contentVersion,
  });
}

export async function rejectAction(approvalId: string): Promise<FormState> {
  return approvalCall(`/approvals/${encodeURIComponent(approvalId)}/reject`, {});
}

export async function requestChangesAction(
  approvalId: string,
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const note = String(formData.get("note") ?? "").trim();
  if (!note) return { error: "Describe what should change." };
  return approvalCall(`/approvals/${encodeURIComponent(approvalId)}/request-changes`, { note });
}

/** Changing visibility creates a NEW approval version that needs consent again. */
export async function changeVisibilityAction(
  approvalId: string,
  visibility: "PUBLIC" | "CONNECTIONS",
): Promise<FormState> {
  return approvalCall(`/approvals/${encodeURIComponent(approvalId)}/visibility`, { visibility });
}

export async function retryTaskAction(taskId: string): Promise<FormState> {
  try {
    await backendFetch(`/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
  } catch (e) {
    return { error: message(e) };
  }
  refresh();
  return { error: null };
}
