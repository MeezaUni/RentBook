import { Calculator, HandCoins, Paperclip, Plus, Undo2 } from "lucide-react";
import { type FormEvent, Fragment, useState } from "react";
import { ACCOUNT_LABELS, CountForm, MovementForm } from "../components/CashForms";
import { Documents, uploadDocs, ACCEPT } from "../components/Documents";
import { ReasonDialog } from "../components/ReasonDialog";
import {
  Alert, Button, Card, cx, Empty, ErrorNote, Field, inputCls, Loading, Modal, Money, MonthPicker, PageHeader, Stat, Table,
} from "../components/ui";
import { api, ApiError, qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatDate, formatINR, formatPeriod, paiseToInput, parseRupees, todayISO } from "../lib/money";
import type { CashMovement, Discrepancy, Expense } from "../lib/types";

interface Summary {
  period: string; target_paise: number; opening_paise: number; top_up_required_paise: number; handed_over_paise: number;
  still_to_hand_over_paise: number; other_adjustments_paise: number; available_paise: number; caretaker_spent_paise: number;
  family_paid_paise: number; total_expenses_paise: number; over_target_paise: number; closing_paise: number;
  by_category: { category: string; amount_paise: number }[]; expenses: Expense[]; movements: CashMovement[];
  discrepancies: Discrepancy[]; notes: string | null;
  previous_period: string; previous_still_to_hand_over_paise: number;
}

type HistoryRow = Pick<Summary, "period" | "target_paise" | "opening_paise" | "top_up_required_paise" | "handed_over_paise" |
  "caretaker_spent_paise" | "family_paid_paise" | "total_expenses_paise" | "closing_paise">;

