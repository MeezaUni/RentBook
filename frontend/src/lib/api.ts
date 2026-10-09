// Thin fetch wrapper. Session is an HttpOnly cookie; the CSRF token lives in memory only.

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let csrfToken = "";
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

async function handle<T>(res: Response): Promise<T> {
  if (res.ok) {
    const type = res.headers.get("content-type") ?? "";
    return (type.includes("application/json") ? res.json() : res.blob()) as Promise<T>;
  }
  let code = "http_error";
  let message = `Request failed (${res.status})`;
  let details: Record<string, unknown> = {};
  try {
    const body = await res.json();
    if (body?.error) {
      code = body.error.code;
      message = body.error.message;
      details = body.error.details ?? {};
    }
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 401 && code === "not_authenticated" && onUnauthorized) onUnauthorized();
  throw new ApiError(res.status, code, message, details);
}

function headers(json: boolean): HeadersInit {
  const h: Record<string, string> = { "X-CSRF-Token": csrfToken };
  if (json) h["Content-Type"] = "application/json";
  return h;
}

export const api = {
  get<T>(url: string): Promise<T> {
    return fetch(url, { credentials: "same-origin" }).then((r) => handle<T>(r));
  },
  post<T>(url: string, body?: unknown): Promise<T> {
    return fetch(url, {
      method: "POST", credentials: "same-origin", headers: headers(true),
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then((r) => handle<T>(r));
  },
  put<T>(url: string, body: unknown): Promise<T> {
    return fetch(url, { method: "PUT", credentials: "same-origin", headers: headers(true), body: JSON.stringify(body) })
      .then((r) => handle<T>(r));
  },
  patch<T>(url: string, body: unknown): Promise<T> {
    return fetch(url, { method: "PATCH", credentials: "same-origin", headers: headers(true), body: JSON.stringify(body) })
      .then((r) => handle<T>(r));
  },
  del<T>(url: string): Promise<T> {
    return fetch(url, { method: "DELETE", credentials: "same-origin", headers: headers(false) }).then((r) => handle<T>(r));
  },
  upload<T>(url: string, form: FormData): Promise<T> {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: headers(false), body: form })
      .then((r) => handle<T>(r));
  },
};

/** Download a protected file (keeps the cookie) and hand it to the browser. */
export async function download(url: string, fallbackName: string) {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) await handle(res);
  const blob = await res.blob();
  const disp = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disp);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = match?.[1] ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  const s = sp.toString();
  return s ? `?${s}` : "";
}
