import type { Publication } from "@/lib/command";

const isLinkedInUrl = (url: string) => url.startsWith("https://www.linkedin.com/feed/update/");

/** The result of the task's latest publish attempt. */
export function PublicationStatus({ publication }: { publication: Publication | undefined }) {
  if (!publication) return null;

  if (publication.status === "PUBLISHED") {
    return (
      <p className="text-xs text-emerald-700 dark:text-emerald-400">
        Published on LinkedIn
        {publication.external_url && isLinkedInUrl(publication.external_url) && (
          <>
            {" · "}
            <a
              href={publication.external_url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
            >
              View post
            </a>
          </>
        )}
      </p>
    );
  }
  if (publication.status === "SENDING") {
    return (
      <p className="text-xs text-muted-foreground" aria-live="polite">
        Publishing to LinkedIn…
      </p>
    );
  }
  if (publication.status === "NEEDS_CHECK") {
    return (
      <p role="alert" className="text-xs text-amber-700 dark:text-amber-400">
        Result unknown. Check your LinkedIn profile before posting again. It will not be resent
        automatically.
      </p>
    );
  }
  return <p className="text-xs text-destructive">LinkedIn did not publish this post.</p>;
}
