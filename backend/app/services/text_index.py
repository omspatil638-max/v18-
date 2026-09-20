"""
text_index.py
-------------
The verification backbone of ContractLens. Pure functions, no I/O.

  * normalize_with_map   – whitespace/case/quote/dash/hyphenation-insensitive normalization
                           that keeps a map back to original character offsets
  * DocIndex.find        – "does this quote exist in the document?" and *where* (page, section)
  * build_sections       – heading detection so every location resolves to a section label
  * chunk_sections       – section-aware chunking for extraction and retrieval

Page numbers and section labels are always derived here, from where a quote actually
matched in the document. They are never taken from LLM output.
"""

import bisect
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MIN_QUOTE_CHARS = 12      # shorter quotes are too ambiguous to count as evidence
MIN_LOOSE_CHARS = 10
MAX_ELLIPSIS_GAP = 1200   # fragments of an elided quote must sit this close together in the document
_ELLIPSIS = re.compile(r"\.{3,}|…")

_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
}
_DASHES = dict.fromkeys("‐‑‒–—―−", "-")
_PAGE_SPLIT = re.compile(r"^---\s*PAGE\s+(\d+)\s*---\s*$", re.IGNORECASE | re.MULTILINE)


# ─── Normalization ────────────────────────────────────────────────────────────

def normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """
    Normalize text for comparison and return (normalized, index_map) where
    index_map[i] is the offset in the ORIGINAL text of normalized char i.

    Normalization: NFKC, case-fold, curly quotes/typographic dashes -> ASCII,
    soft hyphens removed, "word-<newline>continuation" de-hyphenated,
    all whitespace runs -> one space, ends trimmed.
    """
    out: List[str] = []
    idx: List[int] = []
    n = len(text)
    i = 0
    prev_space = True  # drops leading whitespace
    while i < n:
        ch = text[i]

        # Line-wrap hyphenation: "termina-\n tion"
        if ch == "-" and i > 0 and text[i - 1].isalpha():
            j = i + 1
            while j < n and text[j] in " \t\r":
                j += 1
            if j < n and text[j] == "\n":
                k = j + 1
                while k < n and text[k] in " \t\r":
                    k += 1
                if k < n and text[k].islower():
                    i = k
                    continue

        if ch == "­":  # soft hyphen
            i += 1
            continue

        ch = _DASHES.get(_QUOTES.get(ch, ch), _QUOTES.get(ch, ch))
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx.append(i)
                prev_space = True
            i += 1
            continue

        for c in unicodedata.normalize("NFKC", ch).casefold():
            out.append(c)
            idx.append(i)
        prev_space = False
        i += 1

    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def pages_from_raw_text(raw: Optional[str]) -> List[Tuple[int, str]]:
    """Split stored text containing '--- PAGE n ---' markers back into (page, text) pairs."""
    if not raw:
        return []
    parts = _PAGE_SPLIT.split(raw)
    if len(parts) == 1:
        return [(1, raw)]
    pages = []
    for k in range(1, len(parts) - 1, 2):
        pages.append((int(parts[k]), parts[k + 1].strip("\n")))
    return pages


# ─── Sections ─────────────────────────────────────────────────────────────────

@dataclass
class Section:
    index: int
    number: Optional[str]
    heading: str
    label: str
    page_start: int
    offset_start: int
    page_end: int
    text: str


_SMALL_WORDS = {"and", "or", "of", "for", "to", "the", "in", "on", "by", "with", "a", "an", "at", "as", "from"}
_KEYWORD_HEADING = re.compile(
    r"^(ARTICLE|Article|SECTION|Section|SCHEDULE|Schedule|EXHIBIT|Exhibit)\s+"
    r"([0-9]+(?:\.[0-9]+)*|[IVXLC]+|[A-Z])\s*[.:\-–—]?\s*(.*)$"
)
_NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+([A-Z].{1,80})$")


def _looks_like_title(text: str) -> bool:
    t = text.strip()
    if not t or t[-1] in ".;,":
        return False
    words = t.split()
    if len(words) > 12:
        return False
    capped = sum(1 for w in words if w[:1].isupper() or w.lower() in _SMALL_WORDS or not w[:1].isalpha())
    return capped / len(words) >= 0.6


