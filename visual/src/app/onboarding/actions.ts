"use server";

import { redirect } from "next/navigation";

import { ApiError, backendFetch } from "@/lib/api";
import {
  GOALS,
  TEAM_SIZES,
  saveOnboarding,
  type Goal,
  type OnboardingAnswers,
  type TeamSize,
} from "@/lib/onboarding";

export type SaveResult = { error: string | null };

function message(e: unknown) {
  return e instanceof ApiError ? e.message : "Couldn't save. Please try again.";
}

/** Only pass on known choices; the backend validates again. */
function clean(answers: OnboardingAnswers): OnboardingAnswers {
  return {
    workspace_name: String(answers.workspace_name ?? "").slice(0, 80),
    team_size: TEAM_SIZES.includes(answers.team_size as TeamSize) ? answers.team_size : null,
    goals: (answers.goals ?? []).filter((g): g is Goal => GOALS.includes(g as Goal)),
  };
}

export async function saveProgressAction(answers: OnboardingAnswers): Promise<SaveResult> {
  try {
    await saveOnboarding(clean(answers));
    return { error: null };
  } catch (e) {
    return { error: message(e) };
  }
}

/** Save progress, then open the app's own sign-in page via a Composio Connect Link. */
export async function connectFromOnboardingAction(
  answers: OnboardingAnswers,
  slug: string,
): Promise<SaveResult> {
  let url: string;
  try {
    await saveOnboarding(clean(answers));
    ({ redirect_url: url } = await backendFetch<{ redirect_url: string }>(
      `/connectors/${encodeURIComponent(slug)}/connect`,
      { method: "POST" },
    ));
  } catch (e) {
    return { error: message(e) };
  }
  redirect(url);
}

export async function finishOnboardingAction(answers: OnboardingAnswers): Promise<SaveResult> {
  try {
    await saveOnboarding(clean(answers), true);
  } catch (e) {
    return { error: message(e) };
  }
  redirect("/command");
}
