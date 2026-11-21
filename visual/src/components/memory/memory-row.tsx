"use client";

import { useActionState } from "react";

import { deleteMemoryAction, type MemoryActionState } from "@/app/memory/actions";
import { Button } from "@/components/ui/button";
import type { Memory } from "@/lib/memory";

const initial: MemoryActionState = { error: null };

export function MemoryRow({ memory }: { memory: Memory }) {
  const [state, remove, removing] = useActionState(
    deleteMemoryAction.bind(null, memory.id),
    initial,
  );

  return (
    <li className="flex flex-col gap-2 rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {memory.kind === "contact" ? (
            <>
              <p className="font-medium capitalize">{memory.name}</p>
              <p className="break-words text-sm text-muted-foreground">{memory.email}</p>
            </>
          ) : (
            <p className="whitespace-pre-wrap break-words text-sm">{memory.text}</p>
          )}
        </div>
        <form action={remove}>
          <Button
            type="submit"
            variant="outline"
            size="sm"
            disabled={removing}
            aria-label={`Delete memory: ${memory.text}`}
          >
            {removing ? "Deleting…" : "Delete"}
          </Button>
        </form>
      </div>
      {state.error && (
        <p role="alert" className="text-sm text-destructive">
          {state.error}
        </p>
      )}
    </li>
  );
}
