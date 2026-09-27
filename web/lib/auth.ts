import { SUPABASE_URL } from "./env";

/**
 * A sign-in failure, in words the operator can act on.
 *
 * The bare string supabase-js hands back for an unreachable host is
 * "Failed to fetch", which reads as a rejected password and sends the operator
 * to check the one thing that is not wrong. A transport failure is named as one
 * instead, with the URL it could not reach, because that is where the fix is.
 *
 * Both shapes are handled: a transport failure usually resolves into `{ error }`
 * with status 0, and sometimes rejects outright, and the operator should not be
 * shown two different explanations of one problem depending on which.
 */
export function signInMessage(cause: unknown): string {
  const detail =
    typeof cause === "object" && cause !== null && "message" in cause
      ? String((cause as { message: unknown }).message)
      : String(cause);
  const status =
    typeof cause === "object" && cause !== null && "status" in cause
      ? (cause as { status?: number }).status
      : undefined;
  const transport =
    status === 0 || /failed to fetch|networkerror|load failed/i.test(detail);
  if (!transport) return detail;
  return `The browser could not reach the Supabase project at ${SUPABASE_URL || "the configured URL"} (${detail}). This is the network or the project URL, not the password. Check NEXT_PUBLIC_SUPABASE_URL and that the project is not paused.`;
}
