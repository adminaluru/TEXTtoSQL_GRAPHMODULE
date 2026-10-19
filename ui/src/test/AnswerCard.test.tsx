import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AnswerResponse } from "../api/types";
import { AnswerCard } from "../components/AnswerCard";

function show(answer: AnswerResponse, extra: Partial<Parameters<typeof AnswerCard>[0]> = {}) {
  const onChoose = vi.fn();
  render(
    <AnswerCard answer={answer} busy={false} choicesLocked={false} selected={false}
      onSelect={() => undefined} onChoose={onChoose} {...extra} />,
  );
  return { onChoose };
}

describe("AnswerCard", () => {
  it("shows a single amount with its currency", () => {
    show({ answer: "The total payment amount is 4000.00 USD.", result_type: "scalar", row_count: 1,
      data: { alias: "sum_payment_amount", value: "4000.00", currency: "USD", missing_count: 0 }, warnings: [] });
    expect(screen.getByText("4,000.00 USD")).toBeInTheDocument();
  });

  it("lists currencies separately and never adds them up", () => {
    show({ answer: "The total invoice amount is reported per currency.", result_type: "tabular", row_count: 2,
      data: [{ currency: "INR", sum_amount: "70800.00", sum_amount_missing: 0 },
             { currency: "USD", sum_amount: "327000.00", sum_amount_missing: 0 }], warnings: [] });
    expect(screen.getByText("70,800.00")).toBeInTheDocument();
    expect(screen.getByText("327,000.00")).toBeInTheDocument();
    expect(screen.queryByText(/397,800/)).not.toBeInTheDocument();
    expect(screen.queryByText("Sum amount missing")).not.toBeInTheDocument();
  });

  it("shows text from data as text, never as HTML", () => {
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const { container } = render(
      <AnswerCard answer={{ answer: hostile, result_type: "empty", row_count: 0, data: null, warnings: [] }}
        busy={false} choicesLocked={false} selected={false} onSelect={() => undefined} onChoose={() => undefined} />,
    );
    expect(screen.getByText(hostile)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("offers clarification choices and reports the chosen one", async () => {
    const { onChoose } = show({
      answer: "This name matches more than one record. Which one do you mean?", result_type: "clarification",
      row_count: 0, warnings: [],
      data: { clarification_id: "c1", question: "Which one?", reason: "ambiguous_entity", allows_text: false,
        expires_at: "2026-10-15T10:00:00Z",
        options: [{ option_id: "o1", label: "Blue Prism Technologies LE-1001 — Acme Primary" },
                  { option_id: "o2", label: "Blue Prism Technologies LE-1002 — Acme Secondary" }] },
    });
    await userEvent.click(screen.getByRole("button", { name: /LE-1002/ }));
    expect(onChoose).toHaveBeenCalledWith("c1", "o2", "Blue Prism Technologies LE-1002 — Acme Secondary");
  });

  it("locks choices once one has been used", () => {
    show({ answer: "Which one?", result_type: "clarification", row_count: 0, warnings: [],
      data: { clarification_id: "c1", question: "Which one?", reason: "r", allows_text: false,
        expires_at: "2026-10-15T10:00:00Z", options: [{ option_id: "o1", label: "A" }] } },
      { choicesLocked: true });
    expect(screen.getByRole("button", { name: "A" })).toBeDisabled();
  });

  it("always shows the server's notices", () => {
    show({ answer: "The total payment amount is 3000.00 USD.", result_type: "scalar", row_count: 1,
      data: { alias: "v", value: "3000.00", currency: "USD" },
      warnings: ["Payment date: 2026-04-20 (US Central time, end date included)."] });
    const article = screen.getByRole("article");
    expect(within(article).getByText(/US Central time/)).toBeInTheDocument();
  });
});
