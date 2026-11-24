import type { Metadata } from "next";
import { Plus } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { LogoMark } from "@/components/brand/logo";
import { ApprovalCard } from "@/components/command/approval-card";
import { AutoRefresh } from "@/components/command/auto-refresh";
import { CommandComposer } from "@/components/command/command-composer";
import { DraftSummary } from "@/components/command/draft-summary";
import { PublicationStatus } from "@/components/command/publication-status";
import { TaskCard } from "@/components/command/task-card";
import { QuickCommand } from "@/components/dashboard/quick-command";
import {
  getChat,
  getDrafts,
  getPendingApprovals,
  getPublications,
  latestFinalDraft,
  listChats,
  type Chat,
} from "@/lib/command";
import { listConnectors } from "@/lib/connectors";
import { ApiError } from "@/lib/api";
import { EXAMPLES } from "@/lib/examples";
import { getOnboarding, requireOnboarded } from "@/lib/onboarding";

export const metadata: Metadata = { title: "Command Center · Cognitive Mesh" };

type Search = Promise<{ [key: string]: string | string[] | undefined }>;

function ChatList({ chats, activeId }: { chats: Chat[]; activeId: string | null }) {
  return (
    <nav aria-label="Chats" className="flex flex-col gap-0.5">
      <Link
        href="/command"
        className="mb-3 inline-flex h-9 items-center justify-center gap-1.5 rounded-xl bg-ink text-sm font-medium text-paper transition-colors hover:bg-indigo"
      >
        <Plus className="size-4" aria-hidden /> New chat
      </Link>
      <p className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">Recent chats</p>
      {chats.length === 0 && <p className="px-2.5 text-sm text-muted-foreground">No chats yet.</p>}
      {chats.map((c) => (
        <Link
          key={c.id}
          href={`/command?chat=${c.id}`}
          aria-current={c.id === activeId ? "page" : undefined}
          className={`truncate rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
            c.id === activeId
              ? "bg-card font-medium text-ink shadow-[0_1px_2px_rgba(22,22,29,0.08)] ring-1 ring-border"
              : "text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
          }`}
        >
          {c.title}
        </Link>
      ))}
    </nav>
  );
}

async function linkedInLabel(): Promise<string> {
  try {
    const { connectors } = await listConnectors();
    const li = connectors.find((c) => c.slug === "linkedin");
    return li?.display_name ? `LinkedIn · ${li.display_name}` : "your LinkedIn account";
  } catch {
    return "your LinkedIn account";
  }
}

