import type { EmailActionType, EmailPayload } from "@/lib/command";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[72px_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{value}</dd>
    </div>
  );
}

/** The exact email action being approved. Email content is displayed as plain text only. */
export function EmailApprovalDetails({
  actionType,
  payload,
}: {
  actionType: EmailActionType;
  payload: EmailPayload;
}) {
  const args = payload.arguments;
  const to = [args.recipient_email, ...(args.extra_recipients ?? []), ...(args.recipients ?? [])]
    .filter(Boolean)
    .join(", ");
  const target = payload.message;

  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1 rounded-lg border bg-background p-4">
        <Row label="Account" value={payload.account} />
        {to && <Row label="To" value={to} />}
        {!!args.cc?.length && <Row label="Cc" value={args.cc.join(", ")} />}
        {!!args.bcc?.length && <Row label="Bcc" value={args.bcc.join(", ")} />}
        {args.subject && <Row label="Subject" value={args.subject} />}
      </dl>

      {args.body && (
        <blockquote className="max-h-96 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border bg-background p-4 text-sm">
          {args.body}
        </blockquote>
      )}

      {target && (
        <section aria-label="Target email" className="flex flex-col gap-2 rounded-lg border bg-background p-4">
          <h3 className="text-sm font-medium">
            {actionType === "gmail.trash_email" ? "Email to move to Trash" : "Email to forward"}
          </h3>
          <dl className="flex flex-col gap-1">
            <Row label="From" value={target.sender} />
            <Row label="Subject" value={target.subject} />
            {target.date && <Row label="Date" value={target.date} />}
          </dl>
          {target.preview && (
            <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">
              {target.preview}
            </p>
          )}
        </section>
      )}

      {args.additional_text && (
        <p className="whitespace-pre-wrap break-words text-sm">
          <span className="text-muted-foreground">Your note: </span>
          {args.additional_text}
        </p>
      )}
    </div>
  );
}
