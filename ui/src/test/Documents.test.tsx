// Documents panel with a fake API: upload, status, refusals, retry, delete,
// and the workspace rules (never show one company's documents in another).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, type Mock } from "vitest";

import { App } from "../App";
import type { ApiClient, Caller } from "../api/client";
import { ApiError } from "../api/errors";
import type { DocumentInfo, DocumentStatus, MeResponse } from "../api/types";

const ONE: MeResponse = { subject_id: "user:alice", kind: "user", roles: [], tenants: ["tenant-acme"] };
const TWO: MeResponse = { ...ONE, tenants: ["tenant-acme", "tenant-nova"] };

function doc(id: string, filename: string, status: DocumentStatus, extra: Partial<DocumentInfo["latest"]> = {},
             top: Partial<DocumentInfo> = {}): DocumentInfo {
  return {
    document_id: id, filename, searchable: status === "ready", active_version: status === "ready" ? 1 : null,
    duplicate: false,
    latest: {
      version: 1, status, message: null, progress_percent: status === "ready" ? 100 : 10, file_type: "pdf",
      size_bytes: 2048, passage_count: status === "ready" ? 3 : 0, notes: [], can_retry: false,
      uploaded_at: "2026-10-16T09:00:00+00:00", updated_at: "2026-10-16T09:00:01+00:00", ...extra,
    },
    ...top,
  };
}

type Fake = { [K in keyof ApiClient]: Mock<ApiClient[K]> };

function fakeApi(me: MeResponse = ONE, documents: DocumentInfo[] = []): Fake {
  return {
    me: vi.fn<ApiClient["me"]>(async () => me),
    ask: vi.fn<ApiClient["ask"]>(),
    clarify: vi.fn<ApiClient["clarify"]>(),
    listDocuments: vi.fn<ApiClient["listDocuments"]>(async () => ({ documents })),
    uploadDocument: vi.fn<ApiClient["uploadDocument"]>(async (_c: Caller, file: File) => doc("doc_new", file.name, "queued")),
    retryDocument: vi.fn<ApiClient["retryDocument"]>(async () => doc("doc_1", "a.pdf", "queued")),
    listConversations: vi.fn<ApiClient["listConversations"]>(async () => ({ conversations: [] })),
    getConversation: vi.fn<ApiClient["getConversation"]>(),
    deleteConversation: vi.fn<ApiClient["deleteConversation"]>(async () => undefined),
    deleteDocument: vi.fn<ApiClient["deleteDocument"]>(async () => undefined),
    searchDocuments: vi.fn<ApiClient["searchDocuments"]>(async () => ({
      status: "complete", message: null, terms: ["late", "payment", "fee"],
      results: [{ document_id: "doc_1", version: 1, citation: "terms.pdf · page 3", text: "Late payments incur a fee of 2% per month." }],
    })),
  };
}

const user = userEvent.setup({ applyAccept: false });

