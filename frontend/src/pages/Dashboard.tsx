import { AlertTriangle, ArrowRight, Banknote, Clock, Landmark, Wallet, Wrench } from "lucide-react";
import { Link } from "react-router";
import { Alert, Card, ErrorNote, Loading, Money, MonthPicker, PageHeader, StatusBadge, Stat } from "../components/ui";
import { qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatINR, formatPeriod } from "../lib/money";
import type { Building } from "../lib/types";
import { BuildingChoice } from "./Buildings";

interface Dashboard {
  period: string;
  /** The month whose rent is collected in `period` (the previous month when rent is paid the month after). */
  rent_period: string;
  rent: {
    expected_paise: number; confirmed_paise: number; pending_paise: number; outstanding_paise: number;
    overdue_paise: number; arrears_paise: number;
    counts: Record<"paid" | "partially_paid" | "pending_verification" | "unpaid" | "overdue", number>;
  };
  collected_for_period_by_method: { cash_paise: number; online_paise: number };
  received_in_month: { cash_confirmed_paise: number; online_confirmed_paise: number; pending_paise: number };
  occupancy: { tenant_occupied: number; vacant: number; owner_occupied: number };
  buildings: { id: number; name: string; code: string; color: string; flat_count: number; expected_paise: number;
    confirmed_paise: number; outstanding_paise: number }[];
  outstanding_flats: { flat_id: number; flat_label: string; building_name: string; tenant_name: string;
    outstanding_paise: number; periods: string[]; is_overdue: boolean }[];
  maintenance: { target_paise: number; opening_paise: number; top_up_required_paise: number; handed_over_paise: number;
    still_to_hand_over_paise: number; total_expenses_paise: number; caretaker_spent_paise: number;
    family_paid_paise: number; closing_paise: number };
  maintenance_discrepancies: number;
  caretaker_cash_now_paise: number;
  family_cash_now_paise: number;
  maintenance_top_up: { period: string; top_up_required_paise: number; handed_over_paise: number; still_to_hand_over_paise: number };
  salary: { expected_paise: number; confirmed_paise: number; pending_paise: number; outstanding_paise: number; status: string };
  bank_review: { count: number; amount_paise: number };
  pending_payments: number;
}

