from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .models import Evidence, Listing
from .profiles import TargetProfile, match_evidence


_SPACE = re.compile(r"\s+")
_VIN = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b", re.IGNORECASE)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_CANDIDATE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_MILEAGE = re.compile(r"(?:пробег|mileage)\D{0,18}([\d\s\u00a0]{1,12})\s*(?:км|km)\b", re.IGNORECASE)
_PRICE_AMOUNT = r"(?:\d{1,3}(?:[\s\u00a0,.]\d{3})+|\d{3,})"
_MONEY = re.compile(
    rf"(?:(?P<prefix>€|eur|euro|₾|gel|lari|\$|usd|dollar)\s*(?P<prefix_amount>{_PRICE_AMOUNT})|(?P<suffix_amount>{_PRICE_AMOUNT})\s*(?P<suffix>₽|руб(?:\.|лей|ля)?|kgs|сом|₸|тенге|kzt|€|eur|euro|₾|gel|lari|\$|usd|dollar))",
    re.IGNORECASE,
)
_CASH_LABEL = r"(?:за наличные|без кредита|полная цена|наличный расч[её]т)"
_CASH_AFTER = re.compile(
    rf"{_CASH_LABEL}\D{{0,30}}({_PRICE_AMOUNT})\s*(₽|руб(?:\.|лей|ля)?|kgs|сом|₸|тенге|kzt|€|eur|euro|₾|gel|lari|\$|usd|dollar)",
    re.IGNORECASE,
)
_CASH_BEFORE = re.compile(
    rf"({_PRICE_AMOUNT})\s*(₽|руб(?:\.|лей|ля)?|kgs|сом|₸|тенге|kzt|€|eur|euro|₾|gel|lari|\$|usd|dollar)\D{{0,30}}{_CASH_LABEL}",
    re.IGNORECASE,
)

_BLACK = ("черн", "black", "schwarz", "ebony")
_NEGATIVE_STOCK = ("в пути", "под заказ", "в поставке", "на подходе", "ожидается", "in transit")
_POSITIVE_STOCK = ("физически в наличии", "в наличии", "в салоне", "на площадке", "in stock")
_SOLD = ("продан", "продано", "снят с продажи", "объявление снято", "sold")
_LEGACY_TARGET_ID = "teramont-pro-2026"
_LEGACY_TARGET_NAME = "Volkswagen Teramont Pro 2026"


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _excerpt(value: str, limit: int = 120) -> str:
    return value[:limit].strip()


def _safe_title(value: str) -> str:
    value = _EMAIL.sub("", _clean(value))

    def remove_phone(match: re.Match[str]) -> str:
        return "" if len(re.sub(r"\D", "", match.group(0))) >= 10 else match.group(0)

    value = _PHONE_CANDIDATE.sub(remove_phone, value)
    value = re.sub(r"(?:телефон|тел\.?|phone)\s*[:\-]?", "", value, flags=re.IGNORECASE)
    return _excerpt(_clean(value).strip(" ,;|-"), 180)


def _boolean_evidence(text: str, positive: tuple[str, ...], negative: tuple[str, ...] = ()) -> Evidence:
    lowered = text.casefold()
    for marker in negative:
        if marker.casefold() in lowered:
            return Evidence(False, marker)
    for marker in positive:
        if marker.casefold() in lowered:
            return Evidence(True, marker)
    return Evidence(None, None)


def _legacy_teramont_evidence(text: str) -> tuple[Evidence, Evidence, Evidence]:
    lowered = text.casefold()
    model_positive = bool(re.search(r"(?:volkswagen\s+)?teramont\s*pro|терамонт\s*про|途昂\s*pro", text, re.IGNORECASE))
    model_seen = "teramont" in lowered or "терамонт" in lowered
    model_match = Evidence(True, "Teramont Pro") if model_positive else Evidence(False, "Teramont without Pro") if model_seen else Evidence(None, None)
    top_trim = _boolean_evidence(
        text,
        (" peak", "summit", "максимальная комплектация", "топовая комплектация", "top trim", "maximum trim"),
    )
    dcc = _boolean_evidence(
        text,
        ("dcc", "adaptive chassis control", "адаптивная подвеска", "адаптивное шасси"),
        ("без dcc", "no dcc"),
    )
    return model_match, top_trim, dcc


def _color_evidence(text: str, labels: tuple[str, ...], *, primary_only: bool = False) -> Evidence:
    lowered = text.casefold()
    if "black on black" in lowered or "черный на черном" in lowered or "чёрный на чёрном" in lowered:
        return Evidence(True, "black on black")
    for label in labels:
        match = re.search(rf"(?:{label})\b\s*[:\-]?\s*([^.;,|]{{1,40}})", text, re.IGNORECASE)
        if not match:
            continue
        value = _excerpt(match.group(1), 40)
        color = value.split("/", 1)[0].strip() if primary_only else value
        return Evidence(any(marker in color.casefold() for marker in _BLACK), value)
    return Evidence(None, None)


