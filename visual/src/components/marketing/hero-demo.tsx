"use client";

import { Check, CornerDownLeft, RotateCcw } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import Orb from "@/components/brand/orb";

const COMMAND = "Post on Instagram: We're launching AI employees for small teams next week.";
const CAPTION = "We're launching AI employees for small teams next week.";

type Stage = "typing" | "thinking" | "review" | "approved";

const ease = [0.22, 1, 0.36, 1] as const;

const status: Record<Stage, string> = {
  typing: "Waiting for your instruction",
  thinking: "Social specialist is preparing the exact post",
  review: "Ready. Nothing is posted until you approve.",
  approved: "Approved by you. Published once.",
};

/**
 * The hero's one orchestrated moment: a command is typed, the orb thinks, the exact post
 * comes back for approval, and only the click publishes it. Plays once; Replay restarts it.
 */
export function HeroDemo() {
  const reduce = useReducedMotion();
  const [played, setStage] = useState<Stage>("typing");
  const [typedCount, setTyped] = useState(0);
  const timers = useRef<number[]>([]);

  // With reduced motion the finished state is shown straight away.
  const stage: Stage = reduce ? "approved" : played;
  const typed = reduce ? COMMAND.length : typedCount;

  const clear = () => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  };

  /** Queue the sequence. State changes only happen inside the timers. */
  const schedule = useCallback(() => {
    clear();
    const at = (ms: number, fn: () => void) => timers.current.push(window.setTimeout(fn, ms));
    const step = 30;
    for (let i = 1; i <= COMMAND.length; i++) at(600 + i * step, () => setTyped(i));
    const typedAt = 600 + COMMAND.length * step;
    at(typedAt + 350, () => setStage("thinking"));
    at(typedAt + 2700, () => setStage("review"));
    at(typedAt + 5400, () => setStage("approved"));
  }, []);

  const replay = () => {
    setStage("typing");
    setTyped(0);
    schedule();
  };

  useEffect(() => {
    if (!reduce) schedule();
    return clear;
  }, [schedule, reduce]);

  const thinking = stage === "thinking";
  const showCard = stage === "review" || stage === "approved";

  return (
    <div className="relative mx-auto w-full max-w-[560px]">
      <div className="relative aspect-square w-full [mask-image:radial-gradient(closest-side,black_78%,transparent)]">
        <Orb
          colors={["#4f46e5", "#ff6b4a", "#7c6cf2"]}
          backgroundColor="#fafaf7"
          size={0.92}
          glow={1.15}
          speed={thinking ? 2.2 : 0.8}
          forceHoverState
          hoverIntensity={thinking ? 1 : 0.35}
        />
        <p
          aria-live="polite"
          className="pointer-events-none absolute inset-x-0 top-[38%] flex justify-center px-16 text-center text-sm text-muted-foreground"
        >
          <AnimatePresence mode="wait">
            <motion.span
              key={stage}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.35, ease }}
              className="max-w-[15rem]"
            >
              {status[stage]}
            </motion.span>
          </AnimatePresence>
        </p>
      </div>

      <div className="relative -mt-40 flex flex-col gap-3 px-2 sm:-mt-52 sm:px-8">
        <div className="rounded-2xl border bg-card p-2.5 shadow-[0_1px_0_rgba(22,22,29,0.04),0_16px_40px_-16px_rgba(55,48,163,0.35)]">
          <div className="flex items-start gap-3 rounded-xl bg-muted/70 px-3 py-2.5 text-[15px] leading-snug">
            <span className="min-h-[2.6em] flex-1">
              {COMMAND.slice(0, typed)}
              {stage === "typing" && (
                <span className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[3px] animate-pulse bg-indigo" />
              )}
            </span>
            <CornerDownLeft className="mt-1 size-4 shrink-0 text-muted-foreground" aria-hidden />
          </div>
        </div>

        <div className="min-h-[188px]">
          <AnimatePresence>
            {showCard && (
              <motion.article
                initial={reduce ? false : { opacity: 0, y: 18, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.55, ease }}
                className="rounded-2xl border bg-card p-4 shadow-[0_16px_40px_-18px_rgba(22,22,29,0.3)]"
              >
                <header className="flex items-center justify-between gap-3 text-sm">
                  <span className="font-medium">Publish to Instagram</span>
                  {stage === "approved" ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-mint-wash px-2.5 py-0.5 text-xs font-medium text-emerald-700">
                      <Check className="size-3" aria-hidden /> Published
                    </span>
                  ) : (
                    <span className="rounded-full bg-amber-wash px-2.5 py-0.5 text-xs font-medium text-amber-700">
                      Waiting for you
                    </span>
                  )}
                </header>
                <p className="mt-0.5 text-xs text-muted-foreground">To your business account</p>
                <p className="mt-3 rounded-lg border border-dashed px-3 py-2 text-sm">{CAPTION}</p>
                <div className="mt-3 flex items-center gap-2">
                  <span
                    className={`inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors duration-300 ${
                      stage === "approved" ? "bg-mint text-white" : "bg-primary text-primary-foreground"
                    }`}
                  >
                    {stage === "approved" && <Check className="size-3.5" aria-hidden />}
                    {stage === "approved" ? "Approved" : "Approve and publish"}
                  </span>
                  <span className="inline-flex h-8 items-center rounded-lg border px-3 text-sm text-muted-foreground">
                    Reject
                  </span>
                  {stage === "approved" && !reduce && (
                    <button
                      type="button"
                      onClick={replay}
                      className="ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <RotateCcw className="size-3" aria-hidden /> Replay
                    </button>
                  )}
                </div>
              </motion.article>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
