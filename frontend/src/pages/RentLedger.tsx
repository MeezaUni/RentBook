import { Check } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { Button, Card, Empty, ErrorNote, inputCls, Loading, Money, MonthPicker, PageHeader, StatusBadge, Stat, Table } from "../components/ui";
import { api, qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatDate, formatINR, formatPeriod } from "../lib/money";
import type { Due, Payment } from "../lib/types";

interface Ledger {
  period: string;
  dues: Due[];
  arrears: Due[];
  summary: { expected_paise: number; confirmed_paise: number; pending_paise: number; outstanding_paise: number;
    overdue_paise: number; arrears_paise: number; counts: Record<string, number> };
}

export function RentLedgerPage() {
  const [period, setPeriod] = usePeriod();
  const [building, setBuilding] = useState("");
  const { data, error, loading, reload } = useApi<Ledger>(`/api/rent/ledger${qs({ period, building_id: building })}`);
  const pending = useApi<Payment[]>("/api/payments?status=pending");
  const decisions = useApi<Payment[]>("/api/payments?needs_decision=true");
  const { data: blds } = useApi<{ buildings: { id: number; name: string }[] }>("/api/buildings");
  const m = `?month=${period}`;

  async function confirm(id: number) {
    await api.post(`/api/payments/${id}/confirm`);
    pending.reload();
    reload();
  }

  return (
    <>
      <PageHeader title="Rent Ledger" subtitle={`All flats for ${formatPeriod(period)}`}
        actions={<>
          <select className={inputCls + " w-44"} value={building} onChange={(e) => setBuilding(e.target.value)} aria-label="Building">
            <option value="">Both buildings</option>
            {blds?.buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <MonthPicker value={period} onChange={setPeriod} />
        </>} />
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      {data && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Expected" value={formatINR(data.summary.expected_paise)} />
            <Stat label="Confirmed" tone="good" value={formatINR(data.summary.confirmed_paise)} />
            <Stat label="Pending check" tone="info" value={formatINR(data.summary.pending_paise)} hint="Not counted yet" />
            <Stat label="Outstanding" tone="warn" value={formatINR(data.summary.outstanding_paise)} />
            <Stat label="Older arrears" tone={data.summary.arrears_paise ? "bad" : "neutral"} value={formatINR(data.summary.arrears_paise)} />
          </div>

          {(pending.data?.length ?? 0) > 0 && (
            <Card title={`Payments awaiting verification (${pending.data!.length})`}>
              <ul className="divide-y divide-line text-sm">
                {pending.data!.map((p) => (
                  <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span>
                      <Link to={`/flats/${p.flat_id}${m}`} className="font-medium hover:underline">{p.flat_label}</Link> · {p.tenant_name} ·{" "}
                      {p.method === "cash" ? "Cash" : "Online"} on {formatDate(p.received_date)} for {p.allocations.map((a) => formatPeriod(a.period, true)).join(", ")}
                      {p.reference && <span className="text-muted"> · ref {p.reference}</span>}
                    </span>
                    <span className="flex items-center gap-2">
                      <Money paise={p.amount_paise} className="font-semibold" />
                      <Button size="sm" variant="primary" onClick={() => confirm(p.id)}><Check className="size-3.5" /> Confirm</Button>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted">Online payments can also be confirmed by matching them in Bank Reconciliation. To reject one, open the flat.</p>
            </Card>
          )}

          {(decisions.data?.length ?? 0) > 0 && (
            <Card title="Extra amounts needing a decision">
              <ul className="text-sm">
                {decisions.data!.map((p) => (
                  <li key={p.id} className="flex justify-between py-1">
                    <Link to={`/flats/${p.flat_id}${m}`} className="hover:underline">{p.flat_label} · {p.tenant_name} · {formatDate(p.received_date)}</Link>
                    <Money paise={p.unallocated_paise} />
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card title={`Rent for ${formatPeriod(period)}`}>
            {data.dues.length === 0 ? <Empty>No rent due this month.</Empty> : (
              <Table>
                <thead><tr><th>Flat</th><th>Tenant</th><th>Due date</th><th className="text-right">Due</th><th className="text-right">Confirmed</th><th className="text-right">Pending</th><th className="text-right">Outstanding</th><th>Status</th></tr></thead>
                <tbody>
                  {data.dues.map((d) => (
                    <tr key={d.id}>
                      <td><Link to={`/flats/${d.flat_id}${m}`} className="font-medium hover:underline">{d.flat_label}</Link><div className="text-xs text-muted">{d.building_name}</div></td>
                      <td>{d.tenant_name}{d.kind === "opening_balance" && <div className="text-xs text-muted">Opening balance</div>}</td>
                      <td>{formatDate(d.due_date)}</td>
                      <td className="text-right"><Money paise={d.amount_paise} /></td>
                      <td className="text-right"><Money paise={d.confirmed_paise} /></td>
                      <td className="text-right"><Money paise={d.pending_paise} /></td>
                      <td className="text-right font-medium"><Money paise={d.outstanding_paise} /></td>
                      <td><StatusBadge status={d.status} overdue={d.is_overdue} /></td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <Card title="Arrears from earlier months">
            {data.arrears.length === 0 ? <Empty>No arrears.</Empty> : (
              <Table>
                <thead><tr><th>Flat</th><th>Tenant</th><th>Month</th><th>Due date</th><th className="text-right">Outstanding</th><th>Status</th></tr></thead>
                <tbody>
                  {data.arrears.map((d) => (
                    <tr key={d.id}>
                      <td><Link to={`/flats/${d.flat_id}?month=${d.period}`} className="font-medium hover:underline">{d.flat_label}</Link></td>
                      <td>{d.tenant_name}</td>
                      <td>{d.kind === "opening_balance" ? "Opening balance" : formatPeriod(d.period)}</td>
                      <td>{formatDate(d.due_date)}</td>
                      <td className="text-right font-medium"><Money paise={d.outstanding_paise} /></td>
                      <td><StatusBadge status={d.status} overdue={d.is_overdue} /></td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
