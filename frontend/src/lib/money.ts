// Exact money helpers. All amounts travel as integer paise; no floating point.

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];

function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  const tail = digits.slice(-3);
  let head = digits.slice(0, -3);
  const groups: string[] = [];
  while (head.length > 2) {
    groups.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  if (head) groups.unshift(head);
  return [...groups, tail].join(",");
}

/** 1234567 paise -> "₹12,345.67"; whole rupees omit ".00". */
export function formatINR(paise: number | null | undefined, opts: { showZeroPaise?: boolean } = {}): string {
  if (paise === null || paise === undefined) return "—";
  const negative = paise < 0;
  const abs = Math.abs(Math.trunc(paise));
  const rupees = Math.floor(abs / 100).toString();
  const p = abs % 100;
  const frac = p || opts.showZeroPaise ? "." + p.toString().padStart(2, "0") : "";
  return `${negative ? "-" : ""}₹${groupIndian(rupees)}${frac}`;
}

/** Parse what a person types ("12,000", "12000.5", "₹ 500") into paise. Returns null if invalid. */
export function parseRupees(input: string): number | null {
  const cleaned = input.replace(/[₹,\s]/g, "").replace(/^rs\.?/i, "");
  if (!/^\d+(\.\d{0,2})?$/.test(cleaned)) return null;
  const [whole, frac = ""] = cleaned.split(".");
  const paise = Number(whole) * 100 + Number((frac + "00").slice(0, 2));
  return Number.isSafeInteger(paise) ? paise : null;
}

/** Paise -> plain editable string without grouping ("12000" or "12000.50"). */
export function paiseToInput(paise: number): string {
  const r = Math.floor(paise / 100);
  const p = paise % 100;
  return p ? `${r}.${p.toString().padStart(2, "0")}` : `${r}`;
}

// ------------------------------------------------------------------ periods

export function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function currentPeriod(): string {
  return todayISO().slice(0, 7);
}

export function addMonths(period: string, n: number): string {
  const [y, m] = period.split("-").map(Number);
  const idx = y * 12 + (m - 1) + n;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

export function formatPeriod(period: string | null | undefined, short = false): string {
  if (!period) return "—";
  const [y, m] = period.split("-").map(Number);
  const name = MONTHS[m - 1] ?? "?";
  return short ? `${name.slice(0, 3)} ${y}` : `${name} ${y}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MONTHS[m - 1].slice(0, 3)} ${y}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const dt = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return dt.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function isValidPeriod(p: string): boolean {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(p);
}
