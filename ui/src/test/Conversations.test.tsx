// Step 12d: saved conversations in the sidebar, and citations in written answers.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

import { App } from "../App";
import type { ApiClient, Caller } from "../api/client";
import { ApiError } from "../api/errors";
import type { AnswerResponse, ConversationDetail, ConversationInfo, MeResponse } from "../api/types";

const ONE: MeResponse = { subject_id: "user:alice", kind: "user", roles: ["credit_manager"], tenants: ["tenant-acme"] };
const TWO: MeResponse = { ...ONE, tenants: ["tenant-acme", "tenant-nova"] };

type Fake = { [K in keyof ApiClient]: Mock<ApiClient[K]> };

const info = (id: string, title: string): ConversationInfo => ({
  conversation_id: id, title, created_at: "2026-10-19T09:00:00+00:00", updated_at: "2026-10-19T09:05:00+00:00",
  message_count: 2,
});

const SAVED_ANSWER: AnswerResponse = {
  answer: "The count of invoice records with invoice status open is 24.", result_type: "scalar", row_count: 1,
  data: { alias: "count", value: 24 }, warnings: [],
};

const CLARIFICATION: AnswerResponse = {
  answer: "Which customer do you mean?", result_type: "clarification", row_count: 0, warnings: [],
  data: { clarification_id: "c1", question: "Which?", reason: "ambiguous", allows_text: false,
          expires_at: "2026-10-19T09:10:00+00:00", options: [{ option_id: "o1", label: "Blue Prism LE-1001" }] },
};

const detail = (id: string, title: string, extra: ConversationDetail["messages"] = []): ConversationDetail => ({
  ...info(id, title),
  messages: [
    { kind: "question", content: title, created_at: "2026-10-19T09:00:00+00:00" },
    { kind: "answer", content: SAVED_ANSWER, created_at: "2026-10-19T09:00:02+00:00" },
    ...extra,
  ],
});

function fakeApi(me: MeResponse = ONE, saved: Record<string, ConversationInfo[]> = {}): Fake {
  return {
    me: vi.fn<ApiClient["me"]>(async () => me),
    ask: vi.fn<ApiClient["ask"]>(async () => SAVED_ANSWER),
    clarify: vi.fn<ApiClient["clarify"]>(),
    listConversations: vi.fn<ApiClient["listConversations"]>(async (caller: Caller) => ({
      conversations: saved[caller.tenant ?? ""] ?? [],
    })),
    getConversation: vi.fn<ApiClient["getConversation"]>(async (_c: Caller, id: string) => detail(id, "Open invoices?")),
    deleteConversation: vi.fn<ApiClient["deleteConversation"]>(async () => undefined),
    listDocuments: vi.fn<ApiClient["listDocuments"]>(async () => ({ documents: [] })),
    uploadDocument: vi.fn<ApiClient["uploadDocument"]>(),
    retryDocument: vi.fn<ApiClient["retryDocument"]>(),
    deleteDocument: vi.fn<ApiClient["deleteDocument"]>(async () => undefined),
    searchDocuments: vi.fn<ApiClient["searchDocuments"]>(),
  };
}

