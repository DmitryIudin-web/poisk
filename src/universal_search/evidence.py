from __future__ import annotations

import html
import re
from hashlib import sha256
from typing import Any
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
    "чёрный": (r"\bblack\b", r"\bschwarz\b", r"\bчерн(?:ый|ая|ое|ого|ой|ом|ым|ую|ые|ых)?\b", r"\bчёрн(?:ый|ая|ое|ого|ой|ом|ым|ую|ые|ых)?\b"),
    "серый": (r"\bgray\b", r"\bgrey\b", r"\bgrau\b", r"\bсер(?:ый|ая|ое|ого|ой|ом|ым|ую|ые|ых)\b"),
    "синий": (r"\bblue\b", r"\bblau\b", r"\bсин(?:ий|яя|ее|его|ей|ем|им|юю|ие|их)\b", r"\bголуб(?:ой|ая|ое|ого|ой|ом|ым|ую|ые|ых)\b"),
    "голубой": (r"\bblue\b", r"\bblau\b", r"\bголуб(?:ой|ая|ое|ого|ой|ом|ым|ую|ые|ых)\b", r"\bсин(?:ий|яя|ее|его|ей|ем|им|юю|ие|их)\b"),
    "бордо": (r"\bburgundy\b", r"\bbordeaux\b", r"\bdark red\b", r"\bmaroon\b", r"\bбордо\b", r"\bтемно-красн\w*\b", r"\bтёмно-красн\w*\b"),
    "красный": (r"\bred\b", r"\brot\b", r"\bкрасн\w*\b"),
    "белый": (r"\bwhite\b", r"\bweiß\b", r"\bweiss\b", r"\bбел\w*\b"),
}

_NEW_PATTERNS = (r"\bbrand new\b", r"\bnew vehicle\b", r"\bnew car\b", r"\bneuwagen\b", r"\bneu\b", r"\bнов(?:ый|ая|ое)\b", r"\b0\s*km\b")
_USED_PATTERNS = (r"\bused\b", r"\bpre-owned\b", r"\bgebraucht\b", r"\bподержан", r"\bб/?у\b")


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


def _norm_phrase(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).split())


def _phrase_present(text: str, phrase: str) -> bool:
    needle = _norm_phrase(phrase)
    haystack = _norm_phrase(text)
    return bool(needle and needle in haystack)


def _feature_patterns(feature: str) -> tuple[str, ...]:
    key = feature.strip().casefold()
    return FEATURE_ALIASES.get(key, (re.escape(feature.strip()),))


def feature_evidence(text: str, feature: str) -> dict[str, object]:
    for pattern in _feature_patterns(feature):
        match = re.search(pattern, text, re.I)
        if match:
            return {"value": True, "source_text": match.group(0)[:120], "source": "text"}
    return {"value": None, "source_text": None, "source": "text"}


def _requested_color(text: str, requested_colors: list[str]) -> str | None:
    for requested in requested_colors:
        aliases = COLOR_ALIASES.get(requested.casefold(), (re.escape(requested.casefold()),))
        if any(re.search(alias, text, re.I) for alias in aliases):
            return requested
    return None


def _known_color(text: str) -> str | None:
    for name, aliases in COLOR_ALIASES.items():
        if any(re.search(alias, text, re.I) for alias in aliases):
            return name
    return None


def _condition(text: str) -> str | None:
    if any(re.search(pattern, text, re.I) for pattern in _NEW_PATTERNS):
        return "new"
    if any(re.search(pattern, text, re.I) for pattern in _USED_PATTERNS):
        return "used"
    return None


def _body_variant_matches(actual: str, requested: list[str]) -> bool:
    actual_norm = _norm_phrase(actual)
    return any(_norm_phrase(item) in actual_norm or actual_norm in _norm_phrase(item) for item in requested if item.strip())


def _finalize_status(listing: Listing, failures: list[str]) -> Listing:
    listing.missing = sorted(set(listing.missing))
    if failures:
        listing.status = "irrelevant"
    elif listing.missing:
        listing.status = "candidate"
    else:
        listing.status = "relevant"
    return listing


