import { History, User } from "lucide-react";
import { Link } from "react-router";
import { formatINR } from "../lib/money";
import type { FlatCard as FlatCardT } from "../lib/types";
import { cx, StatusBadge } from "./ui";

export function themeFor(code: string) {
  return code === "pink"
    ? { soft: "bg-pink-soft", border: "border-pink-200", accent: "text-pink-accent", bar: "bg-pink-accent" }
    : { soft: "bg-blue-soft", border: "border-blue-200", accent: "text-blue-accent", bar: "bg-blue-accent" };
}

export function FlatCard({ flat, code, period }: { flat: FlatCardT; code: string; period: string }) {
  const t = themeFor(code);
  const tenancy = flat.tenancy;
  const month = `?month=${period}`;
  return (
    <Link
      to={`/flats/${flat.id}${month}`}
      className={cx("group flex min-h-36 flex-col rounded-xl border bg-surface p-3.5 shadow-sm transition hover:shadow-md",
        t.border)}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className={cx("text-lg font-semibold", t.accent)}>{flat.label}</div>
          <div className="text-xs text-muted">{flat.floor_label}</div>
        </div>
        {flat.occupancy === "tenant_occupied" || flat.period_status ? (
          // A tenant with no rent due for this month yet (e.g. moved in after the due day).
          <StatusBadge status={flat.period_status ?? "no_due"} overdue={flat.is_overdue} />
        ) : (
          <StatusBadge status={flat.occupancy} />
        )}
      </div>
      <div className="mt-3 flex-1 text-sm">
        {tenancy ? (
          <>
            <div className="flex items-center gap-1.5 font-medium">
              <User className="size-3.5 text-muted" aria-hidden />
              {tenancy.tenant_name}
            </div>
            <div className="mt-1 text-muted">
              Rent <span className="money text-ink">{formatINR(tenancy.current_rent_paise)}</span> / month
            </div>
            {flat.period_outstanding_paise > 0 && (
              <div className="mt-1 text-red-700">
                Outstanding <span className="money font-medium">{formatINR(flat.period_outstanding_paise)}</span>
              </div>
            )}
          </>
        ) : (
          <div className="text-muted">{flat.occupancy === "owner_occupied" ? "Family lives here" : "No tenant"}</div>
        )}
      </div>
      <div className={cx("mt-2 inline-flex items-center gap-1 text-xs font-medium", t.accent)}>
        <History className="size-3.5" aria-hidden /> Rent history & payments
      </div>
    </Link>
  );
}
