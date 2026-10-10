import { AlertTriangle, Ban, CheckCircle2, FileUp, Info, Link2, Plus, RotateCcw, Split, Trash2, Undo2, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ReasonDialog } from "../components/ReasonDialog";
import {
  Alert, Button, Card, cx, Empty, ErrorNote, Field, inputCls, Loading, Modal, Money, PageHeader, StatusBadge, Stat, Table,
} from "../components/ui";
import { api, ApiError, download } from "../lib/api";
import { useApi } from "../lib/hooks";
import { currentPeriod, formatDate, formatDateTime, formatINR, formatPeriod, paiseToInput, parseRupees } from "../lib/money";
import type { BankTxn, Candidate, Due, Tenancy, Tenant } from "../lib/types";

type Tab = "review" | "import" | "history";

export function BankPage() {
  const [tab, setTab] = useState<Tab>("review");
  const [openImport, setOpenImport] = useState<number | null>(null);
  return (
    <>
      <PageHeader title="Bank Reconciliation" subtitle="Import HDFC statements and match credits to rent. Nothing is marked paid until you confirm." />
      <div className="mb-4 flex gap-1 rounded-lg border border-line bg-white p-1 text-sm no-print" role="tablist">
        {([["review", "Review credits"], ["import", "Import statement"], ["history", "Past imports"]] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => { setTab(k); setOpenImport(null); }}
            className={cx("flex-1 rounded-md px-3 py-1.5 font-medium", tab === k ? "bg-teal-50 text-teal-900" : "hover:bg-stone-100")}>
            {l}
          </button>
        ))}
      </div>
      {tab === "review" && <ReviewQueue />}
      {tab === "import" && <ImportWizard onDone={() => setTab("review")} />}
      {tab === "history" && (openImport ? <ImportDetail id={openImport} onBack={() => setOpenImport(null)} /> : <ImportHistory onOpen={setOpenImport} />)}
    </>
  );
}

// ------------------------------------------------------------------ review

function useTenancies() {
  const { data } = useApi<Tenant[]>("/api/tenants");
  return useMemo(() => (data ?? []).flatMap((t) => t.tenancies).sort((a, b) => a.flat_label.localeCompare(b.flat_label)), [data]);
}

function ReviewQueue() {
  const { data, error, loading, reload } = useApi<BankTxn[]>("/api/bank/transactions?status=needs_review&kind=credit&suggestions=true");
  const summary = useApi<Record<string, number>>("/api/bank/summary");
  const tenancies = useTenancies();
  const refresh = () => { reload(); summary.reload(); };
  return (
    <div className="flex flex-col gap-4">
      {summary.data && <SummaryStats s={summary.data} />}
      <ErrorNote error={error} />
      {loading && !data && <Loading label="Matching transactions…" />}
      {data && data.length === 0 && <Alert tone="success">All imported credits have been reviewed.</Alert>}
      {data?.map((t) => <TxnCard key={t.id} txn={t} tenancies={tenancies} onChange={refresh} />)}
    </div>
  );
}

function SummaryStats({ s }: { s: Record<string, number> }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Stat label="Credits imported" value={formatINR(s.total_credits_paise)} hint={`${s.credit_count} credits · debits ${formatINR(s.total_debits_paise)}`} />
      <Stat label="Allocated to rent" tone="good" value={formatINR(s.allocated_to_rent_confirmed_paise)} hint="Confirmed allocations" />
      <Stat label="Awaiting review" tone={s.awaiting_review_count ? "info" : "neutral"} value={formatINR(s.awaiting_review_paise)}
        hint={`${s.awaiting_review_count} credits · ${s.unmatched_count} untouched`} />
      <Stat label="Not rent" value={formatINR(s.not_rent_paise)} hint={`${s.not_rent_count} credits${s.collision_count ? ` · ${s.collision_count} identical rows flagged` : ""}`} />
    </div>
  );
}

