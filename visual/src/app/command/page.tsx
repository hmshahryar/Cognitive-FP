import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { ApprovalCard } from "@/components/command/approval-card";
import { AutoRefresh } from "@/components/command/auto-refresh";
import { CommandComposer } from "@/components/command/command-composer";
import { DraftSummary } from "@/components/command/draft-summary";
import { TaskCard } from "@/components/command/task-card";
import { getChat, getDrafts, getPendingApprovals, listChats, type Chat } from "@/lib/command";
import { listConnectors } from "@/lib/connectors";
import { ApiError } from "@/lib/api";

export const metadata: Metadata = { title: "Command Center · Cognitive Mesh" };

type Search = Promise<{ [key: string]: string | string[] | undefined }>;

function ChatList({ chats, activeId }: { chats: Chat[]; activeId: string | null }) {
  return (
    <nav aria-label="Chats" className="flex flex-col gap-1">
      <Link
        href="/command"
        className="mb-2 rounded-md border px-3 py-2 text-sm font-medium hover:bg-muted"
      >
        + New chat
      </Link>
      {chats.length === 0 && <p className="px-3 text-sm text-muted-foreground">No chats yet.</p>}
      {chats.map((c) => (
        <Link
          key={c.id}
          href={`/command?chat=${c.id}`}
          aria-current={c.id === activeId ? "page" : undefined}
          className={`truncate rounded-md px-3 py-2 text-sm hover:bg-muted ${
            c.id === activeId ? "bg-muted font-medium" : "text-muted-foreground"
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
  const drafts = await Promise.all(tasks.map((t) => getDrafts(t.id)));
  const awaiting = tasks.filter((t) => t.status === "AWAITING_APPROVAL");
  const pending = (await Promise.all(awaiting.map((t) => getPendingApprovals(t.id)))).flat();
  const accountLabel = pending.length ? await linkedInLabel() : "";
  // Keep refreshing while the AI team is working (including redrafts after feedback).
  const working = tasks.some((t) => t.status === "QUEUED" || t.status === "RUNNING");

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <section aria-label="Conversation" className="flex min-w-0 flex-col gap-4">
        <h1 className="text-xl font-semibold tracking-tight">{chat.title}</h1>
        <ol className="flex flex-col gap-3">
          {messages.map((m) => (
            <li
              key={m.id}
              className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                m.role === "user" ? "self-end bg-primary text-primary-foreground" : "self-start bg-muted"
              }`}
            >
              {m.content}
            </li>
          ))}
        </ol>
        {pending.map((a) => (
          <ApprovalCard key={a.id} approval={a} accountLabel={accountLabel} />
        ))}
        <CommandComposer chatId={chat.id} />
      </section>
      <aside aria-label="Tasks" className="flex flex-col gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">Tasks</h2>
        {tasks.length === 0 ? (
          <p className="text-sm text-muted-foreground">Commands you send appear here.</p>
        ) : (
          tasks.map((t, i) => (
            <TaskCard key={t.id} task={t}>
              {t.status === "RUNNING" && (
                <p className="text-xs text-muted-foreground" aria-live="polite">
                  {drafts[i].length === 0
                    ? "Researching, writing and reviewing…"
                    : "Revising the draft with your feedback…"}
                </p>
              )}
              {t.status === "APPROVED" && (
                <p className="text-xs text-muted-foreground">
                  Approved. Publishing is enabled in the next step.
                </p>
              )}
              <DraftSummary drafts={drafts[i]} />
            </TaskCard>
          ))
        )}
      </aside>
      <AutoRefresh active={working} />
    </div>
  );
}

async function CommandCenter({ searchParams }: { searchParams: Search }) {
  const params = await searchParams;
  const chatId = typeof params.chat === "string" ? params.chat : null;
  const chats = await listChats();

  return (
    <div className="grid gap-6 md:grid-cols-[220px_1fr]">
      <ChatList chats={chats} activeId={chatId} />
      {chatId ? (
        <Suspense key={chatId} fallback={<p className="text-muted-foreground">Loading chat…</p>}>
          <ChatView chatId={chatId} />
        </Suspense>
      ) : (
        <section className="flex flex-col gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">What should your AI team do?</h1>
            <p className="text-sm text-muted-foreground">
              Describe the outcome you want. Anything that posts, sends or changes data will wait
              for your approval first.
            </p>
          </div>
          <CommandComposer chatId={null} />
        </section>
      )}
    </div>
  );
}

export default function CommandPage({ searchParams }: { searchParams: Search }) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
      <Suspense fallback={<p className="text-muted-foreground">Loading…</p>}>
        <CommandCenter searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
