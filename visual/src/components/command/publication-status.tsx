import type { Publication } from "@/lib/command";

const TOOL_COPY: Record<string, { done: string; link: string; working: string; failed: string; check: string; prefix: string }> = {
  LINKEDIN_CREATE_LINKED_IN_POST: {
    done: "Published on LinkedIn",
    link: "View post",
    working: "Publishing to LinkedIn…",
    failed: "LinkedIn did not publish this post.",
    check: "Check your LinkedIn profile before posting again.",
    prefix: "https://www.linkedin.com/feed/update/",
  },
  GMAIL_SEND_EMAIL: {
    done: "Email sent",
    link: "Open in Gmail",
    working: "Sending email…",
    failed: "Gmail did not send this email.",
    check: "Check your Sent folder before trying again.",
    prefix: "https://mail.google.com/",
  },
  GMAIL_FORWARD_MESSAGE: {
    done: "Email forwarded",
    link: "Open in Gmail",
    working: "Forwarding email…",
    failed: "Gmail did not forward this email.",
    check: "Check your Sent folder before trying again.",
    prefix: "https://mail.google.com/",
  },
  GMAIL_MOVE_TO_TRASH: {
    done: "Moved to Gmail Trash",
    link: "Open in Gmail",
    working: "Moving email to Trash…",
    failed: "Gmail did not move this email.",
    check: "Check your Gmail Trash before trying again.",
    prefix: "https://mail.google.com/",
  },
  GOOGLECALENDAR_CREATE_EVENT: {
    done: "Event scheduled",
    link: "Open event",
    working: "Scheduling event…",
    failed: "Google Calendar did not schedule this event.",
    check: "Check your calendar before trying again.",
    prefix: "https://www.google.com/calendar/",
  },
  GOOGLECALENDAR_PATCH_EVENT: {
    done: "Event rescheduled",
    link: "Open event",
    working: "Rescheduling event…",
    failed: "Google Calendar did not reschedule this event.",
    check: "Check your calendar before trying again.",
    prefix: "https://www.google.com/calendar/",
  },
  GOOGLECALENDAR_DELETE_EVENT: {
    done: "Event cancelled",
    link: "Open calendar",
    working: "Cancelling event…",
    failed: "Google Calendar did not cancel this event.",
    check: "Check your calendar before trying again.",
    prefix: "https://calendar.google.com/",
  },
  GOOGLESHEETS_CREATE_GOOGLE_SHEET1: {
    done: "Sheet created",
    link: "Open sheet",
    working: "Creating sheet…",
    failed: "Google Sheets did not create this sheet.",
    check: "Check your Google Drive before trying again.",
    prefix: "https://docs.google.com/spreadsheets/",
  },
  GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND: {
    done: "Rows added",
    link: "Open sheet",
    working: "Adding rows…",
    failed: "Google Sheets did not add these rows.",
    check: "Check the sheet before trying again.",
    prefix: "https://docs.google.com/spreadsheets/",
  },
  GOOGLESHEETS_VALUES_UPDATE: {
    done: "Cell updated",
    link: "Open sheet",
    working: "Updating cell…",
    failed: "Google Sheets did not update this cell.",
    check: "Check the sheet before trying again.",
    prefix: "https://docs.google.com/spreadsheets/",
  },
  HUBSPOT_CREATE_CONTACT: {
    done: "Contact created in HubSpot",
    link: "Open contact",
    working: "Creating contact…",
    failed: "HubSpot did not create this contact.",
    check: "Check HubSpot before trying again.",
    prefix: "https://app.hubspot.com/",
  },
  HUBSPOT_UPDATE_CONTACT: {
    done: "Contact updated in HubSpot",
    link: "Open contact",
    working: "Updating contact…",
    failed: "HubSpot did not update this contact.",
    check: "Check the contact in HubSpot before trying again.",
    prefix: "https://app.hubspot.com/",
  },
  HUBSPOT_CREATE_NOTE: {
    done: "Note added in HubSpot",
    link: "Open contact",
    working: "Adding note…",
    failed: "HubSpot did not add this note.",
    check: "Check the contact's timeline before trying again.",
    prefix: "https://app.hubspot.com/",
  },
  SALESFORCE_CREATE_CONTACT: {
    done: "Contact created in Salesforce",
    link: "Open contact",
    working: "Creating contact…",
    failed: "Salesforce did not create this contact.",
    check: "Check Salesforce before trying again.",
    prefix: "https://login.salesforce.com/",
  },
  SALESFORCE_UPDATE_CONTACT: {
    done: "Contact updated in Salesforce",
    link: "Open contact",
    working: "Updating contact…",
    failed: "Salesforce did not update this contact.",
    check: "Check the contact in Salesforce before trying again.",
    prefix: "https://login.salesforce.com/",
  },
  SALESFORCE_CREATE_NOTE: {
    done: "Note added in Salesforce",
    link: "Open contact",
    working: "Adding note…",
    failed: "Salesforce did not add this note.",
    check: "Check the contact's Notes before trying again.",
    prefix: "https://login.salesforce.com/",
  },
  ZOHO_CREATE_CONTACT: {
    done: "Contact created in Zoho CRM",
    link: "Open contact",
    working: "Creating contact…",
    failed: "Zoho CRM did not create this contact.",
    check: "Check Zoho CRM before trying again.",
    prefix: "https://crm.zoho.com/",
  },
  ZOHO_UPDATE_ZOHO_RECORD: {
    done: "Contact updated in Zoho CRM",
    link: "Open contact",
    working: "Updating contact…",
    failed: "Zoho CRM did not update this contact.",
    check: "Check the contact in Zoho CRM before trying again.",
    prefix: "https://crm.zoho.com/",
  },
  ZOHO_CREATE_NOTE: {
    done: "Note added in Zoho CRM",
    link: "Open contact",
    working: "Adding note…",
    failed: "Zoho CRM did not add this note.",
    check: "Check the contact's Notes before trying again.",
    prefix: "https://crm.zoho.com/",
  },
  NOTION_CREATE_NOTION_PAGE: {
    done: "Page created in Notion",
    link: "Open page",
    working: "Creating page…",
    failed: "Notion did not create this page.",
    check: "Check Notion before trying again.",
    prefix: "https://www.notion.so/",
  },
  FACEBOOK_CREATE_POST: {
    done: "Published on your Facebook Page",
    link: "View post",
    working: "Publishing to Facebook…",
    failed: "Facebook did not publish this post. Nothing went live.",
    check: "Check your Facebook Page before posting again.",
    prefix: "https://www.facebook.com/",
  },
  FACEBOOK_CREATE_PHOTO_POST: {
    done: "Photo published on your Facebook Page",
    link: "View post",
    working: "Publishing to Facebook…",
    failed: "Facebook did not publish this photo. Nothing went live.",
    check: "Check your Facebook Page before posting again.",
    prefix: "https://www.facebook.com/",
  },
  INSTAGRAM_POST_IG_USER_MEDIA: {
    done: "Published on Instagram",
    link: "View post",
    working: "Publishing to Instagram…",
    failed: "Instagram did not publish this post. Nothing went live.",
    check: "Check your Instagram profile before posting again.",
    prefix: "https://www.instagram.com/",
  },
  NOTION_ADD_MULTIPLE_PAGE_CONTENT: {
    done: "Content added in Notion",
    link: "Open page",
    working: "Adding content…",
    failed: "Notion did not add this content.",
    check: "Check the page in Notion before trying again.",
    prefix: "https://www.notion.so/",
  },
};

/** The result of the task's latest approved action. */
export function PublicationStatus({ publication }: { publication: Publication | undefined }) {
  if (!publication) return null;
  const copy = TOOL_COPY[publication.tool_slug] ?? TOOL_COPY.LINKEDIN_CREATE_LINKED_IN_POST;

  if (publication.status === "PUBLISHED") {
    return (
      <p className="text-xs text-emerald-700">
        {copy.done}
        {publication.external_url?.startsWith(copy.prefix) && (
          <>
            {" · "}
            <a
              href={publication.external_url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
            >
              {copy.link}
            </a>
          </>
        )}
      </p>
    );
  }
  if (publication.status === "SENDING") {
    return (
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {copy.working}
      </p>
    );
  }
  if (publication.status === "NEEDS_CHECK") {
    return (
      <p role="alert" className="text-xs text-amber-700">
        Result unknown. {copy.check} It will not be repeated automatically.
      </p>
    );
  }
  return <p className="text-xs text-destructive">{copy.failed}</p>;
}
