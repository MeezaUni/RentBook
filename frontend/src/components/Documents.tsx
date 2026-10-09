import { Download, FileText, Paperclip, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { api, ApiError, download, qs } from "../lib/api";
import { useApi } from "../lib/hooks";
import { formatDateTime } from "../lib/money";
import type { DocumentInfo } from "../lib/types";
import { ReasonDialog } from "./ReasonDialog";
import { Alert, Button } from "./ui";

export const ACCEPT = "image/png,image/jpeg,image/webp,image/heic,application/pdf";

export async function uploadDocs(entityType: string, entityId: number, files: File[], category: string) {
  for (const f of files) {
    const form = new FormData();
    form.append("file", f);
    form.append("entity_type", entityType);
    form.append("entity_id", String(entityId));
    form.append("category", category);
    await api.upload("/api/documents", form);
  }
}

/** Attached proof / receipts for one record, with thumbnails, upload and deliberate delete. */
export function Documents({ entityType, entityId, category = "other", compact }: {
  entityType: string; entityId: number; category?: string; compact?: boolean;
}) {
  const { data, reload } = useApi<DocumentInfo[]>(`/api/documents${qs({ entity_type: entityType, entity_id: entityId })}`);
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<DocumentInfo | null>(null);
  const [needsConfirm, setNeedsConfirm] = useState(false);

  async function onFiles(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    try {
      await uploadDocs(entityType, entityId, [...files], category);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {data?.map((d) => (
          <div key={d.id} className="group relative w-28 rounded-lg border border-line bg-white p-1 text-xs">
            <a href={`/api/documents/${d.id}/file`} target="_blank" rel="noreferrer" title={d.original_name}>
              {d.is_image ? (
                <img src={`/api/documents/${d.id}/file`} alt={d.original_name} className="h-20 w-full rounded object-cover" loading="lazy" />
              ) : (
                <div className="flex h-20 items-center justify-center rounded bg-stone-50"><FileText className="size-8 text-stone-400" /></div>
              )}
            </a>
            <div className="mt-1 truncate" title={d.original_name}>{d.original_name}</div>
            <div className="text-[10px] text-muted">{formatDateTime(d.uploaded_at)}</div>
            <div className="mt-1 flex justify-between">
              <button className="rounded p-0.5 hover:bg-stone-100" aria-label={`Download ${d.original_name}`}
                onClick={() => download(`/api/documents/${d.id}/file?download=true`, d.original_name)}>
                <Download className="size-3.5" />
              </button>
              <button className="rounded p-0.5 text-red-700 hover:bg-red-50" aria-label={`Delete ${d.original_name}`}
                onClick={() => { setNeedsConfirm(false); setDeleting(d); }}>
                <Trash2 className="size-3.5" />
              </button>
            </div>
          </div>
        ))}
        {data && data.length === 0 && compact && <span className="text-xs text-muted">No attachments</span>}
      </div>
      <div>
        <input ref={input} type="file" accept={ACCEPT} multiple className="hidden" onChange={(e) => onFiles(e.target.files)} />
        <Button size="sm" busy={busy} onClick={() => input.current?.click()}>
          <Paperclip className="size-3.5" /> Attach proof
        </Button>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {deleting && (
        <ReasonDialog
          title="Delete document?"
          danger
          confirmLabel={needsConfirm ? "Yes, delete the evidence" : "Delete"}
          description={needsConfirm
            ? <Alert tone="warn">This file is evidence for a financial record. The record stays, but the file will be gone.</Alert>
            : `“${deleting.original_name}” will be removed.`}
          onClose={() => setDeleting(null)}
          onSubmit={async (reason) => {
            try {
              await api.post(`/api/documents/${deleting.id}/delete`, { reason, confirm_linked_financial_record: needsConfirm });
              await reload();
            } catch (e) {
              if (e instanceof ApiError && e.code === "linked_to_financial_record") {
                setNeedsConfirm(true);
                throw new Error("Confirm once more to delete evidence linked to a financial record.");
              }
              throw e;
            }
          }}
        />
      )}
    </div>
  );
}
