import type { ChatError } from "../state/useChat";

/** A failed request: the server's safe message, plus the request ID for support. */
export function ErrorNotice({ error }: { error: ChatError }) {
  const tone = error.kind === "too_slow" || error.kind === "rate_limited" ? "warn" : "error";
  return (
    <div className={`notice ${tone}`} role="alert">
      <span aria-hidden="true">!</span>
      <span>
        {error.message}
        {error.requestId && <span className="request-id"> (reference {error.requestId})</span>}
      </span>
    </div>
  );
}
