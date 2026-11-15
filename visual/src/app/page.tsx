import { Suspense } from "react";

import { BackendStatus } from "@/components/backend-status";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 p-6">
      <h1 className="text-3xl font-semibold tracking-tight">Cognitive Mesh</h1>
      <Suspense fallback={<p className="text-muted-foreground">Checking backend…</p>}>
        <BackendStatus />
      </Suspense>
    </main>
  );
}
