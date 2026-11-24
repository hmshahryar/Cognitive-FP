import "server-only";

import { redirect } from "next/navigation";

import { backendFetch } from "@/lib/api";

export const TEAM_SIZES = ["just_me", "2_10", "11_50", "51_plus"] as const;
export const GOALS = ["email", "calendar", "crm", "docs", "social", "design"] as const;

export type TeamSize = (typeof TEAM_SIZES)[number];
export type Goal = (typeof GOALS)[number];

export type Onboarding = {
  workspace_name: string;
  team_size: TeamSize | null;
  goals: Goal[];
  completed: boolean;
  completed_at: string | null;
};

export type OnboardingAnswers = {
  workspace_name: string;
  team_size: TeamSize | null;
  goals: Goal[];
};

/** Which connectors match each goal (slugs from the backend catalog). */
export const GOAL_CONNECTORS: Record<Goal, string[]> = {
  email: ["gmail"],
  calendar: ["googlecalendar"],
  crm: ["hubspot", "salesforce", "zoho"],
  docs: ["notion", "googlesheets"],
  social: ["instagram", "facebook", "linkedin"],
  design: ["canva"],
};

export function getOnboarding() {
  return backendFetch<Onboarding>("/onboarding");
}

export function saveOnboarding(answers: OnboardingAnswers, complete = false) {
  return backendFetch<Onboarding>("/onboarding", {
    method: "PUT",
    body: JSON.stringify({ ...answers, complete }),
  });
}

/**
 * Send signed-in users who haven't finished onboarding to /onboarding.
 * If the backend can't be reached, the page loads anyway and shows its own error.
 */
export async function requireOnboarded() {
  let done = true;
  try {
    done = (await getOnboarding()).completed;
  } catch {
    done = true;
  }
  if (!done) redirect("/onboarding");
}
