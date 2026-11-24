"use client";

import { ArrowUp } from "lucide-react";
import { useActionState, useRef } from "react";

import { sendCommandAction, type FormState } from "@/app/(app)/command/actions";

/** Starts a new chat with one command, then opens it in Command Center. */
export function QuickCommand({ examples }: { examples: string[] }) {
  const [state, action, pending] = useActionState<FormState, FormData>(
    sendCommandAction.bind(null, null),
    { error: null },
  );
  const box = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);

  return (
    <div className="flex flex-col gap-3">
      <form
        ref={form}
        action={action}
        className="group rounded-2xl border bg-card p-2 shadow-[0_1px_0_rgba(22,22,29,0.03),0_18px_40px_-28px_rgba(55,48,163,0.45)] transition-colors focus-within:border-indigo/50"
      >
        <label htmlFor="quick-command" className="sr-only">
          Tell your AI employees what to do
        </label>
        <textarea
          ref={box}
          id="quick-command"
          name="request"
          rows={2}
          required
          maxLength={8000}
          placeholder="Tell your AI employees what to do…"
          className="w-full resize-none bg-transparent px-3 pt-2 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground/70"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              form.current?.requestSubmit();
            }
          }}
        />
        <div className="flex items-center justify-between gap-3 px-2 pb-1">
          <p className="text-xs text-muted-foreground">
            Enter to send. You&apos;ll approve the result before anything is sent.
          </p>
          <button
            type="submit"
            disabled={pending}
            aria-label="Send command"
            className="flex size-9 items-center justify-center rounded-xl bg-indigo text-white transition-colors hover:bg-indigo-deep disabled:opacity-50"
          >
            <ArrowUp className="size-4" aria-hidden />
          </button>
        </div>
      </form>
      {state.error && (
        <p role="alert" className="text-sm text-[#b23a1f]">
          {state.error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {examples.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => {
              if (box.current) {
                box.current.value = ex;
                box.current.focus();
              }
            }}
            className="max-w-full truncate rounded-full border bg-background px-3 py-1 text-left text-sm text-muted-foreground transition-colors hover:border-indigo/40 hover:text-foreground"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
