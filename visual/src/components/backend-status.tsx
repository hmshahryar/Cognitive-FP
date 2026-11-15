import { auth } from "@clerk/nextjs/server";

import { backendFetch } from "@/lib/api";

type Health = { status: string; service: string; environment: string; database: string };
type Me = {
  user: { id: string; clerk_user_id: string; email: string | null };
  workspace: { id: string; name: string };
};

async function check<T>(fn: () => Promise<T>) {
  try {
    return { ok: true as const, data: await fn() };
  } catch (e) {
    return { ok: false as const, error: e instanceof Error ? e.message : String(e) };
  }
}

function Row({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <li className="flex items-start gap-3">
      <span
        className={`mt-1.5 size-2.5 shrink-0 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`}
      />
      <div>
        <p className="font-medium">{label}</p>
        <p className="text-sm text-muted-foreground break-all">{detail}</p>
      </div>
    </li>
  );
}

/** Shows whether Next.js can reach the FastAPI backend, and whether Clerk auth passes through. */
export async function BackendStatus() {
  const { userId } = await auth();
  const health = await check(() => backendFetch<Health>("/health"));
  const me = userId ? await check(() => backendFetch<Me>("/me")) : null;

  return (
    <section className="w-full max-w-md rounded-xl border p-6">
      <h2 className="mb-4 text-lg font-semibold">Backend connection</h2>
      <ul className="space-y-4">
        <Row
          label="FastAPI reachable"
          ok={health.ok}
          detail={
            health.ok
              ? `${health.data.service} (${health.data.environment}), database ${health.data.database}`
              : health.error
          }
        />
        {me ? (
          <>
            <Row
              label="Authenticated request"
              ok={me.ok}
              detail={
                me.ok
                  ? `Signed in as ${me.data.user.email ?? me.data.user.clerk_user_id}`
                  : me.error
              }
            />
            {me.ok && (
              <Row
                label="Personal workspace"
                ok
                detail={`${me.data.workspace.name} · ${me.data.workspace.id}`}
              />
            )}
          </>
        ) : (
          <Row label="Authenticated request" ok={false} detail="Sign in to test" />
        )}
      </ul>
    </section>
  );
}
