"""
ocr_service.py
--------------
Free, local OCR (RapidOCR on ONNX, no cloud, no Tesseract install) for pages that have no text
layer: scanned contracts, or signature/attachment pages that are only an image.

OCR is imperfect (it reads "$12,000" as "s$12,o00" at a low resolution), and the quote-exists check
would happily "verify" a value against a misread. So anything found on an OCR'd page is never
`verified`: it is demoted to `needs_review` with a note, until a person checks it against the page
image. The document also gets a review flag, and the source viewer cannot highlight text that is
only pixels, and says so.
"""

import asyncio
import importlib.util
import logging
import threading
from dataclasses import dataclass
from statistics import mean, median
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

import pymupdf

from app.core.config import settings

logger = logging.getLogger(__name__)

MIN_PAGE_CHARS = 20                    # a page with fewer text-layer characters than this counts as "no text"
_engine = None
_engine_lock = threading.Lock()
_ocr_lock = threading.Lock()           # the engine is CPU heavy: one page at a time
OCR_NOTE = "Read from a scanned page by OCR, which can misread digits and names. Check it against the page image."


@dataclass
class PageOcr:
    page: int
    text: str
    confidence: Optional[float]
    lines: int


def available() -> bool:
    return bool(settings.OCR_ENABLED) and importlib.util.find_spec("rapidocr_onnxruntime") is not None


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
    return _engine


def _reading_order(result: List[Any]) -> Tuple[str, Optional[float]]:
    """Join detected boxes into lines in reading order. Returns (text, mean confidence)."""
    boxes = []
    for r in result:
        pts, text, conf = r[0], str(r[1] or "").strip(), float(r[2])
        if not text:
            continue
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        boxes.append({"y": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys), "x": min(xs), "text": text, "conf": conf})
    if not boxes:
        return "", None
    tol = 0.6 * median(b["h"] for b in boxes if b["h"] > 0) if any(b["h"] > 0 for b in boxes) else 8.0
    boxes.sort(key=lambda b: (b["y"], b["x"]))
    lines: List[List[Dict[str, Any]]] = []
    for b in boxes:
        if lines and abs(b["y"] - mean(x["y"] for x in lines[-1])) <= tol:
            lines[-1].append(b)
        else:
            lines.append([b])
    text = "\n".join(" ".join(x["text"] for x in sorted(line, key=lambda b: b["x"])) for line in lines)
    return text.replace("\x00", ""), mean(b["conf"] for b in boxes)


def ocr_image(png: bytes) -> Tuple[str, Optional[float]]:
    """Blocking. OCR one page image."""
    with _ocr_lock:
        result, _ = _get_engine()(png)
    return _reading_order(result or [])


def pages_needing_ocr(pdf_path: str, text_chars: Dict[int, int]) -> List[int]:
    """1-based pages with (almost) no text layer that do carry an image: scans, not blank pages."""
    doc = pymupdf.open(pdf_path)
    try:
        out = []
        for i in range(len(doc)):
            n = i + 1
            if text_chars.get(n, 0) < MIN_PAGE_CHARS and doc.load_page(i).get_image_info():
                out.append(n)
        return out
    finally:
        doc.close()


async def ocr_pages(
    pdf_path: str, pages: Iterable[int], on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> Tuple[Dict[int, PageOcr], List[int]]:
    """OCR the given pages (up to OCR_MAX_PAGES). Returns (results, pages skipped because of the limit)."""
    wanted = sorted(pages)
    todo, skipped = wanted[: settings.OCR_MAX_PAGES], wanted[settings.OCR_MAX_PAGES:]
    results: Dict[int, PageOcr] = {}
    doc = pymupdf.open(pdf_path)
    try:
        for k, n in enumerate(todo):
            if on_progress:
                await on_progress(k, len(todo))
            png = await asyncio.to_thread(
                lambda i=n - 1: doc.load_page(i).get_pixmap(matrix=pymupdf.Matrix(settings.OCR_RENDER_SCALE, settings.OCR_RENDER_SCALE), alpha=False).tobytes("png"))
            try:
                text, conf = await asyncio.to_thread(ocr_image, png)
            except Exception:  # noqa: BLE001 - one bad page must not fail the whole document
                logger.exception("OCR failed on page %d", n)
                text, conf = "", None
            results[n] = PageOcr(page=n, text=text, confidence=conf, lines=len(text.splitlines()))
    finally:
        doc.close()
    return results, skipped


def summarize(results: Dict[int, PageOcr], skipped: List[int]) -> Dict[str, Any]:
    confs = [r.confidence for r in results.values() if r.confidence is not None]
    return {
        "kind": "ocr", "engine": "rapidocr", "pages": sorted(results), "skipped_pages": skipped,
        "empty_pages": sorted(n for n, r in results.items() if not r.text.strip()),
        "avg_confidence": round(mean(confs), 3) if confs else None,
        "low_confidence_pages": sorted(n for n, r in results.items() if r.confidence is not None and r.confidence < settings.OCR_LOW_CONFIDENCE),
    }


def demote(rows: List[Dict[str, Any]], ocr_pages: Iterable[int]) -> int:
    """Nothing read from an OCR'd page may stay `verified`. Works on fields, parties, clauses and obligations."""
    pages = set(ocr_pages)
    n = 0
    for row in rows:
        page = row.get("page") if "page" in row else row.get("source_page")
        key = "status" if "status" in row else "verification_status" if "verification_status" in row else None
        if key is None or page not in pages or row.get(key) != "verified":
            continue
        row[key] = "needs_review"
        if row.get("confidence") is not None:
            row["confidence"] = min(float(row["confidence"]), 0.7)
        if "notes" in row:                                   # fields carry a notes dict
            row["notes"] = {**(row.get("notes") or {}), "ocr": OCR_NOTE}
        n += 1
    return n