def _heading_of(line: str) -> Optional[Tuple[str, Optional[str], str]]:
    """Return (label_prefix_keyword, number, heading_text) if the line is a heading."""
    s = line.strip()
    if not s or len(s) > 100:
        return None
    m = _KEYWORD_HEADING.match(s)
    if m:
        keyword = m.group(1).title()
        title = m.group(3).strip()
        if len(title) > 80 or (title and not _looks_like_title(title) and title[-1] in ".;,"):
            return None
        return keyword, m.group(2), title
    m = _NUMBERED_HEADING.match(s)
    if m and _looks_like_title(m.group(2)):
        return "Section", m.group(1), m.group(2).strip().rstrip(":")
    letters = re.sub(r"[^A-Za-z]", "", s)
    if s.isupper() and 3 <= len(s) <= 60 and len(letters) >= 3 and not s.startswith("---"):
        return "", None, s.title()
    return None


def _make_label(keyword: str, number: Optional[str], heading: str) -> str:
    if number:
        base = f"{keyword} {number}"
        return f"{base} — {heading}" if heading else base
    return heading


def build_sections(pages: List[Tuple[int, str]]) -> List[Section]:
    """Split the document into sections by detected headings (fallback: one per page)."""
    lines: List[Tuple[int, int, str]] = []  # (page, offset_in_page, line_text)
    for page_no, text in pages:
        offset = 0
        for raw_line in text.splitlines(keepends=True):
            lines.append((page_no, offset, raw_line))
            offset += len(raw_line)

    heads: List[Tuple[int, str, Optional[str], str]] = []  # (line_idx, keyword, number, heading)
    for li, (_, _, line) in enumerate(lines):
        h = _heading_of(line)
        if h:
            heads.append((li, *h))

    sections: List[Section] = []

    def add(start_li: int, end_li: int, keyword: str, number: Optional[str], heading: str, label: str):
        seg = lines[start_li:end_li]
        if not seg or not "".join(l for _, _, l in seg).strip():
            return
        sections.append(Section(
            index=len(sections), number=number, heading=heading, label=label,
            page_start=seg[0][0], offset_start=seg[0][1], page_end=seg[-1][0],
            text="".join(l for _, _, l in seg),
        ))

    if not heads:
        for page_no, text in pages:
            if text.strip():
                sections.append(Section(
                    index=len(sections), number=None, heading=f"Page {page_no}",
                    label=f"Page {page_no}", page_start=page_no, offset_start=0,
                    page_end=page_no, text=text,
                ))
        return sections

    if heads[0][0] > 0:
        add(0, heads[0][0], "", None, "Preamble", "Preamble")
    for hi, (li, keyword, number, heading) in enumerate(heads):
        end = heads[hi + 1][0] if hi + 1 < len(heads) else len(lines)
        add(li, end, keyword, number, heading, _make_label(keyword, number, heading))
    return sections


# ─── Document index ───────────────────────────────────────────────────────────

@dataclass
class QuoteMatch:
    page_start: int
    page_end: int
    section: Optional[str]
    section_number: Optional[str]
    matched_text: str
    kind: str  # "exact" (normalized) or "loose" (ignores punctuation/hyphenation)


