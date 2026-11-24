import { ClerkProvider } from "@clerk/nextjs";
import { shadcn } from "@clerk/ui/themes";
import type { Metadata } from "next";
import { Geist, Geist_Mono, Instrument_Serif } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const instrumentSerif = Instrument_Serif({
  variable: "--font-instrument-serif",
  subsets: ["latin"],
  weight: "400",
  style: ["normal", "italic"],
});

export const metadata: Metadata = {
  title: "Cognitive Mesh",
  description: "AI employees that prepare the work and wait for your approval before they act.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${instrumentSerif.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <ClerkProvider
          localization={{
            signIn: {
              start: {
                title: "Sign in to Cognitive Mesh",
                titleCombined: "Sign in to Cognitive Mesh",
                subtitle: "Use the account you signed up with.",
                subtitleCombined: "Use the account you signed up with.",
              },
            },
            signUp: {
              start: {
                title: "Create your workspace",
                titleCombined: "Create your workspace",
                subtitle: "Start with Google or your email address.",
                subtitleCombined: "Start with Google or your email address.",
              },
            },
          }}
          appearance={{
            theme: shadcn,
            variables: {
              colorPrimary: "#4f46e5",
              colorForeground: "#16161d",
              colorMutedForeground: "#6b6b76",
              colorBackground: "#ffffff",
              colorInput: "#ffffff",
              colorBorder: "#dedbd4",
              colorRing: "#4f46e5",
              fontFamily: "var(--font-geist-sans)",
              borderRadius: "0.75rem",
            },
          }}
        >
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
