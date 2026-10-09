import { CheckCircle2, Clock, Wand2 } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { addMonths, formatINR, formatPeriod, paiseToInput, parseRupees, todayISO } from "../lib/money";
import type { Due, Payment, Tenancy } from "../lib/types";
import { ACCEPT, uploadDocs } from "./Documents";
import { Alert, Button, cx, Field, inputCls, Modal } from "./ui";

type Excess = "apply_to_arrears" | "apply_forward" | "hold_as_credit" | "refund_due";

const EXCESS_OPTIONS: { value: Excess; label: string; hint: string }[] = [
  { value: "apply_forward", label: "Advance for next month(s)", hint: "Applied to the following months' rent, in order." },
  { value: "apply_to_arrears", label: "Clear older unpaid rent", hint: "Applied to the oldest unpaid months first." },
  { value: "hold_as_credit", label: "Keep as credit", hint: "Recorded as advance credit to apply later." },
  { value: "refund_due", label: "To be refunded", hint: "The extra will be returned to the tenant." },
];

export interface PaymentFormProps {
  tenancy: Tenancy;
  defaultPeriod: string;
  /** Pass to edit an existing payment as a correction (original is reversed, new one linked). */
  correcting?: Payment;
  onClose: () => void;
  onSaved: (p: Payment) => void;
}

