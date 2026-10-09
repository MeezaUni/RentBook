import { ArrowRightLeft, Calculator, HandCoins, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { ACCOUNT_LABELS, CountForm, MovementForm, type Receipt } from "../components/CashForms";
import { ReasonDialog } from "../components/ReasonDialog";
import { Alert, Button, Card, cx, Empty, ErrorNote, Loading, Money, MonthPicker, PageHeader, StatusBadge, Stat, Table } from "../components/ui";
import { api, qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatDate, formatINR, formatPeriod } from "../lib/money";
import type { CashMovement, Discrepancy } from "../lib/types";

interface Cashbook {
  period: string;
  opening_paise: number;
  cash_rent_received_paise: number;
  other_cash_in_paise: number;
  handed_to_caretaker_paise: number;
  other_cash_out_paise: number;
  family_paid_maintenance_cash_paise: number;
  closing_paise: number;
  current_balance_paise: number;
  received_by_tenant: { tenant: string; amount_paise: number }[];
  receipts: Receipt[];
  movements: CashMovement[];
  discrepancies: Discrepancy[];
}

export function CashbookPage() {
  const [period, setPeriod] = usePeriod();
  const { data, error, loading, reload } = useApi<Cashbook>(`/api/cash/cashbook${qs({ period })}`);
  const [form, setForm] = useState<null | { kind?: string; source?: Receipt }>(null);
  const [count, setCount] = useState(false);
  const [reverse, setReverse] = useState<CashMovement | null>(null);
  return (
    <>
      <PageHeader title="Cashbook" subtitle={`Cash held by the family — ${formatPeriod(period)}`}
        actions={<>
          <Button onClick={() => setCount(true)}><Calculator className="size-4" /> Count cash</Button>
          <Button variant="primary" onClick={() => setForm({})}><ArrowRightLeft className="size-4" /> Record cash movement</Button>
          <MonthPicker value={period} onChange={setPeriod} />
        </>} />
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      {data && (
        <div className="flex flex-col gap-4">
          {data.discrepancies.map((d, i) => <Alert key={i} tone="warn" title={`Discrepancy ${formatINR(d.amount_paise)}`}>{d.message}</Alert>)}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Start of month" value={formatINR(data.opening_paise)} />
            <Stat label="Cash rent received" tone="good" value={formatINR(data.cash_rent_received_paise)}
              hint={data.other_cash_in_paise ? `+ ${formatINR(data.other_cash_in_paise)} other cash in` : "Confirmed receipts"} />
            <Stat label="Handed to caretaker" value={formatINR(data.handed_to_caretaker_paise)}
              hint={`Other out ${formatINR(data.other_cash_out_paise)} · maintenance paid ${formatINR(data.family_paid_maintenance_cash_paise)}`} />
            <Stat label="End of month" tone={data.closing_paise < 0 ? "bad" : "neutral"} value={formatINR(data.closing_paise)}
              hint={`Held now: ${formatINR(data.current_balance_paise)}`} />
          </div>

          <Card title="Cash rent receipts">
            {data.receipts.length === 0 ? <Empty>No cash rent received this month.</Empty> : (
              <Table>
                <thead><tr><th>Date</th><th>Flat · tenant</th><th>For</th><th className="text-right">Amount</th><th>Status</th><th className="text-right">Used</th><th className="text-right">With family</th><th /></tr></thead>
                <tbody>{data.receipts.map((r) => (
                  <tr key={r.payment_id}>
                    <td className="whitespace-nowrap">{formatDate(r.received_date)}</td>
                    <td>{r.flat_label} · {r.tenant_name}<div className="text-xs text-muted">recorded by {r.recorded_by}</div></td>
                    <td>{r.periods.map((p) => formatPeriod(p, true)).join(", ")}</td>
                    <td className="text-right"><Money paise={r.amount_paise} /></td>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="text-right text-xs">
                      <Money paise={r.allocated_paise} />
                      {r.links.map((l) => <div key={l.cash_movement_id} className="text-muted">{l.kind_label.split(" ")[0]} {formatDate(l.date)}</div>)}
                    </td>
                    <td className="text-right font-medium"><Money paise={r.with_family_paise} /></td>
                    <td className="text-right">
                      {r.with_family_paise > 0 && (
                        <Button size="sm" onClick={() => setForm({ kind: "caretaker_handover", source: r })}><HandCoins className="size-3.5" /> Use</Button>
                      )}
                    </td>
                  </tr>
                ))}</tbody>
              </Table>
            )}
            {data.received_by_tenant.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted">
                {data.received_by_tenant.map((t) => <span key={t.tenant}>{t.tenant}: <span className="money text-ink">{formatINR(t.amount_paise)}</span></span>)}
              </div>
            )}
          </Card>

          <Card title="Cash movements">
            {data.movements.length === 0 ? <Empty>No cash movements this month.</Empty> : (
              <Table>
                <thead><tr><th>Date</th><th>What</th><th>From → To</th><th className="text-right">Amount</th><th>Notes</th><th /></tr></thead>
                <tbody>{data.movements.map((m) => (
                  <tr key={m.id} className={cx(m.status === "reversed" && "text-muted line-through")}>
                    <td className="whitespace-nowrap">{formatDate(m.movement_date)}</td>
                    <td>{m.kind_label}</td>
                    <td className="text-xs">{ACCOUNT_LABELS[m.from_account]} → {ACCOUNT_LABELS[m.to_account]}</td>
                    <td className="text-right"><Money paise={m.amount_paise} /></td>
                    <td className="text-xs">{m.description}{m.sources.length > 0 && ` (from ${m.sources.length} receipt${m.sources.length > 1 ? "s" : ""})`}
                      {m.status === "reversed" && <div className="no-underline">Reversed: {m.reversed_reason}</div>}</td>
                    <td className="text-right">
                      {m.status === "active" && m.kind !== "opening_balance" && (
                        <button className="rounded p-1 text-red-700 hover:bg-red-50" aria-label="Reverse movement" onClick={() => setReverse(m)}><Undo2 className="size-4" /></button>
                      )}
                    </td>
                  </tr>
                ))}</tbody>
              </Table>
            )}
            <p className="mt-2 text-xs text-muted">Caretaker expenses and top-ups are tracked on the <Link className="underline" to={`/maintenance?month=${period}`}>Maintenance</Link> page.</p>
          </Card>
        </div>
      )}
      {form && <MovementForm initialKind={form.kind} initialSource={form.source} onClose={() => setForm(null)} onDone={reload} />}
      {count && <CountForm account="family_cash" onClose={() => setCount(false)} onDone={reload} />}
      {reverse && (
        <ReasonDialog title="Reverse cash movement?" danger confirmLabel="Reverse" onClose={() => setReverse(null)}
          description={`${reverse.kind_label}, ${formatINR(reverse.amount_paise)} on ${formatDate(reverse.movement_date)}. It stays in the history as reversed.`}
          onSubmit={async (reason) => { await api.post(`/api/cash/movements/${reverse.id}/reverse`, { reason }); reload(); }} />
      )}
    </>
  );
}
