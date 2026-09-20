from app.core.config import settings
from tests.helpers import upload


async def test_rejects_non_pdf_extension(client):
    r = await upload(client, b"just text", "notes.txt")
    assert r.status_code == 400


async def test_rejects_fake_pdf_by_content_even_with_pdf_name_and_mime(client):
    r = await upload(client, b"<html><script>alert(1)</script></html>", "invoice.pdf")
    assert r.status_code == 400 and "valid PDF" in r.json()["detail"]


async def test_rejects_empty_file(client):
    assert (await upload(client, b"", "empty.pdf")).status_code == 400


async def test_rejects_oversize_file_with_413(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)
    r = await upload(client, b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024), "big.pdf")
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]


async def test_accepts_file_at_the_limit_boundary(client, monkeypatch, long_pdf):
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)
    assert len(long_pdf) < 1024 * 1024
    assert (await upload(client, long_pdf, "ok.pdf")).status_code == 202


async def test_path_traversal_filename_cannot_escape_storage(client, long_pdf):
    STORAGE = settings.absolute_storage_path
    r = await upload(client, long_pdf, "..\\..\\..\\Windows\\evil.pdf")
    assert r.status_code == 202
    body = r.json()
    assert body["filename"] == "evil.pdf"
    import re
    stored = list(STORAGE.glob("*.pdf"))
    assert len(stored) == 1
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.pdf", stored[0].name)
    assert "evil" not in stored[0].name                                # user input never reaches the path
    assert not (STORAGE.parent / "evil.pdf").exists()

    r2 = await upload(client, long_pdf, "../../up.pdf")
    assert r2.status_code == 202 and r2.json()["filename"] == "up.pdf"


async def test_file_endpoint_serves_the_original_pdf_to_its_owner(client, long_pdf):
    cid = (await upload(client, long_pdf, "MSA.pdf")).json()["id"]
    r = await client.get(f"/api/contracts/{cid}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content == long_pdf


async def test_system_status_is_honest_about_missing_llm(client):
    body = (await client.get("/api/system/status")).json()
    assert body["llm_configured"] is False and body["llm_provider"] == "none"
    assert "GROQ_API_KEY" in body["llm_reason"] or "LLM_PROVIDER" in body["llm_reason"]
    assert "not legal advice" in body["disclaimer"].lower()
    assert "key" not in " ".join(k for k in body if k.endswith("_key"))      # never exposes secrets
