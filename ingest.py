"""
Ingestion pipeline: XLSX inventory + photo-report PDFs -> normalised job data.

The photo PDFs are produced by Encircle/Rizon-style reporting tools. Each page
carries one or more photos, each labelled "(n)" where n is the photo number the
assessor references in the inventory spreadsheet's "Photo #" column. Captions,
when present, sit in the right-hand column at roughly the same vertical offset
as the photo they describe.
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

import openpyxl
import pymupdf

LABEL_RE = re.compile(r"^\((\d+)\)$")
FILENAME_RE = re.compile(r"^\d+\.(jpe?g|png)$", re.I)
# Boilerplate that appears on every page of the photo report.
CHROME_RE = re.compile(
    r"Created:|Location:|Title:|No\. Items:|Doc\. Id\.:|page \d+ of \d+", re.I
)
CAPTION_MIN_X = 340.0  # captions live in the right-hand column
MAX_THUMB_PX = 1600


@dataclass
class Photo:
    number: int
    series: str
    page: int
    caption: str
    image: bytes
    content_type: str
    width: int
    height: int

    @property
    def key(self) -> str:
        return f"{self.series}:{self.number}"


@dataclass
class InventoryItem:
    row: int
    description: str
    photo_numbers: list[int]
    series: str
    cause_of_damage: str
    location: str
    make: str
    model: str
    serial: str
    assessor_note: str = ""
    photo_keys: list[str] = field(default_factory=list)


@dataclass
class JobData:
    claim_reference: str
    site: str
    assessor_firm: str
    items: list[InventoryItem]
    photos: list[Photo]


def _clean(value) -> str:
    """Normalise a spreadsheet cell to a trimmed string."""
    if value is None:
        return ""
    text = str(value).strip()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text)


def parse_photo_numbers(value) -> list[int]:
    """'21, 22, 23' / '36,37' / 5 -> [21, 22, 23]."""
    if value is None:
        return []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [int(value)]
    return [int(n) for n in re.findall(r"\d+", str(value))]


def normalise_series(value: str) -> str:
    """Map the spreadsheet's attendance label to a stable series key."""
    text = _clean(value).lower()
    if text.startswith("second"):
        return "second"
    if text.startswith("third"):
        return "third"
    return "initial"


# Model/serial columns in these reports are frequently used by the assessor as a
# free-text remark rather than an actual identifier. Detect and reroute those so
# the valuation engine is never handed "Unsure if it was operating" as a model.
_NOTE_HINTS = (
    "unsure",
    "unable to test",
    "not to have been working",
    "not working",
    "most likely",
    "worn off",
    "contents inside",
    "prior to incident",
)


def _is_note(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _NOTE_HINTS)


def load_inventory(xlsx_path: str) -> tuple[list[InventoryItem], dict[str, str]]:
    workbook = openpyxl.load_workbook(xlsx_path, data_only=True)
    sheet = workbook.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))

    meta: dict[str, str] = {}
    header_index = None
    for index, row in enumerate(rows):
        cells = [_clean(c) for c in row]
        if cells and cells[0].lower() == "item" and "photo #" in " ".join(cells).lower():
            header_index = index
            break
        if cells and cells[0]:
            meta.setdefault("header_lines", "")
            meta["header_lines"] += cells[0] + "\n"
    if header_index is None:
        raise ValueError("Could not locate the inventory header row in the workbook.")

    header = [_clean(c).lower() for c in rows[header_index]]

    def col(*names: str) -> int | None:
        for name in names:
            for i, h in enumerate(header):
                if h == name or h.startswith(name):
                    return i
        return None

    idx = {
        "item": col("item"),
        "photo": col("photo #"),
        "series": col("photo series"),
        "cause": col("cause of damage"),
        "location": col("location"),
        "make": col("make"),
        "model": col("model"),
        "serial": col("serial number", "serial"),
    }

    items: list[InventoryItem] = []
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        description = _clean(row[idx["item"]]) if idx["item"] is not None else ""
        if not description:
            continue

        def get(key: str) -> str:
            i = idx[key]
            return _clean(row[i]) if i is not None and i < len(row) else ""

        model, serial = get("model"), get("serial")
        notes: list[str] = []
        if _is_note(model):
            notes.append(model)
            model = ""
        if _is_note(serial):
            notes.append(serial)
            serial = ""

        series = normalise_series(get("series"))
        numbers = parse_photo_numbers(row[idx["photo"]] if idx["photo"] is not None else None)
        items.append(
            InventoryItem(
                row=offset,
                description=description,
                photo_numbers=numbers,
                series=series,
                cause_of_damage=get("cause"),
                location=get("location"),
                make=get("make"),
                model=model,
                serial=serial,
                assessor_note=" ".join(dict.fromkeys(notes)),
                photo_keys=[f"{series}:{n}" for n in numbers],
            )
        )
    return items, meta


