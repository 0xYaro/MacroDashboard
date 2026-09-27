import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Every route but `/login` requires a session, and the middleware is the only
 * place that is decided.
 *
 * A deny-by-default list rather than a per-page guard: a page added next month
 * is protected by existing, and a guard nobody remembered to add is the failure
 * mode this shape removes. There is no signup route to allow, because there is no
 * signup UI anywhere in this app: the single operator account is created from the
 * Supabase dashboard.
 *
 * The middleware also refreshes the auth token on every request, which is why the
 * response it returns must be the one carrying the refreshed cookies. Building a
 * fresh `NextResponse` after calling `getUser` would drop them and log the
 * operator out roughly once an hour.
 */

const PUBLIC_PATHS = ["/login"];

/** Fixture mode has no Supabase project to hold a session, so it has no gate. */
const FIXTURES = process.env.NEXT_PUBLIC_FIXTURES === "1";

export async function middleware(request: NextRequest) {
  if (FIXTURES) return NextResponse.next({ request });

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  // Unconfigured is not authenticated. Without a project there is no session to
  // check, so every route falls to `/login`, which says what is missing.
  if (!url || !key) {
    return isPublic(request) ? NextResponse.next({ request }) : redirect(request);
  }

  let response = NextResponse.next({ request });

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(toSet) {
        for (const { name, value } of toSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of toSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // `getUser`, not `getSession`: the session is whatever the cookie says, and the
  // cookie is client-supplied. Only `getUser` asks the auth server whether the
  // token is real.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (user === null && !isPublic(request)) return redirect(request);

  // A signed-in operator has no business on the login form.
  if (user !== null && isPublic(request)) {
    const to = request.nextUrl.clone();
    to.pathname = "/";
    to.search = "";
    return NextResponse.redirect(to);
  }

  return response;
}

function isPublic(request: NextRequest): boolean {
  return PUBLIC_PATHS.some(
    (path) =>
      request.nextUrl.pathname === path ||
      request.nextUrl.pathname.startsWith(`${path}/`),
  );
}

function redirect(request: NextRequest) {
  const to = request.nextUrl.clone();
  to.pathname = "/login";
  to.search = "";
  return NextResponse.redirect(to);
}

export const config = {
  /**
   * Everything except Next's own static output and the favicon.
   *
   * Static assets are excluded because running the auth check on them would cost
   * a token refresh per image, not because they are public in any interesting
   * sense: there is nothing in `/_next/static` that is not already in the bundle
   * the login page itself serves.
   */
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.svg$).*)"],
};
