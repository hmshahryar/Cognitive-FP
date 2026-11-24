import { ArrowRight, Check } from "lucide-react";
import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";

import { QuickCommand } from "@/components/dashboard/quick-command";
import type { Task } from "@/lib/command";
import { listConnectors, type Connector } from "@/lib/connectors";
import {
  actionLabel,
  getMe,
  listPendingApprovals,
  listTasks,
  statusInfo,
  timeAgo,
  type StatusTone,
} from "@/lib/dashboard";
import { EXAMPLES } from "@/lib/examples";
import { getOnboarding, requireOnboarded } from "@/lib/onboarding";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Home · Cognitive Mesh" };

const TONE: Record<StatusTone, string> = {
  amber: "bg-amber-wash text-amber-700",
  mint: "bg-mint-wash text-emerald-700",
  indigo: "bg-indigo-wash text-indigo-deep",
  coral: "bg-coral-wash text-[#b23a1f]",
  muted: "bg-muted text-muted-foreground",
};

function Pill({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return (
    <span className={cn("shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium", TONE[tone])}>
      {children}
    </span>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

const chatLink = (task: Task | undefined) => (task ? `/command?chat=${task.chat_id}` : "/command");

export default async function DashboardPage() {
  await requireOnboarded();

  const [me, tasks, approvals, connectors, onboarding] = await Promise.all([
    getMe().catch(() => null),
    listTasks().catch(() => null),
    listPendingApprovals().catch(() => null),
    listConnectors().catch(() => null),
    getOnboarding().catch(() => null),
  ]);

  const unreachable = tasks === null || approvals === null;
  const byId = new Map((tasks ?? []).map((t) => [t.id, t]));
  const recent = [...(tasks ?? [])]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 6);
  const connected: Connector[] = (connectors?.connectors ?? []).filter((c) => c.connected);
  const done = (tasks ?? []).filter((t) => t.status === "COMPLETED").length;
  const waiting = approvals?.length ?? 0;

  const goals = onboarding?.goals.length ? onboarding.goals : (["crm", "email", "docs"] as const);
  const examples = goals.slice(0, 3).map((g) => EXAMPLES[g]);
  const rawName = me?.user.first_name?.trim();
  const firstName = rawName ? rawName[0].toUpperCase() + rawName.slice(1) : undefined;

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-10 px-4 py-8 sm:px-8 lg:py-12">
      <header className="flex flex-col gap-2">
        <h1 className="heading text-[clamp(2rem,4vw,2.8rem)]">
          {firstName ? (
            <>
              Good to see you, <em className="text-indigo">{firstName}.</em>
            </>
          ) : (
            <>
              Welcome to <em className="text-indigo">{me?.workspace.name ?? "your workspace"}.</em>
            </>
          )}
        </h1>
        <p className="text-muted-foreground">
          {unreachable
            ? "Can't reach the server right now, so some of your work isn't shown."
            : waiting === 0
              ? "Nothing is waiting for your approval."
              : `${waiting} ${waiting === 1 ? "action is" : "actions are"} waiting for your approval.`}
        </p>
      </header>

      <QuickCommand examples={examples} />

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="flex flex-col gap-10">
          <Section
            title="Waiting for you"
            action={
              waiting > 0 && (
                <Link href="/command" className="text-sm text-indigo hover:underline">
                  Open Command Center
                </Link>
              )
            }
          >
            {waiting === 0 ? (
              <p className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-4 text-sm text-muted-foreground">
                <Check className="size-4 text-mint" aria-hidden />
                You&apos;re all caught up. New requests for approval will show up here.
              </p>
            ) : (
              <ul className="divide-y overflow-hidden rounded-xl border bg-card">
                {approvals!.map((a) => {
                  const task = byId.get(a.task_id);
                  return (
                    <li key={a.id}>
                      <Link
                        href={chatLink(task)}
                        className="flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-muted/50"
                      >
                        <span className="size-2 shrink-0 rounded-full bg-amber ring-4 ring-amber-wash" aria-hidden />
                        <span className="min-w-0 flex-1">
                          <span className="block font-medium">{actionLabel(a.action_type)}</span>
                          {task && (
                            <span className="block truncate text-sm text-muted-foreground">
                              {task.request}
                            </span>
                          )}
                        </span>
                        <span className="hidden text-xs text-muted-foreground sm:block">
                          {timeAgo(a.created_at)}
                        </span>
                        <span className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-indigo">
                          Review <ArrowRight className="size-3.5" aria-hidden />
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>

          <Section
            title="Recent work"
            action={
              recent.length > 0 && (
                <Link href="/command" className="text-sm text-muted-foreground hover:text-foreground">
                  All chats
                </Link>
              )
            }
          >
            {recent.length === 0 ? (
              <p className="rounded-xl border border-dashed px-4 py-4 text-sm text-muted-foreground">
                No tasks yet. Send your first command above.
              </p>
            ) : (
              <ul className="divide-y overflow-hidden rounded-xl border bg-card">
                {recent.map((t) => {
                  const s = statusInfo(t.status);
                  return (
                    <li key={t.id}>
                      <Link
                        href={chatLink(t)}
                        className="flex items-center gap-4 px-4 py-3 transition-colors hover:bg-muted/50"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[15px]">{t.request}</span>
                          <span className="block text-xs text-muted-foreground">
                            {t.route_label ? `${t.route_label} · ` : ""}
                            {timeAgo(t.updated_at)}
                          </span>
                        </span>
                        <Pill tone={s.tone}>{s.label}</Pill>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>
        </div>

        <aside className="flex flex-col gap-10">
          <Section
            title="Your apps"
            action={
              <Link href="/connectors" className="text-sm text-muted-foreground hover:text-foreground">
                Manage
              </Link>
            }
          >
            {connected.length === 0 ? (
              <Link
                href="/connectors"
                className="rounded-xl border border-dashed px-4 py-4 text-sm text-muted-foreground hover:border-indigo/40 hover:text-foreground"
              >
                No apps connected yet. Connect one so your AI employees can start working.
              </Link>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {connected.map((c) => (
                  <li key={c.slug} className="flex items-center gap-3 rounded-lg px-1 py-1">
                    {c.logo_url ? (
                      <Image
                        src={c.logo_url}
                        alt=""
                        width={24}
                        height={24}
                        unoptimized
                        className="size-6 rounded-md object-contain"
                      />
                    ) : (
                      <span className="size-6 rounded-md bg-muted" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{c.name}</span>
                      {c.display_name && (
                        <span className="block truncate text-xs text-muted-foreground">
                          {c.display_name}
                        </span>
                      )}
                    </span>
                    <span className="size-1.5 rounded-full bg-mint" aria-label="Connected" />
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="So far">
            <dl className="grid grid-cols-[1fr_auto] gap-y-2 text-sm">
              <dt className="text-muted-foreground">Waiting for approval</dt>
              <dd className="font-medium tabular-nums">{waiting}</dd>
              <dt className="text-muted-foreground">Tasks done</dt>
              <dd className="font-medium tabular-nums">{done}</dd>
              <dt className="text-muted-foreground">Apps connected</dt>
              <dd className="font-medium tabular-nums">{connected.length}</dd>
            </dl>
          </Section>
        </aside>
      </div>
    </main>
  );
}
