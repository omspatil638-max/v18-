"""
pdf_highlight_service.py
------------------------
Locate a quote on a PDF page as highlight rectangles, and render the page to an image.

The matcher works on the alphanumeric character stream of the page's words. It therefore
survives line wraps, hyphenated line breaks, curly vs straight quotes, ligatures and extra
whitespace: the same tolerance the quote-exists check uses. A quote abbreviated with an ellipsis
is highlighted fragment by fragment. Coordinates are returned normalised to 0..1 of the page, so
the client can overlay them at any zoom level.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pymupdf

MIN_FRAGMENT_CHARS = 8
MAX_RENDER_PIXELS = 4_500_000        # ~ 2100 x 2100: keeps a page image well under a few MB
_ELLIPSIS = re.compile(r"\.{3,}|…")


@dataclass
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def as_dict(self) -> Dict[str, float]:
        return {"x0": round(self.x0, 5), "y0": round(self.y0, 5), "x1": round(self.x1, 5), "y1": round(self.y1, 5)}


def _alnum(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def _page_stream(page: "pymupdf.Page") -> Tuple[str, List[int], List[tuple]]:
    """(alphanumeric stream, char->word index, words) for a page."""
    words = page.get_text("words")            # (x0, y0, x1, y1, text, block, line, word_no)
    chars: List[str] = []
    owner: List[int] = []
    for wi, w in enumerate(words):
        a = _alnum(w[4])
        chars.append(a)
        owner.extend([wi] * len(a))
    return "".join(chars), owner, words


def _line_rects(words: List[tuple], first: int, last: int, width: float, height: float) -> List[Rect]:
    """One rectangle per text line covering words[first..last], normalised to the page."""
    by_line: Dict[Tuple[int, int], List[tuple]] = {}
    for w in words[first:last + 1]:
        by_line.setdefault((w[5], w[6]), []).append(w)
    rects = []
    for group in by_line.values():
        rects.append(Rect(
            min(w[0] for w in group) / width, min(w[1] for w in group) / height,
            max(w[2] for w in group) / width, max(w[3] for w in group) / height,
        ))
    return sorted(rects, key=lambda r: (round(r.y0, 3), r.x0))


def find_quote_rects(page: "pymupdf.Page", quote: str) -> List[Rect]:
    """Highlight rectangles for `quote` on this page; [] if it is not (fully) present here."""
    width, height = page.rect.width, page.rect.height
    if not quote or not width or not height:
        return []
    stream, owner, words = _page_stream(page)
    fragments = [_alnum(f) for f in _ELLIPSIS.split(quote)]
    fragments = [f for f in fragments if len(f) >= MIN_FRAGMENT_CHARS]
    if not fragments:
        return []

    rects: List[Rect] = []
    cursor = 0
    for frag in fragments:
        pos = stream.find(frag, cursor)
        if pos < 0:
            return []                       # every fragment must be here, in order, or we claim nothing
        end = pos + len(frag) - 1
        rects += _line_rects(words, owner[pos], owner[end], width, height)
        cursor = end + 1
    return rects


def page_size(page: "pymupdf.Page") -> Tuple[float, float]:
    return float(page.rect.width), float(page.rect.height)


def render_png(page: "pymupdf.Page", scale: float) -> bytes:
    """PNG of the page. Scale is clamped so a huge page cannot produce a huge image."""
    scale = max(0.5, min(float(scale), 3.0))
    w, h = page.rect.width * scale, page.rect.height * scale
    if w * h > MAX_RENDER_PIXELS:
        scale *= (MAX_RENDER_PIXELS / (w * h)) ** 0.5
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return pix.tobytes("png")
