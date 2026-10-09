import { LockKeyhole } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Alert, Button, Field, inputCls } from "../components/ui";
import { useAuth } from "../lib/auth";

const NAME_KEY = "rentbook.lastUser";

function rememberedName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

export function Login() {
  const { login } = useAuth();
  const [name, setName] = useState(rememberedName);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(name.trim(), pin);
      try {
        localStorage.setItem(NAME_KEY, name.trim()); // convenience only; never the PIN
      } catch {
        /* storage unavailable */
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-line bg-surface p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-3">
          <span className="flex gap-1" aria-hidden>
            <span className="h-8 w-4 rounded bg-pink-accent" />
            <span className="h-10 w-4 rounded bg-blue-accent" />
          </span>
          <div>
            <h1 className="text-xl font-semibold">Rentbook</h1>
            <p className="text-sm text-muted">Family property records</p>
          </div>
        </div>
        <div className="flex flex-col gap-4">
          <Field label="Your name">
            {(id) => <input id={id} className={inputCls} value={name} onChange={(e) => setName(e.target.value)}
              autoComplete="username" required />}
          </Field>
          <Field label="PIN">
            {(id) => <input id={id} className={inputCls} value={pin} onChange={(e) => setPin(e.target.value)}
              type="password" inputMode="numeric" autoComplete="current-password" required />}
          </Field>
          {error && <Alert tone="error">{error}</Alert>}
          <Button type="submit" variant="primary" busy={busy} className="w-full">
            <LockKeyhole className="size-4" aria-hidden /> Log in
          </Button>
        </div>
      </form>
    </div>
  );
}
