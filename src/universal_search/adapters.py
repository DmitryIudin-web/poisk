from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from .evidence import html_to_text

_MONEY_AFTER = re.compile(r"(?<!\d)(\d{1,3}(?:[\s.,]\d{3})+|\d{4,9})(?:[.,]\d{1,2})?\s*(EUR|€|USD|\$|AED|CHF|CZK|RUB|₽|GEL|KGS)", re.I)
_MONEY_BEFORE = re.compile(r"(EUR|€|USD|\$|AED|CHF|CZK|RUB|₽|GEL|KGS)\s*(\d{1,3}(?:[\s.,]\d{3})+|\d{4,9})(?:[.,]\d{1,2})?", re.I)
_EXPORT_LABELS = (r"\bnon[- ]?eu exportpreis\b", r"\bexportpreis\b", r"\bexport price\b", r"\bT1(?:\s+price)?\b")
_NET_LABELS = (r"\bnetto\b", r"\bnet price\b", r"\bex[- ]?vat\b", r"\bexcluding vat\b", r"\bvat excluded\b", r"\bexkl\.?\s*(?:mwst|vat)\b", r"\bohne mwst\b")
_GROSS_LABELS = (r"\bbrutto\b", r"\bgross price\b", r"\bincluding vat\b", r"\bvat included\b", r"\binkl\.?\s*(?:mwst|vat)\b")


def _number(raw: str) -> float:
    compact = re.sub(r"[\s.]", "", raw).replace(",", ".")
    try:
        return float(compact)
    except ValueError:
        return float(re.sub(r"\D", "", raw) or 0)


def _currency(token: str | None) -> str | None:
    if not token:
        return None
    token = token.upper()
    return {"€": "EUR", "$": "USD", "₽": "RUB"}.get(token, token)


def _money_hits(text: str) -> list[tuple[int, float, str | None]]:
    hits: list[tuple[int, float, str | None]] = []
    for match in _MONEY_AFTER.finditer(text):
        hits.append((match.start(), _number(match.group(1)), _currency(match.group(2))))
    for match in _MONEY_BEFORE.finditer(text):
        hits.append((match.start(), _number(match.group(2)), _currency(match.group(1))))
    return sorted(hits, key=lambda item: item[0])


def _labeled_price(text: str, labels: tuple[str, ...]) -> tuple[float | None, str | None]:
    """Find the nearest money amount after a label, otherwise the nearest one before it."""
    for label in labels:
        for match in re.finditer(label, text, re.I):
            after = text[match.end(): min(len(text), match.end() + 100)]
            after_hits = _money_hits(after)
            if after_hits:
                _, value, currency = after_hits[0]
                return value, currency
            before = text[max(0, match.start() - 100): match.start()]
            before_hits = _money_hits(before)
            if before_hits:
                _, value, currency = before_hits[-1]
                return value, currency
    return None, None


@dataclass
class PageEnrichment:
    text: str
    image_urls: list[str] = field(default_factory=list)
    location: str | None = None
    body_variant: str | None = None
    regional_spec: str | None = None
    export_status: bool | None = None
    export_vat: bool | None = None
    vat_status: str | None = None
    net_price: float | None = None
    gross_price: float | None = None
    export_price: float | None = None
    price_currency: str | None = None


class _PageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images: list[str] = []
        self.ld_objects: list[Any] = []
        self._in_ld = False
        self._ld_parts: list[str] = []

    def _image(self, raw: str | None) -> None:
        if not raw:
            return
        value = raw.strip().split(" ", 1)[0]
        if not value or value.startswith(("data:", "javascript:")):
            return
        url = urljoin(self.base_url, value)
        if url.startswith(("http://", "https://")) and url not in self.images:
            self.images.append(url)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {str(k).casefold(): (v or "") for k, v in attrs}
        if tag.casefold() == "script" and "ld+json" in data.get("type", "").casefold():
            self._in_ld = True
            self._ld_parts = []
            return
        if tag.casefold() == "meta":
            key = (data.get("property") or data.get("name") or "").casefold()
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self._image(data.get("content"))
        if tag.casefold() == "img":
            for key in ("data-src", "data-lazy-src", "src", "srcset"):
                if data.get(key):
                    self._image(data[key])
                    break

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._in_ld:
            return
        self._in_ld = False
        raw = "".join(self._ld_parts).strip()
        self._ld_parts = []
        if not raw:
            return
        try:
            self.ld_objects.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _image_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _image_values(item)
    elif isinstance(value, dict):
        for key in ("url", "contentUrl", "thumbnailUrl"):
            if isinstance(value.get(key), str):
                yield value[key]


def _address_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    parts = [value.get("streetAddress"), value.get("addressLocality"), value.get("addressRegion"), value.get("postalCode"), value.get("addressCountry")]
    text = ", ".join(str(part).strip() for part in parts if part)
    return text or None


