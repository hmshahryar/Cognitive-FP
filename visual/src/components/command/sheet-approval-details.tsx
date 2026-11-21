import type { SheetActionType, SheetPayload } from "@/lib/command";

const PREVIEW_ROWS = 20;

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{value}</dd>
    </div>
  );
}

/** The exact sheet change being approved. Cell content is displayed as plain text only. */
export function SheetApprovalDetails({
  actionType,
  payload,
}: {
  actionType: SheetActionType;
  payload: SheetPayload;
}) {
  const rows = payload.rows ?? [];
  const shown = rows.slice(0, PREVIEW_ROWS);

  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1 rounded-lg border bg-background p-4">
        <Row label="Sheet" value={payload.spreadsheet.name} />
        {payload.spreadsheet.tab && <Row label="Tab" value={payload.spreadsheet.tab} />}
        {actionType === "sheets.update_cell" && (
          <>
            <Row label="Cell" value={payload.cell ?? ""} />
            <Row label="Current" value={payload.previous || "(empty)"} />
            <Row label="New value" value={payload.value || "(empty)"} />
          </>
        )}
      </dl>

      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-lg border bg-background">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              {actionType === "sheets.create_spreadsheet" ? "Columns to create" : "Rows to add"}
            </caption>
            <tbody>
              {shown.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b last:border-b-0">
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="whitespace-pre-wrap break-words px-3 py-2">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > PREVIEW_ROWS && (
        <p className="text-xs text-muted-foreground">
          Showing {PREVIEW_ROWS} of {rows.length} rows. All of them will be written.
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Values are saved as plain text, so formulas will not run.
      </p>
    </div>
  );
}
