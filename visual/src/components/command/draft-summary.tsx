import { latestFinalDraft, type Draft } from "@/lib/command";

const SCORES = ["accuracy", "relevance", "quality", "formatting"] as const;

/** Review result and sources for a task's final draft. */
export function DraftSummary({ drafts }: { drafts: Draft[] }) {
  const final = latestFinalDraft(drafts);
  if (!final) return null;
  if (final.research.mode === "sheets") {
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        Sheet change · version {final.version}
      </p>
    );
  }
  if (final.research.mode === "crm") {
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        CRM change · version {final.version}
      </p>
    );
  }
  if (final.research.mode === "social") {
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        Draft v{final.version} · your exact text
      </p>
    );
  }
  if (final.research.mode === "notion") {
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        Notion change · version {final.version}
      </p>
    );
  }
  if (final.research.mode === "calendar") {
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        Calendar change · version {final.version}
      </p>
    );
  }
  if (final.research.mode === "email") {
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        Email version {final.version}
        {final.provider === "user" ? " · your exact command" : " · written by AI"}
      </p>
    );
  }
  if (final.research.mode === "post_as_is") {
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        Draft v{final.version} · your exact text
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2 border-t pt-3 text-sm">
      <p className="text-xs text-muted-foreground">
        Draft v{final.version}
        {drafts.length > 1 && ` · ${drafts.length} versions reviewed`}
        {final.accepted ? " · approved by reviewer" : " · reviewer had notes"}
      </p>
      <dl className="grid grid-cols-4 gap-1 text-center text-xs">
        {SCORES.map((k) => (
          <div key={k} className="rounded bg-muted px-1 py-1">
            <dt className="capitalize text-muted-foreground">{k}</dt>
            <dd className="font-medium">{final.review[k]}/5</dd>
          </div>
        ))}
      </dl>
      {final.sources.length > 0 && (
        <details>
          <summary className="cursor-pointer text-muted-foreground">
            Sources ({final.sources.length})
          </summary>
          <ul className="mt-1 space-y-1">
            {final.sources.map((s) => (
              <li key={s.id}>
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline-offset-2 hover:underline"
                >
                  [{s.id}] {s.title}
                </a>
                {s.publisher && <span className="text-muted-foreground"> — {s.publisher}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!final.accepted && final.review.required_changes.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-muted-foreground">
          {final.review.required_changes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