def parse_listing(url: str, source: str, title: str, body_text: str, profile: SearchProfile, snippet: str = "") -> Listing:
    text = f"{title} {snippet} {body_text}".strip()
    preferred_year_text = f"{title} {snippet}".strip()
    years = [int(value) for value in _YEAR_RE.findall(preferred_year_text)] or [int(value) for value in _YEAR_RE.findall(body_text)]
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

    color = _requested_color(text, profile.colors) if profile.colors else None
    observed_color = _known_color(text)
    evidence = {feature: feature_evidence(text, feature) for feature in profile.required_features}
    missing: list[str] = [name for name, item in evidence.items() if item["value"] is not True]
    failures: list[str] = []

    if not _phrase_present(text, profile.make):
        missing.append("make")
    if not _phrase_present(text, profile.model):
        missing.append("model")
    if profile.trim and not _phrase_present(text, profile.trim):
        missing.append("trim")

    if year is None and (profile.year_from or profile.year_to):
        missing.append("year")
    if profile.year_from and year is not None and year < profile.year_from:
        failures.append("year")
    if profile.year_to and year is not None and year > profile.year_to:
        failures.append("year")

    if profile.max_mileage_km is not None and mileage is None:
        missing.append("mileage")
    if profile.max_mileage_km is not None and mileage is not None and mileage > profile.max_mileage_km:
        failures.append("mileage")

    if profile.colors:
        if color is None and observed_color is None:
            missing.append("color")
        elif color is None and observed_color is not None:
            failures.append("color")

    condition = _condition(text)
    if profile.condition in {"new", "used"}:
        if condition is None:
            missing.append("condition")
        elif condition != profile.condition:
            failures.append("condition")

    for excluded in profile.excluded_features:
        if feature_evidence(text, excluded)["value"] is True:
            failures.append(f"excluded:{excluded}")

    if profile.body_variants:
        missing.append("body_variant")
    if profile.export_vat_required:
        missing.append("export_vat")

    if profile.max_price is not None and not profile.export_vat_required:
        if price is None:
            missing.append("price")
        elif profile.price_currency and currency != profile.price_currency.upper():
            missing.append("price_currency")
        elif price > profile.max_price:
            failures.append("price")

    listing = Listing(
        url=canonical_url(url), source=source, title=title.strip()[:300], snippet=snippet.strip()[:1000],
        price=price, currency=currency, year=year, mileage_km=mileage, color=color, vin=vin,
        evidence=evidence, missing=missing,
    )
    _finalize_status(listing, failures)
    listing.fingerprint = fingerprint(listing.url, listing.vin, listing.title)
    return listing


def apply_page_enrichment(listing: Listing, profile: SearchProfile, enrichment: Any) -> Listing:
    listing.image_urls = list(dict.fromkeys(getattr(enrichment, "image_urls", []) or []))[:16]
    listing.location = getattr(enrichment, "location", None) or listing.location
    listing.body_variant = getattr(enrichment, "body_variant", None) or listing.body_variant
    listing.regional_spec = getattr(enrichment, "regional_spec", None) or listing.regional_spec
    listing.export_status = getattr(enrichment, "export_status", None)
    listing.export_vat = getattr(enrichment, "export_vat", None)
    listing.vat_status = getattr(enrichment, "vat_status", None)
    listing.net_price = getattr(enrichment, "net_price", None)
    listing.gross_price = getattr(enrichment, "gross_price", None)
    listing.export_price = getattr(enrichment, "export_price", None)
    enrichment_currency = getattr(enrichment, "price_currency", None)
    if listing.currency is None and enrichment_currency:
        listing.currency = str(enrichment_currency).upper()

    failures: list[str] = []
    if listing.status == "irrelevant":
        failures.append("preexisting")

    if profile.body_variants:
        if listing.body_variant and _body_variant_matches(listing.body_variant, profile.body_variants):
            listing.missing = [name for name in listing.missing if name != "body_variant"]
        elif listing.body_variant:
            failures.append("body_variant")

    if profile.export_vat_required:
        if listing.export_vat is True:
            listing.missing = [name for name in listing.missing if name != "export_vat"]
        elif "export_vat" not in listing.missing:
            listing.missing.append("export_vat")

    if profile.max_price is not None and profile.export_vat_required:
        effective_price = listing.export_price or listing.net_price
        if effective_price is None:
            if "export_price" not in listing.missing:
                listing.missing.append("export_price")
        else:
            listing.missing = [name for name in listing.missing if name != "export_price"]
            if profile.price_currency and listing.currency and listing.currency != profile.price_currency.upper():
                if "price_currency" not in listing.missing:
                    listing.missing.append("price_currency")
            elif effective_price > profile.max_price:
                failures.append("price")

    _finalize_status(listing, failures)
    listing.fingerprint = fingerprint(listing.url, listing.vin, listing.title)
    return listing