def _parse_number(value: str) -> int | None:
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _extract_price(
    text: str,
    metadata: Mapping[str, Any],
    *,
    default_metadata_currency: str | None = None,
) -> tuple[int | None, int | None, str | None, str | None]:
    matches = list(_MONEY.finditer(text))
    advertised = None
    currency = None
    cash = None
    qualifier = None
    if matches:
        first = matches[0]
        amount = first.group("prefix_amount") or first.group("suffix_amount")
        token = first.group("prefix") or first.group("suffix")
        advertised = _parse_number(amount)
        currency = _currency(token)
        sentence_start = max(text.rfind(marker, 0, first.start()) for marker in ".;|") + 1
        sentence_ends = [position for marker in ".;|" if (position := text.find(marker, first.end())) >= 0]
        sentence_end = min(sentence_ends) if sentence_ends else len(text)
        window = text[sentence_start:sentence_end].casefold()
        conditional = any(marker in window for marker in ("кредит", "trade-in", "trade in", "трейд-ин", "страхов"))
        unconditional = any(marker in window for marker in ("за наличные", "без кредита", "полная цена", "наличный расчет", "наличный расчёт"))
        qualifier = "conditional" if conditional and not unconditional else "unconditional" if unconditional else "unqualified"
        if unconditional:
            cash = advertised

    explicit_cash = _CASH_AFTER.search(text) or _CASH_BEFORE.search(text)
    if explicit_cash:
        cash = _parse_number(explicit_cash.group(1))
        currency = _currency(explicit_cash.group(2))

    meta_price = metadata.get("price")
    metadata_currency = metadata.get("price_currency")
    parsed_metadata_currency = _currency(str(metadata_currency)) if metadata_currency else default_metadata_currency
    if advertised is None and isinstance(meta_price, (int, float)) and not isinstance(meta_price, bool):
        advertised = int(meta_price)
        currency = parsed_metadata_currency
        qualifier = "unqualified"
    meta_cash = metadata.get("cash_price")
    if isinstance(meta_cash, (int, float)) and not isinstance(meta_cash, bool):
        cash = int(meta_cash)
        currency = parsed_metadata_currency
        qualifier = "unconditional"
    return cash, advertised, currency, qualifier


def _currency(value: str) -> str | None:
    lowered = value.casefold()
    if "eur" in lowered or "€" in value or "euro" in lowered:
        return "EUR"
    if "gel" in lowered or "₾" in value or "lari" in lowered:
        return "GEL"
    if "usd" in lowered or "$" in value or "dollar" in lowered:
        return "USD"
    if "сом" in lowered or "kgs" in lowered:
        return "KGS"
    if "тенге" in lowered or "kzt" in lowered or "₸" in value:
        return "KZT"
    if "руб" in lowered or "₽" in value or "rub" in lowered:
        return "RUB"
    return None


def _extract_model_year(text: str, metadata: Mapping[str, Any]) -> int | None:
    structured = metadata.get("model_year")
    if isinstance(structured, (int, float)) and not isinstance(structured, bool):
        return int(structured)
    explicit = re.search(r"(?:model year|modelljahr|модельный год|год выпуска|year)\D{0,12}(20\d{2})", text, re.IGNORECASE)
    if explicit:
        return int(explicit.group(1))
    if re.search(r"first registration|erstzulassung|первая регистрация", text, re.IGNORECASE):
        return None
    fallback = re.search(r"\b(20\d{2})\b", text)
    return int(fallback.group(1)) if fallback else None


def _extract_location(text: str, metadata: Mapping[str, Any]) -> str | None:
    explicit = _clean(metadata.get("location"))
    if explicit:
        return explicit
    for place in (
        "Бишкек", "Алматы", "Астана", "Казахстан", "Кыргызстан", "Беларусь",
        "Армения", "Москва", "Санкт-Петербург", "Екатеринбург", "Самара",
        "Казань", "Новосибирск", "Россия",
    ):
        if place.casefold() in text.casefold():
            return place
    return None


def _region(source: str, location: str | None, text: str) -> str:
    haystack = f"{location or ''} {text}".casefold()
    if "бишкек" in haystack:
        return "bishkek"
    if any(marker in haystack for marker in ("кыргыз", "казахстан", "алматы", "астана", "беларус", "армени")):
        return "eaeu_other"
    if any(marker in haystack for marker in ("росси", "москва", "петербург", "екатеринбург", "самара", "казань", "новосибирск")):
        return "russia"
    if source in {"autoru", "drom", "avito"}:
        return "russia"
    return "unknown"


