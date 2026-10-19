// Every failure the UI can meet, reduced to a few kinds the screens know how
// to show. Server messages are already client-safe; the UI never shows raw
// exceptions, stack traces or response bodies it does not understand.

export type ApiErrorKind =
  | "unauthenticated" // 401: sign in again
  | "forbidden" //       403: not allowed / not a member of that company
  | "conflict" //        409: clarification expired, used or not yours; document cannot be retried
  | "too_large" //       413: uploaded file over the size limit
  | "too_slow" //        504: the question ran out of time
  | "rate_limited" //    429
  | "unavailable" //     503
  | "bad_request" //     400 / 422
  | "not_available" //   404: endpoint missing (e.g. backend in api_key mode)
  | "network" //         no response at all
  | "server"; //         500 and anything unexpected

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    message: string,
    public readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const FALLBACK: Record<ApiErrorKind, string> = {
  unauthenticated: "Please sign in again.",
  forbidden: "You do not have access to this.",
  conflict: "This choice is no longer available. Please ask the question again.",
  too_large: "The file is too large to upload.",
  too_slow: "The question took too long to answer. Please try again or ask a narrower question.",
  rate_limited: "Too many requests. Please wait a moment and try again.",
  unavailable: "The service is temporarily unavailable. Please try again later.",
  bad_request: "The question could not be answered. Try rephrasing it.",
  not_available: "This feature is not available on the server.",
  network: "Cannot reach the FlowOne service. Check that it is running.",
  server: "Something went wrong on the server.",
};

export function kindForStatus(status: number): ApiErrorKind {
  switch (status) {
    case 401: return "unauthenticated";
    case 403: return "forbidden";
    case 404: return "not_available";
    case 409: return "conflict";
    case 413: return "too_large";
    case 429: return "rate_limited";
    case 503: return "unavailable";
    case 504: return "too_slow";
    case 400:
    case 411:
    case 422: return "bad_request";
    default: return "server";
  }
}

export function fallbackMessage(kind: ApiErrorKind): string {
  return FALLBACK[kind];
}
