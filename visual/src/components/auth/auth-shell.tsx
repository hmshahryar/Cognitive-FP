import Link from "next/link";

import { Logo } from "@/components/brand/logo";
import Orb from "@/components/brand/orb";

type Props = {
  heading: React.ReactNode;
  intro: string;
  points: string[];
  children: React.ReactNode;
};

/**
 * Split screen for sign in and sign up: Clerk's form on the paper side, and a dark panel
 * with the orb that says what happens next.
 */
export function AuthShell({ heading, intro, points, children }: Props) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <div className="flex flex-col px-4 py-6 sm:px-10">
        <div className="flex items-center justify-between">
          <Logo />
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            Back to home
          </Link>
        </div>
        <main className="flex flex-1 items-center justify-center py-10">{children}</main>
        <p className="text-center text-xs text-muted-foreground">
          You sign in with Clerk. Cognitive Mesh never sees your password.
        </p>
      </div>

      <aside className="relative hidden overflow-hidden bg-ink text-paper lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="pointer-events-none absolute -top-[12%] -right-[18%] aspect-square w-[95%]">
          <Orb
            colors={["#4f46e5", "#ff6b4a", "#2a2477"]}
            backgroundColor="#16161d"
            size={0.85}
            glow={1.2}
            speed={0.7}
            highlight={1.3}
            forceHoverState
            hoverIntensity={0.35}
            clickPulse={false}
          />
        </div>
        <div className="relative" />
        <div className="relative max-w-md">
          <h2 className="heading text-[clamp(2.2rem,3.6vw,3.25rem)]">{heading}</h2>
          <p className="mt-4 text-lg leading-relaxed text-paper/70">{intro}</p>
          <ol className="mt-8 flex flex-col gap-3">
            {points.map((p, i) => (
              <li key={p} className="flex items-baseline gap-3 text-paper/85">
                <span className="font-serif text-xl text-coral italic">{i + 1}</span>
                {p}
              </li>
            ))}
          </ol>
        </div>
      </aside>
    </div>
  );
}

/** Clerk form styling shared by both pages; colours come from the root ClerkProvider. */
export const authAppearance = {
  elements: {
    rootBox: "w-full max-w-[400px]",
    cardBox: "w-full rounded-2xl border border-border shadow-[0_24px_60px_-34px_rgba(55,48,163,0.45)]",
    card: "bg-card",
    headerTitle: "text-2xl font-semibold tracking-tight",
    headerSubtitle: "text-muted-foreground",
    socialButtonsBlockButton: "h-11 rounded-xl border-border bg-background hover:bg-muted",
    formFieldInput: "h-11 rounded-xl border! border-input! bg-background shadow-none",
    formButtonPrimary: "h-11 rounded-xl bg-primary text-[15px] hover:bg-indigo-deep shadow-none",
    footer: "bg-transparent",
  },
};
