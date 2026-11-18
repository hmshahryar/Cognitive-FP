"use client";

import { useActionState, useState, useTransition } from "react";

import {
  approveAction,
  changeVisibilityAction,
  rejectAction,
  requestChangesAction,
  type FormState,
} from "@/app/command/actions";
import { Button } from "@/components/ui/button";
import type { Approval, Visibility } from "@/lib/command";

const VISIBILITY: Record<Visibility, { label: string; hint: string }> = {
  PUBLIC: { label: "Public", hint: "Anyone on or off LinkedIn" },
  CONNECTIONS: { label: "Connections only", hint: "Only your 1st-degree connections" },
};

/** Consent screen for one exact LinkedIn post. Nothing is published from here yet. */
export function ApprovalCard({
  approval,
  accountLabel,
}: {
  approval: Approval;
  accountLabel: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const [showChanges, setShowChanges] = useState(false);
  const [changesState, submitChanges, sendingChanges] = useActionState<FormState, FormData>(
    requestChangesAction.bind(null, approval.id),
    { error: null },
  );
  const { payload } = approval;
  const busy = pending || sendingChanges;

  const run = (fn: () => Promise<FormState>) =>
    startTransition(async () => setError((await fn()).error));

  return (
    <section
      aria-label="Approval required"
      className="flex flex-col gap-4 rounded-xl border-2 border-amber-500/50 bg-amber-500/5 p-5"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Approve this LinkedIn post?</h2>
        <span className="text-xs text-muted-foreground">
          Version {approval.content_version} · fingerprint{" "}
          <code title={approval.payload_hash}>{approval.payload_hash.slice(0, 12)}</code>
        </span>
      </header>

      <p className="text-sm text-muted-foreground">
        Posting as <span className="font-medium text-foreground">{accountLabel}</span>. This is
        exactly what will be published. Any change creates a new version that needs your approval
        again.
      </p>

      <blockquote className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg border bg-background p-4 text-sm">
        {payload.commentary}
      </blockquote>

      <fieldset className="flex flex-col gap-2" disabled={busy}>
        <legend className="mb-1 text-sm font-medium">Who can see it</legend>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(VISIBILITY) as Visibility[]).map((v) => (
            <label
              key={v}
              className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                payload.visibility === v ? "border-primary bg-primary/5" : ""
              }`}
            >
              <input
                type="radio"
                name={`visibility-${approval.id}`}
                checked={payload.visibility === v}
                onChange={() => run(() => changeVisibilityAction(approval.id, v))}
                className="mt-1"
              />
              <span>
                <span className="font-medium">{VISIBILITY[v].label}</span>
                <span className="block text-xs text-muted-foreground">{VISIBILITY[v].hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="flex flex-wrap gap-2">
        <Button
          disabled={busy}
          onClick={() =>
            run(() => approveAction(approval.id, approval.payload_hash, approval.content_version))
          }
        >
          {pending ? "Working…" : "Approve"}
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => setShowChanges((s) => !s)}>
          Request changes
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => run(() => rejectAction(approval.id))}>
          Reject
        </Button>
      </div>

      {showChanges && (
        <form action={submitChanges} className="flex flex-col gap-2">
          <label htmlFor={`note-${approval.id}`} className="text-sm font-medium">
            What should change?
          </label>
          <textarea
            id={`note-${approval.id}`}
            name="note"
            rows={3}
            required
            maxLength={2000}
            className="w-full resize-none rounded-lg border bg-background p-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <Button type="submit" variant="outline" disabled={busy} className="self-start">
            {sendingChanges ? "Sending…" : "Send to the writer"}
          </Button>
        </form>
      )}

      {(error ?? changesState.error) && (
        <p role="alert" className="text-sm text-destructive">
          {error ?? changesState.error}
        </p>
      )}
    </section>
  );
}
