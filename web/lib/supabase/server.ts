import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { SupabaseClient } from "@supabase/supabase-js";

import { SUPABASE_ANON_KEY, SUPABASE_URL, supabaseConfigured } from "../env";

/**
 * A server client bound to this request's cookies.
 *
 * Not memoised, deliberately: the cookie jar belongs to one request, and a
 * module-level client would serve one visitor's session to the next request that
 * happened to land on the same lambda.
 */
export async function serverClient(): Promise<SupabaseClient | null> {
  if (!supabaseConfigured()) return null;
  const jar = await cookies();
  return createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return jar.getAll();
      },
      setAll(toSet) {
        // A Server Component cannot write cookies. The middleware refreshes the
        // session on every request, so the only writes that reach here are the
        // ones it has already made, and swallowing them is correct rather than
        // merely tolerable.
        try {
          for (const { name, value, options } of toSet) {
            jar.set(name, value, options);
          }
        } catch {
          /* written by the middleware instead */
        }
      },
    },
  });
}