/** Record a rent payment. Optimised for "full rent received", but partial amounts work too. */
export function PaymentForm({ tenancy, defaultPeriod, correcting, onClose, onSaved }: PaymentFormProps) {
  const preferred = correcting?.method ?? "cash";
  const [period, setPeriod] = useState(correcting?.allocations[0]?.period ?? defaultPeriod);
  const [amount, setAmount] = useState(correcting ? paiseToInput(correcting.amount_paise) : "");
  const [receivedDate, setReceivedDate] = useState(correcting?.received_date ?? todayISO());
  const [method, setMethod] = useState<"cash" | "online">(preferred);
  const [reference, setReference] = useState(correcting?.reference ?? "");
  const [sender, setSender] = useState(correcting?.sender_name ?? "");
  const [notes, setNotes] = useState(correcting?.notes ?? "");
  const [confirmed, setConfirmed] = useState(correcting ? correcting.status === "confirmed" : true);
  const [files, setFiles] = useState<File[]>([]);
  const [reason, setReason] = useState("");
  const [due, setDue] = useState<Due | null | undefined>(undefined);
  const [excess, setExcess] = useState<{ excess_paise: number; allocated_paise: number } | null>(null);
  const [excessChoice, setExcessChoice] = useState<Excess>("apply_forward");
  const [excessNote, setExcessNote] = useState("");
  const [duplicate, setDuplicate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const decisionRef = useRef<HTMLDivElement>(null);

  // Bring the "what to do with the extra" question into view when it appears.
  useEffect(() => {
    if (excess || duplicate) decisionRef.current?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [excess, duplicate]);

  // Look up what is still due for the chosen month so "Fill full amount" is one tap.
  useEffect(() => {
    let live = true;
    setDue(undefined);
    api.get<{ due: Due | null }>(`/api/tenancies/${tenancy.id}/due?period=${period}`)
      .then((r) => live && setDue(r.due))
      .catch(() => live && setDue(null));
    return () => { live = false; };
  }, [tenancy.id, period]);

  useEffect(() => { setExcess(null); setDuplicate(false); }, [period, amount]);

  const amountPaise = parseRupees(amount);
  const remaining = due ? due.available_paise : 0;
  const periods = Array.from({ length: 15 }, (_, i) => addMonths(defaultPeriod, 2 - i));
  if (!periods.includes(period)) periods.unshift(period);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!amountPaise || amountPaise <= 0) return setError("Enter the amount received.");
    if (correcting && reason.trim().length < 3) return setError("Say why this payment is being corrected.");
    setBusy(true);
    setError(null);
    const body = {
      tenancy_id: tenancy.id, received_date: receivedDate, amount_paise: amountPaise, method,
      status: confirmed ? "confirmed" : "pending", rent_period: period,
      reference: method === "online" ? reference : null, sender_name: sender || null, notes: notes || null,
      excess_handling: excess ? excessChoice : null, excess_note: excess ? excessNote || null : null,
      allow_duplicate: duplicate,
      ...(correcting ? { reason } : {}),
    };
    try {
      const p = correcting
        ? await api.post<Payment>(`/api/payments/${correcting.id}/correct`, body)
        : await api.post<Payment>("/api/payments", body);
      if (files.length) {
        try {
          await uploadDocs("rent_payment", p.id, files, method === "cash" ? "cash_receipt" : "payment_proof");
        } catch (err) {
          alert(`Payment saved, but the attachment failed: ${err instanceof Error ? err.message : err}`);
        }
      }
      onSaved(p);
    } catch (err) {
      if (err instanceof ApiError && err.code === "excess_payment") {
        setExcess(err.details as { excess_paise: number; allocated_paise: number });
      } else if (err instanceof ApiError && err.code === "possible_duplicate") {
        setDuplicate(true);
        setError("A payment with the same reference, or the same amount, date and method, is already recorded. "
          + "Check the history below. If this really is a separate payment, press Save again.");
      } else {
        setError(err instanceof Error ? err.message : "Could not save");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={correcting ? "Correct payment" : "Record rent payment"} onClose={onClose} wide footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" busy={busy} type="submit" form="payment-form">
        {excess ? "Save with this choice" : duplicate ? "Save anyway" : "Save payment"}
      </Button>
    </>}>
      <form id="payment-form" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2 grid grid-cols-3 gap-2 rounded-lg bg-stone-50 p-3 text-sm">
          <div><div className="text-xs text-muted">Building</div>{tenancy.building_name}</div>
          <div><div className="text-xs text-muted">Flat</div>{tenancy.flat_label}</div>
          <div><div className="text-xs text-muted">Tenant</div>{tenancy.tenant_name}</div>
        </div>

        <Field label="Rent month" hint="Which month's rent this pays for.">
          {(id) => (
            <select id={id} className={inputCls} value={period} onChange={(e) => setPeriod(e.target.value)}>
              {periods.map((p) => <option key={p} value={p}>{formatPeriod(p)}</option>)}
            </select>
          )}
        </Field>
        <Field label="Date money was received" hint="Can be later than the rent month.">
          {(id) => <input id={id} type="date" className={inputCls} value={receivedDate} max={todayISO()}
            onChange={(e) => setReceivedDate(e.target.value)} required />}
        </Field>

        <Field label="Amount received (₹)" className="sm:col-span-2"
          error={amount && amountPaise === null ? "Enter a number like 12000 or 12000.50" : null}
          hint={due === undefined ? "Checking what is due…" : due ? (
            remaining > 0
              ? <>Still due for {formatPeriod(period)}: <b className="money">{formatINR(remaining)}</b> of {formatINR(due.amount_paise)}</>
              : <>Rent for {formatPeriod(period)} is already fully recorded.</>
          ) : <>No rent is due for {formatPeriod(period)} (outside the tenancy or before tracking started).</>}>
          {(id) => (
            <div className="flex gap-2">
              <input id={id} className={inputCls} inputMode="decimal" value={amount} placeholder="0"
                onChange={(e) => setAmount(e.target.value)} required />
              {remaining > 0 && (
                <Button type="button" onClick={() => setAmount(paiseToInput(remaining))}>
                  <Wand2 className="size-4" /> Full {formatINR(remaining)}
                </Button>
              )}
            </div>
          )}
        </Field>

        <fieldset className="sm:col-span-2">
          <legend className="mb-1 text-sm font-medium">Payment method</legend>
          <div className="grid grid-cols-2 gap-2">
            {(["cash", "online"] as const).map((m) => (
              <label key={m} className={cx("flex cursor-pointer items-center gap-2 rounded-lg border p-2.5 text-sm",
                method === m ? "border-teal-600 bg-teal-50" : "border-line")}>
                <input type="radio" name="method" checked={method === m} onChange={() => setMethod(m)} />
                {m === "cash" ? "Cash" : "Online transfer"}
              </label>
            ))}
          </div>
        </fieldset>

        {method === "online" && (
          <Field label="Transaction reference (optional)" hint="UTR / UPI reference helps match the bank statement.">
            {(id) => <input id={id} className={inputCls} value={reference} onChange={(e) => setReference(e.target.value)} />}
          </Field>
        )}
        <Field label="Paid by (optional)" hint="Name of the person who paid, if not the tenant.">
          {(id) => <input id={id} className={inputCls} value={sender} onChange={(e) => setSender(e.target.value)} />}
        </Field>

        <fieldset className="sm:col-span-2">
          <legend className="mb-1 text-sm font-medium">Has the money actually been received?</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className={cx("flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-sm",
              confirmed ? "border-emerald-600 bg-emerald-50" : "border-line")}>
              <input type="radio" name="confirm" checked={confirmed} onChange={() => setConfirmed(true)} className="mt-0.5" />
              <span><CheckCircle2 className="mr-1 inline size-4 text-emerald-700" />Yes — confirmed received</span>
            </label>
            <label className={cx("flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-sm",
              !confirmed ? "border-sky-600 bg-sky-50" : "border-line")}>
              <input type="radio" name="confirm" checked={!confirmed} onChange={() => setConfirmed(false)} className="mt-0.5" />
              <span><Clock className="mr-1 inline size-4 text-sky-700" />Not yet checked — pending verification
                <span className="block text-xs text-muted">Not counted as received until confirmed.</span></span>
            </label>
          </div>
        </fieldset>

        <Field label="Proof (optional)" hint="Screenshot, photo of receipt or PDF. Several files allowed.">
          {(id) => <input id={id} type="file" multiple accept={ACCEPT} className="text-sm"
            onChange={(e) => setFiles([...(e.target.files ?? [])])} />}
        </Field>
        <Field label="Notes (optional)">
          {(id) => <textarea id={id} className={inputCls} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />}
        </Field>

        {correcting && (
          <Field label="Reason for correction" className="sm:col-span-2"
            hint="The original payment is kept as reversed and linked to this one.">
            {(id) => <input id={id} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} required />}
          </Field>
        )}

        <div ref={decisionRef} className="sm:col-span-2 empty:hidden">
        {excess && (
          <div>
            <Alert tone="warn" title={`This is ${formatINR(excess.excess_paise)} more than what is still due for ${formatPeriod(period)}`}>
              {excess.allocated_paise > 0 && <p>{formatINR(excess.allocated_paise)} will settle {formatPeriod(period)}. </p>}
              <p>What should happen to the extra {formatINR(excess.excess_paise)}?</p>
            </Alert>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {EXCESS_OPTIONS.map((o) => (
                <label key={o.value} className={cx("flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-sm",
                  excessChoice === o.value ? "border-amber-600 bg-amber-50" : "border-line")}>
                  <input type="radio" name="excess" className="mt-0.5" checked={excessChoice === o.value}
                    onChange={() => setExcessChoice(o.value)} />
                  <span><b>{o.label}</b><span className="block text-xs text-muted">{o.hint}</span></span>
                </label>
              ))}
            </div>
            <input className={cx(inputCls, "mt-2")} placeholder="Note about the extra amount (optional)" value={excessNote}
              onChange={(e) => setExcessNote(e.target.value)} />
          </div>
        )}
        {error && <Alert tone={duplicate ? "warn" : "error"}>{error}</Alert>}
        </div>
      </form>
    </Modal>
  );
}