export function MaintenancePage() {
  const [period, setPeriod] = usePeriod();
  const { data, error, loading, reload } = useApi<Summary>(`/api/maintenance/summary${qs({ period })}`);
  const history = useApi<HistoryRow[]>("/api/maintenance/history?months=12");
  const [dialog, setDialog] = useState<null | "topup" | "topupPrevious" | "expense" | "movement" | "count">(null);
  const [reverse, setReverse] = useState<Expense | null>(null);
  const [openDocs, setOpenDocs] = useState<number | null>(null);
  const refresh = () => { reload(); history.reload(); };

  return (
    <>
      <PageHeader title="Maintenance" subtitle={`Caretaker's maintenance cash — ${formatPeriod(period)}`}
        actions={<MonthPicker value={period} onChange={setPeriod} />} />
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      {data && (
        <div className="flex flex-col gap-4">
          {data.previous_still_to_hand_over_paise > 0 && (
            <Alert tone="info" title={`${formatPeriod(data.previous_period)}'s top-up: give him ${formatINR(data.previous_still_to_hand_over_paise)}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span>Last month's maintenance money, if you hand it over this month.</span>
                <Button size="sm" className="no-print" onClick={() => setDialog("topupPrevious")}>
                  <HandCoins className="size-3.5" /> Record for {formatPeriod(data.previous_period)}
                </Button>
              </div>
            </Alert>
          )}
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm text-muted">He held <b className="money text-ink">{formatINR(data.opening_paise)}</b> at the start of {formatPeriod(period)}.
                  Target {formatINR(data.target_paise)}.</div>
                <div className="mt-1 text-xl font-semibold">
                  {data.still_to_hand_over_paise > 0
                    ? <>Give him <span className="money text-sky-800">{formatINR(data.still_to_hand_over_paise)}</span> for {formatPeriod(period)}</>
                    : data.top_up_required_paise === 0
                      ? <>No top-up needed this month</>
                      : <>Top-up of {formatINR(data.top_up_required_paise)} handed over ✓</>}
                </div>
                <div className="text-xs text-muted">Top-up required = {formatINR(data.target_paise)} − {formatINR(data.opening_paise)} = {formatINR(data.top_up_required_paise)}
                  {data.handed_over_paise > 0 && ` · handed ${formatINR(data.handed_over_paise)} so far`}</div>
              </div>
              <div className="flex flex-wrap gap-2 no-print">
                <Button variant="primary" onClick={() => setDialog("topup")}><HandCoins className="size-4" /> Record cash handed over for {formatPeriod(period, true)}</Button>
                <Button onClick={() => setDialog("expense")}><Plus className="size-4" /> Record expense</Button>
                <Button onClick={() => setDialog("count")}><Calculator className="size-4" /> Count his cash</Button>
                <Button variant="ghost" onClick={() => setDialog("movement")}>Other movement…</Button>
              </div>
            </div>
          </Card>

          {data.discrepancies.map((d, i) => <Alert key={i} tone="warn" title={`Needs reconciling: ${formatINR(d.amount_paise)}`}>{d.message}</Alert>)}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Available this month" value={formatINR(data.available_paise)} hint={`Start ${formatINR(data.opening_paise)} + handed ${formatINR(data.handed_over_paise)}${data.other_adjustments_paise ? ` ± ${formatINR(data.other_adjustments_paise)}` : ""}`} />
            <Stat label="Spent from his cash" value={formatINR(data.caretaker_spent_paise)} />
            <Stat label="Paid directly by family" tone={data.family_paid_paise ? "warn" : "neutral"} value={formatINR(data.family_paid_paise)}
              hint={data.over_target_paise ? `Expenses ${formatINR(data.over_target_paise)} over the normal budget` : "Within normal budget"} />
            <Stat label="He holds at month end" tone={data.closing_paise < 0 ? "bad" : "good"} value={formatINR(data.closing_paise)} hint="Carried forward to next month" />
          </div>

          <Card title={`Expenses — ${formatINR(data.total_expenses_paise)}`}>
            {data.expenses.length === 0 ? <Empty>No maintenance expenses recorded this month.</Empty> : (
              <Table>
                <thead><tr><th>Date</th><th>Description</th><th>Category</th><th className="text-right">Total</th><th className="text-right">His cash</th><th className="text-right">Family</th><th /></tr></thead>
                <tbody>{data.expenses.map((e) => (
                  <Fragment key={e.id}>
                    <tr className={cx(e.status === "reversed" && "text-muted line-through")}>
                      <td className="whitespace-nowrap">{formatDate(e.expense_date)}</td>
                      <td>{e.description}{e.vendor && <span className="text-xs text-muted"> · {e.vendor}</span>}
                        {e.status === "reversed" && <div className="text-xs">Reversed: {e.reversed_reason}</div>}</td>
                      <td>{e.category}</td>
                      <td className="text-right"><Money paise={e.total_paise} /></td>
                      <td className="text-right"><Money paise={e.caretaker_paid_paise} /></td>
                      <td className="text-right"><Money paise={e.family_paid_paise} />{e.family_payment_method && <div className="text-xs text-muted">{e.family_payment_method}</div>}</td>
                      <td className="whitespace-nowrap text-right">
                        <button className="rounded p-1 hover:bg-stone-100" aria-label="Receipts" onClick={() => setOpenDocs(openDocs === e.id ? null : e.id)}><Paperclip className="size-4" /></button>
                        {e.status === "active" && <button className="rounded p-1 text-red-700 hover:bg-red-50" aria-label="Reverse expense" onClick={() => setReverse(e)}><Undo2 className="size-4" /></button>}
                      </td>
                    </tr>
                    {openDocs === e.id && (
                      <tr><td colSpan={7}><Documents entityType="maintenance_expense" entityId={e.id} category="maintenance_receipt" compact /></td></tr>
                    )}
                  </Fragment>
                ))}</tbody>
              </Table>
            )}
            {data.by_category.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted">
                {data.by_category.map((c) => <span key={c.category}>{c.category}: <span className="money text-ink">{formatINR(c.amount_paise)}</span></span>)}
              </div>
            )}
          </Card>

          <Card title="Caretaker cash movements">
            {data.movements.length === 0 ? <Empty>No cash handed over for {formatPeriod(period)} yet.</Empty> : (
              <ul className="divide-y divide-line text-sm">
                {data.movements.map((m) => (
                  <li key={m.id} className="flex justify-between py-1.5">
                    <span>{m.kind === "caretaker_handover" ? "Given " : ""}{formatDate(m.movement_date)} · {m.kind_label} <span className="text-xs text-muted">({ACCOUNT_LABELS[m.from_account]} → {ACCOUNT_LABELS[m.to_account]})</span>
                      {m.description && <span className="block text-xs text-muted">{m.description}</span>}</span>
                    <Money paise={m.amount_paise} />
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-xs text-muted">To reverse a handover, use the Cashbook. The caretaker's salary is separate and never affects this cash.</p>
          </Card>

          <MonthNote period={period} initial={data.notes} />

          <Card title="Last 12 months">
            <Table>
              <thead><tr><th>Month</th><th className="text-right">Start</th><th className="text-right">Top-up needed</th><th className="text-right">Handed</th><th className="text-right">His spending</th><th className="text-right">Family paid</th><th className="text-right">End</th></tr></thead>
              <tbody>{history.data?.map((h) => (
                <tr key={h.period} className={cx(h.period === period && "bg-teal-50/50")}>
                  <td><button className="hover:underline" onClick={() => setPeriod(h.period)}>{formatPeriod(h.period, true)}</button></td>
                  <td className="text-right"><Money paise={h.opening_paise} /></td>
                  <td className="text-right"><Money paise={h.top_up_required_paise} /></td>
                  <td className="text-right"><Money paise={h.handed_over_paise} /></td>
                  <td className="text-right"><Money paise={h.caretaker_spent_paise} /></td>
                  <td className="text-right"><Money paise={h.family_paid_paise} /></td>
                  <td className={cx("text-right", h.closing_paise < 0 && "text-red-700")}><Money paise={h.closing_paise} /></td>
                </tr>
              ))}</tbody>
            </Table>
          </Card>
        </div>
      )}
      {dialog === "topup" && data && (
        <MovementForm initialKind="caretaker_handover" forPeriod={period} suggestedAmount={data.still_to_hand_over_paise || undefined}
          onClose={() => setDialog(null)} onDone={refresh} />
      )}
      {dialog === "topupPrevious" && data && (
        <MovementForm initialKind="caretaker_handover" forPeriod={data.previous_period}
          suggestedAmount={data.previous_still_to_hand_over_paise || undefined} onClose={() => setDialog(null)} onDone={refresh} />
      )}
      {dialog === "movement" && <MovementForm initialKind="caretaker_return" onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === "count" && <CountForm account="caretaker_cash" onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === "expense" && <ExpenseForm onClose={() => setDialog(null)} onDone={refresh} />}
      {reverse && (
        <ReasonDialog title="Reverse expense?" danger confirmLabel="Reverse" onClose={() => setReverse(null)}
          description={`${reverse.description} — ${formatINR(reverse.total_paise)}. It stays in the history as reversed.`}
          onSubmit={async (reason) => { await api.post(`/api/maintenance/expenses/${reverse.id}/reverse`, { reason }); refresh(); }} />
      )}
    </>
  );
}

function MonthNote({ period, initial }: { period: string; initial: string | null }) {
  const [text, setText] = useState(initial ?? "");
  const [saved, setSaved] = useState(false);
  return (
    <Card title="Notes for this month">
      <textarea className={inputCls} rows={2} value={text} onChange={(e) => { setText(e.target.value); setSaved(false); }} aria-label="Month notes" key={period} />
      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" onClick={async () => { await api.put("/api/maintenance/notes", { period, notes: text }); setSaved(true); }}>Save note</Button>
        {saved && <span className="text-xs text-emerald-700">Saved</span>}
      </div>
    </Card>
  );
}

function ExpenseForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const cats = useApi<{ id: number; name: string; is_active: boolean }[]>("/api/maintenance/categories");
  const [date, setDate] = useState(todayISO());
  const [description, setDescription] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [vendor, setVendor] = useState("");
  const [who, setWho] = useState<"caretaker" | "family" | "split">("caretaker");
  const [total, setTotal] = useState("");
  const [familyPart, setFamilyPart] = useState("");
  const [method, setMethod] = useState("cash");
  const [files, setFiles] = useState<File[]>([]);
  const [shortfall, setShortfall] = useState<{ available_paise: number; shortfall_paise: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const avail = useApi<{ available_paise: number }>(`/api/maintenance/available${qs({ on: date })}`);
  const totalP = parseRupees(total);
  const famP = who === "family" ? totalP : who === "split" ? parseRupees(familyPart) : 0;
  const careP = totalP !== null && famP !== null ? totalP - famP : null;

  async function save(e: FormEvent | null, allowShortfall = false) {
    e?.preventDefault();
    if (!totalP || careP === null || careP < 0) return setError("Check the amounts");
    if (!categoryId) return setError("Choose a category");
    setBusy(true);
    setError(null);
    try {
      const exp = await api.post<Expense>("/api/maintenance/expenses", {
        expense_date: date, description, category_id: Number(categoryId), vendor: vendor || null,
        caretaker_paid_paise: careP, family_paid_paise: famP ?? 0,
        family_payment_method: (famP ?? 0) > 0 ? method : null, allow_shortfall: allowShortfall,
      });
      if (files.length) await uploadDocs("maintenance_expense", exp.id, files, "maintenance_receipt");
      onDone();
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.code === "exceeds_caretaker_cash") setShortfall(err.details as { available_paise: number; shortfall_paise: number });
      else setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Record maintenance expense" onClose={onClose} wide footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" busy={busy} type="submit" form="expense-form">Save expense</Button>
    </>}>
      <form id="expense-form" onSubmit={(e) => save(e)} className="grid gap-3 sm:grid-cols-2">
        <Field label="Date">{(id) => <input id={id} type="date" className={inputCls} value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} required />}</Field>
        <Field label="Category">
          {(id) => (
            <select id={id} className={inputCls} value={categoryId} onChange={(e) => setCategoryId(e.target.value)} required>
              <option value="">Choose…</option>
              {cats.data?.filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="What was it for" className="sm:col-span-2">{(id) => <input id={id} className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} required />}</Field>
        <Field label="Total amount (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} required />}</Field>
        <Field label="Paid to (optional)">{(id) => <input id={id} className={inputCls} value={vendor} onChange={(e) => setVendor(e.target.value)} />}</Field>
        <fieldset className="sm:col-span-2">
          <legend className="mb-1 text-sm font-medium">Who paid?</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {([["caretaker", "From caretaker's maintenance cash"], ["family", "Paid directly by family"], ["split", "Partly each"]] as const).map(([v, l]) => (
              <label key={v} className={cx("flex cursor-pointer items-center gap-2 rounded-lg border p-2.5 text-sm", who === v ? "border-teal-600 bg-teal-50" : "border-line")}>
                <input type="radio" name="who" checked={who === v} onChange={() => setWho(v)} />{l}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted">Caretaker has <span className="money">{formatINR(avail.data?.available_paise)}</span> available on this date.</p>
        </fieldset>
        {who === "split" && (
          <Field label="Amount paid by family (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={familyPart} onChange={(e) => setFamilyPart(e.target.value)} />}</Field>
        )}
        {who !== "caretaker" && (
          <Field label="Family paid by">
            {(id) => (
              <select id={id} className={inputCls} value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="cash">Cash (reduces family cash)</option><option value="bank">Bank transfer</option><option value="other">Other</option>
              </select>
            )}
          </Field>
        )}
        {totalP !== null && careP !== null && (
          <p className="sm:col-span-2 text-sm text-muted">From caretaker's cash: <b className="money">{formatINR(careP)}</b> · paid by family: <b className="money">{formatINR(famP ?? 0)}</b></p>
        )}
        <Field label="Receipt / invoice (optional)" className="sm:col-span-2">
          {(id) => <input id={id} type="file" multiple accept={ACCEPT} className="text-sm" onChange={(e) => setFiles([...(e.target.files ?? [])])} />}
        </Field>
        {shortfall && (
          <div className="sm:col-span-2">
            <Alert tone="warn" title={`The caretaker only has ${formatINR(shortfall.available_paise)}`}>
              <p>This is {formatINR(shortfall.shortfall_paise)} more than his maintenance cash. The budget is not increased automatically.</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button size="sm" variant="primary" onClick={() => {
                  setWho("split");
                  setFamilyPart(paiseToInput(shortfall.shortfall_paise));
                  setShortfall(null);
                }}>Family paid the extra {formatINR(shortfall.shortfall_paise)}</Button>
                <Button size="sm" onClick={() => save(null, true)}>He paid it anyway (record a discrepancy)</Button>
              </div>
            </Alert>
          </div>
        )}
        {error && <div className="sm:col-span-2"><Alert tone="error">{error}</Alert></div>}
      </form>
    </Modal>
  );
}
