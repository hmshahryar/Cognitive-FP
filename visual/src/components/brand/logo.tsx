import Link from "next/link";

import { cn } from "@/lib/utils";

/** A ring with one node on it: an agent on the mesh, waiting for you. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-7", className)}>
      <defs>
        <linearGradient id="cm-ring" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#4f46e5" />
          <stop offset="0.6" stopColor="#7c6cf2" />
          <stop offset="1" stopColor="#ff6b4a" />
        </linearGradient>
      </defs>
      <circle cx="16" cy="16" r="10.5" fill="none" stroke="url(#cm-ring)" strokeWidth="4" />
      <circle cx="24.2" cy="9.4" r="3.4" fill="#16161d" stroke="#fafaf7" strokeWidth="1.6" />
    </svg>
  );
}

export function Logo({ href = "/", className }: { href?: string; className?: string }) {
  return (
    <Link
      href={href}
      className={cn("flex items-center gap-2 text-[15px] font-semibold tracking-tight", className)}
    >
      <LogoMark />
      Cognitive Mesh
    </Link>
  );
}