function TxnCard({ txn, tenancies, onChange }: { txn: BankTxn; tenancies: Tenancy[]; onChange: () => void }) {
  const [alloc, setAlloc] = useState<Candidate | "manual" | null>(null);
  const [notRent, setNotRent] = useState<"all" | "rest" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const s = txn.suggestion;
  async function link(paymentId: number) {
    setError(null);
    try {
      await api.post(`/api/bank/transactions/${txn.id}/link-payment`, { payment_id: paymentId });
      onChange();
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
  }
  return (
    <Card>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-xs text-muted">{formatDate(txn.txn_date)}{txn.reference && ` · ref ${txn.reference}`}</div>
            <div className="break-words font-mono text-sm">{txn.narration}</div>
            {txn.collision_flag && <div className="mt-1 text-xs text-amber-800">An identical row appears more than once in the statement — check it is not a duplicate.</div>}
          </div>
          <div className="text-right">
            <Money paise={txn.credit_paise} className="text-lg font-semibold text-emerald-700" />
            {txn.allocated_paise > 0 && <div className="text-xs text-muted">{formatINR(txn.remaining_paise)} left to allocate</div>}
            <div className="mt-1 flex justify-end gap-1"><StatusBadge status={txn.review_status} />{s && <StatusBadge status={s.category} />}</div>
          </div>
        </div>

        {txn.payments.length > 0 && (
          <div className="rounded-lg bg-emerald-50 p-2 text-sm text-emerald-900">
            Already allocated: {txn.payments.filter((p) => p.status === "confirmed" || p.status === "pending")
              .map((p) => `${p.flat_label} ${p.periods.map((x) => formatPeriod(x, true)).join("+") || "credit"} ${formatINR(p.amount_paise)}`).join("; ")}
          </div>
        )}

        {s?.duplicate_payments.map((d) => (
          <Alert key={d.payment_id} tone="warn" title="This may already be recorded">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>Payment #{d.payment_id}: {d.flat_label} · {d.tenant_name} · {formatINR(d.amount_paise)} on {formatDate(d.received_date)} ({d.status})
                {d.linked_to_bank_transaction && " — already linked to another bank transaction"}</span>
              {d.can_link && <Button size="sm" onClick={() => link(d.payment_id)}><Link2 className="size-3.5" /> It's this payment — link & confirm</Button>}
            </div>
          </Alert>
        ))}

        {s && s.candidates.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="text-xs font-semibold uppercase text-muted">Suggestions (not applied until you confirm)</div>
            {s.candidates.map((c) => (
              <div key={c.tenancy_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line p-2.5 text-sm">
                <div>
                  <div className="font-medium">{c.flat_label} · {c.tenant_name} <span className="text-xs text-muted">({c.building_name})</span></div>
                  <ul className="text-xs text-muted">{c.reasons.map((r) => <li key={r}>• {r}</li>)}</ul>
                  <div className="text-xs">Outstanding {formatINR(c.outstanding_paise)}
                    {c.proposed_allocations.length > 0 && ` · proposes ${c.proposed_allocations.map((p) => `${p.kind === "opening_balance" ? "opening bal." : formatPeriod(p.period, true)} ${formatINR(p.amount_paise)}`).join(", ")}`}
                  </div>
                </div>
                <Button size="sm" variant={s.category === "strong" ? "primary" : "secondary"} onClick={() => setAlloc(c)}>Review & use</Button>
              </div>
            ))}
          </div>
        )}
        {s?.category === "no_match" && <p className="text-sm text-muted">No tenant matched automatically. Allocate it yourself or mark it as not rent.</p>}

        <div className="flex flex-wrap gap-2 border-t border-line pt-3">
          <Button size="sm" onClick={() => setAlloc("manual")}><Split className="size-3.5" /> Allocate manually</Button>
          {txn.allocated_paise > 0
            ? <Button size="sm" onClick={() => setNotRent("rest")}><Ban className="size-3.5" /> Rest is not rent</Button>
            : <Button size="sm" onClick={() => setNotRent("all")}><Ban className="size-3.5" /> Not rent</Button>}
          <span className="self-center text-xs text-muted">Or leave it here for later.</span>
        </div>
        {error && <Alert tone="error">{error}</Alert>}
      </div>
      {alloc && <AllocateDialog txn={txn} tenancies={tenancies} candidate={alloc === "manual" ? null : alloc}
        onClose={() => setAlloc(null)} onDone={onChange} />}
      {notRent && (
        <ReasonDialog title={notRent === "all" ? "Mark as not rent" : "Remaining amount is not rent"}
          description="It will not be counted as rent. You can reopen it later from Past imports."
          onClose={() => setNotRent(null)}
          onSubmit={async (reason) => { await api.post(`/api/bank/transactions/${txn.id}/${notRent === "all" ? "not-rent" : "remainder-not-rent"}`, { reason }); onChange(); }} />
      )}
    </Card>
  );
}

interface Row { key: number; tenancyId: string; target: string; period: string; amount: string }

let rowKey = 1;

function AllocateDialog({ txn, tenancies, candidate, onClose, onDone }: {
  txn: BankTxn; tenancies: Tenancy[]; candidate: Candidate | null; onClose: () => void; onDone: () => void;
}) {
  const initial: Row[] = candidate?.proposed_allocations.length
    ? candidate.proposed_allocations.map((p) => ({ key: rowKey++, tenancyId: String(candidate.tenancy_id), target: `due:${p.rent_due_id}`, period: p.period, amount: paiseToInput(p.amount_paise) }))
    : [{ key: rowKey++, tenancyId: candidate ? String(candidate.tenancy_id) : "", target: "period", period: txn.txn_date.slice(0, 7), amount: paiseToInput(txn.remaining_paise) }];
  if (candidate && candidate.unallocated_paise > 0 && candidate.proposed_allocations.length)
    initial.push({ key: rowKey++, tenancyId: String(candidate.tenancy_id), target: "credit", period: "", amount: paiseToInput(candidate.unallocated_paise) });
  const [rows, setRows] = useState<Row[]>(initial);
  const [saveAlias, setSaveAlias] = useState(Boolean(txn.sender) && (candidate?.identity_signal ?? 0) < 3);
  const [aliasText, setAliasText] = useState(txn.sender ?? "");
  const [restNotRent, setRestNotRent] = useState("");
  const [step, setStep] = useState<"edit" | "confirm">("edit");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outstanding, setOutstanding] = useState<Record<string, Due[]>>({});

  useEffect(() => {
    for (const r of rows) {
      if (r.tenancyId && !outstanding[r.tenancyId]) {
        api.get<Due[]>(`/api/tenancies/${r.tenancyId}/outstanding?through=${currentPeriod()}`)
          .then((d) => setOutstanding((o) => ({ ...o, [r.tenancyId]: d }))).catch(() => undefined);
      }
    }
  }, [rows, outstanding]);

  const parsed = rows.map((r) => ({ ...r, paise: parseRupees(r.amount) }));
  const total = parsed.reduce((s, r) => s + (r.paise ?? 0), 0);
  const invalid = parsed.some((r) => !r.tenancyId || !r.paise || (r.target === "period" && !/^\d{4}-\d{2}$/.test(r.period)));
  const over = total > txn.remaining_paise;
  const left = txn.remaining_paise - total;
  const tenancyOf = (id: string) => tenancies.find((t) => String(t.id) === id);
  const aliasTenant = tenancyOf(rows[0]?.tenancyId ?? "");

  function update(key: number, patch: Partial<Row>) {
    setRows(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/bank/transactions/${txn.id}/allocate`, {
        confirm: true,
        allocations: parsed.map((r) => ({
          tenancy_id: Number(r.tenancyId), amount_paise: r.paise,
          ...(r.target.startsWith("due:") ? { rent_due_id: Number(r.target.slice(4)) }
            : r.target === "credit" ? { hold_as_credit: true } : { period: r.period }),
        })),
        save_alias: saveAlias && aliasTenant && aliasText.trim().length >= 3 ? { tenant_id: aliasTenant.tenant_id, alias: aliasText.trim() } : null,
        remainder_not_rent_reason: left > 0 && restNotRent.trim() ? restNotRent.trim() : null,
      });
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed");
      setStep("edit");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={step === "edit" ? "Allocate bank credit to rent" : "Confirm allocation"} onClose={onClose} wide footer={step === "edit" ? <>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" disabled={invalid || over} onClick={() => setStep("confirm")}>Review</Button>
    </> : <>
      <Button onClick={() => setStep("edit")}>Back</Button>
      <Button variant="primary" busy={busy} onClick={apply}><CheckCircle2 className="size-4" /> Confirm and record</Button>
    </>}>
      <div className="mb-3 rounded-lg bg-stone-50 p-3 text-sm">
        <div className="font-mono text-xs">{txn.narration}</div>
        <div className="mt-1">{formatDate(txn.txn_date)} · credit <b className="money">{formatINR(txn.credit_paise)}</b> · available <b className="money">{formatINR(txn.remaining_paise)}</b></div>
      </div>
      {step === "edit" ? (
        <div className="flex flex-col gap-3">
          {rows.map((r) => {
            const outs = outstanding[r.tenancyId] ?? [];
            return (
              <div key={r.key} className="grid gap-2 rounded-lg border border-line p-2.5 sm:grid-cols-[1.4fr_1.4fr_1fr_auto]">
                <select className={inputCls} value={r.tenancyId} aria-label="Tenancy"
                  onChange={(e) => update(r.key, { tenancyId: e.target.value, target: "period" })}>
                  <option value="">Choose flat / tenant…</option>
                  {tenancies.map((t) => <option key={t.id} value={t.id}>{t.flat_label} · {t.tenant_name}{t.status === "ended" ? " (former)" : ""}</option>)}
                </select>
                <div className="flex gap-1">
                  <select className={inputCls} value={r.target} aria-label="For" onChange={(e) => update(r.key, { target: e.target.value })}>
                    <option value="period">Rent month…</option>
                    {outs.map((d) => <option key={d.id} value={`due:${d.id}`}>{d.kind === "opening_balance" ? "Opening balance" : formatPeriod(d.period)} (owes {formatINR(d.available_paise)})</option>)}
                    <option value="credit">Hold as advance credit</option>
                  </select>
                  {r.target === "period" && <input type="month" className={inputCls} value={r.period} aria-label="Month" onChange={(e) => update(r.key, { period: e.target.value })} />}
                </div>
                <input className={inputCls} inputMode="decimal" value={r.amount} aria-label="Amount" onChange={(e) => update(r.key, { amount: e.target.value })} />
                <button className="justify-self-end rounded p-2 text-red-700 hover:bg-red-50 disabled:opacity-30" disabled={rows.length === 1}
                  aria-label="Remove row" onClick={() => setRows(rows.filter((x) => x.key !== r.key))}><Trash2 className="size-4" /></button>
              </div>
            );
          })}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button size="sm" onClick={() => setRows([...rows, { key: rowKey++, tenancyId: rows[0]?.tenancyId ?? "", target: "period", period: txn.txn_date.slice(0, 7), amount: paiseToInput(Math.max(0, left)) }])}>
              <Plus className="size-3.5" /> Split across another month / flat
            </Button>
            <span className={cx("text-sm", over ? "text-red-700" : "text-muted")}>
              Allocating <b className="money">{formatINR(total)}</b> of {formatINR(txn.remaining_paise)}
              {over && " — more than the credit!"}
            </span>
          </div>
          {left > 0 && !over && (
            <Field label={`Remaining ${formatINR(left)}`} hint="Leave blank to keep it for later review, or describe what it is to mark it as not rent.">
              {(id) => <input id={id} className={inputCls} placeholder="e.g. part is a deposit refund" value={restNotRent} onChange={(e) => setRestNotRent(e.target.value)} />}
            </Field>
          )}
          {txn.sender && (
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={saveAlias} onChange={(e) => setSaveAlias(e.target.checked)} />
              <span>Remember this sender for {aliasTenant?.tenant_name ?? "the tenant"} in future statements:
                <input className={cx(inputCls, "mt-1")} value={aliasText} onChange={(e) => setAliasText(e.target.value)} aria-label="Sender name to remember" /></span>
            </label>
          )}
          {error && <Alert tone="error">{error}</Alert>}
        </div>
      ) : (
        <div className="flex flex-col gap-2 text-sm">
          <p>The following confirmed online rent payments will be recorded, dated {formatDate(txn.txn_date)} and linked to this bank transaction:</p>
          <ul className="list-disc pl-5">
            {parsed.map((r) => {
              const t = tenancyOf(r.tenancyId);
              const due = (outstanding[r.tenancyId] ?? []).find((d) => `due:${d.id}` === r.target);
              const what = r.target === "credit" ? "advance credit" : due ? (due.kind === "opening_balance" ? "opening balance" : formatPeriod(due.period)) : formatPeriod(r.period);
              return <li key={r.key}><b>{formatINR(r.paise ?? 0)}</b> — {t?.flat_label} · {t?.tenant_name} — {what}</li>;
            })}
          </ul>
          {left > 0 && <p>{formatINR(left)} {restNotRent.trim() ? `will be marked as not rent (“${restNotRent.trim()}”)` : "stays unallocated for later review"}.</p>}
          {saveAlias && aliasText.trim() && <p>Sender “{aliasText.trim()}” will be remembered for {aliasTenant?.tenant_name}.</p>}
        </div>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ import

interface Mapping { header_row: number | null; columns: Record<string, number>; date_format: string; valid?: boolean }
interface Preview {
  sheets: string[]; sheet: string | null; mapping: Mapping; fields: string[]; header: string[]; raw_preview: string[][];
  file_previously_imported: { id: number; uploaded_at: string; name: string }[];
  summary: { detected: number; valid: number; credits: number; debits: number; credit_total_paise: number;
    debit_total_paise: number; already_imported: number; possible_duplicates: number; identical_rows: number;
    errors: number; potential_rent: number; period_start: string | null; period_end: string | null };
  statement: StatementInfo;
  checks: StatementCheck[];
  errors: { row_number: number; messages: string[] }[];
  rows: { row_number: number; txn_date: string; narration: string; reference: string | null; debit_paise: number;
    credit_paise: number; balance_paise: number | null; flags: string[]; likely_rent: boolean }[];
}

interface StatementInfo {
  period_start: string | null; period_end: string | null; opening_paise: number | null; closing_paise: number | null;
  debit_total_paise: number | null; credit_total_paise: number | null; debit_count: number | null; credit_count: number | null;
}
interface StatementCheck { code: string; status: "ok" | "warn" | "fail" | "info"; message: string; rows?: number[] }

/** The importer's cross-checks against the bank's own summary (counts, totals, running balance, period). */
function StatementChecks({ statement, checks }: { statement: StatementInfo | null; checks: StatementCheck[] }) {
  if (!checks.length && !statement?.period_start) return null;
  const icon = { ok: <CheckCircle2 className="size-4 text-emerald-700" />, info: <Info className="size-4 text-sky-700" />,
    warn: <AlertTriangle className="size-4 text-amber-700" />, fail: <XCircle className="size-4 text-red-700" /> };
  const label = { ok: "Passed", info: "Note", warn: "Check", fail: "Problem" };
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="mb-1.5 text-sm font-semibold">Statement checks
        {statement?.period_start && <span className="ml-2 font-normal text-muted">Statement period {formatDate(statement.period_start)} – {formatDate(statement.period_end)}</span>}
      </div>
      <ul className="flex flex-col gap-1 text-sm">
        {checks.map((c) => (
          <li key={c.code} className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0" aria-hidden>{icon[c.status]}</span>
            <span><span className="sr-only">{label[c.status]}: </span>{c.message}</span>
          </li>
        ))}
        {!checks.some((c) => c.code === "counts") && (
          <li className="text-xs text-muted">No bank summary block was found, so counts could not be cross-checked.</li>
        )}
      </ul>
    </div>
  );
}

const FIELD_LABELS: Record<string, string> = {
  date: "Transaction date *", narration: "Narration / description *", reference: "Reference (Chq./Ref.No.)",
  value_date: "Value date", debit: "Withdrawal (debit)", credit: "Deposit (credit)", balance: "Closing balance",
  amount: "Single amount column", dr_cr: "Dr/Cr indicator",
};

function ImportWizard({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [sheet, setSheet] = useState("");
  const [profileId, setProfileId] = useState("");
  const [acceptErrors, setAcceptErrors] = useState(false);
  const [skipPossible, setSkipPossible] = useState(true);
  const [profileName, setProfileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ rows_new: number; rows_duplicate: number; rows_error: number } | null>(null);
  const profiles = useApi<{ id: number; name: string }[]>("/api/bank/profiles");

  async function runPreview(m: Mapping | null, sh: string, f = file) {
    if (!f) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", f);
    if (sh) form.append("sheet", sh);
    if (m) form.append("mapping", JSON.stringify(m));
    else if (profileId) form.append("profile_id", profileId);
    try {
      const p = await api.upload<Preview>("/api/bank/preview", form);
      setPreview(p);
      setMapping(p.mapping);
      setSheet(p.sheet ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read the file");
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!file || !mapping) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    if (sheet) form.append("sheet", sheet);
    form.append("mapping", JSON.stringify({ header_row: mapping.header_row, columns: mapping.columns, date_format: mapping.date_format }));
    form.append("accept_errors", String(acceptErrors));
    form.append("skip_possible_duplicates", String(skipPossible));
    if (profileName.trim()) form.append("save_profile_as", profileName.trim());
    try {
      setResult(await api.upload("/api/bank/imports", form));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <Card>
        <Alert tone="success" title="Statement imported">
          {result.rows_new} new transactions · {result.rows_duplicate} already known (skipped) · {result.rows_error} unreadable rows skipped.
          No rent has been marked as paid — review the credits next.
        </Alert>
        <div className="mt-3 flex gap-2">
          <Button variant="primary" onClick={onDone}>Review credits</Button>
          <Button onClick={() => { setResult(null); setPreview(null); setFile(null); }}>Import another</Button>
        </div>
      </Card>
    );
  }

  const ncols = Math.max(0, ...(preview?.raw_preview.map((r) => r.length) ?? [0]));
  const headerRow = mapping?.header_row ?? null;
  const colName = (i: number) => (headerRow !== null && preview?.raw_preview[headerRow]?.[i]) || `Column ${i + 1}`;
  const s = preview?.summary;

  return (
    <div className="flex flex-col gap-4">
      <Card title="1. Choose the statement file">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Statement (CSV, XLSX or XLS)">
            {(id) => <input id={id} type="file" accept=".csv,.xlsx,.xls,.txt" className="text-sm"
              onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); setPreview(null); if (f) void runPreview(null, "", f); }} />}
          </Field>
          {(profiles.data?.length ?? 0) > 0 && (
            <Field label="Saved column layout (optional)">
              {(id) => (
                <select id={id} className={inputCls} value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                  <option value="">Detect automatically</option>
                  {profiles.data!.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              )}
            </Field>
          )}
          {file && <Button busy={busy} onClick={() => runPreview(null, sheet)}><RotateCcw className="size-4" /> Re-read</Button>}
        </div>
        {busy && !preview && <Loading label="Reading statement…" />}
        {error && <div className="mt-3"><Alert tone="error">{error}</Alert></div>}
      </Card>

      {preview && mapping && (
        <>
          {preview.file_previously_imported.length > 0 && (
            <Alert tone="warn" title="This exact file was imported before">
              On {preview.file_previously_imported.map((p) => formatDateTime(p.uploaded_at)).join(", ")}. Importing again is safe — known transactions are skipped.
            </Alert>
          )}
          <Card title="2. Check the columns">
            <div className="grid gap-3 sm:grid-cols-3">
              {preview.sheets.length > 1 && (
                <Field label="Sheet">
                  {(id) => (
                    <select id={id} className={inputCls} value={sheet} onChange={(e) => { setSheet(e.target.value); void runPreview(null, e.target.value); }}>
                      {preview.sheets.map((x) => <option key={x}>{x}</option>)}
                    </select>
                  )}
                </Field>
              )}
              <Field label="Header row" hint="The row with column titles.">
                {(id) => (
                  <select id={id} className={inputCls} value={headerRow ?? ""} onChange={(e) => setMapping({ ...mapping, header_row: e.target.value === "" ? null : Number(e.target.value) })}>
                    <option value="">None</option>
                    {preview.raw_preview.map((r, i) => <option key={i} value={i}>Row {i + 1}: {r.filter(Boolean).slice(0, 3).join(" | ").slice(0, 50)}</option>)}
                  </select>
                )}
              </Field>
              <Field label="Date format" hint="Indian statements are day-first.">
                {(id) => (
                  <select id={id} className={inputCls} value={mapping.date_format} onChange={(e) => setMapping({ ...mapping, date_format: e.target.value })}>
                    <option value="auto">Automatic (day first)</option>
                    <option value="%d/%m/%y">DD/MM/YY</option><option value="%d/%m/%Y">DD/MM/YYYY</option>
                    <option value="%d-%m-%Y">DD-MM-YYYY</option><option value="%d-%b-%Y">DD-Mon-YYYY</option><option value="%Y-%m-%d">YYYY-MM-DD</option>
                  </select>
                )}
              </Field>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              {preview.fields.map((f) => (
                <Field key={f} label={FIELD_LABELS[f] ?? f}>
                  {(id) => (
                    <select id={id} className={inputCls} value={mapping.columns[f] ?? ""}
                      onChange={(e) => {
                        const cols = { ...mapping.columns };
                        if (e.target.value === "") delete cols[f]; else cols[f] = Number(e.target.value);
                        setMapping({ ...mapping, columns: cols });
                      }}>
                      <option value="">—</option>
                      {Array.from({ length: ncols }, (_, i) => <option key={i} value={i}>{colName(i)}</option>)}
                    </select>
                  )}
                </Field>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <Button busy={busy} onClick={() => runPreview(mapping, sheet)}>Apply columns & preview</Button>
              {!mapping.valid && <span className="text-sm text-red-700">Choose at least Date, Narration and the amount column(s), then apply.</span>}
            </div>
            <details className="mt-3 text-xs">
              <summary className="cursor-pointer text-muted">Show the first rows of the file</summary>
              <div className="mt-2 max-h-64 overflow-auto rounded border border-line">
                <table className="text-xs">
                  <tbody>{preview.raw_preview.map((r, i) => (
                    <tr key={i} className={cx(i === headerRow && "bg-teal-50 font-semibold")}><td className="px-1 text-muted">{i + 1}</td>
                      {r.map((c, j) => <td key={j} className="whitespace-nowrap border-l border-line px-1">{c}</td>)}</tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
          </Card>

          {mapping.valid && s && (
            <Card title="3. Review before importing">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Stat label="Transactions" value={String(s.valid)} hint={s.period_start ? `${formatDate(s.period_start)} – ${formatDate(s.period_end)}` : undefined} />
                <Stat label="Credits" tone="good" value={formatINR(s.credit_total_paise)} hint={`${s.credits} credits · ${s.potential_rent} look like rent`} />
                <Stat label="Debits" value={formatINR(s.debit_total_paise)} hint={`${s.debits} debits`} />
                <Stat label="Already imported" tone={s.already_imported ? "info" : "neutral"} value={String(s.already_imported)}
                  hint={`${s.possible_duplicates} possible duplicates · ${s.identical_rows} identical rows`} />
              </div>
              <div className="mt-3"><StatementChecks statement={preview.statement} checks={preview.checks} /></div>
              {preview.checks.some((c) => c.status === "fail") && (
                <div className="mt-3"><Alert tone="error" title="The file does not fully match the bank's own summary">
                  Some rows may be missing or mis-read. Check the column choices above before importing.</Alert></div>
              )}
              {s.errors > 0 && (
                <div className="mt-3">
                  <Alert tone="warn" title={`${s.errors} rows could not be read`}>
                    <ul className="max-h-32 overflow-y-auto text-xs">{preview.errors.map((e) => <li key={e.row_number}>Row {e.row_number}: {e.messages.join("; ")}</li>)}</ul>
                    <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={acceptErrors} onChange={(e) => setAcceptErrors(e.target.checked)} /> Skip these rows and import the rest</label>
                  </Alert>
                </div>
              )}
              <div className="mt-3 max-h-96 overflow-auto">
                <Table>
                  <thead><tr><th>Row</th><th>Date</th><th>Narration</th><th className="text-right">Debit</th><th className="text-right">Credit</th><th>Notes</th></tr></thead>
                  <tbody>{preview.rows.map((r) => (
                    <tr key={r.row_number} className={cx(r.flags.includes("already_imported") && "text-muted")}>
                      <td>{r.row_number}</td><td className="whitespace-nowrap">{formatDate(r.txn_date)}</td>
                      <td className="max-w-md truncate font-mono text-xs" title={r.narration}>{r.narration}</td>
                      <td className="text-right">{r.debit_paise ? <Money paise={r.debit_paise} /> : ""}</td>
                      <td className="text-right">{r.credit_paise ? <Money paise={r.credit_paise} className="text-emerald-700" /> : ""}</td>
                      <td className="text-xs">
                        {r.flags.includes("already_imported") && "Already imported. "}
                        {r.flags.includes("possible_duplicate") && <span className="text-amber-800">Possible duplicate. </span>}
                        {r.flags.includes("outside_statement_period") && <span className="text-sky-800">Dated outside statement period. </span>}
                        {r.flags.includes("identical_rows_in_file") && <span className="text-amber-800">Repeated row. </span>}
                        {r.likely_rent && <span className="text-emerald-800">Looks like rent.</span>}
                      </td>
                    </tr>
                  ))}</tbody>
                </Table>
              </div>
              <div className="mt-3 flex flex-col gap-2 text-sm">
                {s.possible_duplicates > 0 && (
                  <label className="flex items-center gap-2"><input type="checkbox" checked={skipPossible} onChange={(e) => setSkipPossible(e.target.checked)} />
                    Skip possible duplicates (same date, amount and reference as an existing transaction)</label>
                )}
                <Field label="Save this column layout as (optional)">
                  {(id) => <input id={id} className={inputCls} placeholder="e.g. HDFC savings XLS" value={profileName} onChange={(e) => setProfileName(e.target.value)} />}
                </Field>
                <div>
                  <Button variant="primary" busy={busy} disabled={s.errors > 0 && !acceptErrors} onClick={commit}>
                    <FileUp className="size-4" /> Import {s.valid - s.already_imported} new transactions
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------- history

interface ImportRow { id: number; original_name: string; uploaded_at: string; uploaded_by: string | null; rows_total: number;
  rows_new: number; rows_duplicate: number; rows_error: number; period_start: string | null; period_end: string | null; document_id: number | null;
  statement: StatementInfo | null; checks: StatementCheck[] }

function ImportHistory({ onOpen }: { onOpen: (id: number) => void }) {
  const { data, error, loading } = useApi<ImportRow[]>("/api/bank/imports");
  if (loading && !data) return <Loading />;
  return (
    <Card title="Imported statements">
      <ErrorNote error={error} />
      {data?.length === 0 ? <Empty>No statements imported yet.</Empty> : (
        <Table>
          <thead><tr><th>File</th><th>Covers</th><th>Imported</th><th className="text-right">New</th><th className="text-right">Known</th><th className="text-right">Errors</th><th>Checks</th><th /></tr></thead>
          <tbody>{data?.map((i) => (
            <tr key={i.id}>
              <td className="font-medium">{i.original_name}</td>
              <td>{formatDate(i.period_start)} – {formatDate(i.period_end)}</td>
              <td>{formatDateTime(i.uploaded_at)}<div className="text-xs text-muted">{i.uploaded_by}</div></td>
              <td className="text-right">{i.rows_new}</td><td className="text-right">{i.rows_duplicate}</td><td className="text-right">{i.rows_error}</td>
              <td>{i.checks.length === 0 ? <span className="text-xs text-muted">—</span>
                : i.checks.some((c) => c.status === "fail") ? <span className="inline-flex items-center gap-1 text-xs text-red-800"><XCircle className="size-3.5" />Does not match summary</span>
                : <span className="inline-flex items-center gap-1 text-xs text-emerald-800"><CheckCircle2 className="size-3.5" />Matches summary</span>}</td>
              <td className="text-right">
                <Button size="sm" onClick={() => onOpen(i.id)}>Open</Button>
                {i.document_id && <Button size="sm" variant="ghost" onClick={() => download(`/api/documents/${i.document_id}/file?download=true`, i.original_name)}>File</Button>}
              </td>
            </tr>
          ))}</tbody>
        </Table>
      )}
    </Card>
  );
}

function ImportDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const { data, error, loading, reload } = useApi<{ import: ImportRow; summary: Record<string, number>; transactions: BankTxn[] }>(`/api/bank/imports/${id}`);
  const [reverse, setReverse] = useState<{ paymentId: number } | null>(null);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorNote error={error} />;
  return (
    <div className="flex flex-col gap-4">
      <div><Button size="sm" onClick={onBack}>← All imports</Button></div>
      <h2 className="text-lg font-semibold">{data.import.original_name}</h2>
      <SummaryStats s={data.summary} />
      <StatementChecks statement={data.import.statement} checks={data.import.checks} />
      <Card title="Transactions in this statement">
        <Table>
          <thead><tr><th>Date</th><th>Narration</th><th className="text-right">Debit</th><th className="text-right">Credit</th><th>Status</th><th>Allocated to</th></tr></thead>
          <tbody>{data.transactions.map((t) => (
            <tr key={t.id}>
              <td className="whitespace-nowrap">{formatDate(t.txn_date)}</td>
              <td className="max-w-sm truncate font-mono text-xs" title={t.narration}>{t.narration}{t.review_note && <div className="text-muted">{t.review_note}</div>}</td>
              <td className="text-right">{t.debit_paise ? <Money paise={t.debit_paise} /> : ""}</td>
              <td className="text-right">{t.credit_paise ? <Money paise={t.credit_paise} /> : ""}</td>
              <td><StatusBadge status={t.review_status} /></td>
              <td className="text-xs">
                {t.payments.map((p) => (
                  <div key={p.id} className="flex items-center gap-1">
                    <span className={cx(p.status !== "confirmed" && p.status !== "pending" && "line-through text-muted")}>
                      {p.flat_label} {p.periods.map((x) => formatPeriod(x, true)).join("+") || "credit"} {formatINR(p.amount_paise)}
                    </span>
                    {(p.status === "confirmed" || p.status === "pending") && (
                      <button className="text-red-700" aria-label="Reverse allocation" onClick={() => setReverse({ paymentId: p.id })}><Undo2 className="size-3" /></button>
                    )}
                  </div>
                ))}
                {(t.review_status === "not_rent" || t.remainder_not_rent) && (
                  <button className="text-brand underline" onClick={async () => { await api.post(`/api/bank/transactions/${t.id}/reopen`); reload(); }}>Reopen</button>
                )}
              </td>
            </tr>
          ))}</tbody>
        </Table>
      </Card>
      {reverse && (
        <ReasonDialog title="Reverse this allocation?" danger confirmLabel="Reverse"
          description="The rent payment is kept as reversed and the bank credit returns to the review queue."
          onClose={() => setReverse(null)}
          onSubmit={async (reason) => { await api.post(`/api/payments/${reverse.paymentId}/void`, { reason }); reload(); }} />
      )}
    </div>
  );
}

