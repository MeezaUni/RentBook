import { Download, FileSpreadsheet, Printer } from "lucide-react";
import { useState } from "react";
import { Alert, Button, Card, ErrorNote, inputCls, Loading, MonthPicker, PageHeader, Table } from "../components/ui";
import { download, qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatPeriod } from "../lib/money";
import type { Tenant } from "../lib/types";

interface Monthly {
  period: string;
  tables: Record<string, { headers: string[]; rows: string[][] }>;
}

const TITLES: Record<string, string> = {
  summary: "Monthly summary", rent_dues: "Rent due and received", arrears: "Arrears from earlier months",
  receipts: "All rent receipts this month", late_receipts: "Late rent receipts", buildings: "Building-wise collection",
  maintenance_expenses: "Maintenance expenses", maintenance_cash: "Maintenance cash handed over", salary: "Caretaker salary",
  cash_reconciliation: "Family cash reconciliation",
};

/** Display-only formatting of a decimal rupee string from the server (no arithmetic). */
function rupees(c: string): string {
  if (!/^-?\d+(\.\d+)?$/.test(c)) return c;
  const [whole, frac = ""] = c.replace("-", "").split(".");
  const grouped = whole.length > 3
    ? whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + whole.slice(-3)
    : whole;
  return `${c.startsWith("-") ? "-" : ""}${grouped}.${(frac + "00").slice(0, 2)}`;
}

export function ReportsPage() {
  const [period, setPeriod] = usePeriod();
  const { data, error, loading } = useApi<Monthly>(`/api/reports/monthly${qs({ period })}`);
  const tenants = useApi<Tenant[]>("/api/tenants");
  const [tenancyId, setTenancyId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [dlError, setDlError] = useState<string | null>(null);

  async function get(url: string, name: string) {
    setBusy(name);
    setDlError(null);
    try {
      await download(url, name);
    } catch (e) {
      setDlError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy(null);
    }
  }

  const tenancies = (tenants.data ?? []).flatMap((t) => t.tenancies);
  return (
    <>
      <PageHeader title="Reports" subtitle={`Monthly report for ${formatPeriod(period)}. Only confirmed transactions count as received.`}
        actions={<>
          <Button onClick={() => window.print()}><Printer className="size-4" /> Print</Button>
          <Button variant="primary" busy={busy === "xlsx"} onClick={() => get(`/api/reports/export${qs({ period, format: "xlsx" })}`, "xlsx")}>
            <FileSpreadsheet className="size-4" /> Download Excel
          </Button>
          <MonthPicker value={period} onChange={setPeriod} />
        </>} />
      {dlError && <div className="mb-3"><Alert tone="error">{dlError}</Alert></div>}
      <div className="mb-4 grid gap-4 no-print md:grid-cols-2">
        <Card title="Tenant history">
          <div className="flex gap-2">
            <select className={inputCls} value={tenancyId} onChange={(e) => setTenancyId(e.target.value)} aria-label="Tenancy">
              <option value="">Choose tenant…</option>
              {tenancies.map((t) => <option key={t.id} value={t.id}>{t.flat_label} · {t.tenant_name}{t.status === "ended" ? " (former)" : ""}</option>)}
            </select>
            <Button disabled={!tenancyId} busy={busy === "th"}
              onClick={() => get(`/api/reports/export${qs({ report: "tenant_history", tenancy_id: tenancyId, format: "xlsx" })}`, "th")}>
              <Download className="size-4" /> Excel
            </Button>
          </div>
        </Card>
        <Card title="All records (independent of this app)">
          <p className="mb-2 text-sm text-muted">Every table as plain CSV files in a zip. Proof documents and statements are not included.</p>
          <Button busy={busy === "full"} onClick={() => get("/api/reports/full-export", "full")}><Download className="size-4" /> Download records</Button>
        </Card>
      </div>
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      {data && (
        <div className="flex flex-col gap-4">
          <div className="hidden print:block">
            <h1 className="text-xl font-semibold">Rentbook — monthly report {formatPeriod(period)}</h1>
            <p className="text-xs">Printed {new Date().toLocaleString("en-IN")}. Only confirmed transactions count as received; pending and reversed are listed separately.</p>
          </div>
          {Object.entries(data.tables).map(([key, t]) => (
            <Card key={key} title={TITLES[key] ?? key} className="break-inside-avoid"
              actions={<Button size="sm" variant="ghost" busy={busy === key}
                onClick={() => get(`/api/reports/export${qs({ period, report: key, format: "csv" })}`, key)}><Download className="size-3.5" /> CSV</Button>}>
              {t.rows.length === 0 ? <p className="text-sm text-muted">Nothing for this month.</p> : (
                <Table>
                  <thead><tr>{t.headers.map((h) => <th key={h} className={h.includes("₹") ? "text-right" : ""}>{h}</th>)}</tr></thead>
                  <tbody>{t.rows.map((r, i) => (
                    <tr key={i}>{r.map((c, j) => (
                      <td key={j} className={t.headers[j]?.includes("₹") ? "money text-right" : ""}>
                        {t.headers[j]?.includes("₹") ? rupees(c) : c}
                      </td>
                    ))}</tr>
                  ))}</tbody>
                </Table>
              )}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
