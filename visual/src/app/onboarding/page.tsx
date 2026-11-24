import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { OnboardingFlow, type StepId } from "@/components/onboarding/onboarding-flow";
import { listConnectors } from "@/lib/connectors";
import { GOAL_CONNECTORS, getOnboarding } from "@/lib/onboarding";

export const metadata: Metadata = { title: "Set up your workspace · Cognitive Mesh" };

type Search = Promise<{ [key: string]: string | string[] | undefined }>;

const DEFAULT_NAME = "Personal workspace";

export default async function OnboardingPage({ searchParams }: { searchParams: Search }) {
  const onboarding = await getOnboarding();
  if (onboarding.completed) redirect("/dashboard");

  const wanted = new Set(Object.values(GOAL_CONNECTORS).flat());
  let apps: { slug: string; name: string; logo: string | null; connected: boolean }[] = [];
  try {
    const { connectors } = await listConnectors();
    apps = connectors
      .filter((c) => c.available && wanted.has(c.slug))
      .map((c) => ({ slug: c.slug, name: c.name, logo: c.logo_url, connected: c.connected }));
  } catch {
    apps = [];
  }

  const answered =
    onboarding.workspace_name !== DEFAULT_NAME &&
    onboarding.team_size !== null &&
    onboarding.goals.length > 0;
  // Coming back from an app's sign-in page (or a reload) resumes at the connect step.
  const back = (await searchParams).step === "connect";
  const start: StepId = answered || back ? (answered ? "connect" : "name") : "welcome";

  return (
    <OnboardingFlow
      start={start}
      apps={apps}
      initial={{
        workspace_name: onboarding.workspace_name === DEFAULT_NAME ? "" : onboarding.workspace_name,
        team_size: onboarding.team_size,
        goals: onboarding.goals,
      }}
    />
  );
}
