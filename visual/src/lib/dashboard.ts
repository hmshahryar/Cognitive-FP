import "server-only";

import { backendFetch } from "@/lib/api";
import type { Approval, Task, TaskStatus } from "@/lib/command";

export type Me = {
  user: { id: string; email: string | null; first_name: string | null };
  workspace: { id: string; name: string };
};

export const getMe = () => backendFetch<Me>("/me");
export const listTasks = () => backendFetch<Task[]>("/tasks");
export const listPendingApprovals = () => backendFetch<Approval[]>("/approvals?status=PENDING");

/** Plain-language name for an approval's action. */
export function actionLabel(actionType: string): string {
  const known: Record<string, string> = {
    "linkedin.publish_post": "Publish a LinkedIn post",
    "gmail.send_email": "Send an email",
    "gmail.forward_email": "Forward an email",
    "gmail.trash_email": "Move an email to trash",
    "calendar.create_event": "Create a calendar event",
    "calendar.reschedule_event": "Reschedule an event",
    "calendar.cancel_event": "Cancel an event",
    "sheets.create_spreadsheet": "Create a spreadsheet",
    "sheets.append_rows": "Add rows to a sheet",
    "sheets.update_cell": "Update a sheet cell",
    "notion.create_page": "Create a Notion page",
    "notion.append_content": "Add to a Notion page",
    "facebook.publish_post": "Post on Facebook",
    "facebook.publish_photo": "Post a photo on Facebook",
    "instagram.publish_photo": "Post on Instagram",
  };
  if (known[actionType]) return known[actionType];
  if (actionType.includes("create_contact")) return "Add a CRM contact";
  if (actionType.includes("update_contact")) return "Update a CRM contact";
  if (actionType.includes("create_note")) return "Add a CRM note";
  return actionType;
}

export type StatusTone = "amber" | "mint" | "indigo" | "coral" | "muted";

export function statusInfo(status: TaskStatus | string): { label: string; tone: StatusTone } {
  switch (status) {
    case "AWAITING_APPROVAL":
      return { label: "Waiting for you", tone: "amber" };
    case "COMPLETED":
      return { label: "Done", tone: "mint" };
    case "QUEUED":
    case "RUNNING":
    case "APPROVED":
    case "EXECUTING":
      return { label: "Working", tone: "indigo" };
    case "FAILED":
      return { label: "Failed", tone: "coral" };
    case "REJECTED":
      return { label: "Rejected", tone: "muted" };
    case "CANCELLED":
      return { label: "Cancelled", tone: "muted" };
    default:
      return { label: status, tone: "muted" };
  }
}

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function timeAgo(iso: string, now = Date.now()): string {
  const stamp = /[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  const seconds = Math.round((new Date(stamp).getTime() - now) / 1000);
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [60, "minute"],
    [24, "hour"],
    [7, "day"],
    [4.35, "week"],
    [12, "month"],
  ];
  let value = seconds;
  for (const [size, unit] of steps) {
    if (Math.abs(value) < size) return rtf.format(Math.round(value), unit);
    value /= size;
  }
  return rtf.format(Math.round(value), "year");
}