def _structured_facts(parser: _PageParser) -> tuple[list[str], dict[str, Any]]:
    facts: list[str] = []
    found: dict[str, Any] = {}
    for root in parser.ld_objects:
        for item in _walk(root):
            for key in ("name", "model", "vehicleConfiguration", "color", "vehicleInteriorColor"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    facts.append(value.strip())
            for key in ("vehicleModelDate", "modelDate", "productionDate", "releaseDate"):
                value = item.get(key)
                if value is not None:
                    match = re.search(r"\b20[0-3]\d\b", str(value))
                    if match:
                        facts.append(match.group(0))
            mileage = item.get("mileageFromOdometer")
            if isinstance(mileage, dict) and mileage.get("value") is not None:
                value = str(mileage.get("value"))
                unit = str(mileage.get("unitText") or mileage.get("unitCode") or "km")
                if unit.casefold() in {"kmt", "km", "kilometer", "kilometre", "kilometers", "kilometres"}:
                    facts.append(f"{value} km")
            for key in ("vehicleIdentificationNumber", "vin"):
                value = item.get(key)
                if isinstance(value, str) and len(value.strip()) == 17:
                    facts.append(value.strip())
            for raw_url in _image_values(item.get("image")):
                url = urljoin(parser.base_url, raw_url)
                if url.startswith(("http://", "https://")) and url not in parser.images:
                    parser.images.append(url)
            offers = item.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                price = offer.get("price") or offer.get("lowPrice")
                currency = offer.get("priceCurrency")
                if price is not None and currency:
                    facts.append(f"{price} {currency}")
                    found.setdefault("price_currency", str(currency).upper())
                available = offer.get("availableAtOrFrom")
                address = _address_text(available.get("address") if isinstance(available, dict) else None)
                if address:
                    found.setdefault("location", address)
            address = _address_text(item.get("address"))
            if address:
                found.setdefault("location", address)
    return facts, found


def _detect_body_variant(text: str) -> str | None:
    if re.search(r"\bESV\b", text, re.I):
        return "ESV"
    if re.search(r"\b(?:long|long wheelbase|LWB)\b", text, re.I):
        return "Long"
    if re.search(r"\b(?:standard wheelbase|SWB)\b", text, re.I):
        return "Standard"
    return None


def _detect_vat(text: str) -> tuple[bool | None, str | None, float | None, float | None, float | None, str | None]:
    export_price, export_currency = _labeled_price(text, _EXPORT_LABELS)
    net_price, net_currency = _labeled_price(text, _NET_LABELS)
    gross_price, gross_currency = _labeled_price(text, _GROSS_LABELS)
    if export_price is not None:
        net_price = net_price or export_price
    explicit_ex_vat = bool(re.search(r"ex[- ]?vat|vat\s*0%|excluding vat|vat excluded|ohne mwst|exkl\.?\s*(?:mwst|vat)", text, re.I))
    if export_price is not None or explicit_ex_vat:
        return True, "export/ex-VAT stated", net_price, gross_price, export_price, export_currency or net_currency or gross_currency
    if re.search(r"mwst\.?\s*ausweisbar|vat\s*deductible|mehrwertsteuer\s*ausweisbar", text, re.I):
        return None, "VAT deductible", net_price, gross_price, None, net_currency or gross_currency
    if re.search(r"vat\s*included|including vat|inkl\.?\s*(?:mwst|vat)|brutto", text, re.I):
        return None, "VAT included", net_price, gross_price, None, net_currency or gross_currency
    return None, None, net_price, gross_price, None, net_currency or gross_currency


def _dubicars(enrichment: PageEnrichment) -> None:
    text = enrichment.text
    if re.search(r"\bcan be exported\b|\bfor export\b|\bexport only\b", text, re.I):
        enrichment.export_status = True
    elif re.search(r"\bcannot be exported\b|\bnot for export\b", text, re.I):
        enrichment.export_status = False
    specs = []
    for name, pattern in (
        ("GCC", r"\bGCC\s*(?:specs?|specification)?\b"),
        ("US", r"\b(?:US|USA|American)\s*(?:specs?|specification)?\b"),
        ("Canadian", r"\bCanadian\s*(?:specs?|specification)?\b"),
        ("European", r"\bEuropean\s*(?:specs?|specification)?\b"),
    ):
        if re.search(pattern, text, re.I):
            specs.append(name)
    if len(specs) == 1:
        enrichment.regional_spec = specs[0]
    elif len(specs) > 1:
        enrichment.regional_spec = "conflict: " + " / ".join(specs)
    for place in ("Dubai", "Sharjah", "Abu Dhabi", "Ajman", "Ras Al Khor"):
        if re.search(rf"\b{re.escape(place)}\b", text, re.I):
            enrichment.location = enrichment.location or place + ", UAE"
            break


def _mobile_de(enrichment: PageEnrichment) -> None:
    text = enrichment.text
    if re.search(r"\bexport(?:preis| price)?\b|\bnon[- ]?eu\b|\bT1\b", text, re.I):
        enrichment.export_status = True
    if re.search(r"mwst\.?\s*ausweisbar|mehrwertsteuer\s*ausweisbar", text, re.I):
        enrichment.vat_status = enrichment.vat_status or "VAT deductible"


def enrich_detail_page(url: str, raw_html: str) -> PageEnrichment:
    parser = _PageParser(url)
    try:
        parser.feed(raw_html)
    except Exception:
        pass
    text = html_to_text(raw_html)
    facts, structured = _structured_facts(parser)
    if facts:
        text = (text + " " + " ".join(facts)).strip()
    export_vat, vat_status, net_price, gross_price, export_price, price_currency = _detect_vat(text)
    enrichment = PageEnrichment(
        text=text,
        image_urls=parser.images[:16],
        location=structured.get("location"),
        body_variant=_detect_body_variant(text),
        export_vat=export_vat,
        vat_status=vat_status,
        net_price=net_price,
        gross_price=gross_price,
        export_price=export_price,
        price_currency=price_currency or structured.get("price_currency"),
    )
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if host.endswith("dubicars.com"):
        _dubicars(enrichment)
    elif host.endswith("mobile.de"):
        _mobile_de(enrichment)
    return enrichment
