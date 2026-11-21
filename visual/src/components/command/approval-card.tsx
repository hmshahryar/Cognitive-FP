"use client";

import { useActionState, useState, useTransition } from "react";

import {
  approveAction,
  changeVisibilityAction,
  rejectAction,
  requestChangesAction,
  type FormState,
} from "@/app/command/actions";
import { CalendarApprovalDetails } from "@/components/command/calendar-approval-details";
import { EmailApprovalDetails } from "@/components/command/email-approval-details";
import { QualityPanel } from "@/components/command/quality-panel";
import { SheetApprovalDetails } from "@/components/command/sheet-approval-details";
import { Button } from "@/components/ui/button";
import type {
  Approval,
  CalendarActionType,
  Draft,
  SheetActionType,
  Visibility,
} from "@/lib/command";

const VISIBILITY: Record<Visibility, { label: string; hint: string }> = {
  PUBLIC: { label: "Public", hint: "Anyone on or off LinkedIn" },
  CONNECTIONS: { label: "Connections only", hint: "Only your 1st-degree connections" },
};

const COPY: Record<
  Approval["action_type"],
  { title: string; button: string; note: string; canRevise: boolean }
> = {
  "linkedin.publish_post": {
    title: "Approve this LinkedIn post?",
    button: "Approve and publish",
    note: "Approving publishes exactly this text right away.",
    canRevise: true,
  },
  "gmail.send_email": {
    title: "Approve this email?",
    button: "Approve and send",
    note: "Approving sends exactly this email right away.",
    canRevise: true,
  },
  "gmail.forward_email": {
    title: "Approve this forward?",
    button: "Approve and forward",
    note: "Approving forwards this email to the listed recipients right away.",
    canRevise: false,
  },
  "gmail.trash_email": {
    title: "Move this email to Trash?",
    button: "Approve and move to Trash",
    note: "You can restore it from Gmail Trash. Gmail removes Trash items after 30 days.",
    canRevise: false,
  },
  "calendar.create_event": {
    title: "Schedule this event?",
    button: "Approve and schedule",
    note: "Approving adds exactly this event to your calendar right away.",
    canRevise: false,
  },
  "calendar.reschedule_event": {
    title: "Reschedule this event?",
    button: "Approve and reschedule",
    note: "Approving moves this event to the new time right away.",
    canRevise: false,
  },
  "calendar.cancel_event": {
    title: "Cancel this event?",
    button: "Approve and cancel event",
    note: "Approving removes this event from your calendar right away.",
    canRevise: false,
  },
  "sheets.create_spreadsheet": {
    title: "Create this sheet?",
    button: "Approve and create",
    note: "Approving creates this spreadsheet in your Google Drive right away.",
    canRevise: false,
  },
  "sheets.append_rows": {
    title: "Add these rows?",
    button: "Approve and add rows",
    note: "Approving adds exactly these rows to the sheet right away.",
    canRevise: false,
  },
  "sheets.update_cell": {
    title: "Update this cell?",
    button: "Approve and update",
    note: "Approving replaces the current value right away.",
    canRevise: false,
  },
};

const isSheet = (a: Approval): a is Extract<Approval, { action_type: SheetActionType }> =>
  a.action_type.startsWith("sheets.");

const isCalendar = (a: Approval): a is Extract<Approval, { action_type: CalendarActionType }> =>
  a.action_type.startsWith("calendar.");

/** Consent screen for one exact action. Approving runs this exact version once. */
export function ApprovalCard({
  approval,
  accountLabel,
  draft,
}: {
  approval: Approval;
  accountLabel: string;
  draft: Draft | null;
}) {
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const [showChanges, setShowChanges] = useState(false);
  const [changesState, submitChanges, sendingChanges] = useActionState<FormState, FormData>(
    requestChangesAction.bind(null, approval.id),
    { error: null },
  );
  const busy = pending || sendingChanges;
  const copy = COPY[approval.action_type];

  const run = (fn: () => Promise<FormState>) =>
    startTransition(async () => setError((await fn()).error));

  return (
    <section
      aria-label="Approval required"
      className="flex flex-col gap-4 rounded-xl border-2 border-amber-500/50 bg-amber-500/5 p-5"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">{copy.title}</h2>
        <span className="text-xs text-muted-foreground">
          Version {approval.content_version} · fingerprint{" "}
          <code title={approval.payload_hash}>{approval.payload_hash.slice(0, 12)}</code>
        </span>
      </header>

      {approval.action_type === "linkedin.publish_post" ? (
        <>
          <p className="text-sm text-muted-foreground">
            Posting as <span className="font-medium text-foreground">{accountLabel}</span>.{" "}
            {copy.note} Any change creates a new version that needs your approval again.
          </p>

          <blockquote className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg border bg-background p-4 text-sm">
            {approval.payload.commentary}
          </blockquote>

          {draft && <QualityPanel draft={draft} />}

          <fieldset className="flex flex-col gap-2" disabled={busy}>
            <legend className="mb-1 text-sm font-medium">Who can see it</legend>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(VISIBILITY) as Visibility[]).map((v) => (
                <label
                  key={v}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                    approval.payload.visibility === v ? "border-primary bg-primary/5" : ""
                  }`}
                >
                  <input
                    type="radio"
                    name={`visibility-${approval.id}`}
                    checked={approval.payload.visibility === v}
                    onChange={() => run(() => changeVisibilityAction(approval.id, v))}
                    className="mt-1"
                  />
                  <span>
                    <span className="font-medium">{VISIBILITY[v].label}</span>
                    <span className="block text-xs text-muted-foreground">
                      {VISIBILITY[v].hint}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </>
      ) : isSheet(approval) ? (
        <>
          <p className="text-sm text-muted-foreground">{copy.note}</p>
          <SheetApprovalDetails actionType={approval.action_type} payload={approval.payload} />
        </>
      ) : isCalendar(approval) ? (
        <>
          <p className="text-sm text-muted-foreground">{copy.note}</p>
          <CalendarApprovalDetails actionType={approval.action_type} payload={approval.payload} />
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">{copy.note}</p>
          <EmailApprovalDetails actionType={approval.action_type} payload={approval.payload} />
        </>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          disabled={busy}
          onClick={() =>
            run(() => approveAction(approval.id, approval.payload_hash, approval.content_version))
          }
        >
          {pending ? "Working…" : copy.button}
        </Button>
        {copy.canRevise && (
          <Button variant="outline" disabled={busy} onClick={() => setShowChanges((s) => !s)}>
            Request changes
          </Button>
        )}
        <Button variant="ghost" disabled={busy} onClick={() => run(() => rejectAction(approval.id))}>
          Reject
        </Button>
      </div>

      {copy.canRevise && showChanges && (
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
