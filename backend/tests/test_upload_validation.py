import io

import pytest
from fastapi import HTTPException, UploadFile

from app.services.upload_validation import (
    read_upload_limited, sanitize_display_filename, title_from_display_name, validate_pdf_header,
)


@pytest.mark.parametrize("raw,expected", [
    ("contract.pdf", "contract.pdf"),
    ("../../etc/passwd.pdf", "passwd.pdf"),
    ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
    ("C:\\Users\\x\\My Contract (final).PDF", "My Contract (final).pdf"),
    ("a/../../b.pdf", "b.pdf"),
    ("bad<>:\"|?*name.pdf", "bad_______name.pdf"),
    ("null\x00byte.pdf", "nullbyte.pdf"),
    ("weird....name.pdf", "weird.name.pdf"),
])
def test_sanitize_strips_paths_and_dangerous_characters(raw, expected):
    assert sanitize_display_filename(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "notes.txt", "malware.exe", ".pdf", "...pdf", "   .pdf", "../.pdf"])
def test_sanitize_rejects_unusable_or_non_pdf_names(raw):
    with pytest.raises(HTTPException) as exc:
        sanitize_display_filename(raw)
    assert exc.value.status_code == 400


def test_sanitized_name_never_contains_separators():
    for raw in ["a/b\\c.pdf", "../x.pdf", "..\\x.pdf", "/abs/path.pdf"]:
        out = sanitize_display_filename(raw)
        assert "/" not in out and "\\" not in out and ".." not in out


def test_display_name_is_length_capped():
    out = sanitize_display_filename("x" * 500 + ".pdf")
    assert len(out) <= 120 and out.endswith(".pdf")


def test_pdf_header_check_uses_content_not_extension():
    validate_pdf_header(b"%PDF-1.7\n...")
    for bad in (b"<html>", b"PK\x03\x04", b"", b" %PDF-1.4"):
        with pytest.raises(HTTPException) as exc:
            validate_pdf_header(bad)
        assert exc.value.status_code == 400


async def test_read_limited_rejects_oversize_with_413():
    up = UploadFile(file=io.BytesIO(b"%PDF-" + b"0" * (3 * 1024 * 1024)), filename="a.pdf")
    with pytest.raises(HTTPException) as exc:
        await read_upload_limited(up, max_bytes=2 * 1024 * 1024)
    assert exc.value.status_code == 413


async def test_read_limited_accepts_within_limit_and_rejects_empty():
    up = UploadFile(file=io.BytesIO(b"%PDF-1.4 ok"), filename="a.pdf")
    assert await read_upload_limited(up, max_bytes=1024) == b"%PDF-1.4 ok"
    with pytest.raises(HTTPException) as exc:
        await read_upload_limited(UploadFile(file=io.BytesIO(b""), filename="a.pdf"), max_bytes=1024)
    assert exc.value.status_code == 400


def test_title_from_display_name():
    assert title_from_display_name("acme_vendor-agreement.pdf") == "Acme Vendor Agreement"
    assert title_from_display_name("Acme MSA v2.pdf") == "Acme MSA v2"
