import { SignUp } from "@clerk/nextjs";
import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthShell, authAppearance } from "@/components/auth/auth-shell";

export const metadata: Metadata = { title: "Create your workspace · Cognitive Mesh" };

export default function SignUpPage() {
  return (
    <AuthShell
      heading={
        <>
          Hire AI employees that <em>check with you first.</em>
        </>
      }
      intro="Your workspace is private to you. After sign-up we'll set it up together in a few short steps."
      points={[
        "Name your workspace",
        "Connect the first app you want help with",
        "Send your first command and approve the result",
      ]}
    >
      <Suspense>
        <SignUp appearance={authAppearance} fallbackRedirectUrl="/onboarding" />
      </Suspense>
    </AuthShell>
  );
}
