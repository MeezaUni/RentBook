import { type FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { addMonths, currentPeriod, formatDate, formatINR, paiseToInput, parseRupees, todayISO } from "../lib/money";
import { Alert, Button, Field, inputCls, Modal } from "./ui";

export interface Receipt {
  payment_id: number;
  received_date: string;
  tenant_name: string;
  flat_label: string;
  building_name: string;
  periods: string[];
  amount_paise: number;
  status: string;
  recorded_by: string | null;
  notes: string | null;
  allocated_paise: number;
  with_family_paise: number;
  links: { cash_movement_id: number; kind: string; kind_label: string; date: string; amount_paise: number }[];
}

export const MOVEMENT_KINDS: Record<string, { label: string; from: string; to: string; fromChoices?: string[]; toChoices?: string[] }> = {
  caretaker_handover: { label: "Give maintenance cash to caretaker", from: "family_cash", to: "caretaker_cash", fromChoices: ["family_cash", "external"] },
  caretaker_return: { label: "Caretaker returned cash", from: "caretaker_cash", to: "family_cash" },
  family_expense: { label: "Other property expense paid in cash", from: "family_cash", to: "external" },
  bank_deposit: { label: "Cash deposited in bank", from: "family_cash", to: "external" },
  cash_in_other: { label: "Other cash received by family", from: "external", to: "family_cash" },
  adjustment: { label: "Reconciliation adjustment", from: "external", to: "family_cash",
    fromChoices: ["external", "family_cash", "caretaker_cash"], toChoices: ["family_cash", "external", "caretaker_cash"] },
};

export const ACCOUNT_LABELS: Record<string, string> = {
  family_cash: "Family cash", caretaker_cash: "Caretaker's maintenance cash", external: "Outside / bank",
};

/** Kinds whose cash counts towards a maintenance month, which can differ from the date it moved. */
const FOR_MONTH_KINDS = new Set(["caretaker_handover", "caretaker_return"]);
/** Months from handing caretaker money over to the month it is for (Settings → "maintenance_given_for"). */
const HANDOVER_OFFSET: Record<string, number> = { next_month: 1, same_month: 0, previous_month: -1 };

/** Record a cash movement, optionally linked to the tenant cash receipts it came from. */
export function MovementForm({ initialKind = "caretaker_handover", initialSource, suggestedAmount, forPeriod, onClose, onDone }: {
  initialKind?: string; initialSource?: Receipt; suggestedAmount?: number;
  /** Maintenance month the cash is for, e.g. September's top-up handed over in October. */
  forPeriod?: string; onClose: () => void; onDone: () => void;
}) {
  const [forMonth, setForMonth] = useState(forPeriod ?? currentPeriod());
  const [kind, setKind] = useState(initialKind);
  const k = MOVEMENT_KINDS[kind];
  const [from, setFrom] = useState(k.from);
  const [to, setTo] = useState(k.to);
  const [date, setDate] = useState(todayISO());
  const [amount, setAmount] = useState(suggestedAmount ? paiseToInput(suggestedAmount) : initialSource ? paiseToInput(initialSource.with_family_paise) : "");
  const [description, setDescription] = useState("");
  const [links, setLinks] = useState<Record<number, string>>(initialSource ? { [initialSource.payment_id]: paiseToInput(initialSource.with_family_paise) } : {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const receipts = useApi<Receipt[]>(from === "family_cash" ? "/api/cash/receipts?only_with_cash_left=true" : null);
  // Without an explicit month, follow the family's rule (e.g. money given in October is for November).
  const rules = useApi<{ settings: { maintenance_given_for: string } }>(forPeriod ? null : "/api/settings");
  useEffect(() => {
    if (rules.data) setForMonth(addMonths(date.slice(0, 7), HANDOVER_OFFSET[rules.data.settings.maintenance_given_for] ?? 0));
  }, [rules.data]); // only once the rule loads, so a month the user picks is kept
  const paise = parseRupees(amount);
  const sources = Object.entries(links).map(([id, v]) => ({ rent_payment_id: Number(id), amount_paise: parseRupees(v) ?? 0 }))
    .filter((s) => s.amount_paise > 0);
  const linked = sources.reduce((s, x) => s + x.amount_paise, 0);

  function changeKind(v: string) {
    setKind(v);
    setFrom(MOVEMENT_KINDS[v].from);
    setTo(MOVEMENT_KINDS[v].to);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!paise) return setError("Enter the amount");
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/cash/movements", {
        kind, movement_date: date, amount_paise: paise, from_account: from, to_account: to,
        description: description || null, sources: from === "family_cash" ? sources : [],
        period: FOR_MONTH_KINDS.has(kind) ? forMonth : null,
      });
      onDone();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Record cash movement" onClose={onClose} wide footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" busy={busy} type="submit" form="cash-form">Save</Button>
    </>}>
      <form id="cash-form" onSubmit={submit} className="flex flex-col gap-3">
        <Field label="What happened">
          {(id) => (
            <select id={id} className={inputCls} value={kind} onChange={(e) => changeKind(e.target.value)}>
              {Object.entries(MOVEMENT_KINDS).map(([v, x]) => <option key={v} value={v}>{x.label}</option>)}
            </select>
          )}
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="From">
            {(id) => (
              <select id={id} className={inputCls} value={from} disabled={!k.fromChoices} onChange={(e) => setFrom(e.target.value)}>
                {(k.fromChoices ?? [k.from]).map((a) => <option key={a} value={a}>{ACCOUNT_LABELS[a]}</option>)}
              </select>
            )}
          </Field>
          <Field label="To">
            {(id) => (
              <select id={id} className={inputCls} value={to} disabled={!k.toChoices} onChange={(e) => setTo(e.target.value)}>
                {(k.toChoices ?? [k.to]).map((a) => <option key={a} value={a}>{ACCOUNT_LABELS[a]}</option>)}
              </select>
            )}
          </Field>
          <Field label="Date">{(id) => <input id={id} type="date" className={inputCls} value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} required />}</Field>
          <Field label="Amount (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />}</Field>
          {FOR_MONTH_KINDS.has(kind) && (
            <Field label="For which month's maintenance" hint="e.g. September's top-up handed over on 20 October is for September.">
              {(id) => <input id={id} type="month" className={inputCls} value={forMonth} onChange={(e) => e.target.value && setForMonth(e.target.value)} required />}
            </Field>
          )}
        </div>
        <Field label={kind === "adjustment" ? "Why is an adjustment needed? (required)" : "Description (optional)"}>
          {(id) => <input id={id} className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} required={kind === "adjustment"} />}
        </Field>
        {from === "family_cash" && (
          <div>
            <div className="mb-1 text-sm font-medium">Which tenant cash did this come from? (optional)</div>
            {receipts.data?.length === 0 && <p className="text-xs text-muted">No cash receipts with money left to link.</p>}
            <div className="flex max-h-56 flex-col gap-1.5 overflow-y-auto">
              {receipts.data?.map((r) => (
                <label key={r.payment_id} className="flex items-center justify-between gap-2 text-sm">
                  <span>{formatDate(r.received_date)} · {r.flat_label} · {r.tenant_name} <span className="text-xs text-muted">({formatINR(r.with_family_paise)} left)</span></span>
                  <input className={inputCls + " w-28"} inputMode="decimal" placeholder="0" aria-label={`Amount from receipt ${r.payment_id}`}
                    value={links[r.payment_id] ?? ""} onChange={(e) => setLinks({ ...links, [r.payment_id]: e.target.value })} />
                </label>
              ))}
            </div>
            {linked > 0 && <p className="mt-1 text-xs text-muted">Linked {formatINR(linked)}{paise && linked > paise ? " — more than the movement amount!" : ""}</p>}
          </div>
        )}
        <Alert tone="info">Moving cash never creates another rent receipt — the tenant's payment stays recorded once.</Alert>
        {error && <Alert tone="error">{error}</Alert>}
      </form>
    </Modal>
  );
}

