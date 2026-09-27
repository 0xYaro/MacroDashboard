/**
 * The two public keys this app holds, and the one development switch.
 *
 * Only `NEXT_PUBLIC_*` variables exist here. The service-role key never appears
 * in `web/`, by contract: it bypasses RLS, and anything a browser bundle holds
 * is a value the operator has published. The worker holds it; this app reads
 * `macro_mart` and `macro_quality` as `authenticated` and nothing else.
 *
 * `NEXT_PUBLIC_SUPABASE_ANON_KEY` holds a new-style publishable key
 * (`sb_publishable_...`) rather than the legacy anon JWT. Neither
 * `@supabase/supabase-js` nor `@supabase/ssr` inspects the key: both send it as
 * the `apikey` header, so a publishable key is a drop-in. The variable keeps
 * its name so the Vercel project, `docs/CONTRACT.md` and this file agree.
 */

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

/**
 * Fixture mode: two datasets built from the real W39 reading, no Supabase,
 * development only.
 *
 * Read from the environment rather than from a cookie or a query string so it
 * cannot be switched on against a deployment by a link. A build that did not
 * set it has no fixture code path reachable at all.
 */
export const FIXTURES = process.env.NEXT_PUBLIC_FIXTURES === "1";

/**
 * Which fixture dataset a development run uses.
 *
 * Read from the environment rather than switched in the header. Fixture mode
 * is a development switch and not a feature, so nothing about it belongs in
 * the chrome a reader sees: a control that exists only because the data is not
 * real teaches the reader to wonder whether it is.
 *
 * `NEXT_PUBLIC_FIXTURE_DATASET=honest` selects the honesty cases. Anything
 * else, including nothing, is the clean set.
 */
export const FIXTURE_DATASET: "clean" | "honest" =
  process.env.NEXT_PUBLIC_FIXTURE_DATASET === "honest" ? "honest" : "clean";

export function supabaseConfigured(): boolean {
  return SUPABASE_URL.length > 0 && SUPABASE_ANON_KEY.length > 0;
}

/**
 * The sentence an unconfigured deployment shows.
 *
 * It names the two variables, because "failed to connect" sends the operator to
 * the network tab for a problem that is a missing environment variable.
 */
export const NOT_CONFIGURED =
  "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are not both set, so this deployment has no database to read. Set them in the Vercel project, or run with NEXT_PUBLIC_FIXTURES=1 to work against the built-in datasets.";
