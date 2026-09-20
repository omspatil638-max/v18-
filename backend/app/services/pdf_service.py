import os
import pymupdf  # PyMuPDF
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

@dataclass
class PDFPageText:
    page_number: int  # 1-indexed
    text: str
    char_count: int

@dataclass
class PDFExtractionResult:
    is_valid_text_pdf: bool
    page_count: int
    total_char_count: int
    pages: List[PDFPageText]
    full_text: str
    error_message: Optional[str] = None

class PDFService:
    """Handles PDF file inspection, page-preserving text extraction, and text-layer detection."""

    MIN_TOTAL_CHARS_THRESHOLD = 50  # If total text across all pages < 50 chars, consider image-only/scanned PDF

    @classmethod
    def extract_text_from_pdf(cls, file_path: str) -> PDFExtractionResult:
        """
        Extract text page-by-page from a PDF file using PyMuPDF.
        Preserves 1-indexed page numbers.
        Detects if PDF is scanned / has no extractable text layer.
        """
        if not os.path.exists(file_path):
            return PDFExtractionResult(
                is_valid_text_pdf=False,
                page_count=0,
                total_char_count=0,
                pages=[],
                full_text="",
                error_message=f"File not found: {file_path}"
            )

        try:
            doc = pymupdf.open(file_path)
            if doc.needs_pass:
                doc.close()
                return PDFExtractionResult(
                    is_valid_text_pdf=False, page_count=0, total_char_count=0, pages=[], full_text="",
                    error_message="This PDF is password-protected. Remove the password and upload it again.",
                )
            page_count = len(doc)

            if page_count == 0:
                doc.close()
                return PDFExtractionResult(
                    is_valid_text_pdf=False,
                    page_count=0,
                    total_char_count=0,
                    pages=[],
                    full_text="",
                    error_message="PDF file has 0 pages"
                )

            extracted_pages: List[PDFPageText] = []
            full_text_parts: List[str] = []
            total_chars = 0

            for page_num in range(page_count):
                page = doc.load_page(page_num)
                # Extract plain text from page
                page_text = page.get_text("text").replace("\x00", "").strip()
                char_count = len(page_text)
                total_chars += char_count

                # 1-indexed page number
                pdf_page = PDFPageText(
                    page_number=page_num + 1,
                    text=page_text,
                    char_count=char_count
                )
                extracted_pages.append(pdf_page)
                if page_text:
                    full_text_parts.append(f"--- PAGE {page_num + 1} ---\n{page_text}")

            doc.close()

            full_text = "\n\n".join(full_text_parts)

            # Check if PDF has extractable text layer
            is_valid = total_chars >= cls.MIN_TOTAL_CHARS_THRESHOLD

            error_msg = None
            if not is_valid:
                error_msg = (
                    f"Unsupported PDF — no text layer found. "
                    f"Total extracted characters across {page_count} page(s) is {total_chars}. "
                    f"Scanned or image-only PDFs are not supported in this MVP."
                )

            return PDFExtractionResult(
                is_valid_text_pdf=is_valid,
                page_count=page_count,
                total_char_count=total_chars,
                pages=extracted_pages,
                full_text=full_text,
                error_message=error_msg
            )

        except Exception as e:
            return PDFExtractionResult(
                is_valid_text_pdf=False,
                page_count=0,
                total_char_count=0,
                pages=[],
                full_text="",
                error_message=f"Failed to parse PDF file: {str(e)}"
            )

pdf_service = PDFService()
