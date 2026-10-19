import { describe, expect, it, vi } from "vitest";

import { createClient } from "../api/client";
import { ApiError } from "../api/errors";

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status, headers }));
}

const caller = { token: "tok", tenant: "tenant-acme" };

describe("API client", () => {
  it("sends identity only as the bearer token and the tenant header", async () => {
    const fetchImpl = respond(200, { answer: "ok", result_type: "empty", row_count: 0, data: null, warnings: [] });
    await createClient(fetchImpl).ask(caller, "How much?", "conv-1");
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/ask");
    expect(init.headers).toEqual({
      Authorization: "Bearer tok",
      "X-FlowOne-Tenant": "tenant-acme",
      "Content-Type": "application/json",
    });
    expect(JSON.parse(init.body as string)).toEqual({ question: "How much?", conversation_id: "conv-1" });
  });

  it("omits the tenant header when no workspace is chosen", async () => {
    const fetchImpl = respond(200, { subject_id: "user:a", kind: "user", roles: [], tenants: ["t"] });
    await createClient(fetchImpl).me({ token: "tok", tenant: null });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it.each([
    [401, "unauthenticated"], [403, "forbidden"], [404, "not_available"], [409, "conflict"],
    [429, "rate_limited"], [503, "unavailable"], [504, "too_slow"], [400, "bad_request"], [500, "server"],
  ])("maps HTTP %i to %s and keeps the server's safe message and reference", async (status, kind) => {
    const fetchImpl = respond(status, { detail: "Safe message." }, { "X-Request-ID": "req-9" });
    const error = await createClient(fetchImpl).ask(caller, "Q", "c").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect([error.kind, error.message, error.requestId]).toEqual([kind, "Safe message.", "req-9"]);
  });

  it("replaces validation detail lists with plain text", async () => {
    const fetchImpl = respond(422, { detail: [{ loc: ["body", "tenant_id"], msg: "extra" }] });
    const error = await createClient(fetchImpl).ask(caller, "Q", "c").catch((e) => e);
    expect(error.kind).toBe("bad_request");
    expect(error.message).toBe("The question could not be answered. Try rephrasing it.");
  });

  it("reports an unreachable server as a network problem", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const error = await createClient(fetchImpl).ask(caller, "Q", "c").catch((e) => e);
    expect(error.kind).toBe("network");
  });

  it("uploads a document as a form, letting the browser set the content type", async () => {
    const fetchImpl = respond(202, { document_id: "doc_1" });
    const file = new File(["%PDF-"], "terms.pdf");
    await createClient(fetchImpl).uploadDocument(caller, file);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/documents");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ Authorization: "Bearer tok", "X-FlowOne-Tenant": "tenant-acme" });
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("deletes with DELETE and accepts an empty 204 reply", async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 204 }));
    await expect(createClient(fetchImpl).deleteDocument(caller, "doc/1")).resolves.toBeUndefined();
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect([url, init.method]).toEqual(["/api/documents/doc%2F1", "DELETE"]);
  });

  it("maps 413 to too_large and keeps the server's size message", async () => {
    const fetchImpl = respond(413, { detail: "The file is larger than 25 MB." });
    const error = await createClient(fetchImpl).uploadDocument(caller, new File(["x"], "a.pdf")).catch((e) => e);
    expect([error.kind, error.message]).toEqual(["too_large", "The file is larger than 25 MB."]);
  });

  it("replaces a bare Not Found with a helpful message", async () => {
    const fetchImpl = respond(404, { detail: "Not Found" });
    const error = await createClient(fetchImpl).listDocuments(caller).catch((e) => e);
    expect(error.message).toBe("This feature is not available on the server.");
  });

  it("searches documents with a JSON body in the active workspace", async () => {
    const fetchImpl = respond(200, { status: "empty", message: "none", terms: [], results: [] });
    await createClient(fetchImpl).searchDocuments(caller, "late fee");
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect([url, init.method]).toEqual(["/api/documents/search", "POST"]);
    expect(JSON.parse(init.body as string)).toEqual({ query: "late fee", limit: 5 });
  });
});
