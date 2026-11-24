"use client";

import {
  ArrowLeft,
  CalendarDays,
  Check,
  FileText,
  Mail,
  Megaphone,
  Palette,
  Users,
  X,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Image from "next/image";
import Link from "next/link";
import { useState, useTransition } from "react";

import {
  connectFromOnboardingAction,
  finishOnboardingAction,
  saveProgressAction,
} from "@/app/onboarding/actions";
import Orb from "@/components/brand/orb";
import { EXAMPLES } from "@/lib/examples";
import { cn } from "@/lib/utils";

export type StepId = "welcome" | "name" | "team" | "goals" | "connect" | "ready";

type TeamSize = "just_me" | "2_10" | "11_50" | "51_plus";
type Goal = "email" | "calendar" | "crm" | "docs" | "social" | "design";
type Answers = { workspace_name: string; team_size: TeamSize | null; goals: Goal[] };
type App = { slug: string; name: string; logo: string | null; connected: boolean };

const ORDER: StepId[] = ["welcome", "name", "team", "goals", "connect", "ready"];

const TEAM: { id: TeamSize; label: string; hint: string }[] = [
  { id: "just_me", label: "Just me", hint: "Solo founder or freelancer" },
  { id: "2_10", label: "2 to 10", hint: "A small team" },
  { id: "11_50", label: "11 to 50", hint: "A growing company" },
  { id: "51_plus", label: "More than 50", hint: "Several teams" },
];

const GOAL_INFO: { id: Goal; label: string; hint: string; icon: typeof Mail }[] = [
  { id: "email", label: "Email", hint: "Draft and send replies", icon: Mail },
  { id: "calendar", label: "Meetings", hint: "Book and move events", icon: CalendarDays },
  { id: "crm", label: "Customers", hint: "Keep the CRM up to date", icon: Users },
  { id: "docs", label: "Docs and sheets", hint: "Notion pages, sheet rows", icon: FileText },
  { id: "social", label: "Social posts", hint: "Facebook, Instagram, LinkedIn", icon: Megaphone },
  { id: "design", label: "Banners", hint: "Made in your Canva", icon: Palette },
];

const GOAL_APPS: Record<Goal, string[]> = {
  email: ["gmail"],
  calendar: ["googlecalendar"],
  crm: ["hubspot", "salesforce", "zoho"],
  docs: ["notion", "googlesheets"],
  social: ["instagram", "facebook", "linkedin"],
  design: ["canva"],
};


const SAYS: Record<StepId, string> = {
  welcome: "Hi! I'll set up your workspace with you. It takes about a minute.",
  name: "First things first. What should I call your workspace?",
  team: "How many people work with you?",
  goals: "What would you like help with? Pick as many as you like.",
  connect: "Connect one app now so I can start working. You can add more later.",
  ready: "You're all set. Here's something you can ask me first.",
};

const ease = [0.22, 1, 0.36, 1] as const;

/** Chunky, tactile button: it sits on a darker edge and presses down when clicked. */
function PressButton({
  className,
  tone = "indigo",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "indigo" | "plain" }) {
  return (
    <button
      {...props}
      className={cn(
        "inline-flex h-12 items-center justify-center gap-2 rounded-2xl px-6 text-[15px] font-semibold transition-[transform,box-shadow,background-color] duration-100 active:translate-y-1 disabled:pointer-events-none disabled:opacity-45",
        tone === "indigo"
          ? "bg-indigo text-white shadow-[0_4px_0_var(--indigo-deep)] hover:bg-[#5a52ec] active:shadow-none"
          : "border-2 border-border bg-card text-ink shadow-[0_4px_0_var(--border)] hover:bg-muted active:shadow-none",
        className,
      )}
    />
  );
}

function Tile({
  selected,
  onClick,
  children,
  multi,
}: {
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
  multi?: boolean;
}) {
  return (
    <button
      type="button"
      role={multi ? "checkbox" : "radio"}
      aria-checked={selected}
      onClick={onClick}
      className={cn(
        "relative flex w-full items-center gap-3 rounded-2xl border-2 px-4 py-3.5 text-left transition-[transform,box-shadow,border-color,background-color] duration-100 active:translate-y-1 active:shadow-none",
        selected
          ? "border-indigo bg-indigo-wash shadow-[0_4px_0_var(--indigo)]"
          : "border-border bg-card shadow-[0_4px_0_var(--border)] hover:bg-muted/60",
      )}
    >
      {children}
      {multi && (
        <span
          aria-hidden
          className={cn(
            "ml-auto flex size-6 shrink-0 items-center justify-center rounded-lg border-2",
            selected ? "border-indigo bg-indigo text-white" : "border-border",
          )}
        >
          {selected && <Check className="size-4" strokeWidth={3} />}
        </span>
      )}
    </button>
  );
}

export function OnboardingFlow({
  start,
  initial,
  apps,
}: {
  start: StepId;
  initial: Answers;
  apps: App[];
}) {
  const reduce = useReducedMotion();
  const [step, setStep] = useState<StepId>(start);
  const [direction, setDirection] = useState(1);
  const [answers, setAnswers] = useState<Answers>(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const [connecting, setConnecting] = useState<string | null>(null);

  const index = ORDER.indexOf(step);
  const progress = index / (ORDER.length - 1);

  const go = (to: StepId) => {
    setError(null);
    setDirection(ORDER.indexOf(to) > index ? 1 : -1);
    setStep(to);
  };

  const saveAndGo = (to: StepId) =>
    startTransition(async () => {
      const result = await saveProgressAction(answers);
      if (result.error) setError(result.error);
      else go(to);
    });

  const connect = (slug: string) => {
    setConnecting(slug);
    startTransition(async () => {
      const result = await connectFromOnboardingAction(answers, slug);
      if (result?.error) {
        setError(result.error);
        setConnecting(null);
      }
    });
  };

  const finish = () =>
    startTransition(async () => {
      const result = await finishOnboardingAction(answers);
      if (result?.error) setError(result.error);
    });

  const name = answers.workspace_name.trim();
  const suggested = answers.goals.flatMap((g) => GOAL_APPS[g]);
  const shownApps = (suggested.length ? apps.filter((a) => suggested.includes(a.slug)) : apps)
    .sort((a, b) => suggested.indexOf(a.slug) - suggested.indexOf(b.slug))
    .slice(0, 6);
  const anyConnected = apps.some((a) => a.connected);
  const example = EXAMPLES[answers.goals[0] ?? "crm"];

  return (
    <div className="flex min-h-screen flex-col">
      {/* Top bar: exit, progress */}
      <header className="mx-auto flex w-full max-w-3xl items-center gap-4 px-4 pt-6 sm:px-6">
        <Link
          href="/"
          aria-label="Leave setup"
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-5" />
        </Link>
        <div
          role="progressbar"
          aria-label="Setup progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress * 100)}
          className="h-4 flex-1 overflow-hidden rounded-full bg-[#ecebe6]"
        >
          <motion.div
            className="relative h-full rounded-full bg-indigo"
            initial={false}
            animate={{ width: `${Math.max(progress, 0.04) * 100}%` }}
            transition={{ type: "spring", stiffness: 140, damping: 22 }}
          >
            <span className="absolute inset-x-2 top-1 h-1 rounded-full bg-white/30" />
          </motion.div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 pt-8 pb-36 sm:px-6 sm:pt-12">
        {/* The guide: a small orb with a speech bubble */}
        <div className="flex items-center gap-4">
          <div className="relative size-20 shrink-0 overflow-hidden rounded-full bg-ink shadow-[0_4px_0_#000] sm:size-24">
            <Orb
              colors={["#6d64ff", "#ff6b4a", "#2a2477"]}
              backgroundColor="#16161d"
              size={0.86}
              glow={1.5}
              highlight={1.5}
              intro={false}
              speed={pending ? 2.4 : 0.9}
              forceHoverState
              hoverIntensity={pending ? 1 : 0.4}
              clickPulse={false}
            />
          </div>
          <AnimatePresence mode="wait">
            <motion.p
              key={step}
              initial={reduce ? false : { opacity: 0, scale: 0.96, x: -6 }}
              animate={{ opacity: 1, scale: 1, x: 0 }}
              exit={reduce ? undefined : { opacity: 0 }}
              transition={{ duration: 0.3, ease }}
              aria-live="polite"
              className="relative rounded-2xl border-2 bg-card px-4 py-3 text-[17px] leading-snug font-medium before:absolute before:top-1/2 before:-left-[9px] before:size-4 before:-translate-y-1/2 before:rotate-45 before:border-b-2 before:border-l-2 before:border-border before:bg-card"
            >
              {SAYS[step]}
            </motion.p>
          </AnimatePresence>
        </div>

        <div className="relative mt-10 flex-1">
          <AnimatePresence mode="wait" custom={direction}>
            <motion.section
              key={step}
              custom={direction}
              initial={reduce ? false : { opacity: 0, x: 40 * direction }}
              animate={{ opacity: 1, x: 0 }}
              exit={reduce ? undefined : { opacity: 0, x: -40 * direction }}
              transition={{ duration: 0.32, ease }}
            >
              {step === "welcome" && (
                <div className="flex flex-col gap-4">
                  <h1 className="display text-[clamp(2.4rem,6vw,4rem)]">
                    Let&apos;s set up <em className="text-indigo">your team.</em>
                  </h1>
                  <p className="max-w-lg text-lg text-muted-foreground">
                    Four quick questions, then you&apos;ll send your first command. Your AI
                    employees always ask before they act.
                  </p>
                </div>
              )}

              {step === "name" && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (name) saveAndGo("team");
                  }}
                  id="name-form"
                  className="flex flex-col gap-3"
                >
                  <label htmlFor="workspace-name" className="text-sm font-medium text-muted-foreground">
                    Workspace name
                  </label>
                  <input
                    id="workspace-name"
                    autoFocus
                    maxLength={80}
                    value={answers.workspace_name}
                    onChange={(e) => setAnswers({ ...answers, workspace_name: e.target.value })}
                    placeholder="For example, Northwind Studio"
                    className="h-16 w-full rounded-2xl border-2 bg-card px-5 text-2xl font-semibold tracking-tight outline-none placeholder:font-normal placeholder:text-muted-foreground/60 focus:border-indigo"
                  />
                  <p className="text-sm text-muted-foreground">
                    Usually your company or project name. You can change it later.
                  </p>
                </form>
              )}

              {step === "team" && (
                <div role="radiogroup" aria-label="Team size" className="grid gap-3 sm:grid-cols-2">
                  {TEAM.map((t) => (
                    <Tile
                      key={t.id}
                      selected={answers.team_size === t.id}
                      onClick={() => setAnswers({ ...answers, team_size: t.id })}
                    >
                      <span>
                        <span className="block text-lg font-semibold">{t.label}</span>
                        <span className="block text-sm text-muted-foreground">{t.hint}</span>
                      </span>
                    </Tile>
                  ))}
                </div>
              )}

              {step === "goals" && (
                <div role="group" aria-label="What you want help with" className="grid gap-3 sm:grid-cols-2">
                  {GOAL_INFO.map(({ id, label, hint, icon: Icon }) => {
                    const on = answers.goals.includes(id);
                    return (
                      <Tile
                        key={id}
                        multi
                        selected={on}
                        onClick={() =>
                          setAnswers({
                            ...answers,
                            goals: on ? answers.goals.filter((g) => g !== id) : [...answers.goals, id],
                          })
                        }
                      >
                        <span
                          className={cn(
                            "flex size-10 shrink-0 items-center justify-center rounded-xl",
                            on ? "bg-indigo text-white" : "bg-muted text-ink",
                          )}
                        >
                          <Icon className="size-5" aria-hidden />
                        </span>
                        <span>
                          <span className="block font-semibold">{label}</span>
                          <span className="block text-sm text-muted-foreground">{hint}</span>
                        </span>
                      </Tile>
                    );
                  })}
                </div>
              )}

              {step === "connect" && (
                <div className="flex flex-col gap-3">
                  {shownApps.length === 0 && (
                    <p className="rounded-2xl border-2 border-dashed p-5 text-muted-foreground">
                      Apps can&apos;t be loaded right now. You can connect them later from
                      Connectors.
                    </p>
                  )}
                  <ul className="grid gap-3 sm:grid-cols-2">
                    {shownApps.map((a) => (
                      <li
                        key={a.slug}
                        className="flex items-center gap-3 rounded-2xl border-2 bg-card px-4 py-3 shadow-[0_4px_0_var(--border)]"
                      >
                        {a.logo ? (
                          <Image src={a.logo} alt="" width={36} height={36} unoptimized className="size-9 rounded-lg object-contain" />
                        ) : (
                          <span className="size-9 rounded-lg bg-muted" />
                        )}
                        <span className="flex-1 font-semibold">{a.name}</span>
                        {a.connected ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-mint-wash px-2.5 py-1 text-sm font-medium text-emerald-700">
                            <Check className="size-3.5" strokeWidth={3} aria-hidden /> Connected
                          </span>
                        ) : (
                          <button
                            type="button"
                            disabled={pending}
                            onClick={() => connect(a.slug)}
                            className="rounded-xl border-2 border-indigo px-3 py-1.5 text-sm font-semibold text-indigo hover:bg-indigo-wash disabled:opacity-50"
                          >
                            {connecting === a.slug ? "Opening…" : "Connect"}
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-sm text-muted-foreground">
                    You&apos;ll sign in on the app&apos;s own page. Cognitive Mesh only gets the
                    limited permissions it needs, and never your password.
                  </p>
                </div>
              )}

              {step === "ready" && (
                <div className="flex flex-col gap-6">
                  <motion.div
                    initial={reduce ? false : { scale: 0.6, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: "spring", stiffness: 260, damping: 16 }}
                    className="flex size-16 items-center justify-center rounded-full bg-mint text-white shadow-[0_4px_0_#0a8a60]"
                  >
                    <Check className="size-8" strokeWidth={3} aria-hidden />
                  </motion.div>
                  <h1 className="display text-[clamp(2.2rem,5.5vw,3.6rem)]">
                    {name || "Your workspace"} is <em className="text-indigo">ready.</em>
                  </h1>
                  <figure className="rounded-2xl border-2 bg-card p-5">
                    <figcaption className="text-sm text-muted-foreground">Try saying</figcaption>
                    <p className="mt-2 text-lg font-medium">“{example}”</p>
                    <p className="mt-3 text-sm text-muted-foreground">
                      You&apos;ll see the exact result first. Nothing is sent until you approve it.
                    </p>
                  </figure>
                </div>
              )}
            </motion.section>
          </AnimatePresence>
        </div>

        {error && (
          <p role="alert" className="mt-6 rounded-xl bg-coral-wash px-4 py-3 text-sm text-[#b23a1f]">
            {error}
          </p>
        )}
      </main>

      {/* Footer bar, like a lesson's check bar */}
      <footer className="fixed inset-x-0 bottom-0 border-t-2 bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-3 px-4 py-4 sm:px-6">
          {index > 0 && step !== "ready" ? (
            <button
              type="button"
              onClick={() => go(ORDER[index - 1])}
              className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <ArrowLeft className="size-4" aria-hidden /> Back
            </button>
          ) : (
            <span />
          )}

          {step === "welcome" && <PressButton onClick={() => go("name")}>Let&apos;s go</PressButton>}
          {step === "name" && (
            <PressButton type="submit" form="name-form" disabled={!name || pending}>
              {pending ? "Saving…" : "Continue"}
            </PressButton>
          )}
          {step === "team" && (
            <PressButton disabled={!answers.team_size || pending} onClick={() => saveAndGo("goals")}>
              {pending ? "Saving…" : "Continue"}
            </PressButton>
          )}
          {step === "goals" && (
            <PressButton
              disabled={answers.goals.length === 0 || pending}
              onClick={() => saveAndGo("connect")}
            >
              {pending ? "Saving…" : "Continue"}
            </PressButton>
          )}
          {step === "connect" && (
            <PressButton
              tone={anyConnected ? "indigo" : "plain"}
              disabled={pending}
              onClick={() => go("ready")}
            >
              {anyConnected ? "Continue" : "Skip for now"}
            </PressButton>
          )}
          {step === "ready" && (
            <PressButton disabled={pending} onClick={finish}>
              {pending ? "Opening…" : "Open Command Center"}
            </PressButton>
          )}
        </div>
      </footer>
    </div>
  );
}
