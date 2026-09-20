"""Phase 8: the click-to-source PDF viewer. Highlights must land on the right text of the real PDF."""

import re
import struct

import pymupdf
import pytest

from app.core.config import settings
from app.services import job_runner
from tests.helpers import upload, upload_and_process, upload_version

PASSWORD = "correct horse battery"
QUOTE = "This Agreement renews automatically for successive one (1) year terms unless either party gives written notice of non-renewal"


def png_size(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", data[16:24])


def alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


async def highlights(client, cid, page, q, **params):
    from urllib.parse import quote
    extra = "".join(f"&{k}={v}" for k, v in params.items())
    r = await client.get(f"/api/contracts/{cid}/pages/{page}/highlights?q={quote(q)}{extra}")
    assert r.status_code == 200, r.text
    return r.json()


def text_under(pdf_bytes: bytes, page_no: int, rects, w, h) -> str:
    """What the real PDF contains under the returned rectangles: the ground truth for 'is it on the right text'."""
    pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pg = pdf.load_page(page_no - 1)
    out = []
    for r in rects:
        clip = pymupdf.Rect(r["x0"] * w, r["y0"] * h, r["x1"] * w, r["y1"] * h)
        out.append(pg.get_text("text", clip=clip))
    pdf.close()
    return " ".join(out)


# ─── page images ──────────────────────────────────────────────────────────────

async def test_page_image_is_a_real_png_of_the_right_size(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    r = await client.get(f"/api/contracts/{d['id']}/pages/1/image?scale=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "private" in r.headers["cache-control"]
    w, h = png_size(r.content)
    assert (w, h) == (595, 842)                                  # A4 at 1x
    w2, h2 = png_size((await client.get(f"/api/contracts/{d['id']}/pages/1/image?scale=2")).content)
    assert (w2, h2) == (1190, 1684)


async def test_page_out_of_range_and_bad_scale_are_rejected(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    assert (await client.get(f"/api/contracts/{d['id']}/pages/0/image")).status_code == 404
    r = await client.get(f"/api/contracts/{d['id']}/pages/99/image")
    assert r.status_code == 404 and "does not exist" in r.json()["detail"]
    assert (await client.get(f"/api/contracts/{d['id']}/pages/1/image?scale=50")).status_code == 422
    assert (await client.get(f"/api/contracts/{d['id']}/pages/abc/image")).status_code == 422


# ─── highlights ───────────────────────────────────────────────────────────────

async def test_a_quote_is_highlighted_on_the_page_it_is_on_and_only_there(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    page = next(p for p in (await client.get(f"/api/contracts/{d['id']}/text")).json()["pages"] if "renews automatically" in p["text"])["page"]
    hit = await highlights(client, d["id"], page, QUOTE)
    assert hit["matched"] is True and hit["rects"] and hit["page_count"] == 5
    assert (hit["width"], hit["height"]) == (595.0, 842.0)
    other = await highlights(client, d["id"], 1, QUOTE)
    assert other["matched"] is False and other["rects"] == []    # not on page 1: claims nothing


async def test_rectangles_are_normalised_ordered_and_sit_over_the_actual_quote_text(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    page = next(p for p in (await client.get(f"/api/contracts/{d['id']}/text")).json()["pages"] if "renews automatically" in p["text"])["page"]
    hit = await highlights(client, d["id"], page, QUOTE)
    for r in hit["rects"]:
        assert 0 <= r["x0"] < r["x1"] <= 1 and 0 <= r["y0"] < r["y1"] <= 1
    ys = [r["y0"] for r in hit["rects"]]
    assert ys == sorted(ys)
    under = alnum(text_under(long_pdf, page, hit["rects"], hit["width"], hit["height"]))
    assert alnum(QUOTE) in under                                 # the highlighted region really contains the quote


async def test_a_quote_wrapped_over_several_lines_gets_one_rectangle_per_line(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    page = next(p for p in (await client.get(f"/api/contracts/{d['id']}/text")).json()["pages"] if "renews automatically" in p["text"])["page"]
    hit = await highlights(client, d["id"], page, QUOTE)
    assert len(hit["rects"]) >= 2                                # ~130 chars at ~92 chars per line


async def test_matching_ignores_case_punctuation_curly_quotes_and_spacing(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    page = next(p for p in (await client.get(f"/api/contracts/{d['id']}/text")).json()["pages"] if "renews automatically" in p["text"])["page"]
    messy = "  THIS   agreement renews\nautomatically – for successive one (1) year terms, unless  either party gives written notice "
    assert (await highlights(client, d["id"], page, messy))["matched"] is True


async def test_an_ellipsis_abbreviated_quote_is_highlighted_fragment_by_fragment(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    page = next(p for p in (await client.get(f"/api/contracts/{d['id']}/text")).json()["pages"] if "renews automatically" in p["text"])["page"]
    hit = await highlights(client, d["id"], page, "This Agreement renews automatically for successive... at least sixty (60) days before the end")
    assert hit["matched"] is True and len(hit["rects"]) >= 2
    under = alnum(text_under(long_pdf, page, hit["rects"], hit["width"], hit["height"]))
    assert "renewsautomaticallyforsuccessive" in under and "sixty60daysbeforetheend" in under


async def test_a_quote_that_is_not_in_the_document_is_never_highlighted(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    for page in range(1, 6):
        h = await highlights(client, d["id"], page, "The Provider shall pay Customer one million dollars on demand")
        assert h["matched"] is False and h["rects"] == []


@pytest.mark.parametrize("q", ["", "   ", "short", "a b c"])
async def test_empty_or_too_short_quotes_match_nothing(client, llm, long_pdf, q):
    d = await upload_and_process(client, long_pdf)
    assert (await highlights(client, d["id"], 1, q))["matched"] is False


async def test_a_fragment_missing_from_an_elided_quote_means_no_highlight(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    page = next(p for p in (await client.get(f"/api/contracts/{d['id']}/text")).json()["pages"] if "renews automatically" in p["text"])["page"]
    assert (await highlights(client, d["id"], page, "This Agreement renews automatically... and pays a bonus to the Provider"))["matched"] is False


async def test_every_verified_source_in_the_app_can_be_highlighted_on_its_page(client, llm, long_pdf):
    """The end-to-end promise: whatever the UI links to (page + quote) can actually be shown."""
    d = await upload_and_process(client, long_pdf)
    checked = 0
    for f in d["fields"]:
        if f["status"] == "verified" and f["page"] and f["source_quote"]:
            assert (await highlights(client, d["id"], f["page"], f["source_quote"]))["matched"] is True, f["field_key"]
            checked += 1
    for o in d["obligations"][:8]:
        if o["verification_status"] == "verified" and o["source_page"]:
            assert (await highlights(client, d["id"], o["source_page"], o["source_quote"]))["matched"] is True
            checked += 1
    flags = (await client.get(f"/api/contracts/{d['id']}/flags")).json()
    for fl in flags:
        if fl["source_page"] and fl["source_quote"]:
            assert (await highlights(client, d["id"], fl["source_page"], fl["source_quote"]))["matched"] is True, fl["code"]
            checked += 1
    assert checked >= 12


async def test_chat_citations_can_be_highlighted(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    a = (await client.post(f"/api/contracts/{d['id']}/chat", json={"content": "Can we terminate early?"})).json()
    c = a["citations"][0]
    assert (await highlights(client, d["id"], c["source_page"], c["snippet"]))["matched"] is True


# ─── versions and ownership ───────────────────────────────────────────────────

async def test_each_version_renders_and_highlights_its_own_pdf(client, llm, long_pdf, tmp_path):
    from sample_data.generate import long_contract_text, write_pdf
    write_pdf(tmp_path / "v2.pdf", long_contract_text().replace("sixty (60) days", "seventy (70) days"))
    d = await upload_and_process(client, long_pdf)
    v2 = await upload_version(client, d["id"], (tmp_path / "v2.pdf").read_bytes(), label="v2")
    page = next(p for p in (await client.get(f"/api/contracts/{d['id']}/text?version_id={v2['id']}")).json()["pages"]
                if "renews automatically" in p["text"])["page"]
    q = "at least seventy (70) days before the end"
    assert (await highlights(client, d["id"], page, q, version_id=v2["id"]))["matched"] is True
    assert (await highlights(client, d["id"], page, q, version_id=d["current_version_id"]))["matched"] is False


async def test_viewer_endpoints_are_owner_only(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    cid = (await upload(a, long_pdf)).json()["id"]
    await job_runner.wait_idle()
    for path in (f"/api/contracts/{cid}/pages/1/image", f"/api/contracts/{cid}/pages/1/highlights?q=hello+world+quote"):
        assert (await a.get(path)).status_code == 200
        assert (await b.get(path)).status_code == 404 and (await anon.get(path)).status_code == 401


async def test_deleted_contract_pages_are_not_served(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await client.delete(f"/api/contracts/{d['id']}")
    assert (await client.get(f"/api/contracts/{d['id']}/pages/1/image")).status_code == 404
