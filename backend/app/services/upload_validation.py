"""
upload_validation.py
--------------------
Untrusted-upload handling: display-name sanitization, size cap, real PDF header check.

The stored file name is always a server-generated UUID; the user's filename is only
ever used as a sanitized display label, so it can never influence a filesystem path.
"""

import re
import unicodedata
from typing import Optional

from fastapi import HTTPException, UploadFile

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
ALLOWED_EXTENSIONS = (".pdf", ".docx")
_ALLOWED = re.compile(r"[^\w .,()&'+\-]", re.UNICODE)
MAX_DISPLAY_NAME = 120


def sanitize_display_filename(raw: Optional[str]) -> str:
    """
    Reduce an untrusted filename to a safe display label ending in .pdf or .docx.
    Raises HTTPException(400) if nothing usable is left or the type is not supported.
    """
    if not raw:
        raise HTTPException(status_code=400, detail="A file name is required.")

    name = unicodedata.normalize("NFKC", raw)
    name = name.replace("\x00", "")
    # Drop any directory part, whichever separator the client used.
    name = re.split(r"[\\/]", name)[-1]
    # Control characters and anything outside a conservative allowlist become "_".
    name = "".join(ch if unicodedata.category(ch)[0] != "C" else "_" for ch in name)
    name = _ALLOWED.sub("_", name)
    name = re.sub(r"\.{2,}", ".", name).strip(" .")

    ext = next((e for e in ALLOWED_EXTENSIONS if name.lower().endswith(e)), None)
    if ext is None:
        raise HTTPException(status_code=400, detail="Only PDF (.pdf) and Word (.docx) files are supported.")

    stem = name[: -len(ext)].strip(" ._")
    if not stem:
        raise HTTPException(status_code=400, detail="The file name is not usable.")
    return stem[: MAX_DISPLAY_NAME - len(ext)] + ext


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks and abort as soon as it exceeds max_bytes."""
    buf = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            mb = max_bytes // (1024 * 1024)
            raise HTTPException(status_code=413, detail=f"File too large. The maximum size is {mb} MB.")
    if not buf:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return bytes(buf)


def validate_pdf_header(data: bytes) -> None:
    """The content (not the extension or Content-Type header) must start with %PDF-."""
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(status_code=400, detail="The file is not a valid PDF (missing %PDF- header).")


def detect_kind(data: bytes, display_name: str) -> str:
    """'pdf' or 'docx', decided by the CONTENT and required to agree with the file name."""
    is_pdf = data.startswith(PDF_MAGIC)
    is_zip = data.startswith(ZIP_MAGIC)
    named_docx = display_name.lower().endswith(".docx")
    if named_docx:
        if data.startswith(b"\xd0\xcf\x11\xe0"):
            raise HTTPException(status_code=400, detail="This looks like an old Word file (.doc) or an encrypted document. Save it as .docx or PDF and upload that.")
        if not is_zip:
            raise HTTPException(status_code=400, detail="The file is not a valid Word document (.docx).")
        return "docx"
    if not is_pdf:
        raise HTTPException(status_code=400, detail="The file is not a valid PDF (missing %PDF- header).")
    return "pdf"


def title_from_display_name(display_name: str) -> str:
    ext = next((e for e in ALLOWED_EXTENSIONS if display_name.lower().endswith(e)), None)
    stem = display_name[: -len(ext)] if ext else display_name
    title = re.sub(r"[_\-]+", " ", stem).strip()
    return (title.title() if title.islower() or title.isupper() else title)[:255] or "Untitled Contract"