class DocIndex:
    """Searchable, position-aware view of a document."""

    def __init__(self, pages: List[Tuple[int, str]]):
        self.pages = pages
        self._page_text = dict(pages)
        norm_parts: List[str] = []
        self._map_page: List[int] = []
        self._map_off: List[int] = []
        for page_no, text in pages:
            n, m = normalize_with_map(text)
            if not n:
                continue
            if norm_parts:
                norm_parts.append(" ")
                self._map_page.append(page_no)
                self._map_off.append(0)
            norm_parts.append(n)
            self._map_page.extend([page_no] * len(n))
            self._map_off.extend(m)
        self.norm = "".join(norm_parts)

        self._loose_chars: List[str] = []
        self._loose_to_norm: List[int] = []
        for i, c in enumerate(self.norm):
            if c.isalnum():
                self._loose_chars.append(c)
                self._loose_to_norm.append(i)
        self.loose = "".join(self._loose_chars)

        self.sections = build_sections(pages)
        self._section_keys = [(s.page_start, s.offset_start) for s in self.sections]

    # ── lookups ──────────────────────────────────────────────────────────────
    def section_at(self, page: int, offset: int) -> Optional[Section]:
        i = bisect.bisect_right(self._section_keys, (page, offset)) - 1
        return self.sections[i] if i >= 0 else None

    def contains(self, text: str) -> bool:
        """Normalized substring test for short literals (party names, amounts)."""
        t = normalize(text)
        return bool(t) and t in self.norm

    def _locate(self, q: str, start_at: int = 0) -> Optional[Tuple[int, int, str]]:
        """Find one normalized fragment at or after `start_at`. Returns (pos, end, kind) or None."""
        if len(q) < MIN_QUOTE_CHARS:
            return None
        pos = self.norm.find(q, start_at)
        if pos >= 0:
            return pos, pos + len(q), "exact"
        ql = "".join(c for c in q if c.isalnum())
        if len(ql) < MIN_LOOSE_CHARS:
            return None
        lp = self.loose.find(ql, bisect.bisect_left(self._loose_to_norm, start_at))
        if lp < 0:
            return None
        return self._loose_to_norm[lp], self._loose_to_norm[lp + len(ql) - 1] + 1, "loose"

    def find(self, quote: Optional[str]) -> Optional[QuoteMatch]:
        """
        Locate a quote in the document. None means it is NOT verifiably present.

        Models often abbreviate a quote with an ellipsis ("...fee of INR 60,000... invoice by the
        second day"). Such a quote is accepted only if EVERY fragment is found, in order, with the
        fragments no more than MAX_ELLIPSIS_GAP characters apart. The match then covers the real
        document text from the first fragment to the last, so what is displayed is what the
        document says, never the model's stitched-together version.
        """
        if not quote:
            return None
        fragments = [normalize(f) for f in _ELLIPSIS.split(quote)]
        fragments = [f for f in fragments if f]
        if not fragments:
            return None

        first = self._locate(fragments[0])
        if first is None:
            return None
        pos, end, kind = first
        if len(fragments) > 1:
            kind = "elided"
            for frag in fragments[1:]:
                nxt = self._locate(frag, end)
                if nxt is None or nxt[0] - end > MAX_ELLIPSIS_GAP:
                    return None
                end = nxt[1]

        page_start = self._map_page[pos]
        page_end = self._map_page[end - 1]
        off_start = self._map_off[pos]
        page_text = self._page_text.get(page_start, "")
        off_end = self._map_off[end - 1] + 1 if page_end == page_start else len(page_text)
        section = self.section_at(page_start, off_start)
        return QuoteMatch(
            page_start=page_start,
            page_end=page_end,
            section=section.label if section else None,
            section_number=section.number if section else None,
            matched_text=page_text[off_start:off_end],
            kind=kind,
        )


# ─── Chunking ─────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    index: int
    text: str
    page_start: int
    page_end: int
    section_labels: List[str] = field(default_factory=list)

    @property
    def primary_section(self) -> Optional[str]:
        return self.section_labels[0] if self.section_labels else None


def _split_long(text: str, max_chars: int, overlap: int) -> List[str]:
    """Split one oversized section at line boundaries with a small overlap."""
    lines = text.splitlines(keepends=True)
    pieces: List[str] = []
    buf: List[str] = []
    size = 0
    for line in lines:
        while len(line) > max_chars:  # a single enormous line: hard split
            if buf:
                pieces.append("".join(buf))
                buf, size = [], 0
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        if size + len(line) > max_chars and buf:
            pieces.append("".join(buf))
            tail: List[str] = []
            tsize = 0
            for prev in reversed(buf):
                if tsize + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tsize += len(prev)
            buf, size = tail, tsize
        buf.append(line)
        size += len(line)
    if buf and "".join(buf).strip():
        pieces.append("".join(buf))
    return pieces


def chunk_sections(
    sections: List[Section],
    max_chars: int,
    overlap: int = 300,
    pack: bool = True,
) -> List[Chunk]:
    """
    Section-aware chunking.
    pack=True  : merge consecutive small sections up to max_chars (fewer LLM calls)
    pack=False : never merge across sections (retrieval chunks stay section-pure)
    Nothing is ever dropped: the concatenation of chunks covers the whole document.
    """
    chunks: List[Chunk] = []
    cur_text: List[str] = []
    cur_size = 0
    cur_pages: List[int] = []
    cur_labels: List[str] = []

    def flush():
        nonlocal cur_text, cur_size, cur_pages, cur_labels
        if cur_text and "".join(cur_text).strip():
            chunks.append(Chunk(
                index=len(chunks), text="".join(cur_text).strip("\n"),
                page_start=min(cur_pages), page_end=max(cur_pages), section_labels=cur_labels,
            ))
        cur_text, cur_size, cur_pages, cur_labels = [], 0, [], []

    for sec in sections:
        if len(sec.text) > max_chars:
            flush()
            for piece in _split_long(sec.text, max_chars, overlap):
                chunks.append(Chunk(
                    index=len(chunks), text=piece.strip("\n"),
                    page_start=sec.page_start, page_end=sec.page_end, section_labels=[sec.label],
                ))
            continue
        if cur_text and (not pack or cur_size + len(sec.text) > max_chars):
            flush()
        cur_text.append(sec.text if sec.text.endswith("\n") else sec.text + "\n")
        cur_size += len(sec.text)
        cur_pages += [sec.page_start, sec.page_end]
        cur_labels.append(sec.label)
    flush()
    return chunks
