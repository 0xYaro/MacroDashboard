"use client";

import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SourceProvider } from "@/hooks/useSource";
import { RUN_INTERVAL_MS } from "@/lib/time";

/**
 * One query client per document, polling at the run interval and no socket.
 *
 * `staleTime` is the run interval, per `docs/CONTRACT.md`. Macro has no tick:
 * the worker writes at 09:30 and 21:00 Singapore time, so nothing this app
 * reads can change more often than that. A shorter interval would poll a
 * database that cannot have moved; a longer one would let a page sit through a
 * run. There is no Supabase Realtime subscription anywhere, by decision:
 * Accounting departed from the poll-don't-stream rule because Hyperliquid marks
 * move every two seconds, and this repository returns to it.
 *
 * `useState` rather than a module constant: a client created at module scope on
 * the server is shared between requests, which on a multi-tenant host means one
 * visitor's cache answering another's read. This app has one user, so the risk
 * is theoretical, but the shape costs nothing.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: RUN_INTERVAL_MS,
            refetchInterval: RUN_INTERVAL_MS,
            // The tab being focused is not evidence that a run happened.
            refetchOnWindowFocus: false,
            refetchOnReconnect: true,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <SourceProvider>{children}</SourceProvider>
    </QueryClientProvider>
  );
}
