"""Presentation layer for the printable settlement report.

The report template needs a tighter, shorter, more scannable version of what is
held on each Item than the review screen does. This module builds that view. It
reads only; it never mutates an item, never touches a stored figure, and never
invents a fact. Everything here is either a direct copy of stored data, a
selection from it, or a sentence assembled from stored numbers.

Two jobs:

1.  Condense the AI research prose. `valuation_notes` is written for the
    assessor working the claim and runs long. An insurer-facing schedule wants
    80-140 words a line. Sentences are scored and dropped, never truncated
    mid-thought, and sentences carrying uncertainty, substitution or condition
    information are retained ahead of filler.

2.  Decide how much page each line deserves. A confidently identified item with
    a short note gets a compact row; anything uncertain, overridden, flagged or
    carrying an assessor note gets the full layout.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# --------------------------------------------------------------------- prose

# Word budget for the AI-derived prose on one standard line. Identification and
# valuation basis are counted against the same allowance, so a line with a long
# identification gets a correspondingly shorter assessment.
WORD_BUDGET = 140
MIN_ASSESSMENT_WORDS = 55
IDENTIFICATION_WORDS = 40

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

# Openers that carry no information in a report context. Stripped only at the
# start of a sentence and only where the remainder still reads as a sentence.
_FILLER = re.compile(
    r"^(?:based on (?:the )?(?:information|description|details|photographs?|photos?)"
    r"(?: provided| supplied| available)?|as noted(?: above)?|in summary|overall|"
    r"it (?:is |should be )?(?:worth )?not(?:ing|ed) that|please note(?: that)?)\s*[,:]?\s*",
    re.IGNORECASE,
)

# Sentences describing how the figure was arrived at. Pulled out of the
# assessment and shown under their own heading rather than buried in prose.
_BASIS = re.compile(
    r"\b(?:value(?:d|ation)? (?:based|derived|assessed)|priced (?:at|as|on)|"
    r"based on (?:current|typical|comparable|equivalent|market|retail)|"
    r"replacement (?:value|cost) (?:is |has been |was )?(?:based|derived|adopted)|"
    r"adopted (?:value|figure|price))\b",
    re.IGNORECASE,
)

# Signals that a sentence carries something an insurer or a dispute team needs:
# uncertainty, substitution, obsolescence, condition, or an assumption. Scored
# up so that when the budget bites, boilerplate is what goes.
_SIGNAL = re.compile(
    r"\b(?:discontinu\w+|no longer|superseded|obsolete|unavailable|not available|"
    r"unable to|cannot|could not|unclear|unknown|unidentified|uncertain|unsure|"
    r"assum\w+|estimat\w+|approximat\w+|equivalent|substitut\w+|closest|nearest|"
    r"comparable|second-?hand|refurbish\w+|pre-?existing|prior to|not(?: been)? "
    r"(?:working|operating)|working order|condition|corrosion|rust|damage[ds]?|"
    r"serial|model|discontinued|imported|no (?:brand|markings|label))\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    out = []
    for raw in _SENTENCE_SPLIT.split(text):
        s = _FILLER.sub("", raw).strip()
        if not s:
            continue
        s = s[0].upper() + s[1:]
        if not s.endswith((".", "!", "?")):
            s += "."
        out.append(s)
    return out


def _dedupe(sentences: list[str]) -> list[str]:
    """Drop repeats. The model restates its conclusion more than once."""
    seen: set[str] = set()
    out = []
    for s in sentences:
        key = re.sub(r"[^a-z0-9 ]", "", s.lower())
        key = re.sub(r"\s+", " ", key).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _words(text: str) -> int:
    return len(text.split())


def _fit(sentences: list[str], budget: int) -> str:
    """Keep whole sentences up to a word budget, highest value first.

    The first sentence is always kept — it carries the conclusion. The rest are
    ranked by information signal, then restored to their original order so the
    paragraph still reads properly.
    """
    if not sentences:
        return ""
    kept = [0]
    used = _words(sentences[0])
    ranked = sorted(
        range(1, len(sentences)),
        key=lambda i: (-len(_SIGNAL.findall(sentences[i])), i),
    )
    for i in ranked:
        cost = _words(sentences[i])
        if used + cost > budget:
            continue
        kept.append(i)
        used += cost
    return " ".join(sentences[i] for i in sorted(kept))


def _basis_sentence(item, apply_depreciation: bool, carried: str) -> str:
    """One line on how the figure was reached.

    Prefers what the researcher actually said. Falls back to a sentence built
    from stored fields — no figure appears here that is not already on the item.
    """
    if carried:
        return carried
    if item.replacement_value is None:
        return "No defensible replacement figure was established; the line is carried unvalued."
    basis = (
        "Current Australian retail cost of a new equivalent, inclusive of GST, "
        "at the date of this report."
    )
    if apply_depreciation and item.indemnity_value is not None:
        life = item.effective_life_years
        rate = item.effective_depreciation
        if life:
            basis += (
                f" Indemnity derived by straight-line depreciation of {rate * 100:.0f}% "
                f"over an assessed effective life of {_num(life)} years."
            )
        else:
            basis += f" Indemnity derived after depreciation of {rate * 100:.0f}%."
    return basis


def _prose(item, apply_depreciation: bool) -> dict[str, str]:
    identification = _fit(_dedupe(_sentences(item.identified_as)), IDENTIFICATION_WORDS)

    notes = _dedupe(_sentences(item.valuation_notes))
    basis_sentences = [s for s in notes if _BASIS.search(s)]
    assessment_sentences = [s for s in notes if not _BASIS.search(s)]

    carried_basis = basis_sentences[0] if basis_sentences else ""
    basis = _basis_sentence(item, apply_depreciation, carried_basis)

    budget = WORD_BUDGET - _words(identification) - _words(basis)
    assessment = _fit(assessment_sentences, max(budget, MIN_ASSESSMENT_WORDS))

    # An item that failed research has no notes but does have an error. Say so
    # plainly rather than leaving the assessment column blank.
    if not assessment and (item.error or "").strip():
        assessment = "Automated research did not return a defensible figure for this line."

    return {"identification": identification, "assessment": assessment, "basis": basis}


# ------------------------------------------------------------------- sources

# Retailers and manufacturers that come back often enough to be worth naming
# properly. Anything else falls through to a cleaned-up domain.
_SOURCE_NAMES = {
    "bunnings.com.au": "Bunnings",
    "ebay.com.au": "eBay AU",
    "ebay.com": "eBay",
    "amazon.com.au": "Amazon AU",
    "amazon.com": "Amazon",
    "jbhifi.com.au": "JB Hi-Fi",
    "harveynorman.com.au": "Harvey Norman",
    "thegoodguys.com.au": "The Good Guys",
    "officeworks.com.au": "Officeworks",
    "bhphotovideo.com": "B&H Photo",
    "reece.com.au": "Reece Plumbing",
    "totaltools.com.au": "Total Tools",
    "sydneytools.com.au": "Sydney Tools",
    "toolkitdepot.com.au": "Tool Kit Depot",
    "mitre10.com.au": "Mitre 10",
    "kmart.com.au": "Kmart",
    "target.com.au": "Target AU",
    "bigw.com.au": "Big W",
    "catch.com.au": "Catch",
    "appliancesonline.com.au": "Appliances Online",
    "winningappliances.com.au": "Winning Appliances",
    "rheem.com.au": "Rheem Australia",
    "productreview.com.au": "ProductReview",
    "gumtree.com.au": "Gumtree",
    "machineryhouse.com.au": "Machinery House",
    "blackwoods.com.au": "Blackwoods",
    "repco.com.au": "Repco",
    "supercheapauto.com.au": "Supercheap Auto",
    "anaconda.com.au": "Anaconda",
    "bcf.com.au": "BCF",
    "ikea.com": "IKEA",
    "fantasticfurniture.com.au": "Fantastic Furniture",
    "primera.com": "Primera Technology",
    "dremel.com": "Dremel",
    "makita.com.au": "Makita Australia",
}

_STRIP_TLD = re.compile(r"\.(?:com|net|org|co|gov|edu)?\.?(?:au|nz|uk|us)?$")


def _source_label(url: str) -> str:
    host = urlsplit(url if "//" in url else f"//{url}").netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host in _SOURCE_NAMES:
        return _SOURCE_NAMES[host]
    stem = _STRIP_TLD.sub("", host)
    stem = stem.split(".")[-1] if "." in stem else stem
    if not stem:
        return host or url
    return stem.replace("-", " ").title()


def _sources(item, limit: int = 3) -> dict:
    urls, seen = [], set()
    for url in item.source_list:
        url = url.strip()
        label = _source_label(url)
        if label.lower() in seen:
            continue
        seen.add(label.lower())
        urls.append((label, url))
    return {"shown": urls[:limit], "extra": max(len(urls) - limit, 0), "total": len(urls)}


# ------------------------------------------------------------------ assembly


def _num(value) -> str:
    """Trim a float that is really an integer: 25.0 -> 25, 7.5 -> 7.5."""
    if value is None:
        return ""
    return f"{value:g}"


def _meta_line(item) -> str:
    """Storage Shed · Tools · Water damage — never 'Location: ...'."""
    parts = []
    identifier = " ".join(p for p in (item.make, item.model) if p).strip()
    if identifier:
        parts.append(identifier)
    if item.category:
        parts.append(item.category)
    if item.location:
        parts.append(item.location)
    cause = (item.cause_of_damage or "").strip()
    if cause:
        parts.append(cause if "damage" in cause.lower() else f"{cause} damage")
    return " · ".join(parts)


def _depreciation_line(item, apply_depreciation: bool) -> str:
    if not apply_depreciation or item.replacement_value is None:
        return ""
    bits = []
    if item.estimated_age_years is not None:
        bits.append(f"Est. age {_num(item.estimated_age_years)} yrs")
    if item.effective_life_years:
        bits.append(f"Effective life {_num(item.effective_life_years)} yrs")
    if item.condition:
        bits.append(f"Condition {item.condition}")
    bits.append(f"Depreciation {item.effective_depreciation * 100:.0f}%")
    return " · ".join(bits)


def _photo_refs(item) -> str:
    refs = (item.photo_refs or "").strip()
    if not refs:
        return ""
    series = (item.photo_series or "").strip()
    return f"Photographs {refs}" + (f" ({series} attendance)" if series else "")


# How a review state should read on an insurer-facing document.
_STATUS_LABELS = {
    "ai suggested": "AI suggested",
    "reviewed": "Assessor reviewed",
    "overridden": "Assessor adjusted",
    "flagged": "Requires review",
    "insufficient evidence": "Insufficient evidence",
    "awaiting research": "Awaiting research",
    "excluded": "Excluded",
}


def _status(item) -> str:
    state = item.review_state
    return _STATUS_LABELS.get(state, state.capitalize())


def _is_compact(item, prose: dict, has_note: bool) -> bool:
    """A compact row is for lines nobody is going to argue about.

    Either the researcher was confident, or there is so little to say that the
    full layout would be four fifths white space. Anything flagged, overridden,
    unvalued or carrying an assessor note keeps the room to explain itself.
    """
    if item.replacement_value is None or has_note:
        return False
    if item.flagged or item.manual_override:
        return False
    if item.review_state in ("flagged", "insufficient evidence", "awaiting research"):
        return False
    words = _words(prose["identification"]) + _words(prose["assessment"])
    if (item.confidence or "").lower() == "high":
        return words <= 45
    return words <= 32


def build_items(job, photos: dict) -> list[dict]:
    """One view row per non-excluded item, in schedule order."""
    apply_dep = bool(job.apply_depreciation)
    rows: list[dict] = []
    n = 0

    for item in job.items:
        if item.excluded:
            continue
        n += 1

        prose = _prose(item, apply_dep)
        note = (item.assessor_note or "").strip()
        compact = _is_compact(item, prose, bool(note))

        resolved = [photos[k] for k in item.photo_key_list if k in photos]
        shown = resolved[: (1 if compact else 3)]
        caption = ""
        if not compact and resolved:
            caption = (resolved[0].get("caption") or "").strip()
            if len(caption) > 52:
                caption = caption[:49].rstrip(" ,.;") + "…"

        quantity = item.quantity or 1
        replacement = item.replacement_value
        indemnity = item.indemnity_value
        settle_each = indemnity if (apply_dep and indemnity is not None) else replacement

        rows.append(
            {
                "n": n,
                "item": item,
                "compact": compact,
                # Long lines are allowed to break across a page rather than
                # leaving half of one blank above them.
                "allow_split": not compact
                and _words(prose["assessment"]) + _words(prose["identification"]) > 120,
                "title": item.description,
                "meta": _meta_line(item),
                "identification": prose["identification"],
                "assessment": prose["assessment"],
                "basis": prose["basis"],
                "note": note,
                "photos": shown,
                "photo_extra": max(len(resolved) - len(shown), 0),
                "caption": caption,
                "photo_refs": _photo_refs(item),
                "sources": _sources(item),
                "depreciation": _depreciation_line(item, apply_dep),
                "quantity": quantity,
                "replacement_each": replacement,
                "replacement_line": None if replacement is None else replacement * quantity,
                "indemnity_line": None if indemnity is None else indemnity * quantity,
                "primary_label": "Indemnity" if apply_dep else "Replacement",
                "primary_value": None if settle_each is None else settle_each * quantity,
                "confidence": (item.confidence or "").lower(),
                "status": _status(item),
            }
        )
    return rows


def build_summary(rows: list[dict]) -> dict:
    """Counts for the closing summary. Derived, never invented."""
    return {
        "high": sum(1 for r in rows if r["confidence"] == "high"),
        "medium": sum(1 for r in rows if r["confidence"] == "medium"),
        "low": sum(1 for r in rows if r["confidence"] == "low"),
        "unrated": sum(1 for r in rows if not r["confidence"]),
        "reviewed": sum(1 for r in rows if r["item"].reviewed),
        "adjusted": sum(1 for r in rows if r["item"].manual_override),
        "flagged": sum(1 for r in rows if r["item"].flagged),
        "unvalued": sum(1 for r in rows if r["replacement_each"] is None),
    }
