import type { NextConfig } from "next";

/**
 * Nothing clever here on purpose.
 *
 * The app is one authenticated reader over Supabase with no image pipeline, no
 * rewrite table and no analytics hook. `reactStrictMode` stays on because a
 * double-invoked effect is how a leaked interval announces itself in
 * development, and this app runs a one-second render tick.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,

  /**
   * `NEXT_DIST_DIR` lets a long-running dev server build somewhere other than
   * `.next`, and it exists because of a failure that happened three times in one
   * afternoon: a `pnpm build` from this same checkout overwrites the route
   * manifest a running `next dev` is serving from, and the server dies without
   * ever logging an error. The README already warned about two dev servers doing
   * this to each other; a build does it just as well.
   *
   * Unset everywhere that matters, so Vercel and CI are untouched.
   */
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;
