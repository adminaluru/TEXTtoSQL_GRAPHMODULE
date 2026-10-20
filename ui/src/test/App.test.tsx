// End-to-end UI behaviour with a fake API: sign-in, workspaces, asking,
// choices and the negative scenarios listed in docs/project/ROADMAP.md.

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, type Mock } from "vitest";

import { App } from "../App";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import type { AnswerResponse, MeResponse } from "../api/types";

const ONE_COMPANY: MeResponse = { subject_id: "user:alice", kind: "user", roles: ["credit_manager"], tenants: ["tenant-acme"] };
const TWO_COMPANIES: MeResponse = { ...ONE_COMPANY, tenants: ["tenant-acme", "tenant-nova"] };

const scalar = (text: string, value = "500.00"): AnswerResponse => ({
  answer: text, result_type: "scalar", row_count: 1, data: { alias: "v", value, currency: "USD" }, warnings: [],
});

interface FakeApi {
  me: Mock<ApiClient["me"]>;
  ask: Mock<ApiClient["ask"]>;
  clarify: Mock<ApiClient["clarify"]>;
  listDocuments: Mock<ApiClient["listDocuments"]>;
  uploadDocument: Mock<ApiClient["uploadDocument"]>;
  retryDocument: Mock<ApiClient["retryDocument"]>;
  deleteDocument: Mock<ApiClient["deleteDocument"]>;
  searchDocuments: Mock<ApiClient["searchDocuments"]>;
  listConversations: Mock<ApiClient["listConversations"]>;
  getConversation: Mock<ApiClient["getConversation"]>;
  deleteConversation: Mock<ApiClient["deleteConversation"]>;
}

function fakeApi(me: MeResponse = ONE_COMPANY): FakeApi {
  return {
    me: vi.fn<ApiClient["me"]>(async () => me),
    ask: vi.fn<ApiClient["ask"]>(async () => scalar("The total claim amount is 500.00 USD.")),
    clarify: vi.fn<ApiClient["clarify"]>(async () =>
      scalar("The total claim amount for LE-1002 is 1000.00 USD.", "1000.00")),
    listDocuments: vi.fn<ApiClient["listDocuments"]>(async () => ({ documents: [] })),
    uploadDocument: vi.fn<ApiClient["uploadDocument"]>(),
    retryDocument: vi.fn<ApiClient["retryDocument"]>(),
    deleteDocument: vi.fn<ApiClient["deleteDocument"]>(async () => undefined),
    searchDocuments: vi.fn<ApiClient["searchDocuments"]>(),
    listConversations: vi.fn<ApiClient["listConversations"]>(async () => ({ conversations: [] })),
    getConversation: vi.fn<ApiClient["getConversation"]>(),
    deleteConversation: vi.fn<ApiClient["deleteConversation"]>(async () => undefined),
  };
}

