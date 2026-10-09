import { type ReactNode, useState } from "react";
import { Alert, Button, Field, inputCls, Modal } from "./ui";

/** Asks for a reason before an auditable correction (reverse, reject, waive...). */
export function ReasonDialog({ title, description, confirmLabel = "Confirm", danger, onSubmit, onClose }: {
  title: string; description?: ReactNode; confirmLabel?: string; danger?: boolean;
  onSubmit: (reason: string) => Promise<unknown>; onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(reason.trim());
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant={danger ? "danger" : "primary"} busy={busy} disabled={reason.trim().length < 3} onClick={go}>
        {confirmLabel}
      </Button>
    </>}>
      <div className="flex flex-col gap-3">
        {description && <div className="text-sm text-stone-700">{description}</div>}
        <Field label="Reason" hint="Saved in the audit history with your name and the time.">
          {(id) => <textarea id={id} className={inputCls} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
        {error && <Alert tone="error">{error}</Alert>}
      </div>
    </Modal>
  );
}
