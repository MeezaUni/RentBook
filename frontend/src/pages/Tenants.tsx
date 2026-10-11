import { Pencil, Plus, Trash2, UserPlus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { ReasonDialog } from "../components/ReasonDialog";
import { Alert, Button, Card, Empty, ErrorNote, Field, inputCls, Loading, Modal, PageHeader, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { formatDate, formatINR } from "../lib/money";
import type { Tenant } from "../lib/types";

export function TenantsPage() {
  const { data, error, loading, reload } = useApi<Tenant[]>("/api/tenants");
  const [editing, setEditing] = useState<Tenant | "new" | null>(null);
  const [filter, setFilter] = useState<"active" | "all">("active");
  const list = data?.filter((t) => filter === "all" || t.status !== "former") ?? [];
  return (
    <>
      <PageHeader title="Tenants" subtitle="Contact details, tenancies and the bank sender names used to match payments."
        actions={<>
          <select className={inputCls + " w-40"} value={filter} onChange={(e) => setFilter(e.target.value as "active" | "all")} aria-label="Show">
            <option value="active">Current tenants</option><option value="all">Including former</option>
          </select>
          <Button variant="primary" onClick={() => setEditing("new")}><UserPlus className="size-4" /> Add tenant</Button>
        </>} />
      <Alert tone="info">To move a tenant into a flat, open the flat from <Link to="/buildings" className="underline">Buildings</Link> and choose “New tenant moves in”.</Alert>
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {list.map((t) => <TenantCard key={t.id} t={t} onEdit={() => setEditing(t)} onChange={reload} />)}
      </div>
      {data && list.length === 0 && <Empty>No tenants yet.</Empty>}
      {editing && <TenantForm tenant={editing === "new" ? null : editing} onClose={() => setEditing(null)} onDone={reload} />}
    </>
  );
}

function TenantCard({ t, onEdit, onChange }: { t: Tenant; onEdit: () => void; onChange: () => void }) {
  const [alias, setAlias] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  async function addAlias(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post(`/api/tenants/${t.id}/aliases`, { alias, kind: "sender_name" });
      setAlias("");
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }
  return (
    <Card title={<span className="flex items-center gap-2">{t.full_name}<StatusBadge status={t.status === "former" ? "ended" : t.status === "active" ? "active" : "vacant"} /></span>}
      actions={<>
        <Button size="sm" onClick={onEdit}><Pencil className="size-3.5" /> Edit</Button>
        <Button size="sm" variant="danger" onClick={() => setDeleting(true)}><Trash2 className="size-3.5" /> Delete</Button>
      </>}>
      <div className="flex flex-col gap-3 text-sm">
        <div className="text-muted">
          {[t.phone, t.email, t.preferred_payment_method && `pays by ${t.preferred_payment_method}`].filter(Boolean).join(" · ") || "No contact details"}
        </div>
        {t.tenancies.map((x) => (
          <Link key={x.id} to={`/flats/${x.flat_id}`} className="flex justify-between rounded-lg bg-stone-50 p-2 hover:bg-stone-100">
            <span>{x.building_name} · <b>{x.flat_label}</b><span className="block text-xs text-muted">{formatDate(x.start_date)} – {x.end_date ? formatDate(x.end_date) : "present"}</span></span>
            <span className="text-right"><span className="money">{formatINR(x.current_rent_paise)}</span><span className="block text-xs text-muted">due day {x.rent_due_day}</span></span>
          </Link>
        ))}
        {t.notes && <p className="whitespace-pre-wrap text-stone-700">{t.notes}</p>}
        <div>
          <div className="mb-1 text-xs font-semibold uppercase text-muted">Bank sender names</div>
          <div className="flex flex-wrap gap-1.5">
            {t.aliases.map((a) => (
              <span key={a.id} className="inline-flex items-center gap-1 rounded-full border border-line bg-white px-2 py-0.5 text-xs">
                {a.alias}
                <button aria-label={`Remove ${a.alias}`} className="text-red-700" onClick={async () => { await api.del(`/api/aliases/${a.id}`); onChange(); }}>
                  <Trash2 className="size-3" />
                </button>
              </span>
            ))}
            {t.aliases.length === 0 && <span className="text-xs text-muted">None yet — add the name that appears on their bank transfers.</span>}
          </div>
          <form onSubmit={addAlias} className="mt-2 flex gap-2">
            <input className={inputCls} placeholder="e.g. RAVI K SHARMA" value={alias} onChange={(e) => setAlias(e.target.value)} aria-label="New sender name" />
            <Button type="submit" size="sm" disabled={alias.trim().length < 3}><Plus className="size-3.5" /> Add</Button>
          </form>
          {error && <p className="mt-1 text-xs text-red-700">{error}</p>}
        </div>
      </div>
      {deleting && (
        <ReasonDialog title={`Delete ${t.full_name}?`} danger confirmLabel="Delete tenant"
          description={<>This is for tenants added by mistake. It removes them along with their tenancies and bank sender names.
            If any rent has been recorded for them, end their tenancy from the flat page instead, so your history stays correct.</>}
          onSubmit={(reason) => api.post(`/api/tenants/${t.id}/delete`, { reason }).then(onChange)}
          onClose={() => setDeleting(false)} />
      )}
    </Card>
  );
}

function TenantForm({ tenant, onClose, onDone }: { tenant: Tenant | null; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({
    full_name: tenant?.full_name ?? "", phone: tenant?.phone ?? "", email: tenant?.email ?? "",
    preferred_payment_method: tenant?.preferred_payment_method ?? "", notes: tenant?.notes ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = { ...f, preferred_payment_method: f.preferred_payment_method || null };
    try {
      if (tenant) await api.patch(`/api/tenants/${tenant.id}`, body);
      else await api.post("/api/tenants", body);
      onDone();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title={tenant ? "Edit tenant" : "Add tenant"} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" busy={busy} type="submit" form="tenant-form">Save</Button>
    </>}>
      <form id="tenant-form" onSubmit={save} className="flex flex-col gap-3">
        <Field label="Full name">{(id) => <input id={id} className={inputCls} value={f.full_name} onChange={set("full_name")} required />}</Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Phone (optional)">{(id) => <input id={id} className={inputCls} value={f.phone} onChange={set("phone")} />}</Field>
          <Field label="Email (optional)">{(id) => <input id={id} type="email" className={inputCls} value={f.email} onChange={set("email")} />}</Field>
        </div>
        <Field label="Usually pays by">
          {(id) => (
            <select id={id} className={inputCls} value={f.preferred_payment_method} onChange={set("preferred_payment_method")}>
              <option value="">Not set</option><option value="cash">Cash</option><option value="online">Online transfer</option>
            </select>
          )}
        </Field>
        <Field label="Notes (optional)">{(id) => <textarea id={id} rows={3} className={inputCls} value={f.notes} onChange={set("notes")} />}</Field>
        {error && <Alert tone="error">{error}</Alert>}
      </form>
    </Modal>
  );
}
