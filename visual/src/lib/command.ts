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
  research: { mode?: "post_as_is" | "write" | "research" };
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
  status: "SENDING" | "PUBLISHED" | "FAILED" | "NEEDS_CHECK";
  external_id: string | null;
  external_url: string | null;
  created_at: string;
  finished_at: string | null;
};

export const getPublications = (taskId: string) =>
  backendFetch<Publication[]>(`/tasks/${encodeURIComponent(taskId)}/publications`);

export type Visibility = "PUBLIC" | "CONNECTIONS";

export type Approval = {
  id: string;
  task_id: string;
  execution_id: string;
  action_type: string;
  toolkit_slug: string;
  tool_slug: string;
  target_account_id: string;
  payload: { author: string; commentary: string; visibility: Visibility; lifecycleState: string };
  payload_hash: string;
  content_version: number;
  status: string;
  decision_note: string | null;
  created_at: string;
};

export const getPendingApprovals = (taskId: string) =>
  backendFetch<Approval[]>(`/approvals?task_id=${encodeURIComponent(taskId)}&status=PENDING`);

export const listChats = () => backendFetch<Chat[]>("/chats");
export const getChat = (id: string) => backendFetch<ChatDetail>(`/chats/${encodeURIComponent(id)}`);
