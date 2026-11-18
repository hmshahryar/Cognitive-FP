import {
  ClerkProvider,
  Show,
  SignInButton,
  SignUpButton,
  UserButton,
} from "@clerk/nextjs";
import { shadcn } from "@clerk/ui/themes";
import { Suspense } from "react";
import { Button } from "@/components/ui/button";
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Cognitive Mesh",
  description: "Autonomous AI employees for your workspace",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <ClerkProvider appearance={{ theme: shadcn }}>
          <header className="flex items-center gap-3 border-b px-6 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              Cognitive Mesh
            </Link>
            <nav className="flex flex-1 items-center gap-1 text-sm">
              <Suspense>
                <Show when="signed-in">
                  <Link
                    href="/command"
                    className="rounded-md px-2.5 py-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    Command Center
                  </Link>
                  <Link
                    href="/connectors"
                    className="rounded-md px-2.5 py-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    Connectors
                  </Link>
                </Show>
              </Suspense>
            </nav>
            <Suspense>
              <Show when="signed-out">
                <SignInButton>
                  <Button variant="ghost">Sign in</Button>
                </SignInButton>
                <SignUpButton>
                  <Button>Sign up</Button>
                </SignUpButton>
              </Show>
              <Show when="signed-in">
                <UserButton />
              </Show>
            </Suspense>
          </header>
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}