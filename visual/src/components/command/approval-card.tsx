"use client";

import { useActionState, useState, useTransition } from "react";

import {
  approveAction,
  changeVisibilityAction,
  rejectAction,
  requestChangesAction,
  type FormState,
} from "@/app/(app)/command/actions";
import { CalendarApprovalDetails } from "@/components/command/calendar-approval-details";
import { CrmApprovalDetails } from "@/components/command/crm-approval-details";
import { EmailApprovalDetails } from "@/components/command/email-approval-details";
import { NotionApprovalDetails } from "@/components/command/notion-approval-details";
import { QualityPanel } from "@/components/command/quality-panel";
import { SheetApprovalDetails } from "@/components/command/sheet-approval-details";
import { SocialApprovalDetails } from "@/components/command/social-approval-details";
import { Button } from "@/components/ui/button";
import type {
  Approval,
  CalendarActionType,
  CrmActionType,
  Draft,
  NotionActionType,
  SheetActionType,
  SocialActionType,
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
  "crm.create_contact": {
    title: "Add this HubSpot contact?",
    button: "Approve and add",
    note: "I checked HubSpot and found no contact with this email. Approving creates it now.",
    canRevise: false,
  },
  "crm.update_contact": {
    title: "Update this HubSpot contact?",
    button: "Approve and update",
    note: "Approving changes only the fields shown here.",
    canRevise: false,
  },
  "crm.create_note": {
    title: "Add this note in HubSpot?",
    button: "Approve and add note",
    note: "Approving adds exactly this note to the contact's timeline.",
    canRevise: false,
  },
  "crm.salesforce.create_contact": {
    title: "Add this Salesforce contact?",
    button: "Approve and add",
    note: "I checked Salesforce and found no contact with this email. Approving creates it now.",
    canRevise: false,
  },
  "crm.salesforce.update_contact": {
    title: "Update this Salesforce contact?",
    button: "Approve and update",
    note: "Approving changes only the fields shown here.",
    canRevise: false,
  },
  "crm.salesforce.create_note": {
    title: "Add this note in Salesforce?",
    button: "Approve and add note",
    note: "Approving adds exactly this note to the contact's Notes.",
    canRevise: false,
  },
  "crm.zoho.create_contact": {
    title: "Add this Zoho CRM contact?",
    button: "Approve and add",
    note: "I checked Zoho CRM and found no contact with this email. Approving creates it now and skips Zoho automations.",
    canRevise: false,
  },
  "crm.zoho.update_contact": {
    title: "Update this Zoho CRM contact?",
    button: "Approve and update",
    note: "Approving changes only the fields shown here and skips Zoho automations.",
    canRevise: false,
  },
  "crm.zoho.create_note": {
    title: "Add this note in Zoho CRM?",
    button: "Approve and add note",
    note: "Approving adds exactly this note to the contact's Notes.",
    canRevise: false,
  },
  "notion.create_page": {
    title: "Create this Notion page?",
    button: "Approve and create",
    note: "Approving creates exactly this page under the page shown.",
    canRevise: false,
  },
  "notion.append_content": {
    title: "Add this to the Notion page?",
    button: "Approve and add",
    note: "Approving adds exactly this content to the end of the page. Existing content stays.",
    canRevise: false,
  },
  "facebook.publish_post": {
    title: "Publish this on your Facebook Page?",
    button: "Approve and publish",
    note: "Approving publishes exactly this text on the Page right away. Anyone can see it.",
    canRevise: false,
  },
  "facebook.publish_photo": {
    title: "Publish this photo on your Facebook Page?",
    button: "Approve and publish",
    note: "Approving publishes exactly this image and text on the Page right away. Anyone can see it.",
    canRevise: false,
  },
  "instagram.publish_photo": {
    title: "Publish this on Instagram?",
    button: "Approve and publish",
    note: "Approving publishes exactly this image and caption right away. Anyone can see it.",
    canRevise: false,
  },
};

