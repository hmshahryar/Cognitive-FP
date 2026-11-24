"use client";

import { useActionState } from "react";

import { cancelTaskAction, retryTaskAction, type FormState } from "@/app/(app)/command/actions";
import { Button } from "@/components/ui/button";
import type { Task, TaskStatus } from "@/lib/command";
import { cn } from "@/lib/utils";

const LABEL: Record<TaskStatus, string> = {
  QUEUED: "Queued",
  RUNNING: "Working",
  AWAITING_APPROVAL: "Needs your approval",
  APPROVED: "Approved",
  EXECUTING: "Running",
  COMPLETED: "Done",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  REJECTED: "Rejected",
};

const TONE: Record<TaskStatus, string> = {
  QUEUED: "bg-muted text-muted-foreground",
  RUNNING: "bg-indigo-wash text-indigo-deep",
  AWAITING_APPROVAL: "bg-amber-wash text-amber-700",
  APPROVED: "bg-indigo-wash text-indigo-deep",
  EXECUTING: "bg-indigo-wash text-indigo-deep",
  COMPLETED: "bg-mint-wash text-emerald-700",
  FAILED: "bg-coral-wash text-[#b23a1f]",
  CANCELLED: "bg-muted text-muted-foreground",
  REJECTED: "bg-muted text-muted-foreground",
};

const DOT: Record<TaskStatus, string> = {
  QUEUED: "bg-muted-foreground/40",
  RUNNING: "bg-indigo animate-pulse",
  AWAITING_APPROVAL: "bg-amber",
  APPROVED: "bg-indigo animate-pulse",
  EXECUTING: "bg-indigo animate-pulse",
  COMPLETED: "bg-mint",
  FAILED: "bg-coral",
  CANCELLED: "bg-muted-foreground/40",
  REJECTED: "bg-muted-foreground/40",
};

const CANCELLABLE = new Set<TaskStatus>(["QUEUED", "RUNNING", "AWAITING_APPROVAL", "APPROVED"]);
const RETRYABLE = new Set<TaskStatus>(["FAILED", "CANCELLED", "REJECTED"]);

export function TaskCard({
  task,
  canRetry = true,
  children,
}: {
  task: Task;
  canRetry?: boolean;
  children?: React.ReactNode;
}) {
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
    <article className="relative flex flex-col gap-2.5 pl-6">
      {/* Timeline dot */}
      <span
        aria-hidden
        className={cn("absolute top-1.5 left-0 size-2.5 rounded-full ring-4 ring-background", DOT[task.status])}
      />
      <div className="flex items-start justify-between gap-3">
        <p className="line-clamp-2 text-sm leading-snug font-medium">{task.request}</p>
        <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-xs font-medium", TONE[task.status])}>
          {LABEL[task.status]}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        {task.route_label && <>Handled by {task.route_label}. </>}
        {/* Local time; the server can't know the viewer's zone, so this may differ on first paint. */}
        <time dateTime={task.created_at} suppressHydrationWarning>
          {new Date(task.created_at).toLocaleString([], {
            day: "numeric",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </time>
      </p>
      {children}
      {(CANCELLABLE.has(task.status) || (canRetry && RETRYABLE.has(task.status))) && (
        <div className="flex gap-2">
          {CANCELLABLE.has(task.status) && (
            <form action={cancel}>
              <Button type="submit" size="sm" variant="outline" disabled={cancelling}>
                {cancelling ? "Cancelling…" : "Cancel"}
              </Button>
            </form>
          )}
          {canRetry && RETRYABLE.has(task.status) && (
            <form action={retry}>
              <Button type="submit" size="sm" variant="outline" disabled={retrying}>
                {retrying ? "Retrying…" : "Retry"}
              </Button>
            </form>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-[#b23a1f]">
          {error}
        </p>
      )}
    </article>
  );
}
