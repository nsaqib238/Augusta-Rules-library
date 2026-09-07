"""
Cheap PyMuPDF sampling of the text layer before calling Modal or accepting uploads.

Used to skip remote GPU work when the first pages have almost no extractable text
(image-only / scan-style PDFs), while leaving Modal enabled for normal standards.

Upload gate: reject scanned/image-only PDFs (digital or OCR-with-text-layer only).
Detection matches the backup Modal extractor: avg stripped chars/page on a sample.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class ScannedPdfRejectedError(Exception):
    """Raised when a PDF has no usable embedded text layer (scan / image-only)."""

    USER_MESSAGE = (
        "This PDF looks like a scan or image-only file without a searchable text layer. "
        "We only accept digital PDFs (text from the publisher) or PDFs that have already been OCR'd. "
        "Please upload a text-based PDF."
    )

    UNREADABLE_MESSAGE = "Could not read this PDF. Please check the file and try again."

    def __init__(self, message: str, *, stats: Optional["PdfTextPreflightResult"] = None):
        self.stats = stats
        super().__init__(message)


@dataclass(frozen=True)
class PdfTextPreflightResult:
    pages_sampled: int
    total_pages: int
    total_chars: int
    avg_chars_per_page: float


def sample_text_layer_stats(pdf_path: str, max_sample_pages: int = 10) -> Optional[PdfTextPreflightResult]:
    """
    Sum stripped text length on the first ``max_sample_pages`` pages (or fewer if short).

    Returns None if the PDF cannot be opened (caller should not skip Modal).
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not available; text preflight skipped")
        return None

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.warning("Text preflight: could not open PDF %s: %s", pdf_path, e)
        return None

    try:
        total_pages = len(doc)
        if total_pages <= 0:
            return PdfTextPreflightResult(0, 0, 0, 0.0)
        sample_pages = min(max(1, max_sample_pages), total_pages)
        total_chars = 0
        for page_num in range(sample_pages):
            page = doc[page_num]
            total_chars += len((page.get_text() or "").strip())
        avg = total_chars / sample_pages
        return PdfTextPreflightResult(
            pages_sampled=sample_pages,
            total_pages=total_pages,
            total_chars=total_chars,
            avg_chars_per_page=avg,
        )
    except Exception as e:
        logger.warning("Text preflight: read failed for %s: %s", pdf_path, e)
        return None
    finally:
        try:
            doc.close()
        except Exception:
            pass


def validate_digital_pdf_upload(
    pdf_path: str,
    *,
    min_avg_chars_per_page: float = 100.0,
    max_sample_pages: int = 10,
) -> PdfTextPreflightResult:
    """
    Reject scanned/image-only PDFs at upload time.

    Uses the same rule as the backup Modal ``is_digital_pdf``: average stripped
    chars per sampled page must exceed ``min_avg_chars_per_page`` (default 100).
    """
    stats = sample_text_layer_stats(pdf_path, max_sample_pages=max_sample_pages)
    if stats is None:
        raise ScannedPdfRejectedError(ScannedPdfRejectedError.UNREADABLE_MESSAGE)
    if stats.total_pages <= 0:
        raise ScannedPdfRejectedError(ScannedPdfRejectedError.USER_MESSAGE, stats=stats)
    if stats.avg_chars_per_page <= min_avg_chars_per_page:
        logger.info(
            "Upload rejected (scanned/image-only): avg_chars_per_page=%.1f sample=%s/%s total_chars=%s path=%s",
            stats.avg_chars_per_page,
            stats.pages_sampled,
            stats.total_pages,
            stats.total_chars,
            pdf_path,
        )
        raise ScannedPdfRejectedError(ScannedPdfRejectedError.USER_MESSAGE, stats=stats)
    return stats