export function CountForm({ account, onClose, onDone }: { account: "family_cash" | "caretaker_cash"; onClose: () => void; onDone: () => void }) {
  const [date, setDate] = useState(todayISO());
  const [amount, setAmount] = useState("");
  const [notes, setNotes] = useState("");
  const [result, setResult] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const paise = parseRupees(amount);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (paise === null) return setError("Enter the counted amount");
    try {
      const r = await api.post<{ computed_paise: number }>("/api/cash/counts", { account, count_date: date, counted_paise: paise, notes: notes || null });
      setResult(r.computed_paise);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }
  return (
    <Modal title={`Count ${ACCOUNT_LABELS[account].toLowerCase()}`} onClose={onClose} footer={result === null ? <>
      <Button onClick={onClose}>Cancel</Button><Button variant="primary" type="submit" form="count-form">Save count</Button>
    </> : <Button onClick={onClose}>Close</Button>}>
      {result === null ? (
        <form id="count-form" onSubmit={submit} className="flex flex-col gap-3">
          <Field label="Date counted">{(id) => <input id={id} type="date" className={inputCls} value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
          <Field label="Cash actually counted (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />}</Field>
          <Field label="Notes">{(id) => <input id={id} className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
          {error && <Alert tone="error">{error}</Alert>}
        </form>
      ) : result === paise ? (
        <Alert tone="success">The count matches the records ({formatINR(result)}).</Alert>
      ) : (
        <Alert tone="warn" title="The count does not match">
          Records say {formatINR(result)}, counted {formatINR(paise)} (difference {formatINR((paise ?? 0) - result)}).
          Look for a missing expense, handover or receipt; if none, record a reconciliation adjustment with an explanation.
        </Alert>
      )}
    </Modal>
  );
}
