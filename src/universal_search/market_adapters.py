from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

from .adapters import PageEnrichment


def merge_enrichment(base: PageEnrichment | None, extra: PageEnrichment | None) -> PageEnrichment | None:
    if base is None:
        return extra
    if extra is None:
        return base
    base.text = (base.text + " " + extra.text).strip()
    base.image_urls = list(dict.fromkeys([*base.image_urls, *extra.image_urls]))[:20]
    for name in (
        "location", "body_variant", "regional_spec", "export_status", "export_vat",
        "vat_status", "net_price", "gross_price", "export_price", "price_currency",
    ):
        value = getattr(extra, name, None)
        if value is not None:
            setattr(base, name, value)
    return base


def autoscout_enrichment(base: PageEnrichment) -> PageEnrichment:
    text = base.text
    if re.search(r"\b(?:mwst\.?|mehrwertsteuer)\s*ausweisbar\b|\bvat\s+deductible\b", text, re.I):
        base.vat_status = base.vat_status or "VAT deductible"
    if re.search(r"\bexport(?:preis| price| only)?\b|\bnon[- ]?eu\b", text, re.I):
        base.export_status = True
    if re.search(r"\b(?:US|USA|American)\s*(?:import|specs?|specification)\b", text, re.I):
        base.regional_spec = base.regional_spec or "US"
    elif re.search(r"\bGCC\s*(?:specs?|specification)\b", text, re.I):
        base.regional_spec = base.regional_spec or "GCC"
    return base


def myauto_product_id(url: str) -> str | None:
    path = urlparse(url).path
    candidates = re.findall(r"(?<!\d)(\d{6,12})(?!\d)", path)
    candidates = [value for value in candidates if not (1900 <= int(value) <= 2099)]
    return max(candidates, key=len) if candidates else None


def myauto_api_url(url: str) -> str | None:
    product_id = myauto_product_id(url)
    return f"https://api2.myauto.ge/ka/products/{product_id}" if product_id else None


def _walk(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*path, str(index)))
    else:
        yield path, value


def _maybe_image(path: tuple[str, ...], value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    lowered_key = ".".join(path).casefold()
    lowered = value.casefold()
    if not any(token in lowered_key for token in ("photo", "image", "pic")):
        return None
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return None
    if not any(ext in lowered for ext in (".jpg", ".jpeg", ".png", ".webp")):
        return None
    return value


def enrich_myauto_payload(url: str, payload: dict[str, Any]) -> PageEnrichment:
    fragments: list[str] = []
    images: list[str] = []
    location: str | None = None
    body_variant: str | None = None
    price_currency: str | None = None
    price_value: float | None = None
    mileage_value: int | None = None

    for path, value in _walk(payload):
        if value is None or isinstance(value, (dict, list)):
            continue
        key = ".".join(path).casefold()
        if isinstance(value, (str, int, float, bool)):
            text_value = str(value).strip()
            if text_value and len(text_value) <= 500:
                fragments.append(f"{path[-1] if path else 'value'}: {text_value}")
        image = _maybe_image(path, value)
        if image and image not in images:
            images.append(image)
        if location is None and isinstance(value, str) and any(token in key for token in ("city", "location", "address")):
            if re.search(r"Tbilisi|თბილის|Batumi|ბათუმ|Rustavi|რუსთავ|Kutaisi|ქუთაის", value, re.I):
                location = value.strip()
        if body_variant is None and isinstance(value, str) and re.search(r"\bESV\b", value, re.I):
            body_variant = "ESV"
        if price_currency is None and any(token in key for token in ("currency", "currency_id")):
            token = str(value).upper()
            if token in {"USD", "EUR", "GEL", "₾"}:
                price_currency = "GEL" if token == "₾" else token
        if price_value is None and "price" in key and isinstance(value, (int, float)) and float(value) > 0:
            price_value = float(value)
        if mileage_value is None and any(token in key for token in ("mileage", "odometer", "run")):
            try:
                candidate = int(float(str(value)))
                if 0 <= candidate <= 2_000_000:
                    mileage_value = candidate
            except ValueError:
                pass

    if mileage_value is not None:
        fragments.append(f"{mileage_value} km")
    if price_value is not None and price_currency:
        fragments.append(f"{price_value:.0f} {price_currency}")
    text = " ".join(fragments)
    if re.search(r"\bnew\b|ახალი|новый", text, re.I):
        text += " new vehicle"
    if re.search(r"\bcustoms(?: cleared| paid)?\b|განბაჟ|растамож", text, re.I):
        text += " customs information"
    return PageEnrichment(
        text=text,
        image_urls=images[:20],
        location=location,
        body_variant=body_variant,
        price_currency=price_currency,
    )


def apply_site_adapter(url: str, enrichment: PageEnrichment | None) -> PageEnrichment | None:
    if enrichment is None:
        return None
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if "autoscout24." in host:
        return autoscout_enrichment(enrichment)
    return enrichment