async function signIn(api: Fake) {
  render(<App api={api} />);
  await userEvent.type(screen.getByLabelText("Access token"), "dev-token");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

const sidebar = () => screen.getByRole("navigation", { name: "Conversations" });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

afterEach(() => vi.restoreAllMocks());

describe("Saved conversations", () => {
  it("lists the saved conversations of the active company after sign-in", async () => {
    const api = fakeApi(ONE, { "tenant-acme": [info("a1", "Open invoices?"), info("a2", "Claims for Blue Prism")] });
    await signIn(api);
    expect(await within(sidebar()).findByRole("button", { name: "Open invoices?" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("button", { name: "Claims for Blue Prism" })).toBeInTheDocument();
    expect(api.listConversations).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" });
  });

  it("opens a saved conversation with its messages; saved choices stay locked", async () => {
    const api = fakeApi(ONE, { "tenant-acme": [info("a1", "Blue Prism claims?")] });
    api.getConversation.mockResolvedValue(detail("a1", "Blue Prism claims?", [
      { kind: "answer", content: CLARIFICATION, created_at: "2026-10-19T09:01:00+00:00" },
      { kind: "choice", content: { option_id: "o1", label: "Blue Prism LE-1001" }, created_at: "2026-10-19T09:02:00+00:00" },
    ]));
    await signIn(api);
    await userEvent.click(await within(sidebar()).findByRole("button", { name: "Blue Prism claims?" }));
    expect(await screen.findByText("The count of invoice records with invoice status open is 24.")).toBeInTheDocument();
    expect(api.getConversation).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" }, "a1");
    const choice = screen.getAllByRole("button", { name: "Blue Prism LE-1001" })[0];
    expect(choice).toBeDisabled();
    expect(screen.getAllByText("Blue Prism LE-1001").length).toBeGreaterThan(1); // the button and the saved choice
  });

  it("adds new questions to an opened conversation under its own id", async () => {
    const api = fakeApi(ONE, { "tenant-acme": [info("a1", "Open invoices?")] });
    await signIn(api);
    await userEvent.click(await within(sidebar()).findByRole("button", { name: "Open invoices?" }));
    await screen.findByText("The count of invoice records with invoice status open is 24.");
    await userEvent.type(screen.getByLabelText("Ask about your business data"), "And closed ones?");
    await userEvent.click(screen.getByRole("button", { name: "Send question" }));
    await waitFor(() => expect(api.ask).toHaveBeenCalledWith(expect.anything(), "And closed ones?", "a1"));
  });

  it("waits for a conversation to load before accepting a question", async () => {
    const api = fakeApi(ONE, { "tenant-acme": [info("a1", "Open invoices?")] });
    const pending = deferred<ConversationDetail>();
    api.getConversation.mockReturnValue(pending.promise);
    await signIn(api);
    await userEvent.click(await within(sidebar()).findByRole("button", { name: "Open invoices?" }));
    expect(screen.getByRole("status")).toHaveTextContent("Loading conversation");
    expect(screen.getByLabelText("Ask about your business data")).toBeDisabled();
    pending.resolve(detail("a1", "Open invoices?"));
    expect(await screen.findByText("The count of invoice records with invoice status open is 24.")).toBeInTheDocument();
  });

  it("shows why a conversation could not be opened", async () => {
    const api = fakeApi(ONE, { "tenant-acme": [info("a1", "Open invoices?")] });
    api.getConversation.mockRejectedValue(new ApiError("unavailable", "The service is temporarily unavailable."));
    await signIn(api);
    await userEvent.click(await within(sidebar()).findByRole("button", { name: "Open invoices?" }));
    expect(await screen.findByText("The service is temporarily unavailable.")).toBeInTheDocument();
  });

  it("deletes a conversation only after confirmation", async () => {
    const api = fakeApi(ONE, { "tenant-acme": [info("a1", "Open invoices?")] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await signIn(api);
    const remove = await within(sidebar()).findByRole("button", { name: "Delete conversation: Open invoices?" });
    await userEvent.click(remove);
    expect(api.deleteConversation).not.toHaveBeenCalled();
    await userEvent.click(remove);
    await waitFor(() => expect(within(sidebar()).queryByRole("button", { name: "Open invoices?" })).toBeNull());
    expect(api.deleteConversation).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" }, "a1");
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("switching company shows only that company's conversations, and drops a late list", async () => {
    const api = fakeApi(TWO, { "tenant-nova": [info("n1", "Nova question")] });
    const slowAcme = deferred<{ conversations: ConversationInfo[] }>();
    api.listConversations.mockImplementation(async (caller: Caller) =>
      caller.tenant === "tenant-acme" ? slowAcme.promise : { conversations: [info("n1", "Nova question")] });
    await signIn(api);
    await userEvent.selectOptions(screen.getByLabelText("Workspace"), "tenant-acme");
    await userEvent.selectOptions(screen.getByLabelText("Workspace"), "tenant-nova");
    expect(await within(sidebar()).findByRole("button", { name: "Nova question" })).toBeInTheDocument();
    slowAcme.resolve({ conversations: [info("a1", "Acme secret")] });
    await new Promise((r) => setTimeout(r, 20));
    expect(within(sidebar()).queryByText("Acme secret")).toBeNull();
  });
});

describe("Citations in written answers", () => {
  const written: AnswerResponse = {
    answer: "There are 24 open invoices. [database] Late payments incur a fee of 2% per month. [1] Unknown. [9]",
    result_type: "scalar", row_count: 1, data: { alias: "count", value: 24 }, warnings: [], route: "both",
    answer_basis: "written",
    passages: [
      { document_id: "d1", version: 1, citation: "policy.docx · Late payment fees", text: "Late payments incur a fee of 2% per month." },
      { document_id: "d2", version: 1, citation: "terms.pdf · page 1", text: "Payment terms: net 30." },
    ],
  };

  it("turns passage numbers into buttons that highlight the passage", async () => {
    const api = fakeApi();
    api.ask.mockResolvedValue(written);
    await signIn(api);
    await userEvent.type(screen.getByLabelText("Ask about your business data"), "Open invoices and late fee?");
    await userEvent.click(screen.getByRole("button", { name: "Send question" }));
    const cite = await screen.findByRole("button", { name: "Show passage 1" });
    expect(screen.getAllByText("database")[0]).toHaveClass("cite");
    // A number with no passage stays plain text, never a link.
    expect(screen.queryByRole("button", { name: "Show passage 9" })).toBeNull();
    expect(screen.getByText(/Unknown\. \[9\]/)).toBeInTheDocument();
    await userEvent.click(cite);
    expect(screen.getAllByLabelText("Passage 1")[0]).toHaveClass("highlighted");
    expect(screen.getAllByLabelText("Passage 2")[0]).not.toHaveClass("highlighted");
  });

  it("keeps quoted answers unchanged and renders written text as plain text", async () => {
    const api = fakeApi();
    api.ask.mockResolvedValue({ ...written, answer: "<img src=x onerror=alert(1)> [1]" });
    await signIn(api);
    await userEvent.type(screen.getByLabelText("Ask about your business data"), "Q");
    await userEvent.click(screen.getByRole("button", { name: "Send question" }));
    expect(await screen.findByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});
