import type { Draft } from "@/lib/command";

const SCORES = [
  ["accuracy", "Accuracy"],
  ["professionalism", "Professional"],
  ["readability", "Readability"],
  ["originality", "Originality"],
  ["repetition", "Low repetition"],
  ["natural_language", "Natural language"],
] as const;

const isWebUrl = (url: string) => /^https?:\/\//i.test(url);

/** What the quality review found, so the user can verify claims before approving. */
export function QualityPanel({ draft }: { draft: Draft }) {
  if (draft.research.mode === "post_as_is") {
    return (
      <p className="rounded-lg border bg-background p-3 text-sm text-muted-foreground">
        Your exact text. AI did not write, review or change it.
      </p>
    );
  }

  const notes = [
    ...(draft.review.unsupported_claims ?? []).map((c) => `Unsupported: ${c}`),
    ...(draft.quality.style ?? []),
    ...(draft.accepted ? [] : draft.review.required_changes),
  ];
  const uniqueNotes = [...new Set(notes)];
  const snippetOnly = draft.claims.filter((c) => c.basis === "snippet").length;

  return (
    <section aria-label="Quality review" className="flex flex-col gap-3 rounded-lg border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Quality review</h3>
        <span className="text-xs text-muted-foreground">
          {draft.accepted ? "Passed all checks" : "Passed blocking checks, with editor notes"}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-1 text-center text-xs sm:grid-cols-3">
        {SCORES.filter(([key]) => draft.review[key] !== undefined).map(([key, label]) => (
          <div key={key} className="rounded bg-muted px-2 py-1">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium">{draft.review[key]}/5</dd>
          </div>
        ))}
      </dl>

      {draft.research.mode === "write" && (
        <p className="text-xs text-muted-foreground">
          No web research used. Check any factual statements before approving.
        </p>
      )}

      {uniqueNotes.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {uniqueNotes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}

      {draft.claims.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Claims to verify ({draft.claims.length}
            {snippetOnly > 0 && `, ${snippetOnly} checked from a snippet only`})
          </summary>
          <ul className="mt-2 space-y-2">
            {draft.claims.map((claim) => (
              <li key={claim.text} className="text-xs">
                {claim.text}{" "}
                <span className="text-muted-foreground">
                  [{claim.source_ids.join(", ")}]
                  {claim.basis === "snippet" ? " · snippet only" : " · full text"}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {draft.sources.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Sources ({draft.sources.length})
          </summary>
          <ul className="mt-2 space-y-1 text-xs">
            {draft.sources.map((source) => (
              <li key={source.id}>
                {isWebUrl(source.url) ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline-offset-2 hover:underline"
                  >
                    [{source.id}] {source.title}
                  </a>
                ) : (
                  <span>
                    [{source.id}] {source.title}
                  </span>
                )}
                {source.publisher && (
                  <span className="text-muted-foreground"> — {source.publisher}</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="text-xs text-muted-foreground">
        Generated {new Date(draft.created_at).toISOString().slice(0, 16).replace("T", " ")} UTC ·{" "}
        {draft.model} · {draft.prompt_version}
      </p>
    </section>
  );
}
