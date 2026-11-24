import type { NotionActionType, NotionPayload } from "@/lib/command";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{value}</dd>
    </div>
  );
}

/** The exact Notion change being approved. Page data is displayed as plain text only. */
export function NotionApprovalDetails({
  actionType,
  payload,
}: {
  actionType: NotionActionType;
  payload: NotionPayload;
}) {
  const creating = actionType === "notion.create_page";
  const target = creating ? payload.parent : payload.page;
  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1 rounded-lg border bg-background p-4">
        <Row label={creating ? "Goes under" : "Page"} value={target?.title || "Untitled"} />
        {creating && <Row label="New title" value={payload.title ?? ""} />}
      </dl>
      {payload.body ? (
        <blockquote className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border bg-background p-4 text-sm">
          {payload.body}
        </blockquote>
      ) : (
        <p className="text-xs text-muted-foreground">The new page will be empty.</p>
      )}
    </div>
  );
}
