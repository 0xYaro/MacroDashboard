"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { signInMessage } from "@/lib/auth";
import { FIXTURES, NOT_CONFIGURED, supabaseConfigured } from "@/lib/env";
import { browserClient } from "@/lib/supabase/browser";

/**
 * Sign in. Email and password, one account, no signup anywhere.
 *
 * There is no "create account" link and no "forgot password" link, because there
 * is no such flow: public signups are disabled on the project and the single
 * operator account is created from the Supabase dashboard. A link to a route that
 * does not exist is worse than no link.
 *
 * The TOTP step appears only when the signed-in user actually has a verified
 * factor. Asking for a code nobody enrolled would be a locked door with no key,
 * and hiding the step from someone who did enrol would silently downgrade their
 * session to aal1.
 */

type Stage = "password" | "totp";

export default function LoginPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [factor, setFactor] = useState<{
    id: string;
    challengeId: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const configured = supabaseConfigured();

  function done() {
    router.replace("/");
    router.refresh();
  }

  async function submitPassword(event: React.FormEvent) {
    event.preventDefault();
    const client = browserClient();
    if (client === null) {
      setError(NOT_CONFIGURED);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const signIn = await client.auth.signInWithPassword({ email, password });
      if (signIn.error !== null) {
        setError(signInMessage(signIn.error));
        return;
      }

      // Does this account owe a second factor? `getAuthenticatorAssuranceLevel`
      // answers from the token that was just issued, so it cannot be spoofed by
      // a stale list of factors.
      const { data: aal } =
        await client.auth.mfa.getAuthenticatorAssuranceLevel();
      if (
        aal !== null &&
        aal.nextLevel === "aal2" &&
        aal.nextLevel !== aal.currentLevel
      ) {
        const factors = await client.auth.mfa.listFactors();
        const totp = factors.data?.totp.find(
          (item) => item.status === "verified",
        );
        if (totp === undefined) {
          setError(
            "This account owes a second factor but has no verified authenticator enrolled. Enrol one from the Supabase dashboard, or remove the pending factor.",
          );
          return;
        }
        const challenge = await client.auth.mfa.challenge({
          factorId: totp.id,
        });
        if (challenge.error !== null) {
          setError(challenge.error.message);
          return;
        }
        setFactor({ id: totp.id, challengeId: challenge.data.id });
        setStage("totp");
        return;
      }

      done();
    } catch (cause) {
      setError(signInMessage(cause));
    } finally {
      // In a `finally` because every path above returns: without it a thrown
      // fetch leaves the button reading "Signing in" for ever and the operator
      // with no way to try again but a reload.
      setBusy(false);
    }
  }

  async function submitCode(event: React.FormEvent) {
    event.preventDefault();
    const client = browserClient();
    if (client === null || factor === null) return;
    setBusy(true);
    setError(null);
    try {
      const verify = await client.auth.mfa.verify({
        factorId: factor.id,
        challengeId: factor.challengeId,
        code,
      });
      if (verify.error !== null) {
        setError(signInMessage(verify.error));
        return;
      }
      done();
    } catch (cause) {
      setError(signInMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="form-page">
      <div className="form-card">
        <h1>Macro · the regime read</h1>
        <p className="form-lede">
          {FIXTURES
            ? "This build runs on the built-in W39 dataset, which is the real 2026-W39 reading. There is no project to sign in to and no route is gated, so this page is only here to be looked at."
            : "One reading, one operator. Sign in with the email and password created on the Supabase project."}
        </p>

        {!configured && !FIXTURES ? (
          <p className="form-error">{NOT_CONFIGURED}</p>
        ) : null}

        {FIXTURES ? (
          <>
            <button
              className="button-primary"
              onClick={() => router.replace("/")}
              type="button"
            >
              Open the reading
            </button>
          </>
        ) : stage === "password" ? (
          <form onSubmit={(event) => void submitPassword(event)}>
            <label className="field">
              <span>Email</span>
              <input
                autoComplete="username"
                name="email"
                onChange={(event) => setEmail(event.target.value)}
                required
                type="email"
                value={email}
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                autoComplete="current-password"
                name="password"
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </label>
            <button className="button-primary" disabled={busy} type="submit">
              {busy ? "Signing in" : "Sign in"}
            </button>
          </form>
        ) : (
          <form onSubmit={(event) => void submitCode(event)}>
            <p className="form-lede">
              This account has an authenticator enrolled. Enter the current
              six-digit code to confirm the second factor.
            </p>
            <label className="field">
              <span>Authenticator code</span>
              <input
                autoComplete="one-time-code"
                inputMode="numeric"
                name="code"
                onChange={(event) => setCode(event.target.value)}
                pattern="[0-9]*"
                required
                value={code}
              />
            </label>
            <button className="button-primary" disabled={busy} type="submit">
              {busy ? "Verifying" : "Verify"}
            </button>
          </form>
        )}

        {error !== null ? <p className="form-error">{error}</p> : null}
      </div>
    </div>
  );
}
