"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import {
  FIXTURES,
  FIXTURE_DATASET,
  NOT_CONFIGURED,
  supabaseConfigured,
} from "@/lib/env";
import { fixtureSource, supabaseSource, type Source } from "@/lib/data";
import type { FixtureDataset } from "@/lib/fixtures";
import { browserClient } from "@/lib/supabase/browser";

/**
 * Where the rows come from, decided once, above the routes.
 *
 * A component never asks which mode it is in; it asks the source for a
 * relation. Nothing in the chrome says which mode is running: fixture mode is
 * a development switch, not a feature, and a reader should never be shown a
 * control that exists only because the data is not real.
 *
 * Which dataset is a shell question too, read once from the environment. It
 * was a pair of header buttons; those are gone.
 */
interface SourceContextValue {
  source: Source | null;
  /** Why there is no source, when there is none. */
  unavailable: string | null;
  fixtures: boolean;
  dataset: FixtureDataset;
}

const SourceContext = createContext<SourceContextValue | null>(null);

export function SourceProvider({ children }: { children: ReactNode }) {
  const value = useMemo<SourceContextValue>(() => {
    // Read from the environment, so it is a module constant rather than state
    // and the memo has nothing to depend on.
    const dataset = FIXTURE_DATASET;
    if (FIXTURES) {
      return {
        source: fixtureSource(dataset),
        unavailable: null,
        fixtures: true,
        dataset,
      };
    }
    const client = browserClient();
    return {
      // An expired-JWT refusal becomes a real sign-out, which
      // `useSessionGuard` observes and turns into a redirect. One exit from a
      // dead session, not two.
      source:
        client === null
          ? null
          : supabaseSource(client, () => {
              void client.auth.signOut();
            }),
      unavailable: supabaseConfigured() ? null : NOT_CONFIGURED,
      fixtures: false,
      dataset,
    };
  }, []);

  return (
    <SourceContext.Provider value={value}>{children}</SourceContext.Provider>
  );
}

export function useSource(): SourceContextValue {
  const value = useContext(SourceContext);
  if (value === null) {
    throw new Error("useSource must be used inside SourceProvider");
  }
  return value;
}