def normalize_listing(
    source: str,
    url: str,
    listing_id: str | None,
    text: str,
    metadata: Mapping[str, Any] | None,
    profile: TargetProfile | None = None,
    market: str | None = None,
) -> Listing:
    metadata = metadata or {}
    clean_text = _clean(text)
    if profile is None:
        model_match, top_trim, dcc = _legacy_teramont_evidence(clean_text)
        target_id = _LEGACY_TARGET_ID
        target_name = _LEGACY_TARGET_NAME
    else:
        model_match = match_evidence(clean_text, profile.evidence_rules["model_match"])
        top_trim = match_evidence(clean_text, profile.evidence_rules["top_trim"])
        dcc = match_evidence(clean_text, profile.evidence_rules["dcc"]) if "dcc" in profile.evidence_rules else Evidence(None, None)
        target_id = profile.target_id
        target_name = profile.display_name
    year = _extract_model_year(clean_text, metadata)
    exterior = _color_evidence(clean_text, (r"цвет\s+кузова", r"кузов", r"exterior(?:\s+color)?"))
    interior = _color_evidence(
        clean_text,
        (r"цвет\s+салона", r"салон", r"интерьер", r"interior(?:\s+color)?"),
        primary_only=True,
    )
    powertrain_match = match_evidence(clean_text, profile.evidence_rules["powertrain_match"]) if profile is not None and "powertrain_match" in profile.evidence_rules else Evidence(None, None)
    rear_seat_entertainment = match_evidence(clean_text, profile.evidence_rules["rear_seat_entertainment"]) if profile is not None and "rear_seat_entertainment" in profile.evidence_rules else Evidence(None, None)
    steering_left = match_evidence(clean_text, profile.evidence_rules["steering_left"]) if profile is not None and "steering_left" in profile.evidence_rules else Evidence(None, None)
    is_new = _boolean_evidence(clean_text, ("новый автомобиль", "новый", "без пробега", "brand new"), ("с пробегом", "used"))
    mileage_match = _MILEAGE.search(clean_text)
    mileage = _parse_number(mileage_match.group(1)) if mileage_match else 0 if is_new.value is True else None
    in_stock = _boolean_evidence(clean_text, _POSITIVE_STOCK, _NEGATIVE_STOCK)
    sold = _boolean_evidence(clean_text, _SOLD)
    if sold.value is True:
        in_stock = Evidence(False, sold.source_text)
    cash_price, advertised_price, currency, qualifier = _extract_price(
        clean_text,
        metadata,
        default_metadata_currency="RUB" if profile is None or profile.target_id == _LEGACY_TARGET_ID else None,
    )
    vin_match = _VIN.search(clean_text)
    vin = vin_match.group(1).upper() if vin_match else None
    location = _extract_location(clean_text, metadata)

    epts = None
    if re.search(r"э\s*птс.{0,25}(?:нет|отсутств|не оформлен)", clean_text, re.IGNORECASE):
        epts = "missing"
    elif re.search(r"э\s*птс.{0,25}(?:действующ|оформлен|выдан)", clean_text, re.IGNORECASE):
        epts = "valid"

    recycling = None
    if re.search(r"коммерческ\w*\s+утильсбор.{0,30}(?:не уплачен|не списан|не оплачен)", clean_text, re.IGNORECASE):
        recycling = "unpaid"
    elif re.search(r"коммерческ\w*\s+утильсбор.{0,30}(?:уплачен|списан|оплачен)", clean_text, re.IGNORECASE):
        recycling = "paid"

    title = _safe_title(str(metadata.get("title") or "")) or target_name
    return Listing(
        source=source,
        url=url,
        listing_id=str(listing_id) if listing_id is not None else None,
        title=title,
        model_match=model_match,
        year=year,
        exterior_black=exterior,
        interior_black=interior,
        top_trim=top_trim,
        dcc=dcc,
        mileage_km=mileage,
        is_new=is_new,
        in_stock=in_stock,
        sold=sold,
        cash_price=cash_price,
        advertised_price=advertised_price,
        price_currency=currency,
        price_qualifier=qualifier,
        vin=vin,
        region=market if market in {"russia", "kyrgyzstan", "georgia", "europe"} else _region(source, location, clean_text),
        location=location,
        epts_status=epts,
        commercial_recycling_fee_status=recycling,
        target_id=target_id,
        target_name=target_name,
        powertrain_match=powertrain_match,
        rear_seat_entertainment=rear_seat_entertainment,
        steering_left=steering_left,
    )
