import {
  AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, CircleDashed, Clock, Info, Loader2, MinusCircle, X, XCircle,
} from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, useEffect, useId, useRef } from "react";
import type { ApiError } from "../lib/api";
import { addMonths, currentPeriod, formatINR, formatPeriod } from "../lib/money";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ buttons

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand text-white hover:bg-teal-800 border-transparent",
  secondary: "bg-surface text-ink border-line hover:bg-stone-50",
  danger: "bg-surface text-red-700 border-red-200 hover:bg-red-50",
  ghost: "bg-transparent text-ink border-transparent hover:bg-stone-100",
};

export function Button({
  variant = "secondary", busy, className, children, size = "md", ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean; size?: "sm" | "md" }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium transition-colors",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1 text-sm" : "px-3.5 py-2 text-sm",
        VARIANTS[variant], className,
      )}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

// ------------------------------------------------------------------- layout

export function Card({ children, className, title, actions }: {
  children: ReactNode; className?: string; title?: ReactNode; actions?: ReactNode;
}) {
  return (
    <section className={cx("min-w-0 rounded-xl border border-line bg-surface shadow-sm print-plain", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          {actions && <div className="flex flex-wrap gap-2 no-print">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2 no-print">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone = "neutral", icon }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "info"; icon?: ReactNode;
}) {
  const toneCls = {
    neutral: "", good: "text-emerald-700", warn: "text-amber-700", bad: "text-red-700", info: "text-sky-700",
  }[tone];
  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-sm print-plain">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted">
        {icon}
        {label}
      </div>
      <div className={cx("money mt-1.5 text-xl font-semibold", toneCls)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Money({ paise, className }: { paise: number | null | undefined; className?: string }) {
  return <span className={cx("money", className)}>{formatINR(paise)}</span>;
}

// ------------------------------------------------------------------- status

const STATUS: Record<string, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  paid: { label: "Paid", cls: "bg-emerald-50 text-emerald-800 border-emerald-200", Icon: CheckCircle2 },
  confirmed: { label: "Confirmed", cls: "bg-emerald-50 text-emerald-800 border-emerald-200", Icon: CheckCircle2 },
  allocated: { label: "Allocated", cls: "bg-emerald-50 text-emerald-800 border-emerald-200", Icon: CheckCircle2 },
  partially_paid: { label: "Partly paid", cls: "bg-amber-50 text-amber-800 border-amber-200", Icon: CircleDashed },
  partially_allocated: { label: "Partly allocated", cls: "bg-amber-50 text-amber-800 border-amber-200", Icon: CircleDashed },
  pending_verification: { label: "Pending check", cls: "bg-sky-50 text-sky-800 border-sky-200", Icon: Clock },
  pending: { label: "Pending check", cls: "bg-sky-50 text-sky-800 border-sky-200", Icon: Clock },
  unreviewed: { label: "Needs review", cls: "bg-sky-50 text-sky-800 border-sky-200", Icon: Clock },
  unpaid: { label: "Unpaid", cls: "bg-stone-100 text-stone-700 border-stone-200", Icon: MinusCircle },
  overdue: { label: "Overdue", cls: "bg-red-50 text-red-800 border-red-200", Icon: AlertTriangle },
  waived: { label: "Waived", cls: "bg-stone-100 text-stone-600 border-stone-200", Icon: MinusCircle },
  rejected: { label: "Rejected", cls: "bg-stone-100 text-stone-600 border-stone-300 line-through", Icon: XCircle },
  reversed: { label: "Reversed", cls: "bg-stone-100 text-stone-600 border-stone-300 line-through", Icon: XCircle },
  cancelled: { label: "Cancelled", cls: "bg-stone-100 text-stone-600 border-stone-300 line-through", Icon: XCircle },
  not_rent: { label: "Not rent", cls: "bg-stone-100 text-stone-600 border-stone-200", Icon: MinusCircle },
  debit: { label: "Debit", cls: "bg-stone-100 text-stone-600 border-stone-200", Icon: MinusCircle },
  active: { label: "Active", cls: "bg-emerald-50 text-emerald-800 border-emerald-200", Icon: CheckCircle2 },
  ended: { label: "Ended", cls: "bg-stone-100 text-stone-600 border-stone-200", Icon: MinusCircle },
  upcoming: { label: "Upcoming", cls: "bg-sky-50 text-sky-800 border-sky-200", Icon: Clock },
  vacant: { label: "Vacant", cls: "bg-stone-100 text-stone-700 border-stone-200", Icon: MinusCircle },
  owner_occupied: { label: "Owner occupied", cls: "bg-violet-50 text-violet-800 border-violet-200", Icon: Info },
  strong: { label: "Strong match", cls: "bg-emerald-50 text-emerald-800 border-emerald-200", Icon: CheckCircle2 },
  possible: { label: "Possible match", cls: "bg-amber-50 text-amber-800 border-amber-200", Icon: CircleDashed },
  ambiguous: { label: "Ambiguous", cls: "bg-orange-50 text-orange-800 border-orange-200", Icon: AlertTriangle },
  no_match: { label: "No match", cls: "bg-stone-100 text-stone-700 border-stone-200", Icon: MinusCircle },
  potential_duplicate: { label: "Possible duplicate", cls: "bg-red-50 text-red-800 border-red-200", Icon: AlertTriangle },
};

/** Status chip with icon + text, so status never relies on colour alone. */
export function StatusBadge({ status, overdue }: { status: string | null | undefined; overdue?: boolean }) {
  if (!status) return null;
  const s = STATUS[status] ?? { label: status, cls: "bg-stone-100 text-stone-700 border-stone-200", Icon: Info };
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className={cx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", s.cls)}>
        <s.Icon className="size-3.5" aria-hidden />
        {s.label}
      </span>
      {overdue && status !== "overdue" && (
        <span className="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-800">
          <AlertTriangle className="size-3.5" aria-hidden />
          Overdue
        </span>
      )}
    </span>
  );
}

export function Alert({ tone = "info", title, children }: {
  tone?: "info" | "warn" | "error" | "success"; title?: ReactNode; children?: ReactNode;
}) {
  const map = {
    info: ["border-sky-200 bg-sky-50 text-sky-900", Info],
    warn: ["border-amber-200 bg-amber-50 text-amber-900", AlertTriangle],
    error: ["border-red-200 bg-red-50 text-red-900", XCircle],
    success: ["border-emerald-200 bg-emerald-50 text-emerald-900", CheckCircle2],
  } as const;
  const [cls, Icon] = map[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={cx("flex gap-2 rounded-lg border p-3 text-sm", cls)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>
        {title && <div className="font-semibold">{title}</div>}
        {children}
      </div>
    </div>
  );
}

export function ErrorNote({ error }: { error: ApiError | Error | null | undefined }) {
  if (!error) return null;
  return <Alert tone="error">{error.message}</Alert>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-muted" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted">{children}</p>;
}

// -------------------------------------------------------------------- forms

export function Field({ label, hint, error, children, className }: {
  label: string; hint?: ReactNode; error?: string | null; children: (id: string) => ReactNode; className?: string;
}) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      {children(id)}
      {hint && !error && <p className="text-xs text-muted">{hint}</p>}
      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
  );
}

export const inputCls =
  "w-full rounded-lg border border-line bg-white px-3 py-2 text-sm shadow-xs focus:border-teal-600 disabled:bg-stone-50";

export function MonthPicker({ value, onChange, label = "Month" }: { value: string; onChange: (p: string) => void; label?: string }) {
  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-line bg-white p-1 no-print" aria-label={label}>
      <button type="button" className="rounded-md p-1.5 hover:bg-stone-100" onClick={() => onChange(addMonths(value, -1))}
        aria-label="Previous month">
        <ChevronLeft className="size-4" />
      </button>
      <input
        type="month" value={value} aria-label={label}
        onChange={(e) => e.target.value && onChange(e.target.value)}
        className="w-36 rounded-md border-0 bg-transparent px-1 text-center text-sm font-medium"
      />
      <button type="button" className="rounded-md p-1.5 hover:bg-stone-100" onClick={() => onChange(addMonths(value, 1))}
        aria-label="Next month">
        <ChevronRight className="size-4" />
      </button>
      {value !== currentPeriod() && (
        <button type="button" className="rounded-md px-2 py-1 text-xs text-brand hover:bg-stone-100"
          onClick={() => onChange(currentPeriod())}>
          This month
        </button>
      )}
    </div>
  );
}

export function PeriodLabel({ period }: { period: string }) {
  return <span>{formatPeriod(period)}</span>;
}

// -------------------------------------------------------------------- modal

export function Modal({ title, onClose, children, footer, wide }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    // Runs once per opening: focus the first field, close on Escape, restore focus afterwards.
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, select, textarea, button:not([data-close])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, []);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 sm:items-center sm:p-4" onMouseDown={onClose}>
      <div
        ref={ref} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined}
        onMouseDown={(e) => e.stopPropagation()}
        className={cx("flex max-h-[95vh] w-full flex-col rounded-t-2xl bg-surface shadow-xl sm:rounded-2xl",
          wide ? "sm:max-w-3xl" : "sm:max-w-lg")}
      >
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" data-close onClick={onClose} className="rounded-md p-1 hover:bg-stone-100" aria-label="Close">
            <X className="size-5" />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------- table

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[640px] text-left text-sm [&_td]:border-b [&_td]:border-line [&_td]:px-2 [&_td]:py-2 [&_th]:border-b [&_th]:border-line [&_th]:px-2 [&_th]:py-2 [&_th]:text-xs [&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-wide [&_th]:text-muted">
        {children}
      </table>
    </div>
  );
}
