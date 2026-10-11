import { ArrowLeft, CalendarPlus, DoorOpen, Home, IndianRupee, KeyRound, Pencil, Settings2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link, useParams } from "react-router";
import { themeFor } from "../components/FlatCard";
import { PaymentForm } from "../components/PaymentForm";
import { PaymentList } from "../components/PaymentList";
import { ReasonDialog } from "../components/ReasonDialog";
import {
  AddTenancyForm, ChangeRentForm, DueAmountForm, EditTenancyForm, EndOwnerForm, EndTenancyForm, ManualDueForm,
  OwnerOccupancyForm,
} from "../components/TenancyForms";
import { Alert, Button, Card, cx, Empty, ErrorNote, Loading, Money, MonthPicker, PageHeader, StatusBadge, Table } from "../components/ui";
import { api, qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatDate, formatINR, formatPeriod } from "../lib/money";
import type { Due, Payment, Tenancy } from "../lib/types";

interface FlatDetail {
  period: string;
  flat: { id: number; label: string; floor_label: string; notes: string | null;
    building: { id: number; name: string; color: string; code: string } };
  occupancy_in_period: string;
  tenancy_in_period: Tenancy | null;
  occupancy_now: string;
  current_tenancy: Tenancy | null;
  dues: Due[];
  period_payments: Payment[];
  arrears: Due[];
  arrears_total_paise: number;
  credits: Payment[];
  payment_history: Payment[];
  due_history: Due[];
  tenancies: Tenancy[];
  owner_occupancies: { id: number; start_date: string; end_date: string | null; notes: string | null }[];
}

type Dialog = null | "pay" | "add" | "rent" | "end" | "edit" | "owner" | "endOwner" | "manual" | { amount: Due } | { waive: Due };