const isSheet = (a: Approval): a is Extract<Approval, { action_type: SheetActionType }> =>
  a.action_type.startsWith("sheets.");

const isCalendar = (a: Approval): a is Extract<Approval, { action_type: CalendarActionType }> =>
  a.action_type.startsWith("calendar.");

const isCrm = (a: Approval): a is Extract<Approval, { action_type: CrmActionType }> =>
  a.action_type.startsWith("crm.");

const isNotion = (a: Approval): a is Extract<Approval, { action_type: NotionActionType }> =>
  a.action_type.startsWith("notion.");

const isSocial = (a: Approval): a is Extract<Approval, { action_type: SocialActionType }> =>
  a.action_type.startsWith("facebook.") || a.action_type.startsWith("instagram.");

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
      className="relative flex flex-col gap-4 overflow-hidden rounded-2xl border bg-card p-5 shadow-[0_24px_60px_-34px_rgba(55,48,163,0.45)] before:absolute before:inset-x-0 before:top-0 before:h-1 before:bg-amber sm:p-6"
    >
      <header className="flex flex-col gap-2">
        <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-amber-wash px-2.5 py-0.5 text-xs font-medium text-amber-700">
          <span className="size-1.5 rounded-full bg-amber" aria-hidden />
          Waiting for your approval
        </span>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="heading text-xl">{copy.title}</h2>
          <span className="text-xs text-muted-foreground">
            Version {approval.content_version}, fingerprint{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono" title={approval.payload_hash}>
              {approval.payload_hash.slice(0, 12)}
            </code>
          </span>
        </div>
      </header>

      {approval.action_type === "linkedin.publish_post" ? (
        <>
          <p className="text-sm text-muted-foreground">
            Posting as <span className="font-medium text-foreground">{accountLabel}</span>.{" "}
            {copy.note} Any change creates a new version that needs your approval again.
          </p>

          <blockquote className="max-h-96 overflow-y-auto rounded-xl border border-dashed bg-background p-4 text-[15px] leading-relaxed whitespace-pre-wrap">
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
                    approval.payload.visibility === v ? "border-indigo bg-indigo-wash" : "bg-card"
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
      ) : isCrm(approval) ? (
        <>
          <p className="text-sm text-muted-foreground">{copy.note}</p>
          <CrmApprovalDetails actionType={approval.action_type} payload={approval.payload} />
        </>
      ) : isNotion(approval) ? (
        <>
          <p className="text-sm text-muted-foreground">{copy.note}</p>
          <NotionApprovalDetails actionType={approval.action_type} payload={approval.payload} />
        </>
      ) : isSocial(approval) ? (
        <>
          <p className="text-sm text-muted-foreground">{copy.note}</p>
          <SocialApprovalDetails actionType={approval.action_type} payload={approval.payload} />
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">{copy.note}</p>
          <EmailApprovalDetails actionType={approval.action_type} payload={approval.payload} />
        </>
      )}

      <div className="flex flex-wrap gap-2 border-t pt-4">
        <Button
          size="lg"
          disabled={busy}
          onClick={() =>
            run(() => approveAction(approval.id, approval.payload_hash, approval.content_version))
          }
        >
          {pending ? "Working…" : copy.button}
        </Button>
        {copy.canRevise && (
          <Button size="lg" variant="outline" disabled={busy} onClick={() => setShowChanges((s) => !s)}>
            Request changes
          </Button>
        )}
        <Button
          size="lg"
          variant="ghost"
          disabled={busy}
          className="text-muted-foreground hover:text-[#b23a1f]"
          onClick={() => run(() => rejectAction(approval.id))}
        >
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
        <p role="alert" className="rounded-lg bg-coral-wash px-3 py-2 text-sm text-[#b23a1f]">
          {error ?? changesState.error}
        </p>
      )}
    </section>
  );
}
