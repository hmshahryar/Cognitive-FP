import type { Metadata } from "next";

import { requireOnboarded } from "@/lib/onboarding";
import { Suspense } from "react";

import { MemoryRow } from "@/components/memory/memory-row";
import { listMemories, type Memory } from "@/lib/memory";

export const metadata: Metadata = { title: "Memory · Cognitive Mesh" };

function Section({ title, items }: { title: string; items: Memory[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-medium text-muted-foreground">{title}</h2>
      <ul className="flex flex-col gap-2">
        {items.map((memory) => (
          <MemoryRow key={memory.id} memory={memory} />
        ))}
      </ul>
    </section>
  );
}

async function MemoryList() {
  const memories = await listMemories();
  if (memories.length === 0) {
    return (
      <p className="rounded-lg border p-4 text-sm text-muted-foreground">
        Nothing saved yet. In the Command Center, try: remember that I prefer short, direct emails.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      <Section title="Contacts" items={memories.filter((m) => m.kind === "contact")} />
      <Section title="Preferences and facts" items={memories.filter((m) => m.kind === "fact")} />
    </div>
  );
}

export default async function MemoryPage() {
  await requireOnboarded();
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-10 sm:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Memory</h1>
        <p className="text-muted-foreground">
          Your AI team remembers only what you ask it to. Memory can help with a task, but every
          action still needs your approval.
        </p>
      </header>
      <Suspense fallback={<p className="text-muted-foreground">Loading memory…</p>}>
        <MemoryList />
      </Suspense>
    </main>
  );
}