export function FlatPage() {
  const { flatId } = useParams();
  const [period, setPeriod] = usePeriod();
  const { data, error, loading, reload } = useApi<FlatDetail>(`/api/flats/${flatId}${qs({ period })}`);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const close = () => setDialog(null);

  if (loading && !data) return <Loading />;
  if (!data) return <ErrorNote error={error} />;
  const t = themeFor(data.flat.building.code);
  const tenancy = data.tenancy_in_period ?? data.current_tenancy;
  const rentDue = data.dues.find((d) => d.kind === "rent");
  const ownerNow = data.owner_occupancies.find((o) => !o.end_date);
  const allDues = [...data.due_history];
  // Record payments against the oldest unpaid month first (e.g. September's rent paid in October).
  const payPeriod = data.arrears.find((d) => d.kind === "rent")?.period ?? period;

  return (
    <>
      <Link to={`/buildings/${data.flat.building.code}?month=${period}`} className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink no-print">
        <ArrowLeft className="size-4" /> {data.flat.building.name}
      </Link>
      <PageHeader
        title={<span className={t.accent}>{data.flat.label}</span>}
        subtitle={`${data.flat.building.name} · ${data.flat.floor_label}`}
        actions={<MonthPicker value={period} onChange={setPeriod} />}
      />
      <ErrorNote error={error} />
      {saved && <div className="mb-4"><Alert tone="success">{saved}</Alert></div>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className={cx("lg:col-span-2", t.border)} title={`Rent for ${formatPeriod(period)}`}
          actions={tenancy && (
            <Button variant="primary" onClick={() => { setSaved(null); setDialog("pay"); }}>
              <IndianRupee className="size-4" /> Record payment
            </Button>
          )}>
          {!tenancy ? (
            <Empty>{data.occupancy_in_period === "owner_occupied" ? "Owner-occupied — no rent." : "Vacant — no rent due."}</Empty>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-lg font-semibold">{tenancy.tenant_name}</div>
                  <div className="text-sm text-muted">
                    Rent {formatINR(tenancy.current_rent_paise)} · due on day {tenancy.rent_due_day} each month
                  </div>
                </div>
                {rentDue && <StatusBadge status={rentDue.status} overdue={rentDue.is_overdue} />}
              </div>
              {data.dues.length === 0 ? (
                <Alert tone="info">
                  No rent is charged for {formatPeriod(period)} for this tenancy (outside the tenancy dates, before rent tracking
                  started, or a part month not billed automatically).
                  {tenancy && <Button size="sm" className="ml-2" onClick={() => setDialog("manual")}><CalendarPlus className="size-3.5" /> Add charge</Button>}
                </Alert>
              ) : (
                data.dues.map((d) => (
                  <div key={d.id} className="grid grid-cols-2 gap-3 rounded-lg bg-stone-50 p-3 sm:grid-cols-4">
                    <Fig label={d.kind === "opening_balance" ? "Opening balance" : "Rent due"} value={<Money paise={d.amount_paise} />}
                      sub={`Due ${formatDate(d.due_date)}`} />
                    <Fig label="Confirmed paid" value={<Money paise={d.confirmed_paise} className="text-emerald-700" />}
                      sub={d.pending_paise ? `+${formatINR(d.pending_paise)} pending check` : undefined} />
                    <Fig label="Outstanding" value={<Money paise={d.outstanding_paise} className={d.outstanding_paise ? "text-red-700" : ""} />} />
                    <div className="flex flex-col items-start gap-1 text-xs">
                      {d.status !== "waived" ? (
                        <>
                          <button className="text-brand hover:underline" onClick={() => setDialog({ amount: d })}>Adjust amount</button>
                          <button className="text-brand hover:underline" onClick={() => setDialog({ waive: d })}>Waive</button>
                        </>
                      ) : (
                        <>
                          <span className="text-muted">Waived: {d.waived_reason}</span>
                          <button className="text-brand hover:underline" onClick={async () => { await api.post(`/api/rent/dues/${d.id}/unwaive`); reload(); }}>Undo waive</button>
                        </>
                      )}
                      {d.amount_overridden && <span className="text-muted">Adjusted: {d.override_reason}</span>}
                    </div>
                  </div>
                ))
              )}
              <div>
                <h3 className="mb-2 text-sm font-semibold">Payments for {formatPeriod(period)}</h3>
                <PaymentList payments={data.period_payments} tenancies={data.tenancies} dues={allDues} defaultPeriod={period}
                  onChange={reload} emptyText="No payments recorded for this month yet." />
              </div>
            </div>
          )}
        </Card>

        <div className="flex flex-col gap-4">
          <Card title="Arrears">
            {data.arrears.length === 0 ? <p className="text-sm text-muted">No unpaid earlier months.</p> : (
              <ul className="flex flex-col gap-1.5 text-sm">
                {data.arrears.map((d) => (
                  <li key={d.id} className="flex items-center justify-between gap-2">
                    <button className="text-left hover:underline" onClick={() => setPeriod(d.period)}>
                      {d.kind === "opening_balance" ? "Opening balance" : formatPeriod(d.period)}
                      {d.tenancy_id !== tenancy?.id && <span className="block text-xs text-muted">{d.tenant_name} (former)</span>}
                    </button>
                    <span className="flex items-center gap-2"><StatusBadge status={d.status} /><Money paise={d.outstanding_paise} /></span>
                  </li>
                ))}
                <li className="mt-1 flex justify-between border-t border-line pt-1.5 font-semibold">
                  <span>Total arrears</span><Money paise={data.arrears_total_paise} />
                </li>
              </ul>
            )}
          </Card>
          {data.credits.length > 0 && (
            <Card title="Extra amounts">
              <ul className="flex flex-col gap-1.5 text-sm">
                {data.credits.map((p) => (
                  <li key={p.id} className="flex justify-between">
                    <span>Payment of {formatDate(p.received_date)} <StatusBadge status={p.needs_excess_decision ? "overdue" : "pending"} /></span>
                    <Money paise={p.unallocated_paise} />
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted">Open the payment in the history below to apply or refund it.</p>
            </Card>
          )}
          <Card title="Occupancy">
            <div className="flex flex-col gap-2 text-sm">
              <div className="flex items-center justify-between"><span>Now</span><StatusBadge status={data.occupancy_now === "tenant_occupied" ? "active" : data.occupancy_now} /></div>
              {data.current_tenancy ? (
                <>
                  <div className="text-muted">Since {formatDate(data.current_tenancy.start_date)}; rent tracked from {formatPeriod(data.current_tenancy.billing_start_period, true)}</div>
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button size="sm" onClick={() => setDialog("rent")}><Pencil className="size-3.5" /> Change rent</Button>
                    <Button size="sm" onClick={() => setDialog("edit")}><Settings2 className="size-3.5" /> Due day & rules</Button>
                    <Button size="sm" variant="danger" onClick={() => setDialog("end")}><DoorOpen className="size-3.5" /> Move-out</Button>
                  </div>
                </>
              ) : ownerNow ? (
                <Button size="sm" onClick={() => setDialog("endOwner")}><DoorOpen className="size-3.5" /> Family moved out</Button>
              ) : (
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button size="sm" variant="primary" onClick={() => setDialog("add")}><KeyRound className="size-3.5" /> New tenant moves in</Button>
                  <Button size="sm" onClick={() => setDialog("owner")}><Home className="size-3.5" /> Family lives here</Button>
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>

      <div className="mt-4 grid gap-4">
        <Card title="Month-by-month rent">
          {data.due_history.length === 0 ? <Empty>No rent months yet.</Empty> : (
            <Table>
              <thead><tr><th>Month</th><th>Tenant</th><th>Due date</th><th className="text-right">Due</th><th className="text-right">Paid</th><th className="text-right">Outstanding</th><th>Status</th></tr></thead>
              <tbody>
                {data.due_history.map((d) => (
                  <tr key={d.id} className={cx(d.period === period && "bg-teal-50/50")}>
                    <td><button className="hover:underline" onClick={() => setPeriod(d.period)}>{d.kind === "opening_balance" ? `Opening bal. (${formatPeriod(d.period, true)})` : formatPeriod(d.period)}</button></td>
                    <td>{d.tenant_name}</td>
                    <td>{formatDate(d.due_date)}</td>
                    <td className="text-right"><Money paise={d.amount_paise} /></td>
                    <td className="text-right"><Money paise={d.confirmed_paise} /></td>
                    <td className="text-right"><Money paise={d.outstanding_paise} /></td>
                    <td><StatusBadge status={d.status} overdue={d.is_overdue} /></td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card title="All payments for this flat">
          <PaymentList payments={data.payment_history} tenancies={data.tenancies} dues={allDues} defaultPeriod={period}
            onChange={reload} emptyText="No payments recorded yet." />
        </Card>
        <Card title="Tenancy history">
          {data.tenancies.length === 0 ? <Empty>No tenants recorded for this flat.</Empty> : (
            <ul className="divide-y divide-line text-sm">
              {data.tenancies.map((x) => (
                <li key={x.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span><b>{x.tenant_name}</b> · {formatDate(x.start_date)} – {x.end_date ? formatDate(x.end_date) : "present"}</span>
                  <span className="flex items-center gap-2 text-muted">
                    {x.rent_terms.map((r) => `${formatINR(r.amount_paise)} from ${formatPeriod(r.effective_period, true)}`).join(" → ")}
                    <StatusBadge status={x.status} />
                  </span>
                </li>
              ))}
            </ul>
          )}
          {data.owner_occupancies.length > 0 && (
            <p className="mt-2 text-xs text-muted">Owner-occupied: {data.owner_occupancies.map((o) => `${formatDate(o.start_date)} – ${o.end_date ? formatDate(o.end_date) : "present"}`).join(", ")}</p>
          )}
        </Card>
      </div>

      {dialog === "pay" && tenancy && (
        <PaymentForm tenancy={tenancy} defaultPeriod={payPeriod} onClose={close}
          onSaved={(p) => { close(); setSaved(`Saved ${formatINR(p.amount_paise)} (${p.status === "confirmed" ? "confirmed" : "pending verification"}).`); reload(); }} />
      )}
      {dialog === "add" && <AddTenancyForm flatId={data.flat.id} flatLabel={data.flat.label} onClose={close} onDone={reload} />}
      {dialog === "rent" && data.current_tenancy && <ChangeRentForm tenancy={data.current_tenancy} onClose={close} onDone={reload} />}
      {dialog === "end" && data.current_tenancy && <EndTenancyForm tenancy={data.current_tenancy} onClose={close} onDone={reload} />}
      {dialog === "edit" && data.current_tenancy && <EditTenancyForm tenancy={data.current_tenancy} onClose={close} onDone={reload} />}
      {dialog === "owner" && <OwnerOccupancyForm flatId={data.flat.id} onClose={close} onDone={reload} />}
      {dialog === "endOwner" && ownerNow && <EndOwnerForm occId={ownerNow.id} onClose={close} onDone={reload} />}
      {dialog === "manual" && tenancy && <ManualDueForm tenancy={tenancy} period={period} onClose={close} onDone={reload} />}
      {dialog && typeof dialog === "object" && "amount" in dialog && (
        <DueAmountForm dueId={dialog.amount.id} current={dialog.amount.amount_paise} onClose={close} onDone={reload} />
      )}
      {dialog && typeof dialog === "object" && "waive" in dialog && (
        <ReasonDialog title={`Waive rent for ${formatPeriod(dialog.waive.period)}?`} confirmLabel="Waive"
          description="The month will no longer count as outstanding. Payments already received remain recorded."
          onClose={close} onSubmit={async (reason) => { await api.post(`/api/rent/dues/${dialog.waive.id}/waive`, { reason }); reload(); }} />
      )}
    </>
  );
}

function Fig({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}
