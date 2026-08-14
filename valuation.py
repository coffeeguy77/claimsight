"""
AI valuation engine.

For each damaged item the engine researches the current Australian retail cost of
an equivalent new item, estimates the age of the damaged unit, and derives an
indemnity (depreciated) value alongside the replacement value. Every figure is
returned with a confidence rating and the source URLs it was derived from so the
assessor can defend the line to an insurer.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, asdict

import anthropic

MODEL = os.environ.get("VALUATION_MODEL", "claude-sonnet-5")
MAX_RETRIES = 3
# Floor value as a fraction of replacement cost: even a fully depreciated item in
# working order retains some worth. Australian loss adjusting convention.
SALVAGE_FLOOR = 0.10

SYSTEM_PROMPT = """You are a senior contents loss adjuster working Australian insurance claims. \
You value damaged household and commercial contents for settlement purposes.

For each item you are given, you must:

1. IDENTIFY the item as precisely as the supplied make/model/description allows. If the \
make or model is misspelled (assessors type these in the field), correct it and say so. \
If the item is generic, value a mid-range equivalent, not a premium one.

2. RESEARCH the current Australian retail price, in AUD including GST, to buy a NEW \
equivalent item today. Prefer Australian retailers and Australian pricing. If the exact \
model is discontinued, price the closest current-production equivalent and note the \
substitution. Never convert a US price and present it as Australian retail.

3. ESTIMATE the age of the damaged item in years, using model release dates, styling, \
discontinued status and the assessor's notes. Be explicit that this is an estimate.

4. ASSIGN an effective life in years for the item category, using ordinary Australian \
loss-adjusting practice (e.g. consumer electronics 5-7, whitegoods 10-12, power tools \
8-10, furniture 12-15, books/media 10, structural or plumbing components 15-25).

CONTEXT FOR AGE ESTIMATION:
The contents come from long-term storage and are of mixed vintage. Domestic and hobby
items (consumer electronics, hand tools, household goods, books, media) skew old — often
15 to 30 years — and should be aged accordingly unless the model clearly indicates
otherwise. Commercial catering and trade equipment (pizza ovens, bain maries, EFTPOS
terminals, arcade machines, commercial cleaning plant, generators) is more likely to be
a recently stored business fitout: age these at roughly 5 to 12 years unless there is
specific evidence they are older.

RULES:
- All money is AUD including GST. Never return a price in any other currency.
- If the assessor noted the item may not have been working before the incident, say so in \
  your notes and mark confidence "low" — the insurer will likely contest it.
- Quantities: value ONE unit. The platform multiplies by quantity itself.
- If you genuinely cannot find a price, return replacement_value_aud as null and explain. \
  Do NOT invent a number.
- Prefer being defensible over being generous. Every figure may be challenged.

Return ONLY a JSON object, no prose before or after, with exactly these keys:
{
  "identified_as": "string - what you concluded the item is",
  "category": "string - short category label",
  "replacement_value_aud": number or null,
  "estimated_age_years": number or null,
  "effective_life_years": number,
  "confidence": "high" | "medium" | "low",
  "notes": "string - substitutions, caveats, condition and pre-existing-damage remarks",
  "sources": ["url", "url"]
}"""


@dataclass
class ValuationResult:
    identified_as: str = ""
    category: str = ""
    replacement_value_aud: float | None = None
    estimated_age_years: float | None = None
    effective_life_years: float = 10.0
    confidence: str = "low"
    notes: str = ""
    sources: list[str] | None = None
    indemnity_value_aud: float | None = None
    depreciation_rate: float | None = None
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=key)


def build_prompt(item, photo_captions: list[str] | None = None) -> str:
    lines = [f"ITEM: {item.description}"]
    if item.quantity and item.quantity > 1:
        lines.append(f"QUANTITY ON CLAIM: {item.quantity} (value ONE unit)")
    for label, value in (
        ("MAKE", item.make),
        ("MODEL", item.model),
        ("SERIAL", item.serial),
        ("LOCATION", item.location),
        ("CAUSE OF DAMAGE", item.cause_of_damage),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if item.assessor_note:
        lines.append(f"ASSESSOR NOTE: {item.assessor_note}")
    for caption in (photo_captions or [])[:4]:
        if caption:
            lines.append(f"PHOTO CAPTION: {caption}")
    lines.append(
        "\nThe item is non-restorable. Value it for settlement under an Australian "
        "contents policy. Respond with the JSON object only."
    )
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model response: {text[:200]}")
    return json.loads(text[start : end + 1])


def _call_model(client: anthropic.Anthropic, prompt: str, use_search: bool) -> str:
    kwargs: dict = {
        "model": MODEL,
        "max_tokens": 2000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    if use_search:
        kwargs["tools"] = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 6,
                "user_location": {"type": "approximate", "country": "AU"},
            }
        ]
    response = client.messages.create(**kwargs)
    return "".join(block.text for block in response.content if block.type == "text")


def compute_indemnity(
    replacement: float | None,
    age_years: float | None,
    effective_life: float | None,
) -> tuple[float | None, float | None]:
    """Straight-line depreciation with a salvage floor.

    Returns (indemnity_value, depreciation_rate_applied).
    """
    if replacement is None:
        return None, None
    life = effective_life or 10.0
    if life <= 0:
        life = 10.0
    age = age_years if age_years is not None else life * 0.5
    rate = min(max(age / life, 0.0), 1.0 - SALVAGE_FLOOR)
    return round(replacement * (1.0 - rate), 2), round(rate, 4)


def value_item(item, photo_captions: list[str] | None = None) -> ValuationResult:
    """Research and value a single item. Never raises; errors land on the result."""
    prompt = build_prompt(item, photo_captions)
    client = _client()
    use_search = os.environ.get("ENABLE_WEB_SEARCH", "1") != "0"
    last_error = ""

    for attempt in range(MAX_RETRIES):
        try:
            raw = _call_model(client, prompt, use_search)
            data = _extract_json(raw)

            replacement = data.get("replacement_value_aud")
            replacement = float(replacement) if isinstance(replacement, (int, float)) else None
            age = data.get("estimated_age_years")
            age = float(age) if isinstance(age, (int, float)) else None
            life = data.get("effective_life_years")
            life = float(life) if isinstance(life, (int, float)) and life > 0 else 10.0

            indemnity, rate = compute_indemnity(replacement, age, life)
            sources = data.get("sources") or []
            if not isinstance(sources, list):
                sources = []

            return ValuationResult(
                identified_as=str(data.get("identified_as") or "")[:2000],
                category=str(data.get("category") or "")[:120],
                replacement_value_aud=replacement,
                estimated_age_years=age,
                effective_life_years=life,
                confidence=str(data.get("confidence") or "low").lower()[:20],
                notes=str(data.get("notes") or "")[:4000],
                sources=[str(s) for s in sources][:10],
                indemnity_value_aud=indemnity,
                depreciation_rate=rate,
            )
        except anthropic.BadRequestError as exc:
            # Most likely the web search tool is unavailable on this account.
            if use_search:
                use_search = False
                last_error = f"web search unavailable, retried without it: {exc}"
                continue
            last_error = str(exc)
            break
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            last_error = str(exc)
            time.sleep(2 ** attempt)
        except Exception as exc:  # noqa: BLE001 - surface parse failures to the assessor
            last_error = str(exc)
            time.sleep(1)

    return ValuationResult(error=last_error or "Valuation failed.", confidence="low")