export function DashboardPage() {
  const [period, setPeriod] = usePeriod();
  const { data, error, loading } = useApi<Dashboard>(`/api/dashboard${qs({ period })}`);
  const { data: bdata } = useApi<{ buildings: Building[]; rent_period: string }>(`/api/buildings${qs({ period })}`);
  const m = `?month=${period}`;
  return (
    <>
      <PageHeader title="Dashboard" subtitle={`Summary for ${formatPeriod(period)}`}
        actions={<MonthPicker value={period} onChange={setPeriod} />} />
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      {data && (
        <div className="flex flex-col gap-5">
          {bdata && <BuildingChoice buildings={bdata.buildings} period={period} rentPeriod={bdata.rent_period} />}

          {(data.bank_review.count > 0 || data.pending_payments > 0 || data.maintenance_discrepancies > 0) && (
            <div className="grid gap-2 sm:grid-cols-3">
              {data.bank_review.count > 0 && (
                <Link to="/bank" className="flex items-center justify-between rounded-xl border border-sky-200 bg-sky-50 p-3 text-sm text-sky-900 hover:bg-sky-100">
                  <span className="flex items-center gap-2"><Landmark className="size-4" />{data.bank_review.count} bank credits to review</span>
                  <ArrowRight className="size-4" />
                </Link>
              )}
              {data.pending_payments > 0 && (
                <Link to={`/rent${m}`} className="flex items-center justify-between rounded-xl border border-sky-200 bg-sky-50 p-3 text-sm text-sky-900 hover:bg-sky-100">
                  <span className="flex items-center gap-2"><Clock className="size-4" />{data.pending_payments} payments pending verification</span>
                  <ArrowRight className="size-4" />
                </Link>
              )}
              {data.maintenance_discrepancies > 0 && (
                <Link to={`/maintenance${m}`} className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 hover:bg-amber-100">
                  <span className="flex items-center gap-2"><AlertTriangle className="size-4" />Maintenance cash needs reconciling</span>
                  <ArrowRight className="size-4" />
                </Link>
              )}
            </div>
          )}

          <div>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted">Rent for {formatPeriod(data.rent_period)}{data.rent_period !== period && <span className="normal-case font-normal"> · collected in {formatPeriod(period)}</span>}</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Expected" value={formatINR(data.rent.expected_paise)} hint={data.rent_period !== period ? `${formatPeriod(data.rent_period)}'s rent, due in ${formatPeriod(period)}` : "Rent due this month"} />
              <Stat label="Confirmed received" tone="good" value={formatINR(data.rent.confirmed_paise)}
                hint={data.rent.pending_paise > 0 ? `+ ${formatINR(data.rent.pending_paise)} awaiting check` : "Confirmed only"} />
              <Stat label="Outstanding" tone={data.rent.outstanding_paise ? "warn" : "neutral"} value={formatINR(data.rent.outstanding_paise)}
                hint={data.rent.arrears_paise > 0 ? `Plus ${formatINR(data.rent.arrears_paise)} older arrears` : "No older arrears"} />
              <Stat label="Overdue" tone={data.rent.overdue_paise ? "bad" : "neutral"} value={formatINR(data.rent.overdue_paise)}
                hint="Past due date, not yet confirmed" />
              <Stat label="Cash rent" icon={<Banknote className="size-3.5" />} value={formatINR(data.collected_for_period_by_method.cash_paise)}
                hint={`Received in month (any month's rent): ${formatINR(data.received_in_month.cash_confirmed_paise)}`} />
              <Stat label="Online rent" icon={<Landmark className="size-3.5" />} value={formatINR(data.collected_for_period_by_method.online_paise)}
                hint={`Received in month (any month's rent): ${formatINR(data.received_in_month.online_confirmed_paise)}`} />
              <Stat label="Flats" value={`${data.occupancy.tenant_occupied} let`}
                hint={`${data.occupancy.vacant} vacant · ${data.occupancy.owner_occupied} owner-occupied`} />
              <Stat label="Rent dues" value={`${data.rent.counts.paid} paid`}
                hint={`${data.rent.counts.partially_paid} partly · ${data.rent.counts.unpaid + data.rent.counts.overdue} unpaid · ${data.rent.counts.pending_verification} pending`} />
            </div>
          </div>

          <div>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted">Caretaker</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label={`Top-up to give · for ${formatPeriod(data.maintenance_top_up.period, true)}`} icon={<Wrench className="size-3.5" />}
                tone={data.maintenance_top_up.still_to_hand_over_paise ? "info" : "good"}
                value={formatINR(data.maintenance_top_up.still_to_hand_over_paise)}
                hint={`Required ${formatINR(data.maintenance_top_up.top_up_required_paise)}, handed ${formatINR(data.maintenance_top_up.handed_over_paise)}`} />
              <Stat label="Cash he holds now" icon={<Wallet className="size-3.5" />}
                tone={data.caretaker_cash_now_paise < 0 ? "bad" : "neutral"} value={formatINR(data.caretaker_cash_now_paise)}
                hint={`Started the month with ${formatINR(data.maintenance.opening_paise)}`} />
              <Stat label="Maintenance spent" value={formatINR(data.maintenance.total_expenses_paise)}
                hint={data.maintenance.family_paid_paise ? `${formatINR(data.maintenance.family_paid_paise)} paid by family` : `Budget ${formatINR(data.maintenance.target_paise)}`} />
              <Stat label="Salary" value={<StatusBadge status={data.salary.status} />}
                hint={`${formatINR(data.salary.confirmed_paise)} of ${formatINR(data.salary.expected_paise)} paid`} />
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title={`Collection by building — ${formatPeriod(data.rent_period)} rent`}>
              <div className="flex flex-col gap-3">
                {data.buildings.map((b) => {
                  const pct = b.expected_paise ? Math.min(100, Math.round((b.confirmed_paise / b.expected_paise) * 100)) : 0;
                  return (
                    <div key={b.id}>
                      <div className="flex justify-between text-sm">
                        <span className="font-medium">{b.name}</span>
                        <span className="money text-muted">{formatINR(b.confirmed_paise)} of {formatINR(b.expected_paise)}</span>
                      </div>
                      <div className="mt-1 h-2 overflow-hidden rounded-full bg-stone-100" role="progressbar" aria-valuenow={pct}
                        aria-valuemin={0} aria-valuemax={100} aria-label={`${b.name} collected`}>
                        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: b.color }} />
                      </div>
                      <div className="mt-0.5 text-xs text-muted">{pct}% collected · outstanding <Money paise={b.outstanding_paise} /></div>
                    </div>
                  );
                })}
              </div>
            </Card>
            <Card title="Flats with money outstanding">
              {data.outstanding_flats.length === 0 ? (
                <Alert tone="success">Nothing outstanding that is due by now.</Alert>
              ) : (
                <ul className="divide-y divide-line">
                  {data.outstanding_flats.map((f) => (
                    <li key={f.flat_id}>
                      <Link to={`/flats/${f.flat_id}${m}`} className="flex items-center justify-between gap-2 py-2 hover:bg-stone-50">
                        <div>
                          <div className="text-sm font-medium">{f.flat_label} · {f.tenant_name}</div>
                          <div className="text-xs text-muted">{f.building_name} · {f.periods.map((p) => formatPeriod(p, true)).join(", ")}</div>
                        </div>
                        <div className="flex items-center gap-2">
                          {f.is_overdue && <StatusBadge status="overdue" />}
                          <Money paise={f.outstanding_paise} className="font-medium" />
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
