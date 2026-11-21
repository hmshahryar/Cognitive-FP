import type { CalendarActionType, CalendarPayload } from "@/lib/command";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{value}</dd>
    </div>
  );
}

/** Always shown in the calendar's timezone, so the card matches what will be saved. */
function formatRange(start: string, end: string, timeZone: string) {
  const day = new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone,
  });
  const clock = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  });
  const from = new Date(start);
  const to = new Date(end);
  const sameDay = day.format(from) === day.format(to);
  return sameDay
    ? `${day.format(from)}, ${clock.format(from)} to ${clock.format(to)}`
    : `${day.format(from)}, ${clock.format(from)} to ${day.format(to)}, ${clock.format(to)}`;
}

/** The exact calendar change being approved. Event text is displayed as plain text only. */
export function CalendarApprovalDetails({
  actionType,
  payload,
}: {
  actionType: CalendarActionType;
  payload: CalendarPayload;
}) {
  const { event, timezone, conflicts } = payload;
  const current = event.all_day
    ? `${new Intl.DateTimeFormat("en-GB", {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      }).format(new Date(`${event.start}T00:00:00Z`))} (all day)`
    : formatRange(event.start, event.end, timezone);
  const guests = event.attendees.join(", ");

  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1 rounded-lg border bg-background p-4">
        <Row label="Calendar" value={payload.calendar} />
        <Row label="Event" value={event.title} />
        {actionType === "calendar.reschedule_event" && event.new_start && event.new_end ? (
          <>
            <Row label="Current" value={current} />
            <Row label="New time" value={formatRange(event.new_start, event.new_end, timezone)} />
          </>
        ) : (
          <Row label="When" value={current} />
        )}
        {!event.all_day && <Row label="Timezone" value={timezone} />}
        <Row label="Guests" value={guests || "No guests"} />
        {event.location && <Row label="Location" value={event.location} />}
        {actionType === "calendar.create_event" && (
          <Row label="Google Meet" value={event.meet ? "Add a Meet link" : "No Meet link"} />
        )}
      </dl>

      {guests && (
        <p className="text-sm text-muted-foreground">
          Google Calendar will email {event.attendees.length === 1 ? "this guest" : "these guests"}{" "}
          about this change.
        </p>
      )}
      {event.recurring && (
        <p className="text-sm text-muted-foreground">
          This changes only this occurrence of the repeating event.
        </p>
      )}

      {conflicts.length > 0 && (
        <section
          role="alert"
          className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
        >
          <h3 className="font-medium">This overlaps with:</h3>
          <ul className="mt-1 list-disc pl-5">
            {conflicts.map((conflict) => (
              <li key={`${conflict.title}-${conflict.start}`}>
                {conflict.title} ({formatRange(conflict.start, conflict.end, timezone)})
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
