"""Turn a file the assessor uploads into evidence images the platform can store.

Everything downstream of ingestion — the row thumbnails, the evidence tab, the
printed schedule — expects a Photo row holding image bytes. So an upload is
normalised to the same shape:

    a JPEG/PNG            -> one evidence page
    a PDF (quote, receipt,
    manual, spec sheet)   -> one evidence page per page, rendered at 150 dpi

That keeps a receipt visible in the schedule alongside the photographs rather
than sitting in a separate attachments list nobody opens. The original filename
is carried through so the line can still say where the figure came from.

Nothing here writes to the database; the caller owns that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pymupdf

# A phone photo is 4-6 MB and a scanned PDF can be far larger. The cap is on
# what is accepted; what is stored is smaller again after downscaling.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Photographs are stored as blobs on the job, so a claim's database footprint
# is the sum of its evidence. 1600px is enough for a full-page print at 300dpi
# and matches what PDF ingestion already stores.
MAX_PX = 1600

# Rendering resolution for PDF pages. 150dpi keeps invoice text legible when
# the schedule is printed without making an A4 page a 3 MB image.
PDF_DPI = 150

# A 60-page manual is not evidence. Take the front of the document and say so.
MAX_PDF_PAGES = 8

JPEG_QUALITY = 82


class UnsupportedUpload(Exception):
    """The file cannot be turned into evidence. The message is shown to the user."""


@dataclass
class Page:
    data: bytes
    content_type: str
    width: int
    height: int
    label: str


def _sniff(blob: bytes) -> str | None:
    """Identify the format from its magic bytes, not its file extension."""
    if blob[:5] == b"%PDF-":
        return "pdf"
    if blob[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if blob[:2] == b"BM":
        return "bmp"
    if blob[4:8] == b"ftyp" and blob[8:12] in (
        b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"
    ):
        return "heic"
    return None


def _shrink(pix: pymupdf.Pixmap) -> pymupdf.Pixmap:
    """Scale the pixmap so its longest edge is exactly MAX_PX, if it is over.

    Deliberately not Pixmap.shrink(), which only halves: a 4200px photo would
    land at 1050px and throw away more than half the detail it was allowed to
    keep.
    """
    longest = max(pix.width, pix.height)
    if longest <= MAX_PX:
        return pix
    scale = MAX_PX / longest
    width = max(1, round(pix.width * scale))
    height = max(1, round(pix.height * scale))
    try:
        return pymupdf.Pixmap(pix, width, height, None)
    except Exception:  # noqa: BLE001 - fall back to halving rather than fail
        pix.shrink(math.ceil(math.log2(longest / MAX_PX)))
        return pix


def _flatten(pix: pymupdf.Pixmap) -> pymupdf.Pixmap:
    """Drop alpha and convert CMYK, so the result is JPEG-encodable."""
    if pix.n - pix.alpha >= 4 or pix.colorspace is None:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    return pix


def _from_pdf(blob: bytes, filename: str) -> list[Page]:
    try:
        doc = pymupdf.open(stream=blob, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - surfaced to the assessor
        raise UnsupportedUpload(f"That PDF could not be opened ({exc}).") from exc

    if doc.needs_pass:
        raise UnsupportedUpload(
            "That PDF is password protected. Remove the password and upload it again."
        )
    if doc.page_count == 0:
        raise UnsupportedUpload("That PDF has no pages.")

    total = doc.page_count
    pages: list[Page] = []
    for index in range(min(total, MAX_PDF_PAGES)):
        page = doc[index]
        # Render straight to the largest size allowed rather than rendering
        # high and scaling down: an A4 page at 150dpi is 1755px tall, and
        # scaling that to fit 1600 would cost text legibility for nothing.
        longest_inches = max(page.rect.width, page.rect.height) / 72.0
        dpi = int(min(PDF_DPI, MAX_PX / longest_inches)) if longest_inches else PDF_DPI
        pix = _flatten(_shrink(page.get_pixmap(dpi=max(dpi, 36))))
        label = filename if total == 1 else f"{filename} — page {index + 1} of {total}"
        pages.append(
            Page(
                data=pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY),
                content_type="image/jpeg",
                width=pix.width,
                height=pix.height,
                label=label,
            )
        )
    doc.close()
    return pages


def _from_image(blob: bytes, filename: str, kind: str) -> list[Page]:
    try:
        pix = pymupdf.Pixmap(blob)
    except Exception as exc:  # noqa: BLE001 - surfaced to the assessor
        raise UnsupportedUpload(f"That image could not be read ({exc}).") from exc

    # An already-sensible JPEG or PNG is stored as uploaded. Re-encoding it
    # would cost quality for nothing.
    if kind in ("jpeg", "png") and max(pix.width, pix.height) <= MAX_PX:
        return [Page(blob, f"image/{kind}", pix.width, pix.height, filename)]

    pix = _flatten(_shrink(pix))
    return [
        Page(
            data=pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY),
            content_type="image/jpeg",
            width=pix.width,
            height=pix.height,
            label=filename,
        )
    ]


def render(filename: str, blob: bytes) -> list[Page]:
    """Evidence pages for one uploaded file, or raise UnsupportedUpload."""
    filename = (filename or "upload").strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not blob:
        raise UnsupportedUpload(f"{filename} is empty.")
    if len(blob) > MAX_UPLOAD_BYTES:
        raise UnsupportedUpload(
            f"{filename} is {len(blob) / 1_048_576:.0f} MB. "
            f"The limit is {MAX_UPLOAD_BYTES // 1_048_576} MB per file."
        )

    kind = _sniff(blob)
    if kind == "pdf":
        return _from_pdf(blob, filename)
    if kind == "heic":
        raise UnsupportedUpload(
            f"{filename} is a HEIC file, which iPhones produce by default. "
            "Export it as JPEG, or set Camera > Formats to Most Compatible, "
            "then upload it again."
        )
    if kind is None:
        raise UnsupportedUpload(
            f"{filename} is not a supported file. Upload a JPEG, PNG, WEBP, "
            "GIF, BMP or PDF."
        )
    return _from_image(blob, filename, kind)
