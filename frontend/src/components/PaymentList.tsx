import { ArrowRightLeft, Check, ChevronDown, ChevronUp, Landmark, Pencil, Undo2 } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { formatDate, formatDateTime, formatINR, formatPeriod, paiseToInput, parseRupees } from "../lib/money";
import type { Due, Payment, Tenancy } from "../lib/types";
import { Documents } from "./Documents";
import { PaymentForm } from "./PaymentForm";
import { ReasonDialog } from "./ReasonDialog";
import { Alert, Button, cx, Empty, Field, inputCls, Modal, StatusBadge } from "./ui";

export function PaymentList({ payments, tenancies, dues, defaultPeriod, onChange, emptyText }: {
  payments: Payment[]; tenancies: Tenancy[]; dues: Due[]; defaultPeriod: string; onChange: () => void; emptyText: string;
}) {
  if (!payments.length) return <Empty>{emptyText}</Empty>;
  return (
    <ul className="flex flex-col gap-2">
      {payments.map((p) => (
        <PaymentRow key={p.id} p={p} tenancy={tenancies.find((t) => t.id === p.tenancy_id)} dues={dues}
          defaultPeriod={defaultPeriod} onChange={onChange} />
      ))}
    </ul>
  );
}

function PaymentRow({ p, tenancy, dues, defaultPeriod, onChange }: {
  p: Payment; tenancy?: Tenancy; dues: Due[]; defaultPeriod: string; onChange: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<null | "void" | "correct" | "move" | "excess">(null);
  const active = p.status === "pending" || p.status === "confirmed";
  const voidLabel = p.status === "pending" ? "Reject" : "Reverse";

  return (
    <li className={cx("rounded-lg border border-line bg-white", !active && "opacity-70")}>
      <div className="flex flex-wrap items-center justify-between gap-2 p-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cx("money text-base font-semibold", !active && "line-through")}>{formatINR(p.amount_paise)}</span>
            <StatusBadge status={p.status} />
            <span className="rounded bg-stone-100 px-1.5 py-0.5 text-xs">{p.method === "cash" ? "Cash" : "Online"}</span>
            {p.bank_transaction_id && <span className="inline-flex items-center gap-1 text-xs text-muted"><Landmark className="size-3" />from bank statement</span>}
          </div>
          <div className="mt-0.5 text-sm text-muted">
            Received {formatDate(p.received_date)} · for{" "}
            {p.allocations.length
              ? p.allocations.map((a) => `${a.kind === "opening_balance" ? "opening balance" : formatPeriod(a.period, true)} (${formatINR(a.amount_paise)}${a.is_late ? ", late" : ""})`).join(", ")
              : "no month yet"}
          </div>
          {p.unallocated_paise > 0 && (
            <div className="mt-1 text-sm text-amber-800">
              {formatINR(p.unallocated_paise)} extra —{" "}
              {p.excess_resolution === "advance_credit" ? "held as advance credit"
                : p.excess_resolution === "refund_due" ? "to be refunded"
                : p.excess_resolution === "refunded" ? "refunded" : "decision needed"}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {p.status === "pending" && (
            <Button size="sm" variant="primary" onClick={async () => { await api.post(`/api/payments/${p.id}/confirm`); onChange(); }}>
              <Check className="size-3.5" /> Confirm received
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
            Details {open ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
          </Button>
        </div>
      </div>
      {open && (
        <div className="border-t border-line p-3 text-sm">
          <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
            <Row k="Payment #" v={String(p.id)} />
            <Row k="Tenant / flat" v={`${p.tenant_name} · ${p.flat_label}`} />
            {p.reference && <Row k="Reference" v={p.reference} />}
            {p.sender_name && <Row k="Paid by" v={p.sender_name} />}
            <Row k="Recorded" v={`${p.recorded_by ?? "?"}, ${formatDateTime(p.created_at)}`} />
            {p.confirmed_by && <Row k="Confirmed" v={`${p.confirmed_by}, ${formatDateTime(p.confirmed_at)}`} />}
            {p.voided_by && <Row k={p.status === "rejected" ? "Rejected" : "Reversed"} v={`${p.voided_by}, ${formatDateTime(p.voided_at)} — ${p.status_reason}`} />}
            {p.corrects_payment_id && <Row k="Corrects" v={`payment #${p.corrects_payment_id}`} />}
            {p.notes && <Row k="Notes" v={p.notes} />}
            {p.excess_note && <Row k="Extra amount note" v={p.excess_note} />}
          </dl>
          <div className="mt-3">
            <div className="mb-1 text-xs font-semibold uppercase text-muted">Proof</div>
            <Documents entityType="rent_payment" entityId={p.id} category={p.method === "cash" ? "cash_receipt" : "payment_proof"} compact />
          </div>
          {active && (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
              <Button size="sm" onClick={() => setDialog("move")}><ArrowRightLeft className="size-3.5" /> Move to other month</Button>
              {tenancy && <Button size="sm" onClick={() => setDialog("correct")}><Pencil className="size-3.5" /> Correct</Button>}
              {p.unallocated_paise > 0 && <Button size="sm" onClick={() => setDialog("excess")}>Extra amount…</Button>}
              <Button size="sm" variant="danger" onClick={() => setDialog("void")}><Undo2 className="size-3.5" /> {voidLabel}</Button>
            </div>
          )}
        </div>
      )}
      {dialog === "void" && (
        <ReasonDialog title={`${voidLabel} payment of ${formatINR(p.amount_paise)}?`} danger confirmLabel={voidLabel}
          description="The payment stays in the history marked as such, and no longer counts toward rent received."
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => { await api.post(`/api/payments/${p.id}/void`, { reason }); onChange(); }} />
      )}
      {dialog === "correct" && tenancy && (
        <PaymentForm tenancy={tenancy} defaultPeriod={defaultPeriod} correcting={p} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); onChange(); }} />
      )}
      {dialog === "move" && <MoveDialog p={p} dues={dues} onClose={() => setDialog(null)} onDone={onChange} />}
      {dialog === "excess" && <ExcessDialog p={p} onClose={() => setDialog(null)} onDone={onChange} />}
    </li>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-32 shrink-0 text-muted">{k}</dt>
      <dd className="min-w-0 break-words">{v}</dd>
    </div>
  );
}

/** Re-allocate a payment across this tenancy's rent months (e.g. wrong month, or apply credit). */
function MoveDialog({ p, dues, onClose, onDone }: { p: Payment; dues: Due[]; onClose: () => void; onDone: () => void }) {
  const own = dues.filter((d) => d.tenancy_id === p.tenancy_id && d.status !== "waived");
  const current = Object.fromEntries(p.allocations.map((a) => [a.rent_due_id, a.amount_paise]));
  const [amounts, setAmounts] = useState<Record<number, string>>(
    Object.fromEntries(own.map((d) => [d.id, current[d.id] ? paiseToInput(current[d.id]) : ""])));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const allocs = own.map((d) => ({ rent_due_id: d.id, amount_paise: parseRupees(amounts[d.id] || "0") ?? -1 }))
    .filter((a) => a.amount_paise !== 0);
  const total = allocs.reduce((s, a) => s + Math.max(0, a.amount_paise), 0);
  const invalid = allocs.some((a) => a.amount_paise < 0) || total > p.amount_paise;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/payments/${p.id}/reallocate`, { allocations: allocs, reason });
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Move payment to other month(s)" onClose={onClose} wide footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" busy={busy} disabled={invalid || reason.trim().length < 3} onClick={save}>Save</Button>
    </>}>
      <p className="mb-3 text-sm text-muted">Split {formatINR(p.amount_paise)} across this tenancy's months. Months shown are those
        already generated; open a later month on the flat page to create it.</p>
      <div className="flex max-h-72 flex-col gap-1.5 overflow-y-auto">
        {own.map((d) => {
          const room = d.available_paise + (current[d.id] ?? 0);
          return (
            <div key={d.id} className="flex items-center justify-between gap-2 text-sm">
              <span>{d.kind === "opening_balance" ? "Opening balance" : formatPeriod(d.period)}
                <span className="ml-2 text-xs text-muted">room {formatINR(room)}</span></span>
              <input className={cx(inputCls, "w-32")} inputMode="decimal" aria-label={`Amount for ${d.period}`}
                value={amounts[d.id]} onChange={(e) => setAmounts({ ...amounts, [d.id]: e.target.value })} />
            </div>
          );
        })}
      </div>
      <div className={cx("mt-3 text-sm", invalid ? "text-red-700" : "text-muted")}>
        Allocated {formatINR(total)} of {formatINR(p.amount_paise)}
        {total < p.amount_paise && !invalid && ` — ${formatINR(p.amount_paise - total)} will remain as extra`}
      </div>
      <Field label="Reason" className="mt-3">
        {(id) => <input id={id} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} />}
      </Field>
      {error && <div className="mt-2"><Alert tone="error">{error}</Alert></div>}
    </Modal>
  );
}

function ExcessDialog({ p, onClose, onDone }: { p: Payment; onClose: () => void; onDone: () => void }) {
  const [resolution, setResolution] = useState(p.excess_resolution ?? "advance_credit");
  const [note, setNote] = useState(p.excess_note ?? "");
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal title={`Extra ${formatINR(p.unallocated_paise)}`} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" onClick={async () => {
        try {
          await api.post(`/api/payments/${p.id}/excess`, { resolution, note });
          onDone();
          onClose();
        } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
      }}>Save</Button>
    </>}>
      <p className="mb-3 text-sm text-muted">To apply it to a month instead, use “Move to other month”.</p>
      <Field label="What happens to it">
        {(id) => (
          <select id={id} className={inputCls} value={resolution} onChange={(e) => setResolution(e.target.value)}>
            <option value="advance_credit">Held as advance credit</option>
            <option value="refund_due">To be refunded</option>
            <option value="refunded">Refunded to tenant</option>
          </select>
        )}
      </Field>
      <Field label="Note" className="mt-3">
        {(id) => <input id={id} className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} />}
      </Field>
      {error && <div className="mt-2"><Alert tone="error">{error}</Alert></div>}
    </Modal>
  );
}
