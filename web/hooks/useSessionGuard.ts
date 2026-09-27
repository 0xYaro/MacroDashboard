"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { browserClient } from "@/lib/supabase/browser";
import { STALE_AFTER_HIDDEN_MS } from "@/lib/time";

/**
 * What happens when the session ends while the page is open.
 *
 * The middleware only runs on a navigation. A token that expires under a page
 * that is already painted is therefore invisible to it, and without this hook
 * the operator would sit in front of a reading that had stopped updating,
 * looking exactly like a reading that had stopped moving. On a terminal whose
 * data changes twice a day, those two are indistinguishable by eye for hours,
 * which makes this the single most dangerous state this app can be in.
 *
 * So: any end of session takes the operator to `/login`, and the cache is
 * cleared on the way out. Cleared rather than left: a signed-out browser
 * holding a rendered scenario set is a copy of the week's judgment outside the
 * gate, and the next sign-in must read the database rather than a cache from
 * before the gap.
 */
export function useSessionGuard(enabled: boolean): void {
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return;
    const client = browserClient();
    if (client === null) return;

    const { data } = client.auth.onAuthStateChange((event) => {
      if (event !== "SIGNED_OUT") return;
      queryClient.clear();
      router.replace("/login");
      router.refresh();
    });
    return () => data.subscription.unsubscribe();
  }, [enabled, queryClient, router]);
}

/**
 * Refetch everything after the tab has been hidden long enough to doubt it.
 *
 * Ten minutes rather than Accounting's thirty seconds, because the thing behind
 * this app moves twice a day and re-reading on every window switch would be a
 * poll wearing a different name. A backgrounded tab is throttled, so the
 * `refetchInterval` cannot be trusted to have fired while it was away, and
 * neither the browser nor an idle proxy reports that it did not.
 */
export function useVisibilityRefetch(enabled: boolean): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled || typeof document === "undefined") return;
    let hiddenAt: number | null = null;

    function onVisibilityChange() {
      if (document.visibilityState === "hidden") {
        hiddenAt = Date.now();
        return;
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt;
      hiddenAt = null;
      if (away >= STALE_AFTER_HIDDEN_MS) {
        void queryClient.invalidateQueries();
      }
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [enabled, queryClient]);
}
