"use client";

import { useActionState, useEffect, useRef } from "react";

import { sendCommandAction, type FormState } from "@/app/command/actions";
import { Button } from "@/components/ui/button";

export function CommandComposer({ chatId }: { chatId: string | null }) {
  const [state, action, pending] = useActionState<FormState, FormData>(
    sendCommandAction.bind(null, chatId),
    { error: null },
  );
  const formRef = useRef<HTMLFormElement>(null);

  // Clear the box after a successful send.
  useEffect(() => {
    if (!pending && !state.error) formRef.current?.reset();
  }, [pending, state]);

  return (
    <form ref={formRef} action={action} className="flex flex-col gap-2">
      <label htmlFor="request" className="sr-only">
        Command
      </label>
      <textarea
        id="request"
        name="request"
        rows={3}
        required
        maxLength={8000}
        placeholder='e.g. "Publish a LinkedIn post about the latest developments in Agentic AI"'
        className="w-full resize-none rounded-lg border bg-background p-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) formRef.current?.requestSubmit();
        }}
      />
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">Ctrl + Enter to send</p>
        <Button type="submit" disabled={pending}>
          {pending ? "Sending…" : "Send command"}
        </Button>
      </div>
      {state.error && (
        <p role="alert" className="text-sm text-destructive">
          {state.error}
        </p>
      )}
    </form>
  );
}
