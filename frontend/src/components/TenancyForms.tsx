import { type FormEvent, type ReactNode, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { currentPeriod, formatINR, formatPeriod, parseRupees, todayISO } from "../lib/money";
import type { Tenancy, Tenant } from "../lib/types";
import { Alert, Button, Field, inputCls, Modal } from "./ui";

function useSubmit(fn: () => Promise<unknown>, onDone: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e?: FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await fn();
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, submit };
}

function FormModal({ title, onClose, busy, error, submit, children, label = "Save" }: {
  title: string; onClose: () => void; busy: boolean; error: string | null; submit: (e?: FormEvent) => void;
  children: ReactNode; label?: string;
}) {
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" busy={busy} type="submit" form="tenancy-form">{label}</Button>
    </>}>
      <form id="tenancy-form" onSubmit={submit} className="flex flex-col gap-3">
        {children}
        {error && <Alert tone="error">{error}</Alert>}
      </form>
    </Modal>
  );
}

const POLICY_LABELS: Record<string, string> = {
  "": "Use the global setting",
  due_date_in_tenancy: "Full rent if tenancy covers the due date",
  any_overlap: "Full rent for any day occupied",
  full_months_only: "Only full months automatically",
  prorated_daily: "Prorate part months by days",
};

/** Move a tenant into a vacant flat (new or existing tenant). */
export function AddTenancyForm({ flatId, flatLabel, onClose, onDone }: {
  flatId: number; flatLabel: string; onClose: () => void; onDone: () => void;
}) {
  const { data: tenants } = useApi<Tenant[]>("/api/tenants");
  const [tenantId, setTenantId] = useState<string>("new");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [method, setMethod] = useState("");
  const [start, setStart] = useState(todayISO());
  const [rent, setRent] = useState("");
  const [dueDay, setDueDay] = useState("10");
  const [trackFrom, setTrackFrom] = useState(currentPeriod());
  const [policy, setPolicy] = useState("");
  const rentPaise = parseRupees(rent);
  const startPeriod = start.slice(0, 7);
  const s = useSubmit(async () => {
    if (!rentPaise) throw new Error("Enter the monthly rent");
    let tid = Number(tenantId);
    if (tenantId === "new") {
      const t = await api.post<Tenant>("/api/tenants", { full_name: name, phone: phone || null, preferred_payment_method: method || null });
      tid = t.id;
    }
    await api.post("/api/tenancies", {
      tenant_id: tid, flat_id: flatId, start_date: start, monthly_rent_paise: rentPaise, rent_due_day: Number(dueDay),
      billing_start_period: trackFrom < startPeriod ? startPeriod : trackFrom, billing_policy: policy || null,
    });
  }, () => { onDone(); onClose(); });
  const free = tenants?.filter((t) => t.status !== "active") ?? [];
  return (
    <FormModal title={`New tenancy — ${flatLabel}`} onClose={onClose} {...s}>
      <Field label="Tenant">
        {(id) => (
          <select id={id} className={inputCls} value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
            <option value="new">+ New tenant</option>
            {free.map((t) => <option key={t.id} value={t.id}>{t.full_name}</option>)}
          </select>
        )}
      </Field>
      {tenantId === "new" && (
        <>
          <Field label="Full name">{(id) => <input id={id} className={inputCls} value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Phone (optional)">{(id) => <input id={id} className={inputCls} value={phone} onChange={(e) => setPhone(e.target.value)} />}</Field>
            <Field label="Usually pays by">
              {(id) => (
                <select id={id} className={inputCls} value={method} onChange={(e) => setMethod(e.target.value)}>
                  <option value="">Not sure</option><option value="cash">Cash</option><option value="online">Online transfer</option>
                </select>
              )}
            </Field>
          </div>
        </>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Move-in date">{(id) => <input id={id} type="date" className={inputCls} value={start} onChange={(e) => setStart(e.target.value)} required />}</Field>
        <Field label="Monthly rent (₹)" error={rent && !rentPaise ? "Enter an amount" : null}>
          {(id) => <input id={id} className={inputCls} inputMode="decimal" value={rent} onChange={(e) => setRent(e.target.value)} required />}
        </Field>
        <Field label="Rent due on day" hint="Day of each month (31 = last day).">
          {(id) => <input id={id} type="number" min={1} max={31} className={inputCls} value={dueDay} onChange={(e) => setDueDay(e.target.value)} required />}
        </Field>
        <Field label="Track rent in this app from" hint="For long-standing tenants, use the current month and enter older arrears as an opening balance.">
          {(id) => <input id={id} type="month" className={inputCls} value={trackFrom} min={startPeriod} onChange={(e) => setTrackFrom(e.target.value)} required />}
        </Field>
      </div>
      <Field label="Part-month rule">
        {(id) => (
          <select id={id} className={inputCls} value={policy} onChange={(e) => setPolicy(e.target.value)}>
            {Object.entries(POLICY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        )}
      </Field>
    </FormModal>
  );
}

export function ChangeRentForm({ tenancy, onClose, onDone }: { tenancy: Tenancy; onClose: () => void; onDone: () => void }) {
  const [amount, setAmount] = useState("");
  const [from, setFrom] = useState(currentPeriod());
  const [note, setNote] = useState("");
  const paise = parseRupees(amount);
  const s = useSubmit(async () => {
    if (!paise) throw new Error("Enter the new rent");
    await api.post(`/api/tenancies/${tenancy.id}/rent`, { amount_paise: paise, effective_period: from, note: note || null });
  }, () => { onDone(); onClose(); });
  return (
    <FormModal title="Change monthly rent" onClose={onClose} {...s}>
      <Alert tone="info">Earlier months keep their original rent. The new rent applies from the month you choose.</Alert>
      <p className="text-sm">Current rent: <b className="money">{formatINR(tenancy.current_rent_paise)}</b></p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="New monthly rent (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />}</Field>
        <Field label="Effective from month">{(id) => <input id={id} type="month" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} required />}</Field>
      </div>
      <Field label="Note (optional)">{(id) => <input id={id} className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      {tenancy.rent_terms.length > 0 && (
        <div className="text-xs text-muted">History: {tenancy.rent_terms.map((t) => `${formatINR(t.amount_paise)} from ${formatPeriod(t.effective_period, true)}`).join(" · ")}</div>
      )}
    </FormModal>
  );
}

export function EndTenancyForm({ tenancy, onClose, onDone }: { tenancy: Tenancy; onClose: () => void; onDone: () => void }) {
  const [end, setEnd] = useState(todayISO());
  const [reason, setReason] = useState("");
  const s = useSubmit(() => api.post(`/api/tenancies/${tenancy.id}/end`, { end_date: end, reason: reason || null }),
    () => { onDone(); onClose(); });
  return (
    <FormModal title={`Move-out — ${tenancy.tenant_name}`} onClose={onClose} {...s} label="Record move-out">
      <Alert tone="info">The tenant and all payments stay in the history. Unpaid rent for months after the move-out is waived automatically; outstanding earlier rent remains as arrears.</Alert>
      <Field label="Last day of tenancy">{(id) => <input id={id} type="date" className={inputCls} value={end} min={tenancy.start_date} onChange={(e) => setEnd(e.target.value)} required />}</Field>
      <Field label="Reason / notes (optional)">{(id) => <input id={id} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
    </FormModal>
  );
}

export function EditTenancyForm({ tenancy, onClose, onDone }: { tenancy: Tenancy; onClose: () => void; onDone: () => void }) {
  const [dueDay, setDueDay] = useState(String(tenancy.rent_due_day));
  const [policy, setPolicy] = useState(tenancy.billing_policy ?? "");
  const [notes, setNotes] = useState(tenancy.notes ?? "");
  const s = useSubmit(() => api.patch(`/api/tenancies/${tenancy.id}`, {
    rent_due_day: Number(dueDay), billing_policy: policy || null, notes,
  }), () => { onDone(); onClose(); });
  return (
    <FormModal title="Tenancy settings" onClose={onClose} {...s}>
      <Field label="Rent due on day" hint="Also moves the due date of every month not yet fully paid. Paid months keep theirs.">
        {(id) => <input id={id} type="number" min={1} max={31} className={inputCls} value={dueDay} onChange={(e) => setDueDay(e.target.value)} />}
      </Field>
      <Field label="Part-month rule">
        {(id) => (
          <select id={id} className={inputCls} value={policy} onChange={(e) => setPolicy(e.target.value)}>
            {Object.entries(POLICY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        )}
      </Field>
      <Field label="Notes">{(id) => <textarea id={id} rows={3} className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
    </FormModal>
  );
}

export function OwnerOccupancyForm({ flatId, onClose, onDone }: { flatId: number; onClose: () => void; onDone: () => void }) {
  const [start, setStart] = useState(todayISO());
  const [notes, setNotes] = useState("");
  const s = useSubmit(() => api.post(`/api/flats/${flatId}/owner-occupancy`, { start_date: start, notes: notes || null }),
    () => { onDone(); onClose(); });
  return (
    <FormModal title="Mark as owner-occupied" onClose={onClose} {...s}>
      <Alert tone="info">No rent is generated while the family lives in the flat.</Alert>
      <Field label="From">{(id) => <input id={id} type="date" className={inputCls} value={start} onChange={(e) => setStart(e.target.value)} required />}</Field>
      <Field label="Notes (optional)">{(id) => <input id={id} className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
    </FormModal>
  );
}

export function EndOwnerForm({ occId, onClose, onDone }: { occId: number; onClose: () => void; onDone: () => void }) {
  const [end, setEnd] = useState(todayISO());
  const s = useSubmit(() => api.post(`/api/owner-occupancies/${occId}/end`, { end_date: end }), () => { onDone(); onClose(); });
  return (
    <FormModal title="Family moved out" onClose={onClose} {...s}>
      <Field label="Last day">{(id) => <input id={id} type="date" className={inputCls} value={end} onChange={(e) => setEnd(e.target.value)} required />}</Field>
    </FormModal>
  );
}

export function ManualDueForm({ tenancy, period, onClose, onDone }: { tenancy: Tenancy; period: string; onClose: () => void; onDone: () => void }) {
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const paise = parseRupees(amount);
  const s = useSubmit(async () => {
    if (!paise) throw new Error("Enter the amount");
    await api.post(`/api/tenancies/${tenancy.id}/manual-due`, { period, amount_paise: paise, reason });
  }, () => { onDone(); onClose(); });
  return (
    <FormModal title={`Add rent charge for ${formatPeriod(period)}`} onClose={onClose} {...s}>
      <p className="text-sm text-muted">Use this for a part month the automatic rule did not charge.</p>
      <Field label="Amount (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />}</Field>
      <Field label="Reason">{(id) => <input id={id} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} required />}</Field>
    </FormModal>
  );
}

export function DueAmountForm({ dueId, current, onClose, onDone }: { dueId: number; current: number; onClose: () => void; onDone: () => void }) {
  const [amount, setAmount] = useState(String(current / 100));
  const [reason, setReason] = useState("");
  const paise = parseRupees(amount);
  const s = useSubmit(async () => {
    if (paise === null) throw new Error("Enter the amount");
    await api.post(`/api/rent/dues/${dueId}/amount`, { amount_paise: paise, reason });
  }, () => { onDone(); onClose(); });
  return (
    <FormModal title="Adjust this month's rent" onClose={onClose} {...s}>
      <Field label="Amount due (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />}</Field>
      <Field label="Reason">{(id) => <input id={id} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} required />}</Field>
    </FormModal>
  );
}