async function signIn(api: Fake) {
  render(<App api={api} />);
  await user.type(screen.getByLabelText("Access token"), "dev-token");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

const pdf = (name = "terms.pdf", bytes = 10) => new File([new Uint8Array(bytes)], name, { type: "application/pdf" });

function panel() {
  return within(screen.getByRole("region", { name: "Documents" }));
}

describe("Documents panel", () => {
  it("lists the workspace's documents with status, passages and what was skipped", async () => {
    const api = fakeApi(ONE, [
      doc("doc_1", "claims.xlsx", "ready", { file_type: "xlsx", notes: ["1 hidden sheet(s) were skipped."] }),
      doc("doc_2", "scan.pdf", "failed", { message: "This PDF has no readable text (it may be a scanned image)." }),
    ]);
    await signIn(api);
    expect(await panel().findByText("claims.xlsx")).toBeInTheDocument();
    expect(panel().getByText("Ready")).toBeInTheDocument();
    expect(panel().getByText(/3 passages/)).toBeInTheDocument();
    expect(panel().getByText(/1 hidden sheet\(s\) were skipped/)).toBeInTheDocument();
    expect(panel().getByText("Failed")).toBeInTheDocument();
    expect(panel().getByText(/no readable text/)).toBeInTheDocument();
    expect(api.listDocuments).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" });
  });

  it("uploads a file and follows it until it is ready", async () => {
    const api = fakeApi();
    api.listDocuments
      .mockResolvedValueOnce({ documents: [] })
      .mockResolvedValueOnce({ documents: [doc("doc_new", "terms.pdf", "processing")] })
      .mockResolvedValue({ documents: [doc("doc_new", "terms.pdf", "ready")] });
    await signIn(api);
    await panel().findByText("No documents in this workspace yet.");

    await user.upload(screen.getByLabelText("Choose a document to upload"), pdf());
    expect(api.uploadDocument).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" }, expect.any(File));
    expect(await panel().findByText("Reading")).toBeInTheDocument();
    expect(await panel().findByText("Ready", {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it.each([
    ["notes.txt", 10, "Only Word (.docx), PDF (.pdf) and Excel (.xlsx) files are supported."],
    ["empty.pdf", 0, "The file is empty."],
  ])("refuses %s before uploading", async (name, bytes, message) => {
    const api = fakeApi();
    await signIn(api);
    await panel().findByText("No documents in this workspace yet.");
    await user.upload(screen.getByLabelText("Choose a document to upload"), pdf(name, bytes));
    expect(await panel().findByRole("alert")).toHaveTextContent(message);
    expect(api.uploadDocument).not.toHaveBeenCalled();
  });

  it("refuses files over 25 MB before uploading", async () => {
    const api = fakeApi();
    await signIn(api);
    await panel().findByText("No documents in this workspace yet.");
    const big = pdf();
    Object.defineProperty(big, "size", { value: 25 * 1024 * 1024 + 1 });
    await user.upload(screen.getByLabelText("Choose a document to upload"), big);
    expect(await panel().findByRole("alert")).toHaveTextContent("larger than 25 MB");
    expect(api.uploadDocument).not.toHaveBeenCalled();
  });

  it("shows the server's reason and reference when it refuses a file", async () => {
    const api = fakeApi();
    api.uploadDocument.mockRejectedValue(new ApiError("bad_request",
      "Files with macros are not accepted. Save the file as .docx or .xlsx without macros.", "req-7"));
    await signIn(api);
    await user.upload(screen.getByLabelText("Choose a document to upload"), pdf("macro.docx"));
    const alert = await panel().findByRole("alert");
    expect(alert).toHaveTextContent("Files with macros are not accepted");
    expect(alert).toHaveTextContent("reference req-7");
  });

  it("says when the same file was already uploaded", async () => {
    const api = fakeApi(ONE, [doc("doc_1", "terms.pdf", "ready")]);
    api.uploadDocument.mockResolvedValue(doc("doc_1", "terms.pdf", "ready", {}, { duplicate: true }));
    await signIn(api);
    await user.upload(screen.getByLabelText("Choose a document to upload"), pdf("copy.pdf"));
    expect(await panel().findByRole("status")).toHaveTextContent("“copy.pdf” is already uploaded as “terms.pdf”");
  });

  it("offers Try again only when the server allows it", async () => {
    const api = fakeApi(ONE, [
      doc("doc_1", "glitch.pdf", "failed", { message: "Processing failed for a technical reason.", can_retry: true }),
      doc("doc_2", "scan.pdf", "failed", { message: "This PDF has no readable text." }),
    ]);
    await signIn(api);
    const buttons = await panel().findAllByRole("button", { name: "Try again" });
    expect(buttons).toHaveLength(1);
    await user.click(buttons[0]);
    expect(api.retryDocument).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" }, "doc_1");
  });

  it("deletes only after confirmation", async () => {
    const api = fakeApi(ONE, [doc("doc_1", "terms.pdf", "ready")]);
    await signIn(api);
    await user.click(await panel().findByRole("button", { name: "Delete" }));
    expect(panel().getByText("Delete “terms.pdf”?")).toBeInTheDocument();
    await user.click(panel().getByRole("button", { name: "Cancel" }));
    expect(api.deleteDocument).not.toHaveBeenCalled();

    api.listDocuments.mockResolvedValue({ documents: [] });
    await user.click(panel().getByRole("button", { name: "Delete" }));
    await user.click(panel().getByRole("button", { name: "Delete" }));
    expect(api.deleteDocument).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" }, "doc_1");
    expect(await panel().findByText("No documents in this workspace yet.")).toBeInTheDocument();
  });

  it("shows file names and notes as plain text", async () => {
    const hostile = "<img src=x onerror=alert(1)>.pdf";
    const api = fakeApi(ONE, [doc("doc_1", hostile, "ready", { notes: ["<b>bold?</b>"] })]);
    await signIn(api);
    expect(await panel().findByText(hostile)).toBeInTheDocument();
    expect(panel().getByText(/<b>bold\?<\/b>/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("needs a workspace before uploading", async () => {
    await signIn(fakeApi(TWO));
    expect(await panel().findByText("Choose a workspace to see its documents.")).toBeInTheDocument();
    expect(panel().getByRole("button", { name: "Upload document" })).toBeDisabled();
  });

  it("never shows one workspace's documents in another, even when a reply is late", async () => {
    const api = fakeApi(TWO);
    let releaseAcme!: () => void;
    api.listDocuments.mockImplementation(async (caller: Caller) => {
      if (caller.tenant === "tenant-acme") {
        await new Promise<void>((resolve) => (releaseAcme = resolve));
        return { documents: [doc("doc_a", "acme-secret.pdf", "ready")] };
      }
      return { documents: [doc("doc_n", "nova-terms.pdf", "ready")] };
    });
    await signIn(api);
    await user.selectOptions(await screen.findByLabelText("Workspace"), "tenant-acme");
    await waitFor(() => expect(api.listDocuments).toHaveBeenCalledTimes(1));
    await user.selectOptions(screen.getByLabelText("Workspace"), "tenant-nova");
    expect(await panel().findByText("nova-terms.pdf")).toBeInTheDocument();
    releaseAcme();
    await new Promise((r) => setTimeout(r, 50));
    expect(panel().queryByText("acme-secret.pdf")).not.toBeInTheDocument();
  });

  it("signs out when the session has ended", async () => {
    const api = fakeApi();
    api.listDocuments.mockRejectedValue(new ApiError("unauthenticated", "Authentication is required."));
    await signIn(api);
    expect(await screen.findByText(/Your session has ended/)).toBeInTheDocument();
  });

  it("searches and quotes passages with their citation and the words highlighted", async () => {
    const api = fakeApi(ONE, [doc("doc_1", "terms.pdf", "ready")]);
    await signIn(api);
    await user.type(await panel().findByLabelText("Search documents"), "late payment fee");
    await user.click(panel().getByRole("button", { name: "Search" }));
    expect(api.searchDocuments).toHaveBeenCalledWith({ token: "dev-token", tenant: "tenant-acme" }, "late payment fee");
    const results = within(await screen.findByRole("region", { name: "Search results" }));
    expect(results.getByText("terms.pdf · page 3")).toBeInTheDocument();
    const marks = [...document.querySelectorAll(".passage mark")].map((m) => m.textContent);
    expect(marks).toEqual(["Late", "payments", "fee"]);
    expect(document.querySelector(".passage blockquote")?.textContent).toBe("Late payments incur a fee of 2% per month.");
  });

  it("says plainly when nothing matches", async () => {
    const api = fakeApi(ONE, [doc("doc_1", "terms.pdf", "ready")]);
    api.searchDocuments.mockResolvedValue({ status: "empty", message: "No passage in this workspace's documents matches these words.", terms: ["warranty"], results: [] });
    await signIn(api);
    await user.type(await panel().findByLabelText("Search documents"), "warranty");
    await user.click(panel().getByRole("button", { name: "Search" }));
    expect(await panel().findByText(/No passage in this workspace's documents matches/)).toBeInTheDocument();
  });

  it("can only search once a document is ready", async () => {
    await signIn(fakeApi(ONE, [doc("doc_1", "terms.pdf", "processing")]));
    expect(await panel().findByLabelText("Search documents")).toBeDisabled();
  });

  it("shows passage text as plain text, even when it contains markup", async () => {
    const api = fakeApi(ONE, [doc("doc_1", "terms.pdf", "ready")]);
    api.searchDocuments.mockResolvedValue({ status: "complete", message: null, terms: ["fee"],
      results: [{ document_id: "doc_1", version: 1, citation: "x.pdf · page 1", text: "fee <img src=x onerror=alert(1)>" }] });
    await signIn(api);
    await user.type(await panel().findByLabelText("Search documents"), "fee");
    await user.click(panel().getByRole("button", { name: "Search" }));
    await screen.findByRole("region", { name: "Search results" });
    expect(document.querySelector(".passage blockquote")?.textContent).toBe("fee <img src=x onerror=alert(1)>");
    expect(document.querySelector(".passage img")).toBeNull();
  });

  it("clears search results when the workspace changes", async () => {
    const api = fakeApi(TWO, [doc("doc_1", "terms.pdf", "ready")]);
    await signIn(api);
    await user.selectOptions(await screen.findByLabelText("Workspace"), "tenant-acme");
    await user.type(await panel().findByLabelText("Search documents"), "fee");
    await user.click(panel().getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("region", { name: "Search results" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Workspace"), "tenant-nova");
    await waitFor(() => expect(screen.queryByRole("region", { name: "Search results" })).not.toBeInTheDocument());
  });
});
