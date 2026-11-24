import { SignIn } from "@clerk/nextjs";
import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthShell, authAppearance } from "@/components/auth/auth-shell";

export const metadata: Metadata = { title: "Sign in · Cognitive Mesh" };

export default function SignInPage() {
  return (
    <AuthShell
      heading={
        <>
          Welcome back. Your team is <em>waiting on you.</em>
        </>
      }
      intro="Anything your AI employees prepared while you were away is still waiting for your decision. Nothing went out without you."
      points={[
        "Review the approvals waiting for you",
        "Check what ran and what you rejected",
        "Send the next command",
      ]}
    >
      <Suspense>
        <SignIn appearance={authAppearance} fallbackRedirectUrl="/dashboard" />
      </Suspense>
    </AuthShell>
  );
}
