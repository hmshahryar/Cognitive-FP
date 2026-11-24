import {
  ArrowUpRight,
  Building2,
  FileCheck2,
  KeyRound,
  ScrollText,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";

import { LogoMark } from "@/components/brand/logo";
import { HeroDemo } from "@/components/marketing/hero-demo";
import { SiteNav } from "@/components/marketing/site-nav";

const steps = [
  {
    title: "You ask in plain words",
    body: "“Add Sara from Northwind to HubSpot” or “Make a launch banner for Instagram.” No forms, no menus.",
  },
  {
    title: "The right specialist picks it up",
    body: "Email, CRM, calendar, docs or social: each request goes to the AI employee that knows that app.",
  },
  {
    title: "You see exactly what will happen",
    body: "The finished email, the contact fields, the post and its image, and the account it goes to.",
  },
  {
    title: "It runs once, after your yes",
    body: "Approve and it's sent. Reject and nothing leaves. A repeated request can't run twice.",
  },
];

const guarantees = [
  "Edit a word, switch the account or change the action, and the approval no longer counts.",
  "Rejected actions are never executed, not even partly.",
  "Images are checked again right before posting, so what you saw is what goes out.",
  "The same request sent twice is caught before it runs twice.",
];

const appGroups = [
  {
    name: "Email and calendar",
    apps: ["Gmail", "Google Calendar", "Google Sheets"],
    does: "Drafts replies, books meetings and updates rows.",
  },
  {
    name: "CRM",
    apps: ["HubSpot", "Salesforce", "Zoho CRM"],
    does: "Finds, adds and updates contacts and notes.",
  },
  {
    name: "Docs and design",
    apps: ["Notion", "Canva"],
    does: "Creates pages and makes banners in your own Canva.",
  },
  {
    name: "Social",
    apps: ["Facebook Pages", "Instagram"],
    does: "Publishes posts and photos to your business accounts.",
  },
];

const security = [
  {
    icon: Building2,
    title: "Your workspace only",
    body: "Every request is tied to the signed-in account on the server. No one can reach another team's data or apps.",
  },
  {
    icon: KeyRound,
    title: "No keys in your browser",
    body: "You sign in on each app's own page. Access tokens stay on the server and are never sent to the page.",
  },
  {
    icon: ShieldCheck,
    title: "Only the permissions needed",
    body: "Each app is connected with a limited set of permissions instead of full account access.",
  },
  {
    icon: ScrollText,
    title: "Every action on record",
    body: "Requests, approvals and results are kept as a history you can look back on.",
  },
];

function Fingerprint() {
  return (
    <aside
      aria-label="Example approval"
      className="relative rounded-2xl border bg-card p-5 shadow-[0_24px_60px_-30px_rgba(55,48,163,0.45)] sm:p-6"
    >
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-2 font-medium">
          <FileCheck2 className="size-4 text-indigo" aria-hidden />
          Add contact to HubSpot
        </p>
        <span className="rounded-full bg-amber-wash px-2.5 py-0.5 text-xs font-medium text-amber-700">
          Waiting for you
        </span>
      </div>
      <dl className="mt-5 grid grid-cols-[7rem_1fr] gap-x-4 gap-y-3 text-sm">
        <dt className="text-muted-foreground">Account</dt>
        <dd>Your HubSpot portal</dd>
        <dt className="text-muted-foreground">Name</dt>
        <dd>Sara Malik</dd>
        <dt className="text-muted-foreground">Email</dt>
        <dd>sara@northwind.example</dd>
        <dt className="text-muted-foreground">Company</dt>
        <dd>Northwind Traders</dd>
        <dt className="text-muted-foreground">Signed for</dt>
        <dd className="font-mono text-xs leading-5 break-all text-muted-foreground">
          this exact content, version 1
        </dd>
      </dl>
      <div className="mt-6 flex flex-wrap gap-2">
        <span className="inline-flex h-9 items-center rounded-lg bg-primary px-3.5 text-sm font-medium text-primary-foreground">
          Approve and add
        </span>
        <span className="inline-flex h-9 items-center rounded-lg border px-3.5 text-sm">Edit</span>
        <span className="inline-flex h-9 items-center rounded-lg px-3.5 text-sm text-muted-foreground">
          Reject
        </span>
      </div>
      <p className="mt-5 border-t pt-4 text-xs text-muted-foreground">
        Change any field and this approval is cancelled. You&apos;ll be asked again.
      </p>
    </aside>
  );
}

export default function Home() {
  return (
    <>
      <SiteNav />
      <main className="flex-1">
        {/* Hero */}
        <section className="relative overflow-hidden">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 -top-40 h-[640px] bg-[radial-gradient(60%_50%_at_75%_40%,rgba(79,70,229,0.10),transparent_70%),radial-gradient(40%_40%_at_90%_70%,rgba(255,107,74,0.10),transparent_70%)]"
          />
          <div className="relative mx-auto grid w-full max-w-6xl items-center gap-6 px-4 pt-10 pb-20 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:gap-10 lg:pt-16">
            <div className="flex flex-col gap-7">
              <h1 className="display text-[clamp(3.2rem,8vw,6.4rem)]">
                AI employees that <em className="text-indigo">ask before</em> they act.
              </h1>
              <p className="max-w-[34rem] text-lg leading-relaxed text-muted-foreground">
                Tell Cognitive Mesh what you need in plain words. A specialist prepares the exact
                email, post or CRM update, and nothing leaves your workspace until you approve it.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Link
                  href="/sign-up"
                  className="inline-flex h-12 items-center rounded-xl bg-primary px-5 font-medium text-primary-foreground shadow-[0_10px_24px_-10px_rgba(79,70,229,0.7)] transition-colors hover:bg-indigo-deep"
                >
                  Create your workspace
                </Link>
                <a
                  href="#approval"
                  className="inline-flex h-12 items-center rounded-xl border bg-card px-5 font-medium transition-colors hover:border-ink/30"
                >
                  See how approval works
                </a>
              </div>
            </div>
            <HeroDemo />
          </div>
        </section>

        {/* How it works */}
        <section id="how" className="scroll-mt-20 border-t bg-card">
          <div className="mx-auto w-full max-w-6xl px-4 py-24 sm:px-6">
            <h2 className="heading max-w-2xl text-[clamp(2.2rem,4.5vw,3.5rem)]">
              From one sentence to <em>a finished task.</em>
            </h2>
            <ol className="mt-14 grid gap-x-8 gap-y-12 sm:grid-cols-2 lg:grid-cols-4">
              {steps.map((s, i) => (
                <li key={s.title} className="flex flex-col gap-3 border-t border-ink/15 pt-5">
                  <span className="font-serif text-4xl text-indigo italic">{i + 1}</span>
                  <h3 className="text-lg font-semibold tracking-tight">{s.title}</h3>
                  <p className="leading-relaxed text-muted-foreground">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Approval */}
        <section id="approval" className="scroll-mt-20 border-t">
          <div className="mx-auto grid w-full max-w-6xl items-center gap-14 px-4 py-24 sm:px-6 lg:grid-cols-2">
            <div>
              <h2 className="heading text-[clamp(2.2rem,4.5vw,3.5rem)]">
                Your yes is tied to <em>the exact thing</em> you saw.
              </h2>
              <p className="mt-5 max-w-xl text-lg leading-relaxed text-muted-foreground">
                Every approval is locked to the content, the account and the action on the card.
                The AI can prepare work on its own, but it can&apos;t skip this step.
              </p>
              <ul className="mt-8 flex flex-col gap-4">
                {guarantees.map((g) => (
                  <li key={g} className="flex gap-3 leading-relaxed">
                    <span
                      aria-hidden
                      className="mt-2 size-2 shrink-0 rounded-full bg-coral ring-4 ring-coral-wash"
                    />
                    {g}
                  </li>
                ))}
              </ul>
            </div>
            <Fingerprint />
          </div>
        </section>

        {/* Apps */}
        <section id="apps" className="scroll-mt-20 border-t bg-card">
          <div className="mx-auto w-full max-w-6xl px-4 py-24 sm:px-6">
            <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
              <h2 className="heading max-w-xl text-[clamp(2.2rem,4.5vw,3.5rem)]">
                Works in the apps <em>you already use.</em>
              </h2>
              <p className="max-w-sm leading-relaxed text-muted-foreground">
                Connect each app with one click on its own sign-in page. Disconnect any time.
              </p>
            </div>
            <div className="mt-12 grid overflow-hidden rounded-2xl border sm:grid-cols-2">
              {appGroups.map((g, i) => (
                <div
                  key={g.name}
                  className={`flex flex-col gap-4 p-6 sm:p-8 ${i % 2 === 0 ? "sm:border-r" : ""} ${i < 2 ? "border-b" : i === 2 ? "border-b sm:border-b-0" : ""}`}
                >
                  <div>
                    <h3 className="font-semibold tracking-tight">{g.name}</h3>
                    <p className="mt-1 text-sm text-muted-foreground">{g.does}</p>
                  </div>
                  <ul className="flex flex-wrap gap-2">
                    {g.apps.map((a) => (
                      <li
                        key={a}
                        className="rounded-full border bg-background px-3 py-1 text-sm font-medium"
                      >
                        {a}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Security */}
        <section id="security" className="scroll-mt-20 border-t">
          <div className="mx-auto w-full max-w-6xl px-4 py-24 sm:px-6">
            <h2 className="heading max-w-2xl text-[clamp(2.2rem,4.5vw,3.5rem)]">
              Built so your accounts <em>stay yours.</em>
            </h2>
            <div className="mt-14 grid gap-x-10 gap-y-10 sm:grid-cols-2">
              {security.map(({ icon: Icon, title, body }) => (
                <div key={title} className="flex gap-4">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-indigo-wash text-indigo">
                    <Icon className="size-5" aria-hidden />
                  </span>
                  <div>
                    <h3 className="font-semibold tracking-tight">{title}</h3>
                    <p className="mt-1.5 leading-relaxed text-muted-foreground">{body}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Closing call to action */}
        <section className="px-4 pb-24 sm:px-6">
          <div className="relative mx-auto w-full max-w-6xl overflow-hidden rounded-3xl bg-ink px-6 py-16 text-paper sm:px-12 sm:py-20">
            <div
              aria-hidden
              className="pointer-events-none absolute -right-24 -bottom-40 size-[420px] rounded-full bg-[radial-gradient(circle,rgba(255,107,74,0.45),rgba(79,70,229,0.35)_45%,transparent_70%)] blur-2xl"
            />
            <h2 className="heading relative max-w-2xl text-[clamp(2.2rem,5vw,4rem)]">
              Give your first AI employee <em className="text-coral">a task</em> today.
            </h2>
            <p className="relative mt-5 max-w-lg text-lg text-paper/70">
              Set up your workspace, connect one app and send your first command. It takes a few
              minutes.
            </p>
            <Link
              href="/sign-up"
              className="relative mt-8 inline-flex h-12 items-center gap-2 rounded-xl bg-paper px-5 font-medium text-ink transition-colors hover:bg-coral-wash"
            >
              Create your workspace
              <ArrowUpRight className="size-4" aria-hidden />
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-10 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p className="flex items-center gap-2">
            <LogoMark className="size-5" />
            Cognitive Mesh. AI employees with a human in charge.
          </p>
          <nav aria-label="Footer" className="flex gap-5">
            <a href="#how" className="hover:text-foreground">How it works</a>
            <a href="#security" className="hover:text-foreground">Security</a>
            <Link href="/sign-in" className="hover:text-foreground">Sign in</Link>
          </nav>
        </div>
      </footer>
    </>
  );
}
