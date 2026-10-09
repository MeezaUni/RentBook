"""Supporting documents (payment screenshots, receipts, invoices...)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..errors import Conflict, DomainError, NotFound
from ..models import (
    BankTransaction, CashMovement, Document, DocumentLink, Flat, MaintenanceExpense, OpeningBalance, RentPayment,
    SalaryPayment, Tenant, utcnow,
)
from ..security import Actor, require_user
from ..storage import PROOF_TYPES, get_storage, sanitize_filename, validate_upload

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

ENTITY_MODELS = {
    "rent_payment": RentPayment,
    "maintenance_expense": MaintenanceExpense,
    "salary_payment": SalaryPayment,
    "cash_movement": CashMovement,
    "tenant": Tenant,
    "flat": Flat,
    "bank_transaction": BankTransaction,
    "opening_balance": OpeningBalance,
}
FINANCIAL = {"rent_payment", "maintenance_expense", "salary_payment", "cash_movement", "bank_transaction",
             "opening_balance", "bank_import"}
CATEGORIES = {"payment_proof", "bank_screenshot", "statement_evidence", "cash_receipt", "maintenance_receipt",
              "invoice", "other"}


def describe(d: Document) -> dict:
    return {
        "id": d.id, "original_name": d.original_name, "content_type": d.content_type, "size_bytes": d.size_bytes,
        "category": d.category, "uploaded_at": d.uploaded_at.isoformat(), "uploaded_by": d.uploaded_by,
        "is_image": d.content_type in ("image/png", "image/jpeg", "image/webp"),
        "is_pdf": d.content_type == "application/pdf",
        "links": [{"entity_type": l.entity_type, "entity_id": l.entity_id} for l in d.links],
    }


@router.post("/documents")
async def upload(
    file: UploadFile = File(...),
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    category: str = Form("other"),
    actor: Actor = Depends(require_user),
    db: Session = Depends(get_db),
):
    if entity_type not in ENTITY_MODELS:
        raise DomainError("invalid_entity", "Documents cannot be attached to that record type")
    if db.get(ENTITY_MODELS[entity_type], entity_id) is None:
        raise NotFound("Linked record")
    if category not in CATEGORIES:
        category = "other"
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    ctype, sha = validate_upload(data, file.filename or "file", PROOF_TYPES)
    key = get_storage().save(data, ctype)
    doc = Document(storage_key=key, original_name=sanitize_filename(file.filename or "file"), content_type=ctype,
                   size_bytes=len(data), sha256=sha, category=category, uploaded_by=actor.name)
    db.add(doc)
    db.flush()
    db.add(DocumentLink(document_id=doc.id, entity_type=entity_type, entity_id=entity_id))
    audit.record(db, actor.name, "document.upload", "document", doc.id, None,
                 {"name": doc.original_name, "entity_type": entity_type, "entity_id": entity_id, "sha256": sha})
    db.commit()
    db.refresh(doc)
    return describe(doc)


@router.get("/documents")
def list_docs(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).join(DocumentLink).where(
        DocumentLink.entity_type == entity_type, DocumentLink.entity_id == entity_id,
        Document.deleted_at.is_(None)).order_by(Document.uploaded_at)).all()
    return [describe(d) for d in docs]


@router.get("/documents/{doc_id}/file")
def get_file(doc_id: int, download: bool = False, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if d is None or d.deleted_at is not None:
        raise NotFound("Document")
    path = get_storage().path_for(d.storage_key)
    if not path.is_file():
        raise NotFound("Document file")
    inline = not download and d.content_type in ("image/png", "image/jpeg", "image/webp", "application/pdf")
    disp = "inline" if inline else "attachment"
    # Uploaded content is only ever served with its sniffed type, nosniff, and a locked-down CSP.
    # PDFs skip `sandbox` because it prevents the browser's built-in PDF viewer from rendering.
    csp = ("default-src 'none'; frame-ancestors 'self'" if d.content_type == "application/pdf"
           else "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'")
    return FileResponse(
        path, media_type=d.content_type,
        headers={
            "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(d.original_name)}",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": csp,
        },
    )


class DeleteIn(BaseModel):
    reason: str = Field(max_length=2000)
    confirm_linked_financial_record: bool = False


@router.post("/documents/{doc_id}/delete")
def delete_doc(doc_id: int, body: DeleteIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if d is None or d.deleted_at is not None:
        raise NotFound("Document")
    if len(body.reason.strip()) < 3:
        raise DomainError("reason_required", "Please give a reason for deleting this document")
    financial = [l for l in d.links if l.entity_type in FINANCIAL]
    if financial and not body.confirm_linked_financial_record:
        raise Conflict("linked_to_financial_record",
                       "This document is evidence for a financial record. Confirm that you really want to delete it.",
                       {"links": [{"entity_type": l.entity_type, "entity_id": l.entity_id} for l in financial]})
    if d.category == "bank_statement":
        raise Conflict("statement_file", "Bank statement files are kept as the source of imported transactions")
    d.deleted_at, d.deleted_by, d.delete_reason = utcnow(), actor.name, body.reason.strip()
    get_storage().delete(d.storage_key)
    audit.record(db, actor.name, "document.delete", "document", d.id,
                 {"name": d.original_name, "sha256": d.sha256,
                  "links": [{"entity_type": l.entity_type, "entity_id": l.entity_id} for l in d.links]},
                 None, reason=body.reason.strip())
    db.commit()
    return {"ok": True}
