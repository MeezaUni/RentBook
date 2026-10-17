import { DatabaseBackup, Download, KeyRound, Plus, Save, Trash2, Undo2, UserPlus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { ReasonDialog } from "../components/ReasonDialog";
import { Alert, Button, Card, cx, Empty, ErrorNote, Field, inputCls, Loading, Money, PageHeader, StatusBadge, Table } from "../components/ui";
import { api, download, qs } from "../lib/api";
import { useApi } from "../lib/hooks";
import { currentPeriod, formatDate, formatDateTime, formatPeriod, parseRupees, todayISO } from "../lib/money";
import type { Building, Tenant } from "../lib/types";

const SECTIONS = [
  ["property", "Buildings & flats"], ["money", "Rates & rules"], ["opening", "Opening balances"],
  ["categories", "Expense categories"], ["statements", "Statement layouts"], ["security", "Login & PIN"],
  ["backup", "Backup"], ["audit", "Audit history"],
] as const;
type Section = (typeof SECTIONS)[number][0];

export function SettingsPage() {
  const [section, setSection] = useState<Section>("property");
  return (
    <>
      <PageHeader title="Settings" />
      <div className="grid gap-4 md:grid-cols-[200px_1fr]">
        <nav className="flex flex-row flex-wrap gap-1 md:flex-col" aria-label="Settings sections">
          {SECTIONS.map(([k, l]) => (
            <button key={k} onClick={() => setSection(k)} aria-current={section === k}
              className={cx("rounded-lg px-3 py-1.5 text-left text-sm font-medium", section === k ? "bg-teal-50 text-teal-900" : "hover:bg-stone-100")}>
              {l}
            </button>
          ))}
        </nav>
        <div className="min-w-0">
          {section === "property" && <PropertySettings />}
          {section === "money" && <MoneySettings />}
          {section === "opening" && <OpeningBalances />}
          {section === "categories" && <Categories />}
          {section === "statements" && <StatementProfiles />}
          {section === "security" && <Security />}
          {section === "backup" && <Backups />}
          {section === "audit" && <Audit />}
        </div>
      </div>
    </>
  );
}

function useSaver() {
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  async function run(fn: () => Promise<unknown>, ok = "Saved") {
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "success", text: ok });
    } catch (e) {
      setMsg({ tone: "error", text: e instanceof Error ? e.message : "Failed" });
    }
  }
  return { msg, run, note: msg && <Alert tone={msg.tone}>{msg.text}</Alert> };
}

// ---------------------------------------------------------------- property

function PropertySettings() {
  const { data, reload } = useApi<{ buildings: Building[] }>("/api/buildings");
  const s = useSaver();
  if (!data) return <Loading />;
  return (
    <div className="flex flex-col gap-4">
      {s.note}
      {data.buildings.map((b) => <BuildingEditor key={b.id} b={b} save={(fn) => s.run(async () => { await fn(); reload(); })} />)}
    </div>
  );
}

