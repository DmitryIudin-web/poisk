from __future__ import annotations

import html
import re
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

from .schema import Listing, SearchProfile

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_PRICE_SUFFIX_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[\s.,]\d{3})+|\d{4,9})(?:[.,]\d{1,2})?\s*(EUR|€|USD|\$|AED|CHF|CZK|RUB|₽|GEL|KGS)", re.I)
_PRICE_PREFIX_RE = re.compile(r"(EUR|€|USD|\$|AED|CHF|CZK|RUB|₽|GEL|KGS)\s*(\d{1,3}(?:[\s.,]\d{3})+|\d{4,9})(?:[.,]\d{1,2})?", re.I)
_YEAR_RE = re.compile(r"\b(20[0-3]\d)\b")
_MILEAGE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[\s.,]\d{3})*|\d{1,6})\s*(?:km|км)\b", re.I)
_VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b", re.I)

FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "панорамная крыша": ("panoramic roof", "panoramic sunroof", "panoramadach", "skyglass", "панорам"),
    "задние экраны": ("rear entertainment", "rear seat entertainment", "rear screens", "rear monitors", "monitore in den rücksitzen", "задн.*экран", "задн.*монитор"),
    "широкая цифровая торпеда": ("55.?inch", "55.?дюй", "horizon display", "curved oled", "curved display", "digital cockpit", "oled cockpit", "цифров.*торпед", "широк.*экран"),
    "night vision": ("night vision", "nachtsicht", "ночн.*виден"),
    "холодильник": ("refrigerator", "fridge", "coolbox", "kühlschrank", "холодиль"),
    "массаж": ("massage", "massagesitze", "массаж"),
    "360 камера": ("360.?camera", "surround view", "around view", "камера.?360"),
}

COLOR_ALIASES: dict[str, tuple[str, ...]] = {
    "чёрный": ("black", "schwarz", "черн", "чёрн"),
    "серый": ("gray", "grey", "grau", "сер"),
    "синий": ("blue", "blau", "син", "голуб"),
    "голубой": ("blue", "blau", "голуб", "син"),
    "бордо": ("burgundy", "bordeaux", "dark red", "maroon", "бордо", "темно-крас", "тёмно-крас"),
    "красный": ("red", "rot", "красн"),
    "белый": ("white", "weiß", "weiss", "бел"),
}


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw))).strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def fingerprint(url: str, vin: str | None, title: str) -> str:
    basis = vin.upper() if vin else f"{canonical_url(url)}|{title.casefold().strip()}"
    return sha256(basis.encode("utf-8")).hexdigest()[:24]


def _number(raw: str) -> float:
    compact = re.sub(r"[\s.]", "", raw).replace(",", ".")
    try:
        return float(compact)
    except ValueError:
        return float(re.sub(r"\D", "", raw) or 0)


def _feature_patterns(feature: str) -> tuple[str, ...]:
    key = feature.strip().casefold()
    return FEATURE_ALIASES.get(key, (re.escape(feature.strip()),))


def feature_evidence(text: str, feature: str) -> dict[str, object]:
    for pattern in _feature_patterns(feature):
        match = re.search(pattern, text, re.I)
        if match:
            return {"value": True, "source_text": match.group(0)[:120]}
    return {"value": None, "source_text": None}


def parse_listing(url: str, source: str, title: str, body_text: str, profile: SearchProfile, snippet: str = "") -> Listing:
    text = f"{title} {snippet} {body_text}"
    years = [int(value) for value in _YEAR_RE.findall(text)]
    year = max(years) if years else None
    mileage_match = _MILEAGE_RE.search(text)
    mileage = int(_number(mileage_match.group(1))) if mileage_match else None
    vin_match = _VIN_RE.search(text)
    vin = vin_match.group(1).upper() if vin_match else None

    price = None
    currency = None
    match = _PRICE_SUFFIX_RE.search(text)
    if match:
        price = _number(match.group(1))
        token = match.group(2).upper()
        currency = {"€": "EUR", "$": "USD", "₽": "RUB"}.get(token, token)
    else:
        match = _PRICE_PREFIX_RE.search(text)
        if match:
            token = match.group(1).upper()
            price = _number(match.group(2))
            currency = {"€": "EUR", "$": "USD", "₽": "RUB"}.get(token, token)

    lowered = text.casefold()
    color = None
    for requested in profile.colors:
        aliases = COLOR_ALIASES.get(requested.casefold(), (requested.casefold(),))
        if any(re.search(alias, lowered, re.I) for alias in aliases):
            color = requested
            break

    evidence = {feature: feature_evidence(text, feature) for feature in profile.required_features}
    missing: list[str] = [name for name, item in evidence.items() if item["value"] is not True]
    failures: list[str] = []
    if profile.year_from and year is not None and year < profile.year_from:
        failures.append("year")
    if profile.year_to and year is not None and year > profile.year_to:
        failures.append("year")
    if profile.max_mileage_km is not None and mileage is not None and mileage > profile.max_mileage_km:
        failures.append("mileage")
    if profile.colors and color is None:
        missing.append("color")
    if profile.max_price is not None and price is not None and price > profile.max_price:
        failures.append("price")

    status = "irrelevant" if failures else ("candidate" if missing or year is None or mileage is None else "relevant")
    listing = Listing(
        url=canonical_url(url), source=source, title=title.strip()[:300], snippet=snippet.strip()[:1000],
        price=price, currency=currency, year=year, mileage_km=mileage, color=color, vin=vin,
        evidence=evidence, status=status, missing=sorted(set(missing)),
    )
    listing.fingerprint = fingerprint(listing.url, listing.vin, listing.title)
    return listing