async function ChatView({ chatId }: { chatId: string }) {
  let detail;
  try {
    detail = await getChat(chatId);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      return <p className="text-muted-foreground">This chat doesn&apos;t exist.</p>;
    }
    throw e;
  }
  const { chat, messages, tasks } = detail;
  const [drafts, publications] = await Promise.all([
    Promise.all(tasks.map((t) => getDrafts(t.id))),
    Promise.all(tasks.map((t) => getPublications(t.id))),
  ]);
  const awaiting = tasks.filter((t) => t.status === "AWAITING_APPROVAL");
  const pending = (await Promise.all(awaiting.map((t) => getPendingApprovals(t.id)))).flat();
  const accountLabel = pending.length ? await linkedInLabel() : "";
  // Keep refreshing while the AI team is working (including redrafts after feedback).
  const working = tasks.some((t) =>
    ["QUEUED", "RUNNING", "APPROVED", "EXECUTING"].includes(t.status),
  );

  return (
    <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_300px]">
      <section aria-label="Conversation" className="flex min-w-0 flex-col gap-5">
        <h1 className="heading text-2xl">{chat.title}</h1>
        <ol className="flex flex-col gap-4">
          {messages.map((m) =>
            m.role === "user" ? (
              <li
                key={m.id}
                className="max-w-[85%] self-end rounded-2xl rounded-br-md bg-indigo px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap text-white"
              >
                {m.content}
              </li>
            ) : (
              <li key={m.id} className="flex max-w-[90%] items-start gap-3 self-start">
                <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border bg-card">
                  <LogoMark className="size-4" />
                </span>
                <p className="rounded-2xl rounded-tl-md border bg-card px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap">
                  {m.content}
                </p>
              </li>
            ),
          )}
        </ol>
        {pending.map((a) => (
          <ApprovalCard
            key={a.id}
            approval={a}
            accountLabel={accountLabel}
            draft={latestFinalDraft(drafts[tasks.findIndex((t) => t.id === a.task_id)] ?? [])}
          />
        ))}
        <div className="sticky bottom-4 z-10 mt-2">
          <CommandComposer chatId={chat.id} />
        </div>
      </section>
      <aside aria-label="Tasks" className="flex flex-col gap-4 xl:border-l xl:pl-6">
        <h2 className="text-[15px] font-semibold tracking-tight">Tasks in this chat</h2>
        {tasks.length === 0 ? (
          <p className="text-sm text-muted-foreground">Commands you send appear here.</p>
        ) : (
          <div className="relative flex flex-col gap-6 before:absolute before:top-2 before:bottom-2 before:left-[4.5px] before:w-px before:bg-border">
            {tasks.map((t, i) => (
              <TaskCard
                key={t.id}
                task={t}
                canRetry={!publications[i].some((p) => p.status === "NEEDS_CHECK")}
              >
                {t.status === "RUNNING" && (
                  <p className="text-xs text-indigo-deep" aria-live="polite">
                    {drafts[i].length === 0
                      ? "Working on your command…"
                      : "Revising the draft with your feedback…"}
                  </p>
                )}
                {t.status === "APPROVED" && (
                  <p className="text-xs text-indigo-deep" aria-live="polite">
                    Approved. Working on it now…
                  </p>
                )}
                <PublicationStatus publication={publications[i].at(-1)} />
                <DraftSummary drafts={drafts[i]} />
              </TaskCard>
            ))}
          </div>
        )}
      </aside>
      <AutoRefresh active={working} />
    </div>
  );
}

async function CommandCenter({ searchParams }: { searchParams: Search }) {
  const params = await searchParams;
  const chatId = typeof params.chat === "string" ? params.chat : null;
  const [chats, onboarding] = await Promise.all([listChats(), getOnboarding().catch(() => null)]);
  const goals = onboarding?.goals.length ? onboarding.goals : (["crm", "email", "docs"] as const);

  return (
    <div className="grid gap-8 md:grid-cols-[220px_minmax(0,1fr)]">
      <div className="md:sticky md:top-8 md:self-start">
        <ChatList chats={chats} activeId={chatId} />
      </div>
      {chatId ? (
        <Suspense key={chatId} fallback={<p className="text-muted-foreground">Loading chat…</p>}>
          <ChatView chatId={chatId} />
        </Suspense>
      ) : (
        <section className="flex max-w-3xl flex-col gap-6 pt-2 md:pt-8">
          <div className="flex flex-col gap-3">
            <h1 className="heading text-[clamp(2rem,4vw,2.8rem)]">
              What should your <em className="text-indigo">AI team</em> do?
            </h1>
            <p className="max-w-2xl leading-relaxed text-muted-foreground">
              Post to LinkedIn, a Facebook Page or Instagram, handle Gmail, schedule on Google
              Calendar, read and write Google Sheets, manage HubSpot, Salesforce and Zoho CRM
              contacts, or work in Notion. Anything that posts, sends or changes data waits for
              your approval.
            </p>
          </div>
          <QuickCommand examples={goals.slice(0, 3).map((g) => EXAMPLES[g])} />
        </section>
      )}
    </div>
  );
}

export default async function CommandPage({ searchParams }: { searchParams: Search }) {
  await requireOnboarded();
  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-8">
      <Suspense fallback={<p className="text-muted-foreground">Loading…</p>}>
        <CommandCenter searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
