"use client";

import { useActionState } from "react";

import { cancelTaskAction, retryTaskAction, type FormState } from "@/app/command/actions";
import { Button } from "@/components/ui/button";
import type { Task, TaskStatus } from "@/lib/command";

const LABEL: Record<TaskStatus, string> = {
  QUEUED: "Queued",
  RUNNING: "In progress",
  AWAITING_APPROVAL: "Needs your approval",
  APPROVED: "Approved",
  EXECUTING: "Executing",
  COMPLETED: "Completed",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  REJECTED: "Rejected",
};

const TONE: Record<TaskStatus, string> = {
  QUEUED: "bg-muted text-foreground",
  RUNNING: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  AWAITING_APPROVAL: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  APPROVED: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  EXECUTING: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  COMPLETED: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  FAILED: "bg-red-500/15 text-red-700 dark:text-red-400",
  CANCELLED: "bg-muted text-muted-foreground",
  REJECTED: "bg-muted text-muted-foreground",
};

const CANCELLABLE = new Set<TaskStatus>(["QUEUED", "RUNNING", "AWAITING_APPROVAL", "APPROVED"]);
const RETRYABLE = new Set<TaskStatus>(["FAILED", "CANCELLED", "REJECTED"]);

export function TaskCard({ task, children }: { task: Task; children?: React.ReactNode }) {
  const [cancelState, cancel, cancelling] = useActionState<FormState>(
    cancelTaskAction.bind(null, task.id),
    { error: null },
  );
  const [retryState, retry, retrying] = useActionState<FormState>(
    retryTaskAction.bind(null, task.id),
    { error: null },
  );
  const error = cancelState.error ?? retryState.error;

  return (
    <article className="flex flex-col gap-3 rounded-lg border p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="line-clamp-2 text-sm">{task.request}</p>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${TONE[task.status]}`}>
          {LABEL[task.status]}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <time className="text-xs text-muted-foreground" dateTime={task.created_at}>
          {new Date(task.created_at).toLocaleString()}
        </time>
        <div className="flex gap-2">
          {CANCELLABLE.has(task.status) && (
            <form action={cancel}>
              <Button type="submit" size="sm" variant="outline" disabled={cancelling}>
                {cancelling ? "Cancelling…" : "Cancel"}
              </Button>
            </form>
          )}
          {RETRYABLE.has(task.status) && (
            <form action={retry}>
              <Button type="submit" size="sm" variant="outline" disabled={retrying}>
                {retrying ? "Retrying…" : "Retry"}
              </Button>
            </form>
          )}
        </div>
      </div>
      {children}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </article>
  );
}
