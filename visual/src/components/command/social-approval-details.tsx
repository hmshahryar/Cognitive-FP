import type { SocialActionType, SocialPayload } from "@/lib/command";

/** The exact Facebook Page or Instagram post being approved. Text is displayed as plain text. */
export function SocialApprovalDetails({
  actionType,
  payload,
}: {
  actionType: SocialActionType;
  payload: SocialPayload;
}) {
  const instagram = actionType === "instagram.publish_photo";
  const where = instagram
    ? `Instagram @${payload.account?.username ?? ""}`
    : `Facebook Page ${payload.page?.name ?? ""}`;
  const text = instagram ? payload.caption : payload.message;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm">
        Posting to <span className="font-medium">{where}</span>
      </p>
      {payload.image && (
        <figure className="flex flex-col gap-1">
          {/* A plain img: the user's own link, shown exactly as Meta will fetch it. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={payload.image.url}
            alt="Image that will be posted"
            className="max-h-80 w-auto self-start rounded-lg border object-contain"
          />
          <figcaption className="break-all text-xs text-muted-foreground">
            {payload.image.width && payload.image.height
              ? `${payload.image.width}×${payload.image.height} · `
              : ""}
            {Math.round(payload.image.size / 1024)} KB · fingerprint{" "}
            <code>{payload.image.sha256.slice(0, 12)}</code>. If the image at this link changes,
            the post is blocked.
          </figcaption>
          {payload.design && (
            <p className="text-xs text-muted-foreground">
              Banner made in your Canva.
              {payload.design.edit_url?.startsWith("https://www.canva.com/") && (
                <>
                  {" "}
                  <a
                    href={payload.design.edit_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline underline-offset-2"
                  >
                    Open in Canva
                  </a>
                  . Edits there need a new banner command before they can be posted.
                </>
              )}
            </p>
          )}
        </figure>
      )}
      {text ? (
        <blockquote className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border bg-background p-4 text-sm">
          {text}
        </blockquote>
      ) : (
        <p className="text-xs text-muted-foreground">No text. Only the image will be posted.</p>
      )}
    </div>
  );
}