function BuildingEditor({ b, save }: { b: Building; save: (fn: () => Promise<unknown>) => void }) {
  const [name, setName] = useState(b.name);
  const [color, setColor] = useState(b.color);
  const [flats, setFlats] = useState(b.flats.map((f) => ({ id: f.id, label: f.label, floor_label: f.floor_label, notes: f.notes ?? "" })));
  return (
    <Card title={b.name}>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Building name">{(id) => <input id={id} className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Colour">{(id) => <input id={id} type="color" className="h-9 w-16 rounded border border-line" value={color} onChange={(e) => setColor(e.target.value)} />}</Field>
        <Button onClick={() => save(() => api.patch(`/api/buildings/${b.id}`, { name, color }))}><Save className="size-4" /> Save</Button>
      </div>
      <div className="mt-4">
        <Table>
          <thead><tr><th>Flat identifier</th><th>Floor label</th><th>Notes</th><th /></tr></thead>
          <tbody>{flats.map((f, i) => (
            <tr key={f.id}>
              <td><input className={inputCls} value={f.label} aria-label="Flat identifier" onChange={(e) => setFlats(flats.map((x, j) => j === i ? { ...x, label: e.target.value } : x))} /></td>
              <td><input className={inputCls} value={f.floor_label} aria-label="Floor label" onChange={(e) => setFlats(flats.map((x, j) => j === i ? { ...x, floor_label: e.target.value } : x))} /></td>
              <td><input className={inputCls} value={f.notes} aria-label="Notes" onChange={(e) => setFlats(flats.map((x, j) => j === i ? { ...x, notes: e.target.value } : x))} /></td>
              <td><Button size="sm" onClick={() => save(() => api.patch(`/api/flats/${f.id}`, { label: f.label, floor_label: f.floor_label, notes: f.notes }))}>Save</Button></td>
            </tr>
          ))}</tbody>
        </Table>
        <p className="mt-2 text-xs text-muted">A floor label is shared by all flats on that floor. History is kept when identifiers change.</p>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------- money

interface SettingsResp {
  settings: { billing_policy: string; rent_due_month: string; tracking_start_period: string | null;
    maintenance_given_for: string; salary_paid_for: string; overdue_grace_days: number; payment_method_labels: { cash: string; online: string } };
  billing_policies: Record<string, string>;
  rent_due_months: Record<string, string>;
  handover_months: Record<string, string>;
  rate_keys: Record<string, string>;
  rates: { id: number; key: string; amount_paise: number; effective_period: string; note: string | null; created_by: string | null }[];
}

function MoneySettings() {
  const { data, reload } = useApi<SettingsResp>("/api/settings");
  const s = useSaver();
  const [rateKey, setRateKey] = useState("maintenance_target");
  const [rateAmount, setRateAmount] = useState("");
  const [rateFrom, setRateFrom] = useState(currentPeriod());
  const [rateNote, setRateNote] = useState("");
  if (!data) return <Loading />;
  const st = data.settings;
  return (
    <div className="flex flex-col gap-4">
      {s.note}
      <Card title="Monthly amounts (effective-dated)">
        <p className="mb-3 text-sm text-muted">Changing an amount only affects the month you choose and later; earlier months keep their old values.</p>
        {Object.entries(data.rate_keys).map(([k, label]) => (
          <div key={k} className="mb-3">
            <div className="text-sm font-semibold">{label}</div>
            <ul className="text-sm text-muted">
              {data.rates.filter((r) => r.key === k).map((r) => (
                <li key={r.id}><Money paise={r.amount_paise} className="text-ink" /> from {r.effective_period === "2000-01" ? "the beginning" : formatPeriod(r.effective_period)}{r.note && ` — ${r.note}`}</li>
              ))}
            </ul>
          </div>
        ))}
        <form className="grid gap-2 sm:grid-cols-5 sm:items-end" onSubmit={(e) => {
          e.preventDefault();
          const p = parseRupees(rateAmount);
          s.run(async () => { if (p === null) throw new Error("Enter an amount"); await api.post("/api/settings/rates", { key: rateKey, amount_paise: p, effective_period: rateFrom, note: rateNote || null }); setRateAmount(""); reload(); });
        }}>
          <Field label="Change">{(id) => (
            <select id={id} className={inputCls} value={rateKey} onChange={(e) => setRateKey(e.target.value)}>
              {Object.entries(data.rate_keys).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>)}</Field>
          <Field label="New amount (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={rateAmount} onChange={(e) => setRateAmount(e.target.value)} />}</Field>
          <Field label="From month">{(id) => <input id={id} type="month" className={inputCls} value={rateFrom} onChange={(e) => setRateFrom(e.target.value)} />}</Field>
          <Field label="Note">{(id) => <input id={id} className={inputCls} value={rateNote} onChange={(e) => setRateNote(e.target.value)} />}</Field>
          <Button type="submit">Save</Button>
        </form>
      </Card>
      <RulesCard st={st} policies={data.billing_policies} dueMonths={data.rent_due_months} handoverMonths={data.handover_months} run={s.run} reload={reload} />
    </div>
  );
}

function RulesCard({ st, policies, dueMonths, handoverMonths, run, reload }: { st: SettingsResp["settings"]; policies: Record<string, string>;
  dueMonths: Record<string, string>; handoverMonths: Record<string, string>; run: (fn: () => Promise<unknown>) => void; reload: () => void }) {
  const [start, setStart] = useState(st.tracking_start_period ?? "");
  const [dueMonth, setDueMonth] = useState(st.rent_due_month);
  const [mntFor, setMntFor] = useState(st.maintenance_given_for);
  const [salaryFor, setSalaryFor] = useState(st.salary_paid_for);
  const [policy, setPolicy] = useState(st.billing_policy);
  const [grace, setGrace] = useState(String(st.overdue_grace_days));
  const [cash, setCash] = useState(st.payment_method_labels.cash);
  const [online, setOnline] = useState(st.payment_method_labels.online);
  return (
    <Card title="Rent rules">
      <div className="flex flex-col gap-3">
        <Field label="Records start from" hint="Rent, maintenance and salary before this month are never shown as owed. Leave empty for no limit.">
          {(id) => <input id={id} type="month" className={inputCls + " w-48"} value={start} onChange={(e) => setStart(e.target.value)} />}
        </Field>
        <Field label="When is a month's rent due?" hint="Each tenant's due day is set on their flat page. Applies to rent months generated from now on.">
          {(id) => (
            <select id={id} className={inputCls} value={dueMonth} onChange={(e) => setDueMonth(e.target.value)}>
              {Object.entries(dueMonths).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          )}
        </Field>
        <Field label="Maintenance money handed over in a month is for…">
          {(id) => (
            <select id={id} className={inputCls} value={mntFor} onChange={(e) => setMntFor(e.target.value)}>
              {Object.entries(handoverMonths).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          )}
        </Field>
        <Field label="Salary paid in a month is for…">
          {(id) => (
            <select id={id} className={inputCls} value={salaryFor} onChange={(e) => setSalaryFor(e.target.value)}>
              {Object.entries(handoverMonths).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          )}
        </Field>
        <Field label="Part-month rule (default for all tenancies)" hint="Applies to rent months generated from now on. Individual tenancies can override it.">
          {(id) => (
            <select id={id} className={inputCls} value={policy} onChange={(e) => setPolicy(e.target.value)}>
              {Object.entries(policies).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          )}
        </Field>
        <Field label="Days after the due date before rent shows as overdue">
          {(id) => <input id={id} type="number" min={0} max={60} className={inputCls + " w-32"} value={grace} onChange={(e) => setGrace(e.target.value)} />}
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Label for cash">{(id) => <input id={id} className={inputCls} value={cash} onChange={(e) => setCash(e.target.value)} />}</Field>
          <Field label="Label for online">{(id) => <input id={id} className={inputCls} value={online} onChange={(e) => setOnline(e.target.value)} />}</Field>
        </div>
        <div>
          <Button onClick={() => run(async () => {
            await api.put("/api/settings", { key: "tracking_start_period", value: start || null });
            await api.put("/api/settings", { key: "rent_due_month", value: dueMonth });
            await api.put("/api/settings", { key: "maintenance_given_for", value: mntFor });
            await api.put("/api/settings", { key: "salary_paid_for", value: salaryFor });
            await api.put("/api/settings", { key: "billing_policy", value: policy });
            await api.put("/api/settings", { key: "overdue_grace_days", value: Number(grace) });
            await api.put("/api/settings", { key: "payment_method_labels", value: { cash, online } });
            reload();
          })}><Save className="size-4" /> Save rules</Button>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------- opening balances

interface Opening { id: number; kind: string; kind_label: string; as_of_date: string; amount_paise: number; description: string;
  notes: string | null; tenant_name: string | null; flat_label: string | null; status: string; created_by: string | null }

function OpeningBalances() {
  const { data, reload, error } = useApi<Opening[]>("/api/opening-balances");
  const tenants = useApi<Tenant[]>("/api/tenants");
  const [kind, setKind] = useState("tenant_arrears");
  const [date, setDate] = useState(todayISO());
  const [amount, setAmount] = useState("");
  const [desc, setDesc] = useState("");
  const [notes, setNotes] = useState("");
  const [tenancyId, setTenancyId] = useState("");
  const [reverse, setReverse] = useState<Opening | null>(null);
  const s = useSaver();
  const tenancies = (tenants.data ?? []).flatMap((t) => t.tenancies);
  async function add(e: FormEvent) {
    e.preventDefault();
    await s.run(async () => {
      const p = parseRupees(amount);
      if (!p) throw new Error("Enter an amount");
      await api.post("/api/opening-balances", { kind, as_of_date: date, amount_paise: p, description: desc, notes: notes || null,
        tenancy_id: kind === "tenant_arrears" ? Number(tenancyId) || null : null });
      setAmount(""); setDesc(""); setNotes("");
      reload();
    }, "Opening balance recorded");
  }
  return (
    <div className="flex flex-col gap-4">
      <Alert tone="info">Opening balances record amounts owed or held <b>before</b> you started using Rentbook. They are always labelled as
        manually entered — no payments are invented. Tenant arrears can later be settled by real payments.</Alert>
      <Card title="Add opening balance">
        <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
          <Field label="Type">{(id) => (
            <select id={id} className={inputCls} value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="tenant_arrears">Rent a tenant still owed</option>
              <option value="caretaker_cash">Maintenance cash the caretaker held</option>
              <option value="family_cash">Cash the family held</option>
            </select>)}</Field>
          {kind === "tenant_arrears" && (
            <Field label="Tenant">{(id) => (
              <select id={id} className={inputCls} value={tenancyId} onChange={(e) => setTenancyId(e.target.value)} required>
                <option value="">Choose…</option>
                {tenancies.map((t) => <option key={t.id} value={t.id}>{t.flat_label} · {t.tenant_name}</option>)}
              </select>)}</Field>
          )}
          <Field label="As of date">{(id) => <input id={id} type="date" className={inputCls} value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} required />}</Field>
          <Field label="Amount (₹)">{(id) => <input id={id} className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />}</Field>
          <Field label="Description" className="sm:col-span-2">{(id) => <input id={id} className={inputCls} placeholder="e.g. Unpaid rent for June–August before using the app" value={desc} onChange={(e) => setDesc(e.target.value)} required />}</Field>
          <Field label="Supporting notes (optional)" className="sm:col-span-2">{(id) => <textarea id={id} className={inputCls} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
          <div className="sm:col-span-2"><Button type="submit" variant="primary"><Plus className="size-4" /> Add</Button></div>
        </form>
        <div className="mt-3">{s.note}</div>
      </Card>
      <Card title="Recorded opening balances">
        <ErrorNote error={error} />
        {data?.length === 0 ? <Empty>None.</Empty> : (
          <Table>
            <thead><tr><th>Date</th><th>Type</th><th>For</th><th>Description</th><th className="text-right">Amount</th><th>Status</th><th /></tr></thead>
            <tbody>{data?.map((o) => (
              <tr key={o.id}>
                <td>{formatDate(o.as_of_date)}</td><td>{o.kind_label}</td><td>{o.flat_label ? `${o.flat_label} · ${o.tenant_name}` : "—"}</td>
                <td>{o.description}{o.notes && <div className="text-xs text-muted">{o.notes}</div>}</td>
                <td className="text-right"><Money paise={o.amount_paise} /></td><td><StatusBadge status={o.status === "active" ? "active" : "reversed"} /></td>
                <td>{o.status === "active" && <button className="text-red-700" aria-label="Reverse" onClick={() => setReverse(o)}><Undo2 className="size-4" /></button>}</td>
              </tr>
            ))}</tbody>
          </Table>
        )}
      </Card>
      {reverse && <ReasonDialog title="Reverse opening balance?" danger confirmLabel="Reverse" onClose={() => setReverse(null)}
        onSubmit={async (reason) => { await api.post(`/api/opening-balances/${reverse.id}/reverse`, { reason }); reload(); }} />}
    </div>
  );
}

// -------------------------------------------------------------- categories

function Categories() {
  const { data, reload } = useApi<{ id: number; name: string; is_active: boolean }[]>("/api/maintenance/categories");
  const [name, setName] = useState("");
  const s = useSaver();
  return (
    <Card title="Expense categories">
      {s.note}
      <ul className="my-3 flex flex-col gap-1.5">
        {data?.map((c) => <CategoryRow key={c.id} c={c} run={(fn) => s.run(async () => { await fn(); reload(); })} />)}
      </ul>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); s.run(async () => { await api.post("/api/maintenance/categories", { name }); setName(""); reload(); }); }}>
        <input className={inputCls} placeholder="New category" value={name} onChange={(e) => setName(e.target.value)} aria-label="New category" />
        <Button type="submit" disabled={!name.trim()}><Plus className="size-4" /> Add</Button>
      </form>
    </Card>
  );
}

function CategoryRow({ c, run }: { c: { id: number; name: string; is_active: boolean }; run: (fn: () => Promise<unknown>) => void }) {
  const [name, setName] = useState(c.name);
  return (
    <li className="flex items-center gap-2">
      <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} aria-label="Category name" />
      <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={c.is_active}
        onChange={(e) => run(() => api.patch(`/api/maintenance/categories/${c.id}`, { name, is_active: e.target.checked }))} /> In use</label>
      <Button size="sm" onClick={() => run(() => api.patch(`/api/maintenance/categories/${c.id}`, { name, is_active: c.is_active }))}>Save</Button>
    </li>
  );
}

// -------------------------------------------------------------- statements

function StatementProfiles() {
  const { data, reload } = useApi<{ id: number; name: string; mapping: { columns: Record<string, number> } }[]>("/api/bank/profiles");
  return (
    <Card title="Saved statement column layouts">
      <p className="mb-3 text-sm text-muted">Layouts are saved from the Bank → Import screen. Most HDFC statements are detected automatically.</p>
      {data?.length === 0 ? <Empty>No saved layouts.</Empty> : (
        <ul className="divide-y divide-line">
          {data?.map((p) => (
            <li key={p.id} className="flex items-center justify-between py-2 text-sm">
              <span><b>{p.name}</b> <span className="text-xs text-muted">{Object.keys(p.mapping.columns).join(", ")}</span></span>
              <button className="text-red-700" aria-label={`Delete ${p.name}`} onClick={async () => { await api.del(`/api/bank/profiles/${p.id}`); reload(); }}><Trash2 className="size-4" /></button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- security

function Security() {
  const users = useApi<{ id: number; name: string; is_active: boolean; last_login_at: string | null }[]>("/api/users");
  const [cur, setCur] = useState(""); const [n1, setN1] = useState(""); const [n2, setN2] = useState("");
  const [uname, setUname] = useState(""); const [upin, setUpin] = useState(""); const [mine, setMine] = useState("");
  const a = useSaver(); const b = useSaver();
  return (
    <div className="flex flex-col gap-4">
      <Card title="Change your PIN">
        <form className="grid gap-3 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); a.run(async () => {
          if (n1 !== n2) throw new Error("The new PINs do not match");
          await api.post("/api/auth/change-pin", { current_pin: cur, new_pin: n1 }); setCur(""); setN1(""); setN2("");
        }, "PIN changed. Other devices were logged out."); }}>
          <Field label="Current PIN">{(id) => <input id={id} type="password" inputMode="numeric" className={inputCls} value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" />}</Field>
          <Field label="New PIN (6–12 digits)">{(id) => <input id={id} type="password" inputMode="numeric" className={inputCls} value={n1} onChange={(e) => setN1(e.target.value)} autoComplete="new-password" />}</Field>
          <Field label="Repeat new PIN">{(id) => <input id={id} type="password" inputMode="numeric" className={inputCls} value={n2} onChange={(e) => setN2(e.target.value)} autoComplete="new-password" />}</Field>
          <div className="sm:col-span-3"><Button type="submit"><KeyRound className="size-4" /> Change PIN</Button></div>
        </form>
        <div className="mt-3">{a.note}</div>
      </Card>
      <Card title="Family members who can log in">
        <ul className="mb-3 text-sm">
          {users.data?.map((u) => <li key={u.id}>{u.name} <span className="text-xs text-muted">· last login {formatDateTime(u.last_login_at)}</span></li>)}
        </ul>
        <form className="grid gap-3 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); b.run(async () => {
          await api.post("/api/users", { name: uname, pin: upin, your_pin: mine }); setUname(""); setUpin(""); setMine(""); users.reload();
        }, "Login created"); }}>
          <Field label="Name">{(id) => <input id={id} className={inputCls} value={uname} onChange={(e) => setUname(e.target.value)} />}</Field>
          <Field label="Their PIN">{(id) => <input id={id} type="password" inputMode="numeric" className={inputCls} value={upin} onChange={(e) => setUpin(e.target.value)} autoComplete="new-password" />}</Field>
          <Field label="Your PIN (to confirm)">{(id) => <input id={id} type="password" inputMode="numeric" className={inputCls} value={mine} onChange={(e) => setMine(e.target.value)} autoComplete="current-password" />}</Field>
          <div className="sm:col-span-3"><Button type="submit"><UserPlus className="size-4" /> Add family member</Button></div>
        </form>
        <div className="mt-3">{b.note}</div>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ backup

function Backups() {
  const { data, reload } = useApi<{ name: string; size_bytes: number; created_at: string }[]>("/api/backups");
  const s = useSaver();
  const [busy, setBusy] = useState(false);
  return (
    <Card title="Backups" actions={<Button variant="primary" busy={busy} onClick={async () => {
      setBusy(true);
      await s.run(async () => { await api.post("/api/backups"); reload(); }, "Backup created");
      setBusy(false);
    }}><DatabaseBackup className="size-4" /> Create backup now</Button>}>
      <p className="mb-3 text-sm text-muted">A backup contains the database, all uploaded documents and plain CSV copies of every table. Download it and keep
        a copy somewhere else (USB drive, another computer). Restoring is done from the command line — see the README.</p>
      {s.note}
      {data?.length === 0 ? <Empty>No backups yet.</Empty> : (
        <ul className="mt-3 divide-y divide-line text-sm">
          {data?.map((b) => (
            <li key={b.name} className="flex items-center justify-between py-2">
              <span>{b.name}<span className="block text-xs text-muted">{formatDateTime(b.created_at)} · {(b.size_bytes / 1024 / 1024).toFixed(2)} MB</span></span>
              <Button size="sm" onClick={() => download(`/api/backups/${encodeURIComponent(b.name)}`, b.name)}><Download className="size-3.5" /> Download</Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------- audit

interface AuditEvent { id: number; ts: string; actor: string; action: string; entity_type: string; entity_id: number | null;
  before: Record<string, unknown> | null; after: Record<string, unknown> | null; reason: string | null }

function Audit() {
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");
  const { data, loading } = useApi<{ total: number; events: AuditEvent[] }>(`/api/audit${qs({ offset, limit: 50, entity_type: filter })}`);
  return (
    <Card title="Audit history" actions={
      <select className={inputCls + " w-48"} value={filter} onChange={(e) => { setFilter(e.target.value); setOffset(0); }} aria-label="Filter">
        <option value="">Everything</option>
        {["rent_payment", "rent_due", "tenancy", "tenant", "bank_transaction", "bank_import", "cash_movement", "maintenance_expense",
          "salary_payment", "opening_balance", "document", "setting", "rate_setting", "user"].map((x) => <option key={x} value={x}>{x.replace(/_/g, " ")}</option>)}
      </select>}>
      {loading && !data && <Loading />}
      <ul className="divide-y divide-line text-sm">
        {data?.events.map((e) => (
          <li key={e.id} className="py-2">
            <div className="flex flex-wrap justify-between gap-2">
              <span><b>{e.action}</b> · {e.entity_type}{e.entity_id ? ` #${e.entity_id}` : ""}</span>
              <span className="text-xs text-muted">{e.actor} · {formatDateTime(e.ts)}</span>
            </div>
            {e.reason && <div className="text-xs">Reason: {e.reason}</div>}
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">Details</summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-stone-50 p-2">{JSON.stringify({ before: e.before, after: e.after }, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ul>
      {data && (
        <div className="mt-3 flex items-center justify-between text-sm">
          <span className="text-muted">{data.total} events</span>
          <span className="flex gap-2">
            <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Newer</Button>
            <Button size="sm" disabled={offset + 50 >= data.total} onClick={() => setOffset(offset + 50)}>Older</Button>
          </span>
        </div>
      )}
    </Card>
  );
}
