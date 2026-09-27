"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { useSessionGuard, useVisibilityRefetch } from "@/hooks/useSessionGuard";
import { useSource } from "@/hooks/useSource";
import { browserClient } from "@/lib/supabase/browser";

const ROUTES: readonly { href: string; label: string }[] = [
  { href: "/", label: "Desk" },
  { href: "/data", label: "Data" },
  { href: "/catalysts", label: "Catalysts" },
  { href: "/history", label: "History" },
  { href: "/calibration", label: "Calibration" },
];

/**
 * The chrome: where you are, which dataset you are looking at, and the way out.
 *
 * There is no signup link and no password-reset link, because there is no
 * signup flow: the single operator account is created from the Supabase
 * dashboard, and a link to a route that does not exist is worse than no link.
 *
 * **No fixture chrome.** Fixture mode still works for development, and it
 * still cannot be switched on against a deployment by a link, but it is not
 * part of what a reader sees. Which dataset a developer wants is a question
 * for their shell, not a control on the terminal: `NEXT_PUBLIC_FIXTURE_DATASET`
 * selects it, and a reader never sees that either mode exists.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { fixtures } = useSource();
  const [signingOut, setSigningOut] = useState(false);

  // A session that ends under an open page takes the operator to /login rather
  // than leaving a reading that has quietly stopped updating on screen.
  useSessionGuard(!fixtures);
  // And a tab that was away long enough to doubt re-reads rather than assuming.
  useVisibilityRefetch(!fixtures);

  async function signOut() {
    setSigningOut(true);
    const client = browserClient();
    if (client !== null) await client.auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="shell">
      <a className="skip-link" href="#reading">
        Skip to the reading
      </a>
      <header className="shell-head">
        <span className="shell-mark">Macro · the regime read</span>
        <nav className="shell-nav" aria-label="Sections">
          {ROUTES.map((route) => (
            <Link
              aria-current={pathname === route.href ? "page" : undefined}
              href={route.href}
              key={route.href}
            >
              {route.label}
            </Link>
          ))}
        </nav>
        <div className="shell-right">
          {fixtures ? null : (
            <button
              className="button-plain"
              disabled={signingOut}
              onClick={() => void signOut()}
              type="button"
            >
              {signingOut ? "Signing out" : "Sign out"}
            </button>
          )}
        </div>
      </header>
      <main className="shell-main" id="reading">
        {children}
      </main>
    </div>
  );
}
