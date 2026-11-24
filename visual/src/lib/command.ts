import "server-only";

import { backendFetch } from "@/lib/api";

export type Chat = { id: string; title: string; created_at: string; updated_at: string };

export type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  task_id: string | null;
  created_at: string;
};

export type TaskStatus =
  | "QUEUED"
  | "RUNNING"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "EXECUTING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "REJECTED";

export type Task = {
  id: string;
  chat_id: string;
  request: string;
  status: TaskStatus;
  current_execution_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  route_label: string | null;
};

export type ChatDetail = { chat: Chat; messages: Message[]; tasks: Task[] };

export type DraftSource = {
  id: string;
  url: string;
  title: string;
  publisher: string | null;
  published_at: string | null;
};

export type Draft = {
  id: string;
  version: number;
  text: string;
  source_ids: string[];
  sources: DraftSource[];
  review: {
    approved?: boolean;
    accuracy?: number;
    relevance?: number;
    quality?: number;
    formatting?: number;
    professionalism?: number;
    readability?: number;
    originality?: number;
    repetition?: number;
    natural_language?: number;
    unsupported_claims?: string[];
    issues: string[];
    required_changes: string[];
  };
  accepted: boolean;
  is_final: boolean;
  model: string;
  provider: string;
  prompt_version: string;
  claims: DraftClaim[];
  quality: { blocking?: string[]; style?: string[] };
  research: {
    mode?: "post_as_is" | "write" | "research" | "email" | "calendar" | "sheets" | "crm" | "notion" | "social";
  };
  created_at: string;
};

export type DraftClaim = {
  text: string;
  source_ids: string[];
  basis: "full_text" | "snippet";
};

export const getDrafts = (taskId: string) =>
  backendFetch<Draft[]>(`/tasks/${encodeURIComponent(taskId)}/drafts`);

/** Each revision cycle has its own final draft: the newest one is the current post. */
export const latestFinalDraft = (drafts: Draft[]) =>
  drafts.reduce<Draft | null>(
    (latest, d) => (d.is_final && (!latest || d.version > latest.version) ? d : latest),
    null,
  );

export type Publication = {
  id: string;
  approval_id: string;
  tool_slug: string;
  status: "SENDING" | "PUBLISHED" | "FAILED" | "NEEDS_CHECK";
  external_id: string | null;
  external_url: string | null;
  created_at: string;
  finished_at: string | null;
};

export const getPublications = (taskId: string) =>
  backendFetch<Publication[]>(`/tasks/${encodeURIComponent(taskId)}/publications`);

export type Visibility = "PUBLIC" | "CONNECTIONS";

export type LinkedInPayload = {
  author: string;
  commentary: string;
  visibility: Visibility;
  lifecycleState: string;
};

export type EmailPayload = {
  account: string;
  arguments: {
    recipient_email?: string;
    extra_recipients?: string[];
    recipients?: string[];
    cc?: string[];
    bcc?: string[];
    subject?: string;
    body?: string;
    additional_text?: string;
  };
  message?: { id: string; sender: string; subject: string; date: string; preview: string };
};

type ApprovalBase = {
  id: string;
  task_id: string;
  execution_id: string;
  toolkit_slug: string;
  tool_slug: string;
  target_account_id: string;
  payload_hash: string;
  content_version: number;
  status: string;
  decision_note: string | null;
  created_at: string;
};

export type EmailActionType = "gmail.send_email" | "gmail.forward_email" | "gmail.trash_email";

export type CalendarActionType =
  | "calendar.create_event"
  | "calendar.reschedule_event"
  | "calendar.cancel_event";

export type CalendarPayload = {
  calendar: string;
  timezone: string;
  arguments: { send_updates: "all" | "none"; create_meeting_room?: boolean };
  event: {
    title: string;
    start: string;
    end: string;
    attendees: string[];
    meet?: boolean;
    location?: string | null;
    all_day?: boolean;
    recurring?: boolean;
    new_start?: string;
    new_end?: string;
  };
  conflicts: { title: string; start: string; end: string }[];
};

export type SheetActionType =
  | "sheets.create_spreadsheet"
  | "sheets.append_rows"
  | "sheets.update_cell";

export type SheetPayload = {
  spreadsheet: { id: string | null; name: string; tab: string | null };
  rows?: string[][];
  cell?: string;
  value?: string;
  previous?: string;
};

export type CrmActionType =
  | "crm.create_contact"
  | "crm.update_contact"
  | "crm.create_note"
  | "crm.salesforce.create_contact"
  | "crm.salesforce.update_contact"
  | "crm.salesforce.create_note"
  | "crm.zoho.create_contact"
  | "crm.zoho.update_contact"
  | "crm.zoho.create_note";

export type CrmPayload = {
  contact: { id: string | null; email: string };
  account?: { id: string; name: string } | null;
  skipped?: string[];
  properties?: Record<string, string>;
  changes?: { field: string; previous: string; value: string }[];
  mode?: "fill" | "change";
  note?: string;
};

export type NotionActionType = "notion.create_page" | "notion.append_content";

export type NotionPayload = {
  parent?: { id: string; title: string };
  page?: { id: string; title: string };
  title?: string;
  body: string;
};

export type SocialActionType =
  | "facebook.publish_post"
  | "facebook.publish_photo"
  | "instagram.publish_photo";

export type SocialPayload = {
  page?: { id: string; name: string };
  account?: { id: string; username: string };
  message?: string;
  caption?: string;
  design?: { id: string; edit_url: string | null };
  image?: {
    url: string;
    sha256: string;
    content_type: string;
    size: number;
    width: number | null;
    height: number | null;
  };
};

export type Approval = ApprovalBase &
  (
    | { action_type: "linkedin.publish_post"; payload: LinkedInPayload }
    | { action_type: EmailActionType; payload: EmailPayload }
    | { action_type: CalendarActionType; payload: CalendarPayload }
    | { action_type: SheetActionType; payload: SheetPayload }
    | { action_type: CrmActionType; payload: CrmPayload }
    | { action_type: NotionActionType; payload: NotionPayload }
    | { action_type: SocialActionType; payload: SocialPayload }
  );

export const getPendingApprovals = (taskId: string) =>
  backendFetch<Approval[]>(`/approvals?task_id=${encodeURIComponent(taskId)}&status=PENDING`);

export const listChats = () => backendFetch<Chat[]>("/chats");
export const getChat = (id: string) => backendFetch<ChatDetail>(`/chats/${encodeURIComponent(id)}`);