def _encode(page: pymupdf.Page, xref: int, doc: pymupdf.Document) -> tuple[bytes, str, int, int]:
    """Extract an embedded image, downscaling anything unreasonably large."""
    info = doc.extract_image(xref)
    data, ext = info["image"], info["ext"]
    width, height = info.get("width", 0), info.get("height", 0)
    if max(width, height) > MAX_THUMB_PX:
        try:
            pix = pymupdf.Pixmap(data)
            if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            scale = MAX_THUMB_PX / max(width, height)
            pix = pymupdf.Pixmap(pix, 0)
            data = pix.tobytes("jpeg", jpg_quality=82)
            ext = "jpeg"
            width, height = int(width * scale), int(height * scale)
        except Exception:
            pass  # keep the original bytes if re-encoding fails
    return data, f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}", width, height


def load_photos(pdf_path: str, series: str) -> list[Photo]:
    doc = pymupdf.open(pdf_path)
    photos: list[Photo] = []

    for page_number, page in enumerate(doc, start=1):
        blocks = page.get_text("blocks")

        labels: list[tuple[int, float]] = []
        captions: list[tuple[float, float, str]] = []
        for x0, y0, _x1, y1, text, *_ in blocks:
            stripped = text.strip()
            match = LABEL_RE.match(stripped)
            if match:
                labels.append((int(match.group(1)), y0))
                continue
            if x0 >= CAPTION_MIN_X and stripped and not CHROME_RE.search(stripped):
                captions.append((y0, y1, stripped))

        rects: list[tuple[int, pymupdf.Rect]] = []
        for image in page.get_images(full=True):
            xref = image[0]
            found = page.get_image_rects(xref)
            if found:
                rects.append((xref, found[0]))
        rects.sort(key=lambda pair: pair[1].y0)
        labels.sort(key=lambda pair: pair[1])

        for position, (xref, rect) in enumerate(rects):
            # Prefer a label whose y sits within the photo's band; the tool draws
            # the "(n)" marker over the top-left of its photo.
            number = None
            for candidate, y in labels:
                if rect.y0 - 12 <= y <= rect.y1 + 12:
                    number = candidate
                    labels = [pair for pair in labels if pair[0] != candidate]
                    break
            if number is None and position < len(labels):
                number = labels[position][0]
            if number is None:
                continue

            caption = " ".join(
                text
                for y0, y1, text in captions
                if y1 >= rect.y0 - 20 and y0 <= rect.y1 + 20
            )
            caption = re.sub(r"\s+", " ", caption).strip()

            data, content_type, width, height = _encode(page, xref, doc)
            photos.append(
                Photo(
                    number=number,
                    series=series,
                    page=page_number,
                    caption=caption,
                    image=data,
                    content_type=content_type,
                    width=width,
                    height=height,
                )
            )

    # Deduplicate: the same photo number should map to a single image.
    seen: dict[str, Photo] = {}
    for photo in photos:
        seen.setdefault(photo.key, photo)
    return sorted(seen.values(), key=lambda p: (p.series, p.number))


def build_job(
    xlsx_path: str,
    pdf_sources: Iterable[tuple[str, str]],
    claim_reference: str = "",
    site: str = "",
    assessor_firm: str = "",
) -> JobData:
    items, meta = load_inventory(xlsx_path)
    photos: list[Photo] = []
    for path, series in pdf_sources:
        photos.extend(load_photos(path, series))

    header_lines = meta.get("header_lines", "")
    if not assessor_firm and header_lines:
        assessor_firm = header_lines.strip().splitlines()[0]

    # Fold assessor captions into items that lack a make/model, since the caption
    # often carries the only identifying detail.
    by_key = {p.key: p for p in photos}
    for item in items:
        if item.assessor_note:
            continue
        note = next(
            (
                by_key[k].caption
                for k in item.photo_keys
                if k in by_key and by_key[k].caption
            ),
            "",
        )
        if note:
            item.assessor_note = note

    return JobData(
        claim_reference=claim_reference,
        site=site,
        assessor_firm=assessor_firm,
        items=items,
        photos=photos,
    )
