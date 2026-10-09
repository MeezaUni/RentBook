import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Tenancy } from "../lib/types";
import { PaymentForm } from "./PaymentForm";
import { StatusBadge } from "./ui";

const tenancy: Tenancy = {
  id: 7, tenant_id: 3, tenant_name: "Test Tenant", flat_id: 1, flat_label: "P-G1", building_id: 1,
  building_name: "Pink Building", start_date: "2025-01-01", end_date: null, rent_due_day: 5,
  billing_start_period: "2025-01", billing_policy: null, notes: null, status: "active",
  current_rent_paise: 1_200_000, rent_terms: [],
};

const due = {
  id: 11, tenancy_id: 7, period: "2026-10", kind: "rent", amount_paise: 1_200_000, available_paise: 900_000,
  confirmed_paise: 300_000, pending_paise: 0, outstanding_paise: 900_000, status: "partially_paid",
};

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("PaymentForm", () => {
  // Pin the clock so "today" (the date input's max) is deterministic.
  beforeAll(() => vi.useFakeTimers({ now: new Date("2026-10-20T10:00:00"), toFake: ["Date"] }));
  afterAll(() => vi.useRealTimers());

  let calls: { url: string; body: Record<string, unknown> | null }[];

  beforeEach(() => {
    calls = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, body });
      if (url.startsWith("/api/tenancies/7/due")) return json({ due });
      if (url === "/api/payments" && body && !body.excess_handling && body.amount_paise > 900_000) {
        return json({ error: { code: "excess_payment", message: "extra", details: { excess_paise: 300_000, allocated_paise: 900_000 } } }, 409);
      }
      if (url === "/api/payments") return json({ id: 99, amount_paise: body!.amount_paise, status: body!.status });
      return json({});
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("fills the remaining amount in one tap and saves a confirmed cash payment", async () => {
    const onSaved = vi.fn();
    render(<PaymentForm tenancy={tenancy} defaultPeriod="2026-10" onClose={() => {}} onSaved={onSaved} />);
    const fill = await screen.findByRole("button", { name: /Full ₹9,000/ });
    await userEvent.click(fill);
    expect(screen.getByLabelText("Amount received (₹)")).toHaveValue("9000");
    await userEvent.click(screen.getByRole("button", { name: "Save payment" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const post = calls.find((c) => c.url === "/api/payments")!;
    expect(post.body).toMatchObject({ tenancy_id: 7, amount_paise: 900_000, method: "cash", status: "confirmed", rent_period: "2026-10" });
  });

  it("keeps the rent month separate from the date money was received", async () => {
    render(<PaymentForm tenancy={tenancy} defaultPeriod="2026-10" onClose={() => {}} onSaved={() => {}} />);
    await screen.findByRole("button", { name: /Full/ });
    await userEvent.selectOptions(screen.getByLabelText("Rent month"), "2026-09");
    await screen.findByRole("button", { name: /Full/ });
    await userEvent.clear(screen.getByLabelText("Date money was received"));
    await userEvent.type(screen.getByLabelText("Date money was received"), "2026-10-08");
    await userEvent.type(screen.getByLabelText("Amount received (₹)"), "9000");
    await userEvent.click(screen.getByRole("button", { name: "Save payment" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/payments")).toBe(true));
    const post = calls.find((c) => c.url === "/api/payments")!;
    expect(post.body).toMatchObject({ rent_period: "2026-09", received_date: "2026-10-08" });
  });

  it("asks what to do with an overpayment instead of guessing", async () => {
    const onSaved = vi.fn();
    render(<PaymentForm tenancy={tenancy} defaultPeriod="2026-10" onClose={() => {}} onSaved={onSaved} />);
    await screen.findByRole("button", { name: /Full/ });
    await userEvent.type(screen.getByLabelText("Amount received (₹)"), "12000");
    await userEvent.click(screen.getByRole("button", { name: "Save payment" }));
    expect(await screen.findByText(/₹3,000 more than what is still due/)).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    await userEvent.click(screen.getByLabelText(/Keep as credit/));
    await userEvent.click(screen.getByRole("button", { name: "Save with this choice" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls.filter((c) => c.url === "/api/payments").at(-1)!.body).toMatchObject({ excess_handling: "hold_as_credit" });
  });

  it("can record a payment as pending verification", async () => {
    render(<PaymentForm tenancy={tenancy} defaultPeriod="2026-10" onClose={() => {}} onSaved={() => {}} />);
    await screen.findByRole("button", { name: /Full/ });
    await userEvent.click(screen.getByLabelText("Online transfer"));
    await userEvent.type(screen.getByLabelText(/Transaction reference/), "UTR42");
    await userEvent.click(screen.getByLabelText(/Not yet checked/));
    await userEvent.type(screen.getByLabelText("Amount received (₹)"), "9000");
    await userEvent.click(screen.getByRole("button", { name: "Save payment" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/payments")).toBe(true));
    expect(calls.find((c) => c.url === "/api/payments")!.body).toMatchObject({ method: "online", status: "pending", reference: "UTR42" });
  });
});

describe("StatusBadge", () => {
  it("shows status as text, not colour alone", () => {
    render(<StatusBadge status="partially_paid" overdue />);
    expect(screen.getByText("Partly paid")).toBeInTheDocument();
    expect(screen.getByText("Overdue")).toBeInTheDocument();
  });
});
