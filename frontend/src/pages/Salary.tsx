import { Check, Plus, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Documents } from "../components/Documents";
import { ReasonDialog } from "../components/ReasonDialog";
import { Alert, Button, Card, cx, Empty, ErrorNote, Field, inputCls, Loading, Modal, Money, MonthPicker, PageHeader, StatusBadge, Stat, Table } from "../components/ui";
import { api, qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatDate, formatINR, formatPeriod, paiseToInput, parseRupees, todayISO } from "../lib/money";
import type { SalaryMonth, SalaryPayment } from "../lib/types";

export function SalaryPage() {
  const [period, setPeriod] = usePeriod();
  const { data, error, loading, reload } = useApi<SalaryMonth>(`/api/salary${qs({ period })}`);
  const history = useApi<SalaryMonth[]>("/api/salary/history?months=12");
  const [adding, setAdding] = useState(false);
  const [cancel, setCancel] = useState<SalaryPayment | null>(null);
  const [docs, setDocs] = useState<number | null>(null);
  const refresh = () => { reload(); history.reload(); };
  return (
    <>
      <PageHeader title="Caretaker Salary" subtitle="Paid separately from maintenance cash — never part of the maintenance balance."
        actions={<>
          <Button variant="primary" onClick={() => setAdding(true)}><Plus className="size-4" /> Record salary payment</Button>
          <MonthPicker value={period} onChange={setPeriod} />
        </>} />
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      {data && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label={`Salary for ${formatPeriod(period, true)}`} value={formatINR(data.expected_paise)} />
            <Stat label="Paid (confirmed)" tone="good" value={formatINR(data.confirmed_paise)} hint={data.pending_paise ? `+ ${formatINR(data.pending_paise)} pending` : undefined} />
            <Stat label="Still to pay" tone={data.outstanding_paise ? "warn" : "neutral"} value={formatINR(data.outstanding_paise)}
              hint={data.overpaid_paise ? `Overpaid by ${formatINR(data.overpaid_paise)}` : undefined} />
            <Stat label="Status" value={<StatusBadge status={data.status} />} />
          </div>
          <Card title="Payments">
            {data.payments.length === 0 ? <Empty>No salary payments recorded for {formatPeriod(period)}.</Empty> : (
              <ul className="flex flex-col gap-2">
                {data.payments.map((p) => (
                  <li key={p.id} className={cx("rounded-lg border border-line p-3 text-sm", p.status === "cancelled" && "opacity-60")}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="flex flex-wrap items-center gap-2">
                        <Money paise={p.amount_paise} className="font-semibold" /> <StatusBadge status={p.status} />
                        <span className="text-muted">{p.method} · {formatDate(p.payment_date)}{p.bank_reference && ` · ref ${p.bank_reference}`}</span>
                      </span>
                      <span className="flex gap-1.5">
                        {p.status === "pending" && <Button size="sm" variant="primary" onClick={async () => { await api.post(`/api/salary/${p.id}/confirm`); refresh(); }}><Check className="size-3.5" /> Confirm</Button>}
                        {p.status !== "cancelled" && <Button size="sm" variant="danger" onClick={() => setCancel(p)}><X className="size-3.5" /> Cancel</Button>}
                        <Button size="sm" variant="ghost" onClick={() => setDocs(docs === p.id ? null : p.id)}>Proof</Button>
                      </span>
                    </div>
                    {p.notes && <p className="mt-1 text-muted">{p.notes}</p>}
                    {p.status_reason && <p className="mt-1 text-muted">Cancelled: {p.status_reason}</p>}
                    {docs === p.id && <div className="mt-2"><Documents entityType="salary_payment" entityId={p.id} category="payment_proof" compact /></div>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Last 12 months">
            <Table>
              <thead><tr><th>Month</th><th className="text-right">Salary</th><th className="text-right">Paid</th><th className="text-right">Pending</th><th>Status</th></tr></thead>
              <tbody>{history.data?.map((h) => (
                <tr key={h.period}>
                  <td><button className="hover:underline" onClick={() => setPeriod(h.period)}>{formatPeriod(h.period)}</button></td>
                  <td className="text-right"><Money paise={h.expected_paise} /></td>
                  <td className="text-right"><Money paise={h.confirmed_paise} /></td>
                  <td className="text-right"><Money paise={h.pending_paise} /></td>
                  <td><StatusBadge status={h.status} /></td>
                </tr>
              ))}</tbody>
            </Table>
          </Card>
        </div>
      )}
      {adding && data && <SalaryForm period={period} suggested={data.outstanding_paise} onClose={() => setAdding(false)} onDone={refresh} />}
      {cancel && (
        <ReasonDialog title="Cancel salary payment?" danger confirmLabel="Cancel payment" onClose={() => setCancel(null)}
          onSubmit={async (reason) => { await api.post(`/api/salary/${cancel.id}/cancel`, { reason }); refresh(); }} />
      )}
    </>
  );
}

function SalaryForm({ period, suggested, onClose, onDone }: { period: string; suggested: number; onClose: () => void; onDone: () => void }) {
  const [month, setMonth] = useState(period);
  const [amount, setAmount] = useState(suggested ? paiseToInput(suggested) : "");
  const [date, setDate] = useState(todayISO());
  const [method, setMethod] = useState("bank");
  const [ref, setRef] = useState("");
  const [confirmed, setConfirmed] = useState(true);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const paise = parseRupees(amount);
    if (!paise) return setError("Enter the amount");
    setBusy(true);
    try {
      await api.post("/api/salary", { salary_month: month, amount_paise: paise, payment_date: date, method,
        bank_reference: ref || null, status: confirmed ? "confirmed" : "pending", notes: notes || null });
      onDone();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Record salary payment" onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} type="submit" form="salary-form">Save</Button>
    </>}>
      <form id="salary-form" onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        <Field label="Salary for month">{(id) => <input id={id} type="month" className={inputCls} value={month} onChange={(e) => setMonth(e.target.value)} required />}</Field>
        <Field label="Amount (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />}</Field>
        <Field label="Payment date">{(id) => <input id={id} type="date" className={inputCls} value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} required />}</Field>
        <Field label="Method">
          {(id) => (
            <select id={id} className={inputCls} value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="bank">Bank transfer</option><option value="cash">Cash</option><option value="other">Other</option>
            </select>
          )}
        </Field>
        {method === "bank" && <Field label="Bank reference (optional)" className="sm:col-span-2">{(id) => <input id={id} className={inputCls} value={ref} onChange={(e) => setRef(e.target.value)} />}</Field>}
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> Payment completed (untick to keep it pending)
        </label>
        <Field label="Notes (optional)" className="sm:col-span-2">{(id) => <input id={id} className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
        {method === "cash" && <div className="sm:col-span-2"><Alert tone="info">Salary paid in cash is recorded here only — it does not come out of the caretaker's maintenance cash.</Alert></div>}
        {error && <div className="sm:col-span-2"><Alert tone="error">{error}</Alert></div>}
      </form>
    </Modal>
  );
}
