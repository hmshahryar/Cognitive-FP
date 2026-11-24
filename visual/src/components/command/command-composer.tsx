"use client";

import { ArrowUp } from "lucide-react";
import { useActionState, useEffect, useRef } from "react";

import { sendCommandAction, type FormState } from "@/app/(app)/command/actions";

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
    <div className="flex flex-col gap-2">
      <form
        ref={formRef}
        action={action}
        className="rounded-2xl border bg-card p-2 shadow-[0_1px_0_rgba(22,22,29,0.03),0_18px_40px_-28px_rgba(55,48,163,0.45)] transition-colors focus-within:border-indigo/50"
      >
        <label htmlFor="request" className="sr-only">
          Command
        </label>
        <textarea
          id="request"
          name="request"
          rows={2}
          required
          maxLength={8000}
          placeholder={chatId ? "Reply or send the next command…" : "Tell your AI employees what to do…"}
          className="w-full resize-none bg-transparent px-3 pt-2 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground/70"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              formRef.current?.requestSubmit();
            }
          }}
        />
        <div className="flex items-center justify-between gap-3 px-2 pb-1">
          <p className="text-xs text-muted-foreground">
            Enter to send, Shift + Enter for a new line
          </p>
          <button
            type="submit"
            disabled={pending}
            aria-label={pending ? "Sending" : "Send command"}
            className="flex size-9 items-center justify-center rounded-xl bg-indigo text-white transition-colors hover:bg-indigo-deep disabled:opacity-50"
          >
            <ArrowUp className={pending ? "size-4 animate-pulse" : "size-4"} aria-hidden />
          </button>
        </div>
      </form>
      {state.error && (
        <p role="alert" className="text-sm text-[#b23a1f]">
          {state.error}
        </p>
      )}
    </div>
  );
}