async function signIn(api: ApiClient) {
  render(<App api={api} />);
  await userEvent.type(screen.getByLabelText("Access token"), "dev-token");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

async function ask(question: string) {
  await userEvent.type(screen.getByLabelText("Ask about your business data"), question);
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("FlowOne UI", () => {
  it("signs in, uses the only workspace and shows the answer with its currency", async () => {
    const api = fakeApi();
    await signIn(api);
    await ask("Total claims for LE-1001?");
    expect(await screen.findByText("500.00 USD")).toBeInTheDocument();
    expect(api.ask).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" },
      "Total claims for LE-1001?", expect.any(String));
  });

  it("explains when the server is not in per-user sign-in mode", async () => {
    const api = fakeApi();
    api.me.mockRejectedValue(new ApiError("not_available", "Not Found"));
    await signIn(api);
    expect(await screen.findByText(/without FlowOne sign-in/)).toBeInTheDocument();
  });

  it("rejects an invalid token at sign-in", async () => {
    const api = fakeApi();
    api.me.mockRejectedValue(new ApiError("unauthenticated", "Authentication is required."));
    await signIn(api);
    expect(await screen.findByText(/not valid or has expired/)).toBeInTheDocument();
  });

  it("asks for a workspace when the user belongs to several companies", async () => {
    await signIn(fakeApi(TWO_COMPANIES));
    expect(await screen.findByText("Choose a workspace")).toBeInTheDocument();
    expect(screen.queryByLabelText("Ask about your business data")).not.toBeInTheDocument();
  });

  it("returns to sign-in when the session expires mid-conversation", async () => {
    const api = fakeApi();
    api.ask.mockRejectedValue(new ApiError("unauthenticated", "Authentication is required."));
    await signIn(api);
    await ask("Q");
    expect(await screen.findByText("Your session has ended. Please sign in again.")).toBeInTheDocument();
  });

  it("ignores a second send while an answer is on its way", async () => {
    const api = fakeApi();
    const pending = deferred<AnswerResponse>();
    api.ask.mockReturnValue(pending.promise);
    await signIn(api);
    await ask("First");
    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Ask about your business data"), "Second{Enter}");
    expect(api.ask).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve(scalar("Done.")));
  });

  it("drops a late answer after the workspace was switched", async () => {
    const api = fakeApi(TWO_COMPANIES);
    const pending = deferred<AnswerResponse>();
    api.ask.mockReturnValue(pending.promise);
    await signIn(api);
    await userEvent.selectOptions(await screen.findByLabelText("Workspace"), "tenant-acme");
    await ask("Acme question");
    await userEvent.selectOptions(screen.getByLabelText("Workspace"), "tenant-nova");
    await act(async () => pending.resolve(scalar("Acme-only answer.")));
    expect(screen.queryByText("Acme-only answer.")).not.toBeInTheDocument();
    expect(screen.queryByText("Acme question")).not.toBeInTheDocument();     // nothing from Acme in Nova
    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled(); // empty box, not stuck busy
    await userEvent.type(screen.getByLabelText("Ask about your business data"), "x");
    expect(screen.getByRole("button", { name: "Send question" })).toBeEnabled();
  });

  it("does not stay busy after starting a new conversation mid-request", async () => {
    const api = fakeApi();
    api.ask.mockReturnValue(new Promise(() => undefined));   // never answers
    await signIn(api);
    await ask("Slow question");
    await userEvent.click(screen.getByRole("button", { name: /New conversation/ }));
    await userEvent.type(screen.getByLabelText("Ask about your business data"), "Next");
    expect(screen.getByRole("button", { name: "Send question" })).toBeEnabled();
  });

  it("runs the choose-one flow and locks the choices", async () => {
    const api = fakeApi();
    api.ask.mockResolvedValue({
      answer: "This name matches more than one record. Which one do you mean?", result_type: "clarification",
      row_count: 0, warnings: [],
      data: { clarification_id: "c1", question: "Which one?", reason: "ambiguous_entity", allows_text: false,
        expires_at: "2026-10-15T10:00:00Z",
        options: [{ option_id: "o1", label: "LE-1001 — Acme Primary" }, { option_id: "o2", label: "LE-1002 — Acme Secondary" }] },
    });
    await signIn(api);
    await ask("Claims for Blue Prism?");
    await userEvent.click(await screen.findByRole("button", { name: "LE-1002 — Acme Secondary" }));
    expect(await screen.findByText("1,000.00 USD")).toBeInTheDocument();
    expect(api.clarify).toHaveBeenCalledWith(expect.anything(), expect.any(String), "c1", "o2");
    expect(screen.getByRole("button", { name: "LE-1001 — Acme Primary" })).toBeDisabled();
  });

  it("explains an expired choice and keeps the buttons locked", async () => {
    const api = fakeApi();
    api.ask.mockResolvedValue({
      answer: "Which one?", result_type: "clarification", row_count: 0, warnings: [],
      data: { clarification_id: "c1", question: "Which one?", reason: "r", allows_text: false,
        expires_at: "2026-10-15T10:00:00Z", options: [{ option_id: "o1", label: "Option A" }] },
    });
    api.clarify.mockRejectedValue(new ApiError("conflict", "This clarification is no longer available."));
    await signIn(api);
    await ask("Q");
    await userEvent.click(await screen.findByRole("button", { name: "Option A" }));
    expect(await screen.findByText("This clarification is no longer available.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Option A" })).toBeDisabled();
  });

  it("tells the user when a question took too long, with a reference", async () => {
    const api = fakeApi();
    api.ask.mockRejectedValue(new ApiError("too_slow", "The question took too long to answer.", "req-42"));
    await signIn(api);
    await ask("Heavy question");
    expect(await screen.findByText(/took too long/)).toBeInTheDocument();
    expect(screen.getByText(/reference req-42/)).toBeInTheDocument();
  });

  it("refreshes the workspaces after a not-permitted answer", async () => {
    const api = fakeApi();
    api.ask.mockRejectedValue(new ApiError("forbidden", "You do not have access to the selected company."));
    await signIn(api);
    await ask("Q");
    await waitFor(() => expect(api.me).toHaveBeenCalledTimes(2));
  });

  it("shows a documents answer with quoted passages, citations and where it came from", async () => {
    const api = fakeApi();
    api.ask.mockResolvedValue({
      answer: "Here is what your documents say. Each passage is quoted exactly, with where it comes from.",
      result_type: "documents", row_count: 1, data: null, warnings: [], route: "documents",
      passages: [{ document_id: "doc_1", version: 2, citation: "credit_policy.docx · section “Late payment fees”, paragraphs 3–4",
                   text: "Late payments incur a fee of 2% per month." }],
    });
    await signIn(api);
    await ask("What does our contract say about late payment fees?");
    expect(await screen.findByText("Late payments incur a fee of 2% per month.")).toBeInTheDocument();
    expect(screen.getAllByText(/credit_policy.docx · section “Late payment fees”/).length).toBeGreaterThan(0);
    expect(screen.getByText("Answered from your documents")).toBeInTheDocument();
    expect(screen.getByText("Document passages")).toBeInTheDocument();
    expect(screen.queryByText("Structured data")).not.toBeInTheDocument();
  });

  it("shows the database figure first and the passages under it for a combined answer", async () => {
    const api = fakeApi();
    api.ask.mockResolvedValue({
      ...scalar("There are 12 overdue invoices.", "12"), route: "both",
      passages: [{ document_id: "d", version: 1, citation: "policy.pdf · page 2", text: "Reminders are sent 3 days after the due date." }],
    });
    await signIn(api);
    await ask("How many invoices are overdue and what does the policy say about reminders?");
    expect(await screen.findByText("There are 12 overdue invoices.")).toBeInTheDocument();
    expect(screen.getByText("Reminders are sent 3 days after the due date.")).toBeInTheDocument();
    expect(screen.getByText("Answered from business data and your documents")).toBeInTheDocument();
  });

  it("keeps plain database answers unchanged when no route is reported", async () => {
    await signIn(fakeApi());
    await ask("Total claims?");
    await screen.findByText("500.00 USD");
    expect(screen.queryByText(/Answered from/)).not.toBeInTheDocument();
  });

  it("shows passage text from documents as plain text", async () => {
    const api = fakeApi();
    api.ask.mockResolvedValue({ answer: "Here is what your documents say.", result_type: "documents", row_count: 1,
      data: null, warnings: [], route: "documents",
      passages: [{ document_id: "d", version: 1, citation: "x.pdf · page 1", text: "<script>alert(1)</script><img src=x>" }] });
    await signIn(api);
    await ask("policy?");
    expect(await screen.findByText("<script>alert(1)</script><img src=x>")).toBeInTheDocument();
    expect(document.querySelector(".answer img, .answer script")).toBeNull();
  });
});
