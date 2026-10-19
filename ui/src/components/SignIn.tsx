import { useState } from "react";

import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import type { MeResponse } from "../api/types";

interface Props {
  api: ApiClient;
  notice: string | null;
  onSignedIn: (token: string, me: MeResponse) => void;
}

/**
 * Phase 1 development sign-in: paste a token from
 *   python -m tools.issue_dev_token --subject alice --tenant tenant-acme
 * Production replaces this screen with the identity provider's sign-in
 * (Entra ID); the rest of the UI does not change.
 */
export function SignIn({ api, notice, onSignedIn }: Props) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const submit = async () => {
    const value = token.trim();
    if (!value || checking) return;
    setChecking(true);
    setError(null);
    try {
      const me = await api.me({ token: value, tenant: null });
      if (me.tenants.length === 0) {
        setError("This account has no access to any company.");
        return;
      }
      onSignedIn(value, me);
    } catch (e) {
      const kind = e instanceof ApiError ? e.kind : "server";
      setError(
        kind === "not_available"
          ? "The UI reached a server without FlowOne sign-in. Check that the UI points at the FlowOne API " +
            "(FLOWONE_API_URL in ui/.env.local) and that the API runs with AUTH_MODE=jwt (see docs/project/IDENTITY.md)."
          : kind === "unauthenticated"
            ? "This token is not valid or has expired. Issue a new one."
            : e instanceof Error
              ? e.message
              : "Sign-in failed.",
      );
    } finally {
      setChecking(false);
    }
  };

  return (
    <main className="panel signin">
      <h1>Sign in to FlowOne</h1>
      {notice && <p className="notice warn" role="alert">{notice}</p>}
      <p className="muted">
        Development sign-in: paste an access token created with
        <code> python -m tools.issue_dev_token --subject alice --tenant tenant-acme</code>.
        It is kept in this tab's memory only.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label htmlFor="token">Access token</label>
        <textarea id="token" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" spellCheck={false} />
        {error && <p className="notice error" role="alert">{error}</p>}
        <button type="submit" className="primary" disabled={checking || !token.trim()}>
          {checking ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
