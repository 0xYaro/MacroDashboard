"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { SUPABASE_ANON_KEY, SUPABASE_URL, supabaseConfigured } from "../env";

/**
 * One browser client per document.
 *
 * Memoised because the client owns the realtime socket and the auth token
 * refresh timer. A second instance would open a second socket and race the first
 * one's refresh, which shows up as a session that logs itself out.
 */
let client: SupabaseClient | null = null;

export function browserClient(): SupabaseClient | null {
  if (!supabaseConfigured()) return null;
  client ??= createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  return client;
}
