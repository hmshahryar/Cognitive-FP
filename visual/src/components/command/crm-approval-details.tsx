import type { CrmActionType, CrmPayload } from "@/lib/command";

const LABELS: Record<string, string> = {
  firstname: "First name",
  lastname: "Last name",
  email: "Email",
  phone: "Phone",
  company: "Company",
  jobtitle: "Job title",
  website: "Website",
  city: "City",
  country: "Country",
};

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{value}</dd>
    </div>
  );
}

/** The exact CRM change being approved. Contact data is displayed as plain text only. */
export function CrmApprovalDetails({
  actionType,
  payload,
}: {
  actionType: CrmActionType;
  payload: CrmPayload;
}) {
  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1 rounded-lg border bg-background p-4">
        <Row label="Contact" value={payload.contact.email} />
        {actionType.endsWith(".create_contact") &&
          Object.entries(payload.properties ?? {})
            .filter(([field]) => field !== "email")
            .map(([field, value]) => (
              <Row key={field} label={LABELS[field] ?? field} value={value} />
            ))}
      </dl>

      {actionType.endsWith(".update_contact") && (
        <div className="overflow-x-auto rounded-lg border bg-background">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Contact fields to change</caption>
            <thead className="border-b text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Field</th>
                <th scope="col" className="px-3 py-2 font-medium">Current</th>
                <th scope="col" className="px-3 py-2 font-medium">New value</th>
              </tr>
            </thead>
            <tbody>
              {(payload.changes ?? []).map((change) => (
                <tr key={change.field} className="border-b last:border-b-0">
                  <td className="px-3 py-2">{LABELS[change.field] ?? change.field}</td>
                  <td className="break-words px-3 py-2">{change.previous || "(empty)"}</td>
                  <td className="break-words px-3 py-2 font-medium">{change.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {actionType.endsWith(".create_note") && (
        <blockquote className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border bg-background p-4 text-sm">
          {payload.note}
        </blockquote>
      )}

      {payload.mode === "fill" && (
        <p className="text-xs text-muted-foreground">
          This contact already exists, so only its empty fields will be filled.
        </p>
      )}

      {(payload.skipped ?? []).length > 0 && (
        <div className="rounded-lg border border-amber-500/40 p-3 text-xs">
          <p className="font-medium">These details will not be saved:</p>
          <ul className="mt-1 list-disc pl-4 text-muted-foreground">
            {payload.skipped?.map((item) => (
              <li key={item} className="break-words">{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
