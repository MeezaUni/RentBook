"""Private document storage.

Files live outside any web-served directory and are only reachable through
authenticated API endpoints. Storage names are generated; user filenames are
kept only as sanitized metadata. The `Storage` interface lets a different
backend (e.g. an S3-compatible bucket) replace `LocalStorage` later.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Protocol

from .config import settings
from .errors import DomainError

# Allowed file kinds, identified by content (magic bytes), not by the user's filename.
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/heic"}
PROOF_TYPES = IMAGE_TYPES | {"application/pdf"}
STATEMENT_TYPES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
}


def sniff_type(data: bytes, filename: str) -> str | None:
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "image/heic"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        # Zip container: accept only if it is an Excel workbook.
        if b"xl/" in data[:4096] or filename.lower().endswith(".xlsx"):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return None
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/vnd.ms-excel"
    lower = filename.lower()
    if lower.endswith((".csv", ".txt")) or lower.endswith(".xls"):
        # Some banks ship "xls" files that are really CSV/HTML text.
        try:
            data[:65536].decode("utf-8")
        except UnicodeDecodeError:
            try:
                data[:65536].decode("cp1252")
            except UnicodeDecodeError:
                return None
        if b"\x00" in data[:65536]:
            return None
        return "text/csv"
    return None


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ ()-]+")


def sanitize_filename(name: str) -> str:
    name = Path(name or "file").name  # drop any path components
    name = _SAFE_NAME.sub("_", name).strip(" .") or "file"
    return name[:200]


class Storage(Protocol):
    def save(self, data: bytes, content_type: str) -> str: ...
    def read(self, key: str) -> bytes: ...
    def path_for(self, key: str) -> Path: ...
    def delete(self, key: str) -> None: ...


_KEY_RE = re.compile(r"^[0-9a-f]{32}\.[a-z]{3,4}$")


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise DomainError("invalid_key", "Invalid storage key")
        return self.root / key[:2] / key

    def save(self, data: bytes, content_type: str) -> str:
        key = uuid.uuid4().hex + EXTENSIONS.get(content_type, ".bin")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def path_for(self, key: str) -> Path:
        return self._path(key)

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()


def get_storage() -> Storage:
    return LocalStorage(settings.uploads_dir)


def validate_upload(data: bytes, filename: str, allowed: set[str]) -> tuple[str, str]:
    """Returns (content_type, sha256). Raises DomainError if not acceptable."""
    if not data:
        raise DomainError("empty_file", "The uploaded file is empty.")
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise DomainError("file_too_large", f"File is larger than {settings.max_upload_mb} MB.", status=413)
    ctype = sniff_type(data, filename)
    if ctype is None or ctype not in allowed:
        raise DomainError(
            "unsupported_file",
            "Unsupported file type. Allowed: "
            + ", ".join(sorted({EXTENSIONS[t] for t in allowed})),
            status=415,
        )
    return ctype, hashlib.sha256(data).hexdigest()
